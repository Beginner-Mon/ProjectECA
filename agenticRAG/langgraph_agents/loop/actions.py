"""Action records for write tools that need the user's approval (stage 4).

One row per proposed write (`agent_actions`, migration 021, RLS). The status
machine of plan §3:

    proposed -> approved -> executing -> succeeded | failed | unknown
    proposed -> declined | expired | invalidated
    approved -> invalidated | expired      (execute's re-checks, rules 1-2)

Every change of `status` is ONE statement, a compare-and-set:

    UPDATE agent_actions SET status = $to ... WHERE action_id = $1
       AND user_id = $2 AND status = $from RETURNING action_id

A returned row means this caller won; no row means somebody else already moved
it (or it is not the caller's). Never a read followed by a write. Two clicks,
a retry, two parallel resumes or a replay: exactly one wins.

Two stores behind one Protocol, with one contract suite
(`test_actions_store.py`):

* `MemoryActionStore`: in-process; the compare-and-set runs under one lock
  with no await inside it.
* `PgActionStore`: asyncpg through `PostgresClient.user_scope(user_id)`, so the
  RLS owner policy applies. Every method takes the owner's `user_id` first.

`payload_enc` (the sealed arguments) is set to NULL whenever an action reaches
a final status. The sealing helpers here bind the encryption context
{user_id, plugin_id, action_id}; see `crypto.py`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol

from .crypto import Sealer, payload_context, require_sealer

ACTION_TTL_S = 30 * 60          # E3: expires_at = created_at + 30 min

PROPOSED = "proposed"
APPROVED = "approved"
EXECUTING = "executing"
SUCCEEDED = "succeeded"
FAILED = "failed"
UNKNOWN = "unknown"
DECLINED = "declined"
EXPIRED = "expired"
INVALIDATED = "invalidated"

STATUSES = frozenset({PROPOSED, APPROVED, EXECUTING, SUCCEEDED, FAILED, UNKNOWN,
                      DECLINED, EXPIRED, INVALIDATED})
FINAL_STATUSES = frozenset({SUCCEEDED, FAILED, UNKNOWN, DECLINED, EXPIRED, INVALIDATED})

TRANSITIONS: dict[str, frozenset[str]] = {
    PROPOSED: frozenset({APPROVED, DECLINED, EXPIRED, INVALIDATED}),
    APPROVED: frozenset({EXECUTING, INVALIDATED, EXPIRED}),
    EXECUTING: frozenset({SUCCEEDED, FAILED, UNKNOWN}),
}

# Fields a caller may set while moving the status.
_TRANSITION_FIELDS = frozenset({"result_ref"})


def is_legal(from_status: str, to_status: str) -> bool:
    return to_status in TRANSITIONS.get(from_status, frozenset())


def _check_fields(fields: dict) -> Optional[str]:
    bad = set(fields) - _TRANSITION_FIELDS
    if bad:
        raise ValueError(f"transition cannot set: {sorted(bad)}")
    value = fields.get("result_ref")
    return None if value is None else str(value)


def _check_status(*names: str) -> None:
    for n in names:
        if n not in STATUSES:
            raise ValueError(f"unknown action status: {n!r}")


# ── Record and helpers ────────────────────────────────────────────────────

@dataclass
class ActionRecord:
    action_id: str
    user_id: str
    session_id: str
    thread_id: str
    plugin_id: str
    tool: str
    payload_sha256: str
    expires_at: datetime
    plugin_version: str = ""
    tool_def_sha256: str = ""
    payload_display: list = field(default_factory=list)   # [{label, value}]
    recipients: list = field(default_factory=list)
    provenance: list = field(default_factory=list)        # [{tool, source_label}]
    payload_enc: Optional[bytes] = None
    risk: str = "write"
    status: str = PROPOSED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    result_ref: Optional[str] = None


def make_action_id(thread_id: str, tool_call_id: str) -> str:
    """`action_id = uuid5(thread_id, tool_call_id)` (E1)."""
    return str(uuid.uuid5(uuid.UUID(str(thread_id)), str(tool_call_id)))


def payload_sha256(arguments: Any) -> str:
    """sha256 of the canonical JSON of the arguments."""
    canon = json.dumps(arguments, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def new_action(*, user_id: str, session_id: str, thread_id: str, tool_call_id: str,
               plugin_id: str, tool: str, arguments: Any, now: Optional[datetime] = None,
               **extra: Any) -> ActionRecord:
    """A `proposed` record with E1 id, E3 expiry and the payload hash set."""
    now = now or datetime.now(timezone.utc)
    return ActionRecord(
        action_id=make_action_id(thread_id, tool_call_id),
        user_id=str(user_id), session_id=str(session_id), thread_id=str(thread_id),
        plugin_id=plugin_id, tool=tool, payload_sha256=payload_sha256(arguments),
        created_at=now, updated_at=now, expires_at=now + timedelta(seconds=ACTION_TTL_S),
        **extra)


async def seal_payload(arguments: Any, *, user_id: str, plugin_id: str,
                       action_id: str, sealer: Optional[Sealer] = None) -> bytes:
    """Seal the call arguments, binding {user_id, plugin_id, action_id}.
    Raises `SealerUnavailable` when no sealer is configured."""
    sealer = sealer or require_sealer()
    data = json.dumps(arguments, sort_keys=True, ensure_ascii=False,
                      default=str).encode("utf-8")
    return await sealer.seal(data, payload_context(user_id, plugin_id, action_id))


async def open_payload(blob: bytes, *, user_id: str, plugin_id: str, action_id: str,
                       sealer: Optional[Sealer] = None) -> Any:
    """Open a sealed payload. A different user, plugin or action id fails with
    `SealError`."""
    sealer = sealer or require_sealer()
    data = await sealer.open(blob, payload_context(user_id, plugin_id, action_id))
    return json.loads(data.decode("utf-8"))


class ActionStore(Protocol):
    async def propose(self, record: ActionRecord) -> bool: ...

    async def transition(self, user_id: str, action_id: str, from_status: str,
                         to_status: str, **fields: Any) -> bool: ...

    async def get(self, user_id: str, action_id: str) -> Optional[ActionRecord]: ...

    async def pending_for_session(self, user_id: str, session_id: str) -> list[ActionRecord]: ...

    async def invalidate_for_session(self, user_id: str, session_id: str) -> list[str]: ...

    async def sweep_stale_executing(self, user_id: str, session_id: str,
                                    older_than_s: float) -> list[str]: ...


# ── In-memory store ───────────────────────────────────────────────────────

class MemoryActionStore:
    def __init__(self, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._rows: dict[str, ActionRecord] = {}

    async def propose(self, record: ActionRecord) -> bool:
        with self._lock:
            if record.action_id in self._rows:
                return False
            self._rows[record.action_id] = dataclasses.replace(record)
            return True

    async def transition(self, user_id, action_id, from_status, to_status, **fields) -> bool:
        _check_status(from_status, to_status)
        result_ref = _check_fields(fields)
        if not is_legal(from_status, to_status):
            return False
        with self._lock:     # check and write under one lock: a compare-and-set
            row = self._rows.get(str(action_id))
            if row is None or row.user_id != str(user_id) or row.status != from_status:
                return False
            row.status = to_status
            row.updated_at = self._clock()
            if result_ref is not None:
                row.result_ref = result_ref
            if to_status in FINAL_STATUSES:
                row.payload_enc = None
            return True

    async def get(self, user_id, action_id) -> Optional[ActionRecord]:
        with self._lock:
            row = self._rows.get(str(action_id))
            if row is None or row.user_id != str(user_id):
                return None
            return dataclasses.replace(row)

    async def pending_for_session(self, user_id, session_id) -> list[ActionRecord]:
        with self._lock:
            rows = [dataclasses.replace(r, payload_enc=None)
                    for r in self._rows.values()
                    if r.user_id == str(user_id) and r.session_id == str(session_id)
                    and r.status == PROPOSED]
        return sorted(rows, key=lambda r: r.created_at)

    async def invalidate_for_session(self, user_id, session_id) -> list[str]:
        out: list[str] = []
        with self._lock:
            now = self._clock()
            for r in self._rows.values():
                if (r.user_id == str(user_id) and r.session_id == str(session_id)
                        and r.status == PROPOSED):
                    r.status, r.updated_at, r.payload_enc = INVALIDATED, now, None
                    out.append(r.action_id)
        return out

    async def sweep_stale_executing(self, user_id, session_id, older_than_s) -> list[str]:
        out: list[str] = []
        with self._lock:
            now = self._clock()
            cutoff = now - timedelta(seconds=float(older_than_s))
            for r in self._rows.values():
                if (r.user_id == str(user_id) and r.session_id == str(session_id)
                        and r.status == EXECUTING and r.updated_at < cutoff):
                    r.status, r.updated_at, r.payload_enc = UNKNOWN, now, None
                    out.append(r.action_id)
        return out


# ── Postgres store ────────────────────────────────────────────────────────

_COLS = ("action_id, user_id, session_id, thread_id, plugin_id, plugin_version, "
         "tool, tool_def_sha256, payload_sha256, payload_display, recipients, "
         "provenance, payload_enc, risk, status, created_at, updated_at, "
         "expires_at, result_ref")
# The pending list never carries the sealed payload.
_COLS_NO_PAYLOAD = _COLS.replace("payload_enc", "NULL::bytea AS payload_enc")

SQL_PROPOSE = f"""
INSERT INTO agent_actions ({_COLS})
VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7, $8, $9,
        $10::jsonb, $11::jsonb, $12::jsonb, $13, $14, $15, $16, $17, $18, $19)
ON CONFLICT (action_id) DO NOTHING
RETURNING action_id
"""

SQL_TRANSITION = """
UPDATE agent_actions
   SET status = $3,
       updated_at = now(),
       result_ref = COALESCE($5, result_ref),
       payload_enc = CASE WHEN $4::boolean THEN NULL ELSE payload_enc END
 WHERE action_id = $1::uuid AND user_id = $2::uuid AND status = $6
RETURNING action_id
"""

SQL_GET = f"SELECT {_COLS} FROM agent_actions WHERE action_id = $1::uuid AND user_id = $2::uuid"

SQL_PENDING = f"""
SELECT {_COLS_NO_PAYLOAD} FROM agent_actions
 WHERE user_id = $1::uuid AND session_id = $2::uuid AND status = 'proposed'
 ORDER BY created_at
"""

SQL_INVALIDATE = """
UPDATE agent_actions
   SET status = 'invalidated', payload_enc = NULL, updated_at = now()
 WHERE user_id = $1::uuid AND session_id = $2::uuid AND status = 'proposed'
RETURNING action_id
"""

SQL_SWEEP = """
UPDATE agent_actions
   SET status = 'unknown', payload_enc = NULL, updated_at = now()
 WHERE user_id = $1::uuid AND session_id = $2::uuid AND status = 'executing'
   AND updated_at < now() - ($3 * interval '1 second')
RETURNING action_id
"""


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _row_to_record(r: Any) -> ActionRecord:
    return ActionRecord(
        action_id=str(r["action_id"]), user_id=str(r["user_id"]),
        session_id=str(r["session_id"]), thread_id=str(r["thread_id"]),
        plugin_id=r["plugin_id"], plugin_version=r["plugin_version"], tool=r["tool"],
        tool_def_sha256=r["tool_def_sha256"], payload_sha256=r["payload_sha256"],
        payload_display=_load(r["payload_display"]), recipients=_load(r["recipients"]),
        provenance=_load(r["provenance"]),
        payload_enc=bytes(r["payload_enc"]) if r["payload_enc"] is not None else None,
        risk=r["risk"], status=r["status"], created_at=r["created_at"],
        updated_at=r["updated_at"], expires_at=r["expires_at"],
        result_ref=r["result_ref"])


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


class PgActionStore:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def propose(self, record: ActionRecord) -> bool:
        pg = self._get_pg_client()
        async with pg.user_scope(str(record.user_id)) as conn:
            row = await conn.fetchrow(
                SQL_PROPOSE,
                record.action_id, record.user_id, record.session_id, record.thread_id,
                record.plugin_id, record.plugin_version, record.tool,
                record.tool_def_sha256, record.payload_sha256,
                _js(record.payload_display), _js(record.recipients),
                _js(record.provenance), record.payload_enc, record.risk, record.status,
                record.created_at, record.updated_at, record.expires_at,
                record.result_ref)
        return row is not None

    async def transition(self, user_id, action_id, from_status, to_status, **fields) -> bool:
        _check_status(from_status, to_status)
        result_ref = _check_fields(fields)
        if not is_legal(from_status, to_status):
            return False
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(
                SQL_TRANSITION, str(action_id), str(user_id), to_status,
                to_status in FINAL_STATUSES, result_ref, from_status)
        return row is not None

    async def get(self, user_id, action_id) -> Optional[ActionRecord]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_GET, str(action_id), str(user_id))
        return _row_to_record(row) if row else None

    async def pending_for_session(self, user_id, session_id) -> list[ActionRecord]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_PENDING, str(user_id), str(session_id))
        return [_row_to_record(r) for r in rows]

    async def invalidate_for_session(self, user_id, session_id) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_INVALIDATE, str(user_id), str(session_id))
        return [str(r["action_id"]) for r in rows]

    async def sweep_stale_executing(self, user_id, session_id, older_than_s) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_SWEEP, str(user_id), str(session_id),
                                    float(older_than_s))
        return [str(r["action_id"]) for r in rows]


# ── Resolution ────────────────────────────────────────────────────────────

_store: Optional[ActionStore] = None


def set_store(store: Optional[ActionStore]) -> None:
    """Install a store explicitly (tests, api/main.py). None resets."""
    global _store
    _store = store


def get_action_store() -> ActionStore:
    """Postgres unless `VVA_LOOP_LEASE_STORE=memory` (the switch the lease,
    rate and plugin stores share)."""
    global _store
    if _store is not None:
        return _store
    if os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory":
        _store = MemoryActionStore()
    else:
        _store = PgActionStore()
    return _store
