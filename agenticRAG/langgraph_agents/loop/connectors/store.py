"""Connection and pending-authorisation stores (stage 5, migration 022).

Two Protocols, each with a Memory and a Pg version and one contract suite
(`test_connectors_store.py`; the Pg runs go through `test_pg_client` as
`eca_user`, under RLS, inside `user_scope(user_id)`).

`PendingStore` (`oauth_pending`): a started authorisation, keyed by the sha256
of its `state`. `consume` is the single-use gate:

    SELECT ... FOR UPDATE   WHERE state_sha256 = $1 AND user_id = $2
                              AND NOT used AND expires_at > $3
    UPDATE ... SET used = true, verifier_enc = NULL

in ONE transaction, so two callbacks with the same state cannot both win (the
second waits on the row lock, then its predicate no longer matches). A wrong
user, an unknown, used or expired state all give `None`: the caller cannot tell
them apart, and a wrong user does not burn the owner's state.

`ConnectionStore` (`user_connections`): tokens as sealed bytes (the service
seals; the store never sees plaintext). `locked(user_id, plugin_id)` is the
per-connection lock refresh runs under:

* Pg: `SELECT ... FOR UPDATE` on the row, inside `user_scope`, held until the
  block ends; the block's `save` updates on the same connection.
* Memory: one `asyncio.Lock` per (user, plugin).

The row is read AFTER the lock is taken (`Locked.current`), so a caller that
waited sees what the winner saved.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Optional, Protocol

ACTIVE = "active"
AUTH_EXPIRED = "auth_expired"
STATUSES = (ACTIVE, AUTH_EXPIRED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Encryption contexts ───────────────────────────────────────────────────

def connection_context(user_id: str, plugin_id: str, purpose: str) -> dict[str, str]:
    """{user_id, plugin_id} (F6) plus which column the blob belongs to, so an
    access blob copied into the refresh column does not open."""
    return {"user_id": str(user_id), "plugin_id": str(plugin_id),
            "purpose": purpose}


def pending_context(user_id: str, plugin_id: str, state_sha256: str) -> dict[str, str]:
    """{user_id, plugin_id, state_sha256} (F5)."""
    return {"user_id": str(user_id), "plugin_id": str(plugin_id),
            "state_sha256": str(state_sha256)}


# ── Records ───────────────────────────────────────────────────────────────

@dataclass
class Connection:
    user_id: str
    plugin_id: str
    access_enc: Optional[bytes] = None
    refresh_enc: Optional[bytes] = None
    expires_at: Optional[datetime] = None
    scopes: list[str] = field(default_factory=list)
    status: str = ACTIVE
    issuer: str = ""
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


@dataclass
class Pending:
    state_sha256: str
    user_id: str
    plugin_id: str
    verifier_enc: Optional[bytes]
    issuer: str
    expires_at: datetime
    used: bool = False
    created_at: datetime = field(default_factory=_now)


class PendingStore(Protocol):
    async def put(self, pending: Pending) -> None: ...

    async def consume(self, user_id: str, state_sha256: str,
                      now: datetime) -> Optional[Pending]: ...


class Locked(Protocol):
    current: Optional[Connection]

    async def save(self, conn: Connection) -> None: ...

    async def mark_expired(self) -> None: ...


class ConnectionStore(Protocol):
    async def get(self, user_id: str, plugin_id: str) -> Optional[Connection]: ...

    async def list(self, user_id: str) -> list[Connection]: ...

    async def upsert(self, conn: Connection) -> None: ...

    async def delete(self, user_id: str, plugin_id: str) -> bool: ...

    def locked(self, user_id: str, plugin_id: str): ...


def _expired_copy(conn: Connection) -> Connection:
    """Tokens are dropped when a connection expires: a dead token is not kept."""
    return dataclasses.replace(conn, access_enc=None, refresh_enc=None,
                               status=AUTH_EXPIRED, updated_at=_now())


# ── Memory ────────────────────────────────────────────────────────────────

class MemoryPendingStore:
    def __init__(self) -> None:
        self._rows: dict[str, Pending] = {}
        self._lock = asyncio.Lock()

    async def put(self, pending: Pending) -> None:
        # Opportunistic purge of this user's used or already-expired rows.
        now = _now()
        for key in [k for k, r in self._rows.items()
                    if str(r.user_id) == str(pending.user_id)
                    and (r.used or r.expires_at < now)]:
            del self._rows[key]
        self._rows[pending.state_sha256] = dataclasses.replace(
            pending, user_id=str(pending.user_id))

    async def consume(self, user_id: str, state_sha256: str,
                      now: datetime) -> Optional[Pending]:
        async with self._lock:
            row = self._rows.get(state_sha256)
            if (row is None or row.used or str(row.user_id) != str(user_id)
                    or row.expires_at <= now):
                return None
            taken = dataclasses.replace(row)
            row.used = True
            row.verifier_enc = None
            return taken


class _MemoryLocked:
    def __init__(self, store: "MemoryConnectionStore", key: tuple[str, str]) -> None:
        self._store = store
        self._key = key
        row = store._rows.get(key)
        self.current: Optional[Connection] = dataclasses.replace(row) if row else None

    async def save(self, conn: Connection) -> None:
        self._store._rows[self._key] = dataclasses.replace(
            conn, user_id=self._key[0], plugin_id=self._key[1], updated_at=_now())
        self.current = dataclasses.replace(self._store._rows[self._key])

    async def mark_expired(self) -> None:
        row = self._store._rows.get(self._key)
        if row is not None:
            self._store._rows[self._key] = _expired_copy(row)
            self.current = dataclasses.replace(self._store._rows[self._key])


class MemoryConnectionStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], Connection] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @staticmethod
    def _key(user_id: str, plugin_id: str) -> tuple[str, str]:
        return (str(user_id), plugin_id)

    async def get(self, user_id: str, plugin_id: str) -> Optional[Connection]:
        row = self._rows.get(self._key(user_id, plugin_id))
        return dataclasses.replace(row) if row else None

    async def list(self, user_id: str) -> list[Connection]:
        uid = str(user_id)
        return [dataclasses.replace(r) for (u, _), r in sorted(self._rows.items())
                if u == uid]

    async def upsert(self, conn: Connection) -> None:
        self._rows[self._key(conn.user_id, conn.plugin_id)] = dataclasses.replace(
            conn, user_id=str(conn.user_id), updated_at=_now())

    async def delete(self, user_id: str, plugin_id: str) -> bool:
        return self._rows.pop(self._key(user_id, plugin_id), None) is not None

    @asynccontextmanager
    async def locked(self, user_id: str, plugin_id: str) -> AsyncIterator[_MemoryLocked]:
        key = self._key(user_id, plugin_id)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield _MemoryLocked(self, key)   # read AFTER the lock is taken


# ── Postgres ──────────────────────────────────────────────────────────────

_CONN_COLS = ("user_id, plugin_id, access_enc, refresh_enc, expires_at, scopes, "
              "status, issuer, created_at, updated_at")

SQL_GET_CONN = (f"SELECT {_CONN_COLS} FROM user_connections "
                "WHERE user_id = $1::uuid AND plugin_id = $2")
SQL_LIST_CONN = (f"SELECT {_CONN_COLS} FROM user_connections "
                 "WHERE user_id = $1::uuid ORDER BY plugin_id")
SQL_LOCK_CONN = SQL_GET_CONN + " FOR UPDATE"
SQL_UPSERT_CONN = (
    "INSERT INTO user_connections (user_id, plugin_id, access_enc, refresh_enc, "
    "expires_at, scopes, status, issuer, created_at, updated_at) "
    "VALUES ($1::uuid, $2, $3, $4, $5, $6::text[], $7, $8, now(), now()) "
    "ON CONFLICT (user_id, plugin_id) DO UPDATE SET "
    "access_enc = EXCLUDED.access_enc, refresh_enc = EXCLUDED.refresh_enc, "
    "expires_at = EXCLUDED.expires_at, scopes = EXCLUDED.scopes, "
    "status = EXCLUDED.status, issuer = EXCLUDED.issuer, updated_at = now()")
SQL_UPDATE_CONN = (
    "UPDATE user_connections SET access_enc = $3, refresh_enc = $4, "
    "expires_at = $5, scopes = $6::text[], status = $7, issuer = $8, "
    "updated_at = now() WHERE user_id = $1::uuid AND plugin_id = $2")
SQL_EXPIRE_CONN = (
    "UPDATE user_connections SET access_enc = NULL, refresh_enc = NULL, "
    "status = 'auth_expired', updated_at = now() "
    "WHERE user_id = $1::uuid AND plugin_id = $2")
SQL_DELETE_CONN = ("DELETE FROM user_connections "
                   "WHERE user_id = $1::uuid AND plugin_id = $2 RETURNING plugin_id")

SQL_PURGE_PENDING = ("DELETE FROM oauth_pending WHERE user_id = $1::uuid "
                     "AND (used OR expires_at < $2)")
SQL_PUT_PENDING = (
    "INSERT INTO oauth_pending (state_sha256, user_id, plugin_id, verifier_enc, "
    "issuer, created_at, expires_at, used) "
    "VALUES ($1, $2::uuid, $3, $4, $5, $6, $7, false)")
SQL_LOCK_PENDING = (
    "SELECT state_sha256, user_id, plugin_id, verifier_enc, issuer, created_at, "
    "expires_at, used FROM oauth_pending "
    "WHERE state_sha256 = $1 AND user_id = $2::uuid AND NOT used "
    "AND expires_at > $3 FOR UPDATE")
SQL_USE_PENDING = ("UPDATE oauth_pending SET used = true, verifier_enc = NULL "
                   "WHERE state_sha256 = $1 AND user_id = $2::uuid")


def _row_to_conn(row) -> Connection:
    return Connection(
        user_id=str(row["user_id"]), plugin_id=row["plugin_id"],
        access_enc=bytes(row["access_enc"]) if row["access_enc"] is not None else None,
        refresh_enc=bytes(row["refresh_enc"]) if row["refresh_enc"] is not None else None,
        expires_at=row["expires_at"], scopes=list(row["scopes"] or []),
        status=row["status"], issuer=row["issuer"],
        created_at=row["created_at"], updated_at=row["updated_at"])


def _conn_args(c: Connection) -> tuple:
    return (str(c.user_id), c.plugin_id, c.access_enc, c.refresh_enc, c.expires_at,
            list(c.scopes), c.status, c.issuer)


class PgPendingStore:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def put(self, pending: Pending) -> None:
        pg = self._get_pg_client()
        async with pg.user_scope(str(pending.user_id)) as conn:
            await conn.execute(SQL_PURGE_PENDING, str(pending.user_id), _now())
            await conn.execute(
                SQL_PUT_PENDING, pending.state_sha256, str(pending.user_id),
                pending.plugin_id, pending.verifier_enc, pending.issuer,
                pending.created_at, pending.expires_at)

    async def consume(self, user_id: str, state_sha256: str,
                      now: datetime) -> Optional[Pending]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_LOCK_PENDING, state_sha256, str(user_id), now)
            if row is None:
                return None
            await conn.execute(SQL_USE_PENDING, state_sha256, str(user_id))
        return Pending(
            state_sha256=row["state_sha256"], user_id=str(row["user_id"]),
            plugin_id=row["plugin_id"],
            verifier_enc=(bytes(row["verifier_enc"])
                          if row["verifier_enc"] is not None else None),
            issuer=row["issuer"], expires_at=row["expires_at"], used=False,
            created_at=row["created_at"])


class _PgLocked:
    def __init__(self, conn, user_id: str, plugin_id: str, current) -> None:
        self._conn = conn
        self._user_id = str(user_id)
        self._plugin_id = plugin_id
        self.current: Optional[Connection] = current

    async def _reload(self) -> None:
        row = await self._conn.fetchrow(SQL_GET_CONN, self._user_id, self._plugin_id)
        self.current = _row_to_conn(row) if row else None

    async def save(self, conn: Connection) -> None:
        await self._conn.execute(SQL_UPDATE_CONN, *_conn_args(
            dataclasses.replace(conn, user_id=self._user_id, plugin_id=self._plugin_id)))
        await self._reload()

    async def mark_expired(self) -> None:
        await self._conn.execute(SQL_EXPIRE_CONN, self._user_id, self._plugin_id)
        await self._reload()


class PgConnectionStore:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def get(self, user_id: str, plugin_id: str) -> Optional[Connection]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_GET_CONN, str(user_id), plugin_id)
        return _row_to_conn(row) if row else None

    async def list(self, user_id: str) -> list[Connection]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_LIST_CONN, str(user_id))
        return [_row_to_conn(r) for r in rows]

    async def upsert(self, conn: Connection) -> None:
        pg = self._get_pg_client()
        async with pg.user_scope(str(conn.user_id)) as c:
            await c.execute(SQL_UPSERT_CONN, *_conn_args(conn))

    async def delete(self, user_id: str, plugin_id: str) -> bool:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_DELETE_CONN, str(user_id), plugin_id)
        return row is not None

    @asynccontextmanager
    async def locked(self, user_id: str, plugin_id: str) -> AsyncIterator[_PgLocked]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_LOCK_CONN, str(user_id), plugin_id)
            yield _PgLocked(conn, user_id, plugin_id,
                            _row_to_conn(row) if row else None)


# ── Resolution ────────────────────────────────────────────────────────────

_connections: Optional[ConnectionStore] = None
_pending: Optional[PendingStore] = None


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


def _memory_mode() -> bool:
    return os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory"


def set_stores(connections: Optional[ConnectionStore] = None,
               pending: Optional[PendingStore] = None) -> None:
    """Install stores explicitly (tests, api/main.py). No argument resets."""
    global _connections, _pending
    _connections = connections
    _pending = pending


def get_connection_store() -> ConnectionStore:
    global _connections
    if _connections is None:
        _connections = MemoryConnectionStore() if _memory_mode() else PgConnectionStore()
    return _connections


def get_pending_store() -> PendingStore:
    global _pending
    if _pending is None:
        _pending = MemoryPendingStore() if _memory_mode() else PgPendingStore()
    return _pending
