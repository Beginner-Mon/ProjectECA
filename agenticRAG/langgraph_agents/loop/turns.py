"""Paused loop turns (`loop_turns`, migration 021; stage 4).

A turn that proposed a write action ends with `awaiting_approval`. Its sealed
transcript and a small state dict are saved here under `thread_id`, so
`POST /chat/resume` can continue it in another request, even another Lambda.

    paused --claim--> resumed --finish--> finished
    paused --invalidate_for_session--> invalidated
    resumed --pause--> paused            (a resumed turn proposes again)

`claim` is the compare-and-set that makes two parallel resumes safe:

    UPDATE loop_turns SET status = 'resumed' ...
     WHERE thread_id = $1 AND user_id = $2 AND status = 'paused' RETURNING ...

One caller gets the row, the other gets None. A wrong user gets None too, the
same as an unknown thread (E1: the route answers 404 either way).

`transcript_enc` is sealed with {user_id, purpose="loop_turn", thread_id}
(`seal_transcript`). `finish` and `invalidate_for_session` drop it. `state_json`
holds no payload text.

Two stores behind one Protocol, one contract suite: `MemoryTurnStore` and
`PgTurnStore` (`user_scope(user_id)`, RLS owner policy).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol

from .crypto import Sealer, require_sealer, transcript_context

PAUSED = "paused"
RESUMED = "resumed"
FINISHED = "finished"
INVALIDATED = "invalidated"


@dataclass
class TurnRecord:
    thread_id: str
    user_id: str
    session_id: str
    expires_at: datetime
    request_id: str = ""
    status: str = PAUSED
    transcript_enc: Optional[bytes] = None
    state_json: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def seal_transcript(transcript: list, *, user_id: str, thread_id: str,
                          sealer: Optional[Sealer] = None) -> bytes:
    """Seal a turn's messages, binding {user_id, purpose, thread_id}."""
    sealer = sealer or require_sealer()
    data = json.dumps(transcript, ensure_ascii=False, default=str).encode("utf-8")
    return await sealer.seal(data, transcript_context(user_id, thread_id))


async def open_transcript(blob: bytes, *, user_id: str, thread_id: str,
                          sealer: Optional[Sealer] = None) -> list:
    sealer = sealer or require_sealer()
    data = await sealer.open(blob, transcript_context(user_id, thread_id))
    return json.loads(data.decode("utf-8"))


class TurnStore(Protocol):
    async def pause(self, user_id: str, session_id: str, thread_id: str, *,
                    request_id: str, transcript_enc: bytes, state_json: dict,
                    expires_at: datetime) -> bool: ...

    async def claim(self, user_id: str, thread_id: str) -> Optional[TurnRecord]: ...

    async def get(self, user_id: str, thread_id: str) -> Optional[TurnRecord]: ...

    async def finish(self, user_id: str, thread_id: str) -> bool: ...

    async def invalidate_for_session(self, user_id: str, session_id: str) -> list[str]: ...

    # L3 retention sweep
    async def sweep_stale_paused(self, user_id: str, older_than_s: float) -> list[str]: ...
    async def sweep_old_turns(self, user_id: str, older_than_days: int) -> list[str]: ...
    async def sweep_stuck_resumed(self, user_id: str, older_than_s: float) -> list[str]: ...


# ── In-memory store ───────────────────────────────────────────────────────

class MemoryTurnStore:
    def __init__(self, clock: Optional[Callable[[], datetime]] = None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._rows: dict[str, TurnRecord] = {}

    @staticmethod
    def _copy(r: TurnRecord) -> TurnRecord:
        return dataclasses.replace(r, state_json=copy.deepcopy(r.state_json))

    async def pause(self, user_id, session_id, thread_id, *, request_id,
                    transcript_enc, state_json, expires_at) -> bool:
        with self._lock:
            now = self._clock()
            row = self._rows.get(str(thread_id))
            if row is None:
                self._rows[str(thread_id)] = TurnRecord(
                    thread_id=str(thread_id), user_id=str(user_id),
                    session_id=str(session_id), expires_at=expires_at,
                    request_id=str(request_id), status=PAUSED,
                    transcript_enc=transcript_enc,
                    state_json=copy.deepcopy(state_json), created_at=now, updated_at=now)
                return True
            # Re-pause: only the owner, only from `resumed`.
            if row.user_id != str(user_id) or row.status != RESUMED:
                return False
            row.status, row.request_id, row.expires_at = PAUSED, str(request_id), expires_at
            row.transcript_enc = transcript_enc
            row.state_json = copy.deepcopy(state_json)
            row.updated_at = now
            return True

    async def claim(self, user_id, thread_id) -> Optional[TurnRecord]:
        with self._lock:     # check and write under one lock: a compare-and-set
            row = self._rows.get(str(thread_id))
            if row is None or row.user_id != str(user_id) or row.status != PAUSED:
                return None
            row.status, row.updated_at = RESUMED, self._clock()
            return self._copy(row)

    async def get(self, user_id, thread_id) -> Optional[TurnRecord]:
        with self._lock:
            row = self._rows.get(str(thread_id))
            if row is None or row.user_id != str(user_id):
                return None
            return self._copy(row)

    async def finish(self, user_id, thread_id) -> bool:
        with self._lock:
            row = self._rows.get(str(thread_id))
            if row is None or row.user_id != str(user_id) or row.status != RESUMED:
                return False
            row.status, row.transcript_enc, row.state_json = FINISHED, None, {}
            row.updated_at = self._clock()
            return True

    async def invalidate_for_session(self, user_id, session_id) -> list[str]:
        out: list[str] = []
        with self._lock:
            now = self._clock()
            for r in self._rows.values():
                if (r.user_id == str(user_id) and r.session_id == str(session_id)
                        and r.status == PAUSED):
                    r.status, r.transcript_enc, r.state_json = INVALIDATED, None, {}
                    r.updated_at = now
                    out.append(r.thread_id)
        return out


    async def sweep_stale_paused(self, user_id, older_than_s) -> list[str]:
        out: list[str] = []
        with self._lock:
            now = self._clock()
            cutoff = now - timedelta(seconds=float(older_than_s))
            for r in self._rows.values():
                if (r.user_id == str(user_id) and r.status == PAUSED
                        and r.updated_at < cutoff):
                    r.status, r.transcript_enc, r.state_json = INVALIDATED, None, {}
                    r.updated_at = now
                    out.append(r.thread_id)
        return out

    async def sweep_old_turns(self, user_id, older_than_days) -> list[str]:
        out: list[str] = []
        with self._lock:
            now = self._clock()
            cutoff = now - timedelta(days=int(older_than_days))
            to_delete = [tid for tid, r in self._rows.items()
                         if r.user_id == str(user_id)
                         and r.status in (FINISHED, INVALIDATED)
                         and r.updated_at < cutoff]
            for tid in to_delete:
                del self._rows[tid]
                out.append(tid)
        return out

    async def sweep_stuck_resumed(self, user_id, older_than_s) -> list[str]:
        """Resumed turns whose updated_at is older than older_than_s become invalidated."""
        out: list[str] = []
        with self._lock:
            now = self._clock()
            cutoff = now - timedelta(seconds=float(older_than_s))
            for r in self._rows.values():
                if (r.user_id == str(user_id) and r.status == RESUMED
                        and r.updated_at < cutoff):
                    r.status, r.transcript_enc, r.state_json = INVALIDATED, None, {}
                    r.updated_at = now
                    out.append(r.thread_id)
        return out

# ── Postgres store ────────────────────────────────────────────────────────

_COLS = ("thread_id, user_id, session_id, request_id, status, created_at, "
         "updated_at, expires_at, transcript_enc, state_json")

SQL_PAUSE = """
INSERT INTO loop_turns (thread_id, user_id, session_id, request_id, status,
                        created_at, updated_at, expires_at, transcript_enc, state_json)
VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'paused', now(), now(), $5, $6, $7::jsonb)
ON CONFLICT (thread_id) DO UPDATE
   SET status = 'paused',
       request_id = EXCLUDED.request_id,
       expires_at = EXCLUDED.expires_at,
       transcript_enc = EXCLUDED.transcript_enc,
       state_json = EXCLUDED.state_json,
       updated_at = now()
 WHERE loop_turns.status = 'resumed' AND loop_turns.user_id = EXCLUDED.user_id
RETURNING thread_id
"""

SQL_CLAIM = f"""
UPDATE loop_turns
   SET status = 'resumed', updated_at = now()
 WHERE thread_id = $1::uuid AND user_id = $2::uuid AND status = 'paused'
RETURNING {_COLS}
"""

SQL_GET = f"SELECT {_COLS} FROM loop_turns WHERE thread_id = $1::uuid AND user_id = $2::uuid"

SQL_FINISH = """
UPDATE loop_turns
   SET status = 'finished', transcript_enc = NULL, state_json = '{}'::jsonb,
       updated_at = now()
 WHERE thread_id = $1::uuid AND user_id = $2::uuid AND status = 'resumed'
RETURNING thread_id
"""

SQL_INVALIDATE = """
UPDATE loop_turns
   SET status = 'invalidated', transcript_enc = NULL, state_json = '{}'::jsonb,
       updated_at = now()
 WHERE user_id = $1::uuid AND session_id = $2::uuid AND status = 'paused'
RETURNING thread_id
"""

SQL_SWEEP_STALE_PAUSED = """
UPDATE loop_turns
   SET status = 'invalidated', transcript_enc = NULL, state_json = '{}'::jsonb,
       updated_at = now()
 WHERE user_id = $1::uuid AND status = 'paused'
   AND updated_at < now() - ($2 * interval '1 second')
RETURNING thread_id
"""

SQL_SWEEP_OLD_TURNS = """
DELETE FROM loop_turns
 WHERE user_id = $1::uuid AND status IN ('finished','invalidated')
   AND updated_at < now() - ($2 * interval '1 day')
RETURNING thread_id
"""

SQL_SWEEP_STUCK_RESUMED = """
UPDATE loop_turns
   SET status = 'invalidated', transcript_enc = NULL, state_json = '{}'::jsonb,
       updated_at = now()
 WHERE user_id = $1::uuid AND status = 'resumed'
   AND updated_at < now() - ($2 * interval '1 second')
RETURNING thread_id
"""




def _row_to_record(r: Any) -> TurnRecord:
    state = r["state_json"]
    if isinstance(state, (str, bytes)):
        state = json.loads(state)
    return TurnRecord(
        thread_id=str(r["thread_id"]), user_id=str(r["user_id"]),
        session_id=str(r["session_id"]), request_id=r["request_id"], status=r["status"],
        created_at=r["created_at"], updated_at=r["updated_at"],
        expires_at=r["expires_at"],
        transcript_enc=bytes(r["transcript_enc"]) if r["transcript_enc"] is not None else None,
        state_json=state or {})


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


class PgTurnStore:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def pause(self, user_id, session_id, thread_id, *, request_id,
                    transcript_enc, state_json, expires_at) -> bool:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(
                SQL_PAUSE, str(thread_id), str(user_id), str(session_id),
                str(request_id), expires_at, transcript_enc,
                json.dumps(state_json, ensure_ascii=False, default=str))
        return row is not None

    async def claim(self, user_id, thread_id) -> Optional[TurnRecord]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_CLAIM, str(thread_id), str(user_id))
        return _row_to_record(row) if row else None

    async def get(self, user_id, thread_id) -> Optional[TurnRecord]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_GET, str(thread_id), str(user_id))
        return _row_to_record(row) if row else None

    async def finish(self, user_id, thread_id) -> bool:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            row = await conn.fetchrow(SQL_FINISH, str(thread_id), str(user_id))
        return row is not None

    async def invalidate_for_session(self, user_id, session_id) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_INVALIDATE, str(user_id), str(session_id))
        return [str(r["thread_id"]) for r in rows]

    async def sweep_stale_paused(self, user_id, older_than_s) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_SWEEP_STALE_PAUSED, str(user_id), float(older_than_s))
        return [str(r["thread_id"]) for r in rows]

    async def sweep_old_turns(self, user_id, older_than_days) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_SWEEP_OLD_TURNS, str(user_id), int(older_than_days))
        return [str(r["thread_id"]) for r in rows]

    async def sweep_stuck_resumed(self, user_id, older_than_s) -> list[str]:
        pg = self._get_pg_client()
        async with pg.user_scope(str(user_id)) as conn:
            rows = await conn.fetch(SQL_SWEEP_STUCK_RESUMED, str(user_id), float(older_than_s))
        return [str(r["thread_id"]) for r in rows]



# ── Resolution ────────────────────────────────────────────────────────────

_store: Optional[TurnStore] = None


def set_store(store: Optional[TurnStore]) -> None:
    global _store
    _store = store


def get_turn_store() -> TurnStore:
    global _store
    if _store is not None:
        return _store
    if os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory":
        _store = MemoryTurnStore()
    else:
        _store = PgTurnStore()
    return _store
