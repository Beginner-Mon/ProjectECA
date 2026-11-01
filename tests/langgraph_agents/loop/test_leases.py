"""Tests for `loop.leases`: the contract for the memory store, plus PG-only
integration tests (skipped without ECA_TEST_PG_DSN)."""

from __future__ import annotations

import asyncio
import os

import pytest

from langgraph_agents.loop.leases import (
    MemoryLeaseStore, PgLeaseStore, get_lease_store, set_store,
)


def _store(max_concurrent=3):
    return MemoryLeaseStore(max_concurrent=max_concurrent)


# ── Memory store: the full contract ───────────────────────────────────────

def test_acquire_first_turn_succeeds():
    assert asyncio.run(_store().acquire("u1", "s1", "req1", 120.0)) is True


def test_second_turn_same_session_refused():
    store = _store()
    assert asyncio.run(store.acquire("u1", "s1", "req1", 120.0)) is True
    # Same session, different request while the first lease is live.
    assert asyncio.run(store.acquire("u1", "s1", "req2", 120.0)) is False


def test_release_then_acquire_succeeds():
    store = _store()
    assert asyncio.run(store.acquire("u1", "s1", "req1", 120.0)) is True
    asyncio.run(store.release("u1", "req1"))
    assert asyncio.run(store.acquire("u1", "s1", "req2", 120.0)) is True


def test_different_sessions_concurrent_up_to_max_concurrent():
    store = _store(max_concurrent=3)
    assert asyncio.run(store.acquire("u1", "s1", "r1", 120.0)) is True
    assert asyncio.run(store.acquire("u1", "s2", "r2", 120.0)) is True
    assert asyncio.run(store.acquire("u1", "s3", "r3", 120.0)) is True
    # 4th concurrent refused (A5: global cap counts live leases of other rows).
    assert asyncio.run(store.acquire("u1", "s4", "r4", 120.0)) is False


def test_cancel_seen_next_round():
    store = _store()
    assert asyncio.run(store.acquire("u1", "s1", "req1", 120.0)) is True
    asyncio.run(store.request_cancel("u1", "s1"))
    assert asyncio.run(store.is_cancelled("u1", "s1")) is True


def test_is_cancelled_false_when_no_lease():
    store = _store()
    assert asyncio.run(store.is_cancelled("u1", "s1")) is False


def test_heartbeat_extends():
    store = _store()
    assert asyncio.run(store.acquire("u1", "s1", "req1", 1.0)) is True
    asyncio.run(store.heartbeat("u1", "req1"))
    assert asyncio.run(store.is_cancelled("u1", "s1")) is False


# A3: cross-user — user B's cancel on user A's session does not cancel A's turn.
def test_cross_user_cancel_does_not_touch_other_user():
    store = _store()
    assert asyncio.run(store.acquire("uA", "sA", "reqA", 120.0)) is True
    asyncio.run(store.request_cancel("uB", "sA"))
    assert asyncio.run(store.is_cancelled("uA", "sA")) is False
    # And user A's own cancel does work.
    asyncio.run(store.request_cancel("uA", "sA"))
    assert asyncio.run(store.is_cancelled("uA", "sA")) is True


# ── PG-only integration suite ─────────────────────────────────────────────

needs_pg = pytest.mark.skipif(
    not os.getenv("ECA_TEST_PG_DSN"),
    reason="ECA_TEST_PG_DSN not set — PG contract run by K after migration",
)


def _pg_tables_present() -> bool:
    """True when migration 019 has been applied (agent_leases exists).

    Read-only check — no writes to the database.
    """
    try:
        from langgraph_agents.shared import get_pg_client

        async def _check() -> bool:
            pg = get_pg_client()
            async with pg.transaction():
                row = await pg.fetchval(
                    "SELECT to_regclass('agent_leases')")
            return bool(row)

        return asyncio.run(_check())
    except Exception:  # noqa: BLE001
        return False


skip_if_no_pg_tables = pytest.mark.skipif(
    not _pg_tables_present(),
    reason="agent_leases table missing — migration 019 not applied yet "
           "(K runs the PG contract after applying migrations)",
)


@needs_pg
@skip_if_no_pg_tables
@pytest.mark.integration
class TestPgLeaseContract:
    def _apg(self):
        return PgLeaseStore(max_concurrent=3)

    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        store = self._apg()
        assert await store.acquire("u1", "s1", "req1", 120.0) is True
        assert await store.acquire("u1", "s1", "req2", 120.0) is False
        await store.release("u1", "req1")
        assert await store.acquire("u1", "s1", "req2", 120.0) is True
        await store.release("u1", "req2")

    @pytest.mark.asyncio
    async def test_cancel_seen(self):
        store = self._apg()
        await store.acquire("u1", "s1", "req1", 120.0)
        await store.request_cancel("u1", "s1")
        assert await store.is_cancelled("u1", "s1") is True
        await store.release("u1", "req1")
