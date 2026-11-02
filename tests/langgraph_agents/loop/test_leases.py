"""Tests for `loop.leases`: the contract, parametrised over Memory and PG
stores (A3, A5).

Every contract case runs on both stores via the `store` fixture. The PG
parameter is marked `integration`: without `ECA_TEST_PG_DSN` (or with the
`agent_leases` table missing) it skips; K runs the PG run against the test
database after Tri applies migration 019. All ids are UUID strings (the
lease columns are UUID), and each PG test deletes its own rows in its
fixture finalizer.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from langgraph_agents.loop.leases import (
    MemoryLeaseStore, PgLeaseStore,
)


def _uid(i: int) -> str:
    """Deterministic, human-readable UUID string (columns are UUID)."""
    return str(uuid.UUID(int=i))


# Users: _U1 shared, _UA/_UB the cross-user cancel pair.
# Sessions: _S1 shared, _SA the cross-user cancel session.
_U1, _UA, _UB = _uid(1), _uid(2), _uid(3)
_S1, _SA = _uid(101), _uid(102)


def _new_store(kind: str, max_concurrent=3):
    if kind == "memory":
        return MemoryLeaseStore(max_concurrent=max_concurrent)
    return PgLeaseStore(max_concurrent=max_concurrent)


_STORE_PARAMS = [
    pytest.param("memory", id="memory"),
    pytest.param("pg", id="pg", marks=pytest.mark.integration),
]


@pytest.fixture(params=_STORE_PARAMS)
def store(request):
    """Yield a store instance and a cleanup callback.

    For the memory store, cleanup is a no-op. For PG, the callback deletes
    the test's own rows by request_id. PG is skipped when unavailable.
    """
    kind = request.param
    if kind == "pg":
        ok, reason = _pg_available()
        if not ok:
            pytest.skip(reason or "PG unavailable")
    store = _new_store(kind)
    created: list[str] = []

    def cleanup():
        try:
            asyncio.run(_delete_rows(store, created))
        except Exception:  # noqa: BLE001
            pass

    request.addfinalizer(cleanup)

    # Attach a helper that acquires + records the request_id for cleanup.
    async def acquire(user_id: str, session_id: str, ttl_s: float = 120.0):
        request_id = str(uuid.uuid4())
        created.append(request_id)
        ok = await store.acquire(user_id, session_id, request_id, ttl_s)
        return ok, request_id

    store.acquire_with_id = acquire  # type: ignore[attr-defined]
    return store


async def _delete_rows(store, request_ids):
    """Delete this test's rows from the PG store (memory store: no-op)."""
    if isinstance(store, PgLeaseStore):
        pg = store._get_pg_client()
        async with pg.transaction():
            for rid in request_ids:
                await pg.execute(
                    "DELETE FROM agent_leases WHERE request_id = $1", rid,
                )


# ── Contract: both stores ─────────────────────────────────────────────────


def test_acquire_first_turn_succeeds(store):
    ok, _ = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok is True


def test_second_turn_same_session_refused(store):
    ok1, _req1 = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok1 is True
    ok2, _ = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok2 is False


def test_release_then_acquire_succeeds(store):
    ok1, req1 = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok1 is True
    asyncio.run(store.release(_U1, req1))
    ok2, _ = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok2 is True


def test_different_sessions_concurrent_up_to_max_concurrent(store):
    # R1: different users AND sessions — this matches the PG global-count
    # statement (COUNT … WHERE user_id <> this user), so the cap is proven
    # global across users, not only across this user's other sessions.
    pairs = [(_uid(i), _uid(100 + i)) for i in range(20, 24)]
    results = []
    for uid, sid in pairs:
        ok, _ = asyncio.run(store.acquire_with_id(uid, sid))
        results.append(ok)
    assert results == [True, True, True, False]  # 4th refused (A5)


def test_expired_lease_reclaimed(store):
    ok, _ = asyncio.run(store.acquire_with_id(_U1, _S1, ttl_s=0.01))
    assert ok is True
    import time as _time
    _time.sleep(0.05)
    ok2, _ = asyncio.run(store.acquire_with_id(_U1, _S1, ttl_s=120.0))
    assert ok2 is True  # expired row reclaimed


def test_cancel_seen(store):
    ok, _ = asyncio.run(store.acquire_with_id(_U1, _S1))
    assert ok is True
    asyncio.run(store.request_cancel(_U1, _S1))
    assert asyncio.run(store.is_cancelled(_U1, _S1)) is True


def test_is_cancelled_false_when_no_lease(store):
    assert asyncio.run(store.is_cancelled(_U1, _S1)) is False


def test_heartbeat_extends(store):
    ok, req = asyncio.run(store.acquire_with_id(_U1, _S1, ttl_s=0.01))
    assert ok is True
    import time as _time
    _time.sleep(0.02)  # past the 0.01s TTL
    # Heartbeat renews the lease (user_id + request_id).
    asyncio.run(store.heartbeat(_U1, req))
    _time.sleep(0.05)
    assert asyncio.run(store.is_cancelled(_U1, _S1)) is False  # still alive after heartbeat


# A3: cross-user — user B's cancel on user A's session does not cancel A's turn.
def test_cross_user_cancel_does_not_touch_other_user(store):
    ok, _ = asyncio.run(store.acquire_with_id(_UA, _SA))
    assert ok is True
    asyncio.run(store.request_cancel(_UB, _SA))
    assert asyncio.run(store.is_cancelled(_UA, _SA)) is False
    asyncio.run(store.request_cancel(_UA, _SA))
    assert asyncio.run(store.is_cancelled(_UA, _SA)) is True


# ── PG availability ───────────────────────────────────────────────────────

# Two separate skip reasons:
#  * ECA_TEST_PG_DSN unset / connection fails → "PG unreachable: <type>"
#    (unset → "ECA_TEST_PG_DSN not set")
#  * connection works but agent_leases missing → "migration 019 not applied"


def _pg_available() -> tuple[bool, str | None]:
    """Return (ok, skip_reason). skip_reason is set when PG can't be used."""
    if not os.getenv("ECA_TEST_PG_DSN"):
        return False, "ECA_TEST_PG_DSN not set"
    try:
        from langgraph_agents.shared import get_pg_client

        async def _check() -> bool:
            pg = get_pg_client()
            async with pg.transaction():
                row = await pg.fetchval(
                    "SELECT to_regclass('agent_leases')")
            return bool(row)

        tables_ok = asyncio.run(_check())
        if not tables_ok:
            return False, "migration 019 not applied"
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"PG unreachable: {type(exc).__name__}"
