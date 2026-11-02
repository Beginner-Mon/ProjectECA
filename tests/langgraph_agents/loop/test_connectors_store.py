"""Contract suite for `PendingStore` and `ConnectionStore` (stage 5A, migration 022).

Every case runs on the memory stores and, marked `integration`, on Postgres as
`eca_user` under RLS (`user_scope()`), through `test_pg_client`. The PG runs skip
here and run in the CI job `Loop PG contract`, which applies every migration
(so 022 must apply on a bare PG after 021).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from langgraph_agents.loop.connectors.store import (
    ACTIVE, AUTH_EXPIRED, Connection, MemoryConnectionStore, MemoryPendingStore, Pending,
    PgConnectionStore, PgPendingStore,
)


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Harness:
    def __init__(self, connections, pending, run_sql=None) -> None:
        self.connections = connections
        self.pending = pending
        self.users: list[str] = []
        self.is_pg = run_sql is not None
        self.run_sql = run_sql

    def user(self) -> str:
        u = _id()
        self.users.append(u)
        return u


@pytest.fixture(params=[
    pytest.param("memory", id="memory"),
    pytest.param("pg", id="pg", marks=pytest.mark.integration),
])
def h(request):
    if request.param == "memory":
        yield Harness(MemoryConnectionStore(), MemoryPendingStore())
        return

    pg = request.getfixturevalue("test_pg_client")

    async def _has_tables():
        async with pg.get_client().transaction() as conn:
            return bool(await conn.fetchval("SELECT to_regclass('user_connections')")
                        and await conn.fetchval("SELECT to_regclass('oauth_pending')"))

    if not asyncio.run(_has_tables()):
        pytest.skip("migration 022 not applied")

    async def run_sql(user_id, sql, *args):
        async with pg.get_client().user_scope(user_id) as conn:
            return await conn.fetchval(sql, *args)

    harness = Harness(PgConnectionStore(get_pg_client=pg.get_client),
                      PgPendingStore(get_pg_client=pg.get_client), run_sql)
    yield harness

    async def _cleanup():
        for uid in harness.users:
            async with pg.get_client().user_scope(uid) as conn:
                await conn.execute("DELETE FROM user_connections")
                await conn.execute("DELETE FROM oauth_pending")

    try:
        asyncio.run(_cleanup())
    except Exception:  # noqa: BLE001 - cleanup must not mask the result
        pass


def _pending(user: str, digest: str | None = None, *, ttl: float = 600.0,
             plugin: str = "mock-calendar") -> Pending:
    return Pending(state_sha256=digest or uuid.uuid4().hex + uuid.uuid4().hex,
                   user_id=user, plugin_id=plugin, verifier_enc=b"\x01sealed-verifier",
                   issuer="https://auth.test", expires_at=_now() + timedelta(seconds=ttl))


def _conn(user: str, plugin: str = "mock-calendar", **over) -> Connection:
    base = dict(user_id=user, plugin_id=plugin, access_enc=b"\x01access", refresh_enc=b"\x01refresh",
                expires_at=_now() + timedelta(hours=1), scopes=["a.read", "a.write"],
                status=ACTIVE, issuer="https://auth.test")
    base.update(over)
    return Connection(**base)


# ── PendingStore ──────────────────────────────────────────────────────────

async def test_consume_returns_the_record_once(h):
    u = h.user()
    p = _pending(u)
    await h.pending.put(p)
    got = await h.pending.consume(u, p.state_sha256, _now())
    assert got is not None
    assert (got.plugin_id, got.issuer, got.verifier_enc) == (p.plugin_id, p.issuer, p.verifier_enc)
    assert str(got.user_id) == u
    assert await h.pending.consume(u, p.state_sha256, _now()) is None     # single use


async def test_consume_of_an_unknown_state_is_none(h):
    u = h.user()
    assert await h.pending.consume(u, "f" * 64, _now()) is None


async def test_consume_after_expiry_is_none(h):
    u = h.user()
    p = _pending(u, ttl=600)
    await h.pending.put(p)
    assert await h.pending.consume(u, p.state_sha256, p.expires_at + timedelta(seconds=1)) is None


async def test_consume_just_before_expiry_works(h):
    u = h.user()
    p = _pending(u, ttl=600)
    await h.pending.put(p)
    assert await h.pending.consume(u, p.state_sha256, p.expires_at - timedelta(seconds=1)) is not None


async def test_another_users_consume_is_none_and_does_not_burn_the_owners(h):
    owner, other = h.user(), h.user()
    p = _pending(owner)
    await h.pending.put(p)
    assert await h.pending.consume(other, p.state_sha256, _now()) is None
    assert await h.pending.consume(owner, p.state_sha256, _now()) is not None


async def test_concurrent_consumes_have_exactly_one_winner(h):
    u = h.user()
    p = _pending(u)
    await h.pending.put(p)
    results = await asyncio.gather(*[h.pending.consume(u, p.state_sha256, _now())
                                     for _ in range(5)])
    assert sum(r is not None for r in results) == 1


# ── ConnectionStore ───────────────────────────────────────────────────────

async def test_upsert_get_round_trip(h):
    u = h.user()
    c = _conn(u)
    await h.connections.upsert(c)
    got = await h.connections.get(u, c.plugin_id)
    assert got is not None and str(got.user_id) == u
    assert (got.access_enc, got.refresh_enc) == (c.access_enc, c.refresh_enc)
    assert got.scopes == ["a.read", "a.write"] and got.status == ACTIVE
    assert got.issuer == "https://auth.test"
    assert abs((got.expires_at - c.expires_at).total_seconds()) < 1
    assert await h.connections.get(u, "other") is None


async def test_upsert_replaces_the_row(h):
    u = h.user()
    await h.connections.upsert(_conn(u))
    await h.connections.upsert(_conn(u, access_enc=b"\x01new", status=AUTH_EXPIRED,
                                     refresh_enc=None, scopes=[]))
    got = await h.connections.get(u, "mock-calendar")
    assert got.access_enc == b"\x01new" and got.refresh_enc is None
    assert got.status == AUTH_EXPIRED and got.scopes == []
    assert len(await h.connections.list(u)) == 1


async def test_list_is_per_user_and_ordered(h):
    a, b = h.user(), h.user()
    await h.connections.upsert(_conn(a, "zeta"))
    await h.connections.upsert(_conn(a, "alpha"))
    await h.connections.upsert(_conn(b, "beta"))
    assert [c.plugin_id for c in await h.connections.list(a)] == ["alpha", "zeta"]
    assert [c.plugin_id for c in await h.connections.list(b)] == ["beta"]


async def test_one_user_cannot_read_or_delete_anothers_connection(h):
    a, b = h.user(), h.user()
    await h.connections.upsert(_conn(a))
    assert await h.connections.get(b, "mock-calendar") is None
    assert await h.connections.delete(b, "mock-calendar") is False
    assert await h.connections.get(a, "mock-calendar") is not None


async def test_delete_reports_whether_a_row_existed(h):
    u = h.user()
    await h.connections.upsert(_conn(u))
    assert await h.connections.delete(u, "mock-calendar") is True
    assert await h.connections.delete(u, "mock-calendar") is False
    assert await h.connections.get(u, "mock-calendar") is None


async def test_locked_on_a_missing_row_has_no_current(h):
    u = h.user()
    async with h.connections.locked(u, "mock-calendar") as lk:
        assert lk.current is None


async def test_locked_save_persists_and_mark_expired_drops_the_tokens(h):
    u = h.user()
    await h.connections.upsert(_conn(u))
    async with h.connections.locked(u, "mock-calendar") as lk:
        assert lk.current.access_enc == b"\x01access"
        await lk.save(_conn(u, access_enc=b"\x01rotated", refresh_enc=b"\x01r2"))
        assert lk.current.access_enc == b"\x01rotated"
    got = await h.connections.get(u, "mock-calendar")
    assert (got.access_enc, got.refresh_enc) == (b"\x01rotated", b"\x01r2")
    async with h.connections.locked(u, "mock-calendar") as lk:
        await lk.mark_expired()
        assert lk.current.status == AUTH_EXPIRED
    got = await h.connections.get(u, "mock-calendar")
    assert got.status == AUTH_EXPIRED and got.access_enc is None and got.refresh_enc is None


async def test_the_lock_serialises_and_the_waiter_sees_the_winners_save(h):
    u = h.user()
    await h.connections.upsert(_conn(u))
    seen: list[bytes] = []
    order: list[str] = []

    async def first():
        async with h.connections.locked(u, "mock-calendar") as lk:
            order.append("first-in")
            await asyncio.sleep(0.15)
            await lk.save(_conn(u, access_enc=b"\x01winner"))
            order.append("first-out")

    async def second():
        await asyncio.sleep(0.03)            # let `first` take the lock
        async with h.connections.locked(u, "mock-calendar") as lk:
            order.append("second-in")
            seen.append(lk.current.access_enc)

    await asyncio.gather(first(), second())
    assert order == ["first-in", "first-out", "second-in"]
    assert seen == [b"\x01winner"]           # read AFTER the lock was taken


async def test_an_error_inside_the_lock_rolls_the_save_back(h):
    if not h.is_pg:
        pytest.skip("memory has no transaction; only the Postgres lock rolls back")
    u = h.user()
    await h.connections.upsert(_conn(u))
    with pytest.raises(RuntimeError):
        async with h.connections.locked(u, "mock-calendar") as lk:
            await lk.save(_conn(u, access_enc=b"\x01half"))
            raise RuntimeError("boom")
    got = await h.connections.get(u, "mock-calendar")
    if h.is_pg:                               # a transaction: the save rolled back
        assert got.access_enc == b"\x01access"
    else:                                     # memory has no rollback; the row is whole
        assert got.access_enc in (b"\x01access", b"\x01half")


# ── RLS (Postgres only) ───────────────────────────────────────────────────

async def test_rls_hides_other_users_rows_even_without_a_where(h):
    if not h.is_pg:
        pytest.skip("RLS exists only on Postgres")
    a, b = h.user(), h.user()
    await h.connections.upsert(_conn(a))
    await h.pending.put(_pending(a))
    assert await h.run_sql(b, "SELECT count(*) FROM user_connections") == 0
    assert await h.run_sql(b, "SELECT count(*) FROM oauth_pending") == 0
    assert await h.run_sql(a, "SELECT count(*) FROM user_connections") == 1
