"""Prelude operations for the agent loop turn (R4).

This module consolidates all the per-turn setup operations into a single
store call (critical path) and background retention sweeps (off critical path).

The critical path is one `user_scope` transaction with data-modifying CTEs:
- `executing` older than write timeout → `unknown`
- session's `proposed` actions → `invalidated`
- session's `paused` turns → `invalidated`

Retention sweeps run off the critical path (background task, at most once
per hour per user per process).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from langgraph_agents.loop.actions import (
    PgActionStore, SQL_INVALIDATE as SQL_INVALIDATE_ACTIONS, SQL_SWEEP,
    SQL_SWEEP_EXPIRED_PROPOSED, SQL_SWEEP_OLD_METADATA,
    SQL_SWEEP_PAYLOAD_AFTER_FINAL, SQL_SWEEP_STALE_APPROVED,
    get_action_store,
)
from langgraph_agents.loop.execute import STALE_EXECUTING_S
from langgraph_agents.loop.turns import (
    SQL_INVALIDATE as SQL_INVALIDATE_TURNS,
    SQL_SWEEP_OLD_TURNS, SQL_SWEEP_STALE_PAUSED, SQL_SWEEP_STUCK_RESUMED,
    get_turn_store,
)

logger = logging.getLogger("langgraph.loop.prelude")

# Critical path: one statement, three data-modifying CTEs built from the
# store constants. All three take ($1 user_id, $2 session_id); SQL_SWEEP
# also takes $3, the stale threshold in seconds.
SQL_PRELUDE = (
    "WITH stale AS (" + SQL_SWEEP + "), "
    "inv AS (" + SQL_INVALIDATE_ACTIONS + "), "
    "paused AS (" + SQL_INVALIDATE_TURNS + ") "
    "SELECT 'invalidated' AS kind, action_id::text AS id FROM inv "
    "UNION ALL SELECT 'stale', action_id::text FROM stale "
    "UNION ALL SELECT 'paused', thread_id::text FROM paused"
)

# In-memory timestamp dict to track last retention run per user
_retention_last_run: dict[str, float] = {}


async def run_prelude(user_id: str, session_id: str) -> list[str]:
    """Critical path: one call, one statement.

    Returns the list of invalidated action IDs (kind = 'invalidated') for
    load_context. On any exception, logs WARNING `loop_prelude_failed` with
    the exception class and returns [].
    """
    action_store = get_action_store()
    turn_store = get_turn_store()

    if isinstance(action_store, PgActionStore):
        # PG stores: one user_scope, one fetch with all three UPDATEs.
        try:
            pg = action_store._get_pg_client()
            async with pg.user_scope(str(user_id)) as conn:
                rows = await conn.fetch(
                    SQL_PRELUDE, str(user_id), str(session_id), STALE_EXECUTING_S)
            return [r["id"] for r in rows if r["kind"] == "invalidated"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("loop_prelude_failed", extra={"error": type(exc).__name__})
            return []

    # Memory stores: call the three memory-store methods, as _loop_turn_prelude did before 6A
    try:
        from langgraph_agents.loop.execute import sweep_stale
        results = await asyncio.gather(
            sweep_stale(user_id, session_id),
            action_store.invalidate_for_session(user_id, session_id),
            turn_store.invalidate_for_session(user_id, session_id),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, BaseException):
                logger.warning("loop_prelude_failed", extra={"error": type(r).__name__})
        invalidated = results[1]
        return list(invalidated) if isinstance(invalidated, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("loop_prelude_failed", extra={"error": type(exc).__name__})
        return []


def schedule_retention(user_id: str, spawn: Callable[[asyncio.Task], None], *, now: Callable[[], float] = time.monotonic) -> bool:
    """Schedule retention sweeps off the critical path.

    Runs at most once per user per hour per process. When retention is due,
    calls `spawn(run_retention(user_id))` and returns True.
    """
    last_run = _retention_last_run.get(user_id, 0)
    if now() - last_run >= 3600:  # 1 hour
        _retention_last_run[user_id] = now()
        # Pass the coroutine to spawn; spawn is responsible for creating the task
        spawn(run_retention(user_id))
        return True
    return False


async def run_retention(user_id: str) -> None:
    """Run all L3 retention sweeps off the critical path.

    Catches every exception and logs one WARNING `loop_retention_failed`.
    """
    try:
        action_store = get_action_store()
        turn_store = get_turn_store()

        if isinstance(action_store, PgActionStore):
            # PG: one user_scope, the existing statements in order.
            pg = action_store._get_pg_client()
            async with pg.user_scope(str(user_id)) as conn:
                await conn.execute(SQL_SWEEP_EXPIRED_PROPOSED, str(user_id))
                await conn.execute(SQL_SWEEP_STALE_APPROVED, str(user_id), 3600.0)
                await conn.execute(SQL_SWEEP_PAYLOAD_AFTER_FINAL, str(user_id))
                await conn.execute(SQL_SWEEP_OLD_METADATA, str(user_id), 30)
                await conn.execute(SQL_SWEEP_STALE_PAUSED, str(user_id), 1800.0)
                await conn.execute(SQL_SWEEP_OLD_TURNS, str(user_id), 30)
                await conn.execute(SQL_SWEEP_STUCK_RESUMED, str(user_id), 600.0)
            return

        # Memory: call the same methods
        await action_store.sweep_expired_proposed(user_id)
        await action_store.sweep_stale_approved(user_id, 3600.0)
        await action_store.sweep_payload_after_final(user_id)
        await action_store.sweep_old_metadata(user_id, 30)
        await turn_store.sweep_stale_paused(user_id, 1800.0)
        await turn_store.sweep_old_turns(user_id, 30)
        await turn_store.sweep_stuck_resumed(user_id, 600.0)

    except Exception as exc:  # noqa: BLE001
        logger.warning("loop_retention_failed", extra={"error": type(exc).__name__})