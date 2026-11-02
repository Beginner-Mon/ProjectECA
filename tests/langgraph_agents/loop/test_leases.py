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


def _new_store(kind: str, max_concurrent=3, pg_client=None):
    if kind == "memory":
        return MemoryLeaseStore(max_concurrent=max_concurrent)
    # The test database only: the factory returns the `test_pg_client`
    # fixture's client, never `get_pg_client()` (that is the app database).
    return PgLeaseStore(max_concurrent=max_concurrent,
                        get_pg_client=pg_client.get_client)


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
    pg_client = (request.getfixturevalue("test_pg_client")
                 if kind == "pg" else None)
    store = _new_store(kind, pg_client=pg_client)
    created: list[str] = []

    def cleanup():
        try:
            asyncio.run(_delete_rows(store, created))
        except Exception:  # noqa: BLE001
            pass

    request.addfinalizer(cleanup)

    # Attach a helper that acquires + records the request_id for cleanup.
    async def acquire(user_id: str, session_id: str, ttl_s: float = 120.0,
                      request_id: str | None = None):
        if request_id is None:
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


def test_same_user_four_sessions_fourth_refused(store):
    # S1: the cap counts the caller's own other sessions — one user with
    # many sessions cannot take every slot.
    u = _uid(50)
    results = []
    for i in range(4):
        ok, _ = asyncio.run(store.acquire_with_id(u, _uid(500 + i)))
        results.append(ok)
    assert results == [True, True, True, False]  # 4th refused


def test_cap_shared_across_users_release_frees_slot(store):
    # S1: the cap is on loop turns running across the whole system. Three
    # users fill it, a fourth user is refused; when one releases, the
    # fourth gets in.
    users = [_uid(60 + i) for i in range(3)]
    sessions = [_uid(600 + i) for i in range(3)]
    reqs = []
    for uid, sid in zip(users, sessions):
        ok, req = asyncio.run(store.acquire_with_id(uid, sid))
        assert ok is True
        reqs.append(req)
    ok, _ = asyncio.run(store.acquire_with_id(_uid(63), _uid(603)))
    assert ok is False  # cap full (fourth user refused)
    asyncio.run(store.release(users[0], reqs[0]))  # one slot frees
    ok, _ = asyncio.run(store.acquire_with_id(_uid(63), _uid(603)))
    assert ok is True  # the fourth user gets in


def test_renewal_same_request_id_when_cap_full(store):
    # S1: renewal by the same request_id succeeds while the cap is full —
    # the count excludes the renewing pair's own row.
    pairs = [(_uid(70 + i), _uid(700 + i)) for i in range(3)]  # cap = 3
    first = None
    for uid, sid in pairs:
        ok, req = asyncio.run(store.acquire_with_id(uid, sid))
        assert ok is True
        if first is None:
            first = (uid, sid, req)
    uid, sid, req = first
    ok, _ = asyncio.run(store.acquire_with_id(uid, sid, request_id=req))
    assert ok is True  # same request renews


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


# ── Test-DSN guard (unit: runs without any database) ─────────────────────

from tests.langgraph_agents.loop.conftest import resolve_test_dsn  # noqa: E402

_APP = "postgresql://u:p@ep-snowy-sky-123.us-east-1.aws.neon.tech/db"
_TEST = "postgresql://u:p@ep-other-branch-456.us-east-1.aws.neon.tech/db"


def test_resolve_test_dsn_unset():
    dsn, reason = resolve_test_dsn({"VVA_PG_DSN": _APP})
    assert dsn is None
    assert reason == "ECA_TEST_PG_DSN not set"


def test_resolve_test_dsn_same_host_as_app_is_refused():
    dsn, reason = resolve_test_dsn(
        {"VVA_PG_DSN": _APP, "ECA_TEST_PG_DSN": _APP.replace("/db", "/other")})
    assert dsn is None
    assert reason == "ECA_TEST_PG_DSN points at the app database"


def test_resolve_test_dsn_pooler_of_app_host_is_refused():
    pooled = "postgresql://u:p@ep-snowy-sky-123-pooler.us-east-1.aws.neon.tech/db"
    dsn, reason = resolve_test_dsn(
        {"VVA_PG_DSN": _APP, "ECA_TEST_PG_DSN": pooled})
    assert dsn is None
    assert reason == "ECA_TEST_PG_DSN points at the app database"


def test_resolve_test_dsn_different_host_is_returned():
    dsn, reason = resolve_test_dsn(
        {"VVA_PG_DSN": _APP, "ECA_TEST_PG_DSN": _TEST})
    assert dsn == _TEST
    assert reason is None



# ── F7: asyncpg returns UUID objects; a renewal compares as strings ──────

def test_pg_acquire_renewal_compares_uuid_with_str():
    """A live row owned by this request (UUID object from the driver) is a
    renewal, not a conflict, when the caller passes the id as a string."""
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone

    req = uuid.uuid4()
    live = datetime.now(timezone.utc) + timedelta(seconds=60)

    class _Conn:
        async def execute(self, *a, **k):
            return None

        async def fetchrow(self, *a, **k):
            return {"request_id": req, "expires_at": live}  # UUID, not str

        async def fetchval(self, *a, **k):
            return 0

    class _Pg:
        @asynccontextmanager
        async def transaction(self):
            yield _Conn()

    store = PgLeaseStore(max_concurrent=3, get_pg_client=lambda: _Pg())
    assert asyncio.run(store.acquire(_U1, _S1, str(req), 120.0)) is True
    # Another request on the same live session is still refused.
    other = str(uuid.uuid4())
    assert asyncio.run(store.acquire(_U1, _S1, other, 120.0)) is False
