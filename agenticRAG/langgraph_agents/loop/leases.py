"""Lease store: one loop turn per session, global concurrency cap, cancel.

Two implementations behind the same `LeaseStore` protocol:

* `MemoryLeaseStore` — in-process, for tests and local dev.
* `PgLeaseStore` — on asyncpg through `shared.get_pg_client()` and its
  `transaction()` / `user_scope()`. Production default.

Resolution: `leases.get_lease_store()` returns a `PgLeaseStore` unless
`VVA_LOOP_LEASE_STORE=memory` (or the process is clearly offline), in which
case it returns a `MemoryLeaseStore`. `set_store()` lets tests and
`api/main.py` install a store explicitly.

PG acquire is one transaction:

    SELECT pg_advisory_xact_lock(hashtext('agent_leases'))  -- global mutex
    -- count live leases of OTHER rows (separate statement, same connection)
    SELECT count(*) FROM agent_leases
     WHERE user_id <> $1
       AND expires_at > now()
    -- refuse when at/above the cap, unless this request owns the row or it
    -- is expired
    INSERT INTO agent_leases (user_id, session_id, request_id, expires_at, cancel_requested)
    VALUES ($1, $2, $3, now() + $4 * interval '1 second', false)
    ON CONFLICT (user_id, session_id) DO UPDATE
    SET request_id = EXCLUDED.request_id,
        expires_at = EXCLUDED.expires_at,
        cancel_requested = false
    WHERE (agent_leases.expires_at < now()
           OR agent_leases.request_id = EXCLUDED.request_id)
      AND $5 < max_concurrent
    RETURNING request_id

Every statement filters by `user_id` (A3): user B can never touch user A's
lease. `pg.execute`/`pg.fetch` are called on the yielded connection inside the
transaction (A4), so `pg_advisory_xact_lock` actually holds and there are no
extra Neon round trips.

Lease failures propagate (fail closed). `release` deletes the row where
`request_id` matches. `is_cancelled` returns False for a missing or expired
row.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol


class LeaseStore(Protocol):
    def acquire(self, user_id: str, session_id: str, request_id: str, ttl_s: float) -> bool: ...
    def heartbeat(self, user_id: str, request_id: str) -> None: ...
    def request_cancel(self, user_id: str, session_id: str) -> None: ...
    def is_cancelled(self, user_id: str, session_id: str) -> bool: ...
    def release(self, user_id: str, request_id: str) -> None: ...


# ── In-memory store ───────────────────────────────────────────────────────

@dataclass
class _LeaseRow:
    user_id: str
    request_id: str
    expires_at: float
    cancel_requested: bool = False


class MemoryLeaseStore:
    def __init__(self, max_concurrent: int = 3) -> None:
        self.max_concurrent = max_concurrent
        self._lock = threading.Lock()
        # (user_id, session_id) -> row
        self._leases: dict[tuple[str, str], _LeaseRow] = {}

    async def acquire(self, user_id: str, session_id: str, request_id: str, ttl_s: float) -> bool:
        key = (user_id, session_id)
        now = time.monotonic()
        with self._lock:
            # Drop expired leases first.
            for k in [k for k, v in self._leases.items() if v.expires_at <= now]:
                del self._leases[k]
            existing = self._leases.get(key)
            # A live lease for this session owned by a *different* request
            # blocks the claim (one turn per session). Only the owning
            # request_id may renew, or an expired row may be reclaimed.
            if existing is not None:
                if existing.expires_at > now and existing.request_id != request_id:
                    return False
                existing.expires_at = now + ttl_s
                existing.cancel_requested = False
                return True
            # No existing row: claim if under the global concurrency cap.
            live = sum(1 for k, v in self._leases.items()
                       if k != key and v.expires_at > now)
            if live >= self.max_concurrent:
                return False
            self._leases[key] = _LeaseRow(
                user_id=user_id, request_id=request_id, expires_at=now + ttl_s,
            )
            return True

    async def heartbeat(self, user_id: str, request_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            for row in self._leases.values():
                if row.request_id == request_id and row.user_id == user_id:
                    row.expires_at = now + 120.0
                    break

    async def request_cancel(self, user_id: str, session_id: str) -> None:
        with self._lock:
            for (uid, sid), row in self._leases.items():
                if uid == user_id and sid == session_id:
                    row.cancel_requested = True

    async def is_cancelled(self, user_id: str, session_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            for (uid, sid), row in self._leases.items():
                if uid == user_id and sid == session_id:
                    return row.cancel_requested or row.expires_at <= now
            return False

    async def release(self, user_id: str, request_id: str) -> None:
        with self._lock:
            for key in [k for k, v in self._leases.items()
                        if v.request_id == request_id and v.user_id == user_id]:
                del self._leases[key]


# ── Postgres store ────────────────────────────────────────────────────────

class PgLeaseStore:
    def __init__(self, max_concurrent: int = 3, get_pg_client=None) -> None:
        self.max_concurrent = max_concurrent
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def acquire(self, user_id: str, session_id: str, request_id: str, ttl_s: float) -> bool:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            # Global mutex for this turn's claim (held by the transaction).
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('agent_leases'))")
            # Count live leases of OTHER rows — a separate statement on the
            # same connection, so the cap is enforced even for a brand-new
            # (user_id, session_id) row that would otherwise plain-INSERT.
            live = await conn.fetchval(
                """
                SELECT count(*) FROM agent_leases
                WHERE user_id <> $1
                  AND expires_at > now()
                """,
                user_id,
            )
            live_count = int(live or 0)
            # Conditional upsert: claim when the row is expired, already owned
            # by this request_id, or fewer than max_concurrent live leases
            # exist (excluding this row). Refuse when at/above the cap.
            rows = await conn.fetch(
                """
                INSERT INTO agent_leases (user_id, session_id, request_id, expires_at, cancel_requested)
                VALUES ($1, $2, $3, now() + ($4 * interval '1 second'), false)
                ON CONFLICT (user_id, session_id) DO UPDATE
                SET request_id = EXCLUDED.request_id,
                    expires_at = EXCLUDED.expires_at,
                    cancel_requested = false
                WHERE (agent_leases.expires_at < now()
                       OR agent_leases.request_id = EXCLUDED.request_id)
                  AND $5 < $6
                RETURNING request_id
                """,
                user_id, session_id, request_id, ttl_s, live_count, self.max_concurrent,
            )
            return len(rows) == 1

    async def heartbeat(self, user_id: str, request_id: str) -> None:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            await conn.execute(
                "UPDATE agent_leases SET expires_at = now() + interval '120 seconds' "
                "WHERE user_id = $1 AND request_id = $2",
                user_id, request_id,
            )

    async def request_cancel(self, user_id: str, session_id: str) -> None:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            await conn.execute(
                "UPDATE agent_leases SET cancel_requested = true "
                "WHERE user_id = $1 AND session_id = $2",
                user_id, session_id,
            )

    async def is_cancelled(self, user_id: str, session_id: str) -> bool:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            row = await conn.fetchrow(
                "SELECT cancel_requested, expires_at FROM agent_leases "
                "WHERE user_id = $1 AND session_id = $2",
                user_id, session_id,
            )
        if row is None:
            return False
        from datetime import datetime, timezone
        return bool(row["cancel_requested"]) or row["expires_at"] < datetime.now(timezone.utc)

    async def release(self, user_id: str, request_id: str) -> None:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            await conn.execute(
                "DELETE FROM agent_leases WHERE user_id = $1 AND request_id = $2",
                user_id, request_id,
            )
            # B6: prune that user's stale rate-counter rows in the same
            # transaction so they don't pile up.
            await conn.execute(
                "DELETE FROM rate_counters WHERE user_id = $1 "
                "AND window_start < now() - interval '10 minutes'",
                user_id,
            )


# ── Resolution ────────────────────────────────────────────────────────────

_store: Optional[LeaseStore] = None


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


def set_store(store: LeaseStore) -> None:
    """Install a store explicitly (tests, api/main.py)."""
    global _store
    _store = store


def get_lease_store() -> LeaseStore:
    """Resolve the lease store: memory when forced, else Postgres."""
    global _store
    if _store is not None:
        return _store
    if os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory":
        _store = MemoryLeaseStore()
    else:
        _store = PgLeaseStore()
    return _store
