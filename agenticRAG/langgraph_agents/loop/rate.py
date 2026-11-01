"""Rate counter per (user, bucket): 30/min, 60 s window.

Two implementations behind the same `RateCounter` protocol:

* `MemoryRateCounter` — in-process, for tests and local dev.
* `PgRateCounter` — on asyncpg through `shared.get_pg_client()`. Production
  default.

`rate.get_rate_counter()` returns a `PgRateCounter` unless
`VVA_LOOP_LEASE_STORE=memory` (shared switch with the lease store), in which
case it returns a `MemoryRateCounter`. `set_store()` lets tests override.

`hit()` is async everywhere now: `PgRateCounter.hit` uses the connection
yielded by `pg.transaction()` (A4) so the upsert runs on the same connection
as the lock, and callers must `await` it (A6 — an unawaited coroutine is
truthy, so the gate used to always allow).

PG `hit()` is one upsert:

    INSERT INTO rate_counters (user_id, bucket, window_start, count)
    VALUES ($1, $2, date_trunc('minute', now() AT TIME ZONE 'UTC'), 1)
    ON CONFLICT (user_id, bucket, window_start) DO UPDATE
    SET count = rate_counters.count + 1
    WHERE rate_counters.window_start =
          date_trunc('minute', now() AT TIME ZONE 'UTC')
    RETURNING count

Returns True when the resulting count is ≤ limit (the call is allowed).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol


class RateCounter(Protocol):
    async def hit(self, user_id: str, bucket: str, limit: int = 30, window_s: int = 60) -> bool: ...


class MemoryRateCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # (user_id, bucket, window_start) -> count
        self._counts: dict[tuple[str, str, float], int] = {}

    async def hit(self, user_id: str, bucket: str, limit: int = 30, window_s: int = 60) -> bool:
        now = time.monotonic()
        window_start = int(now // window_s) * window_s
        key = (user_id, bucket, window_start)
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key] <= limit


class PgRateCounter:
    def __init__(self, get_pg_client=None) -> None:
        self._get_pg_client = get_pg_client or _default_get_pg_client

    async def hit(self, user_id: str, bucket: str, limit: int = 30, window_s: int = 60) -> bool:
        pg = self._get_pg_client()
        async with pg.transaction() as conn:
            rows = await conn.fetch(
                """
                INSERT INTO rate_counters (user_id, bucket, window_start, count)
                VALUES ($1, $2, date_trunc('minute', now() AT TIME ZONE 'UTC'), 1)
                ON CONFLICT (user_id, bucket, window_start) DO UPDATE
                SET count = rate_counters.count + 1
                WHERE rate_counters.window_start =
                      date_trunc('minute', now() AT TIME ZONE 'UTC')
                RETURNING count
                """,
                user_id, bucket,
            )
        return bool(rows) and int(rows[0]["count"]) <= limit


# ── Resolution ────────────────────────────────────────────────────────────

_counter: Optional[RateCounter] = None


def _default_get_pg_client():
    from langgraph_agents.shared import get_pg_client
    return get_pg_client()


def set_store(counter: RateCounter) -> None:
    """Install a counter explicitly (tests, api/main.py)."""
    global _counter
    _counter = counter


def get_rate_counter() -> RateCounter:
    """Resolve the rate counter: memory when forced, else Postgres."""
    global _counter
    if _counter is not None:
        return _counter
    if os.getenv("VVA_LOOP_LEASE_STORE", "").strip().lower() == "memory":
        _counter = MemoryRateCounter()
    else:
        _counter = PgRateCounter()
    return _counter
