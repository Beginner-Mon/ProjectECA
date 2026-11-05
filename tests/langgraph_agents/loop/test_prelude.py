"""Tests for `loop.prelude.run_prelude` and `run_retention` (R4)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from langgraph_agents.loop import actions as A
from langgraph_agents.loop import execute as E
from langgraph_agents.loop import prelude as P
from langgraph_agents.loop import turns as T
from langgraph_agents.loop.actions import (
    APPROVED, EXECUTING, EXPIRED, INVALIDATED, PROPOSED, SUCCEEDED, UNKNOWN,
    MemoryActionStore, new_action,
)
from langgraph_agents.loop.execute import STALE_EXECUTING_S
from langgraph_agents.loop.prelude import run_prelude, run_retention, schedule_retention
from langgraph_agents.loop.turns import (
    FINISHED, INVALIDATED as T_INVALIDATED, PAUSED, RESUMED, MemoryTurnStore,
)


def _id() -> str:
    return str(uuid.uuid4())


class _FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def _action(user: str, session: str, *, thread: str | None = None,
            call: str | None = None, now: datetime | None = None,
            **extra) -> A.ActionRecord:
    return new_action(
        user_id=user, session_id=session, thread_id=thread or _id(),
        tool_call_id=call or _id(), plugin_id="dev-reminders", tool="create_reminder",
        arguments={"title": "uống thuốc", "at": "08:00"},
        payload_display=[{"label": "title", "value": "uống thuốc"}],
        recipients=["a@x.org", "b@y.org"],
        provenance=[{"tool": "kb_search", "source_label": "kb"}],
        payload_enc=b"\x01sealed-bytes", plugin_version="1", tool_def_sha256="d" * 64,
        now=now, **extra)


def _install(monkeypatch, action_store, turn_store) -> None:
    monkeypatch.setattr(P, "get_action_store", lambda: action_store)
    monkeypatch.setattr(P, "get_turn_store", lambda: turn_store)


def _memory_stores(monkeypatch):
    clock = _FakeClock()
    store_a = MemoryActionStore(clock=clock)
    store_t = MemoryTurnStore(clock=clock)
    _install(monkeypatch, store_a, store_t)
    return clock, store_a, store_t


# ── PG path with a fake client ─────────────────────────────────────────────

class _FakeConn:
    def __init__(self):
        self.executed = []
        self.rows = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def fetch(self, sql, *args):
        self.executed.append((sql, args))
        return self.rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakePG:
    def __init__(self, conn):
        self._conn = conn
        self.scopes = []

    def user_scope(self, user_id):
        self.scopes.append(str(user_id))
        return self._conn


def test_prelude_pg_path_one_fetch_in_one_user_scope(monkeypatch):
    """PG path: one fetch inside one user_scope(<that user>); the statement
    holds all three UPDATEs; only invalidated ids come back."""
    conn = _FakeConn()
    conn.rows = [
        {"kind": "stale", "id": "stale-action-id"},
        {"kind": "invalidated", "id": "inv-action-id"},
        {"kind": "paused", "id": "paused-thread-id"},
    ]
    pg = _FakePG(conn)
    _install(monkeypatch,
             A.PgActionStore(get_pg_client=lambda: pg),
             T.PgTurnStore(get_pg_client=lambda: pg))

    user_id = _id()
    session_id = _id()
    invalidated = asyncio.run(run_prelude(user_id, session_id))

    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert pg.scopes == [user_id]
    assert args[:2] == (user_id, session_id)
    assert sql.count("UPDATE") == 3
    assert invalidated == ["inv-action-id"]


def test_prelude_pg_fetch_raises_returns_empty_and_logs_warning(monkeypatch, caplog):
    """The fetch raises: [] comes back and one WARNING carries the class."""
    conn = _FakeConn()

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    conn.fetch = boom
    pg = _FakePG(conn)
    _install(monkeypatch,
             A.PgActionStore(get_pg_client=lambda: pg),
             T.PgTurnStore(get_pg_client=lambda: pg))

    with caplog.at_level(logging.WARNING, logger="langgraph.loop.prelude"):
        invalidated = asyncio.run(run_prelude(_id(), _id()))

    assert invalidated == []
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING
                and r.getMessage() == "loop_prelude_failed"]
    assert len(warnings) == 1
    assert warnings[0].error == "RuntimeError"


# ── Memory path ────────────────────────────────────────────────────────────

def test_prelude_memory_proposed_becomes_invalidated(monkeypatch):
    """A proposed action of the session becomes invalidated."""
    store_a = MemoryActionStore()
    store_t = MemoryTurnStore()
    _install(monkeypatch, store_a, store_t)

    user_id = _id()
    session_id = _id()
    rec = _action(user_id, session_id)
    asyncio.run(store_a.propose(rec))

    invalidated = asyncio.run(run_prelude(user_id, session_id))

    assert invalidated == [rec.action_id]
    got = asyncio.run(store_a.get(user_id, rec.action_id))
    assert got.status == INVALIDATED


def test_prelude_memory_paused_becomes_invalidated(monkeypatch):
    """A paused turn of the session becomes invalidated."""
    store_a = MemoryActionStore()
    store_t = MemoryTurnStore()
    _install(monkeypatch, store_a, store_t)

    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)))

    invalidated = asyncio.run(run_prelude(user_id, session_id))

    assert invalidated == []          # only action ids are returned
    got = asyncio.run(store_t.get(user_id, thread_id))
    assert got.status == T_INVALIDATED


def test_prelude_memory_old_executing_becomes_unknown(monkeypatch):
    """An executing action older than STALE_EXECUTING_S becomes unknown."""
    clock, store_a, store_t = _memory_stores(monkeypatch)
    monkeypatch.setattr(E, "get_action_store", lambda: store_a)

    user_id = _id()
    session_id = _id()
    rec = _action(user_id, session_id, now=clock.now)
    asyncio.run(store_a.propose(rec))
    asyncio.run(store_a.transition(user_id, rec.action_id, PROPOSED, APPROVED))
    asyncio.run(store_a.transition(user_id, rec.action_id, APPROVED, EXECUTING))
    clock.advance(seconds=STALE_EXECUTING_S + 1)

    invalidated = asyncio.run(run_prelude(user_id, session_id))

    assert invalidated == []          # executing -> unknown, not invalidated
    got = asyncio.run(store_a.get(user_id, rec.action_id))
    assert got.status == UNKNOWN


def test_prelude_memory_invalidated_ids_returned(monkeypatch):
    """Every invalidated action id of the session comes back."""
    store_a = MemoryActionStore()
    store_t = MemoryTurnStore()
    _install(monkeypatch, store_a, store_t)

    user_id = _id()
    session_id = _id()
    rec1 = _action(user_id, session_id)
    rec2 = _action(user_id, session_id)
    asyncio.run(store_a.propose(rec1))
    asyncio.run(store_a.propose(rec2))

    invalidated = asyncio.run(run_prelude(user_id, session_id))

    assert sorted(invalidated) == sorted([rec1.action_id, rec2.action_id])


# ── schedule_retention ─────────────────────────────────────────────────────

def test_schedule_retention_due_on_first_call():
    """It is due on the first call."""
    P._retention_last_run.clear()
    spawned = []

    assert schedule_retention("user1", spawned.append, now=lambda: 10_000.0) is True
    assert len(spawned) == 1
    spawned[0].close()


def test_schedule_retention_not_due_at_59_min():
    """Not due again at +59 min."""
    P._retention_last_run.clear()
    spawned = []
    base = 10_000.0

    assert schedule_retention("user1", spawned.append, now=lambda: base) is True
    assert schedule_retention(
        "user1", spawned.append, now=lambda: base + 59 * 60) is False
    assert len(spawned) == 1
    spawned[0].close()


def test_schedule_retention_due_again_at_61_min():
    """Due again at +61 min."""
    P._retention_last_run.clear()
    spawned = []
    base = 10_000.0

    assert schedule_retention("user1", spawned.append, now=lambda: base) is True
    assert schedule_retention(
        "user1", spawned.append, now=lambda: base + 61 * 60) is True
    assert len(spawned) == 2
    for coro in spawned:
        coro.close()


def test_run_prelude_returns_before_retention_coroutine_runs(monkeypatch):
    """The turn does not wait for retention: spawn only stores the coroutine."""
    P._retention_last_run.clear()
    started = []

    async def fake_retention(user_id):
        started.append(user_id)

    monkeypatch.setattr(P, "run_retention", fake_retention)
    store_a = MemoryActionStore()
    store_t = MemoryTurnStore()
    _install(monkeypatch, store_a, store_t)

    spawned = []
    user_id = _id()
    session_id = _id()

    async def turn():
        invalidated = await run_prelude(user_id, session_id)
        schedule_retention(user_id, spawned.append, now=lambda: 10_000.0)
        return invalidated

    invalidated = asyncio.run(turn())

    assert invalidated == []
    assert len(spawned) == 1
    assert started == []              # the coroutine has not started
    spawned[0].close()


# ── Each retention rule on the memory stores ──────────────────────────────

def test_retention_expired_proposed(monkeypatch):
    """proposed whose 30 min TTL has passed -> expired."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))

    clock.advance(minutes=31)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)).status == EXPIRED


def test_retention_expired_proposed_not_before_ttl(monkeypatch):
    """At 29 min the TTL has not passed: still proposed."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))

    clock.advance(minutes=29)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)).status == PROPOSED


def test_retention_stale_approved(monkeypatch):
    """approved older than 3600 s -> invalidated."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))
    asyncio.run(store_a.transition(user_id, rec.action_id, PROPOSED, APPROVED))

    clock.advance(seconds=3601)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)).status == INVALIDATED


def test_retention_stale_approved_not_before_limit(monkeypatch):
    """At 3599 s it is still approved."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))
    asyncio.run(store_a.transition(user_id, rec.action_id, PROPOSED, APPROVED))

    clock.advance(seconds=3599)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)).status == APPROVED


def test_retention_payload_after_final(monkeypatch):
    """A final action loses payload_display and recipients."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))
    asyncio.run(store_a.transition(user_id, rec.action_id, PROPOSED, APPROVED))
    asyncio.run(store_a.transition(user_id, rec.action_id, APPROVED, EXECUTING))
    asyncio.run(store_a.transition(user_id, rec.action_id, EXECUTING, SUCCEEDED))

    asyncio.run(run_retention(user_id))

    got = asyncio.run(store_a.get(user_id, rec.action_id))
    assert got.payload_display == []
    assert got.recipients == []


def test_retention_old_metadata(monkeypatch):
    """An action older than 30 days -> deleted."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))

    clock.advance(days=31)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)) is None


def test_retention_old_metadata_not_before_limit(monkeypatch):
    """At 29 days the action is still there."""
    clock, store_a, _ = _memory_stores(monkeypatch)
    user_id = _id()
    rec = _action(user_id, _id(), now=clock.now)
    asyncio.run(store_a.propose(rec))

    clock.advance(days=29)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_a.get(user_id, rec.action_id)) is not None


def test_retention_stale_paused(monkeypatch):
    """paused older than 1800 s -> invalidated."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))

    clock.advance(seconds=1801)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_t.get(user_id, thread_id)).status == T_INVALIDATED


def test_retention_stale_paused_not_before_limit(monkeypatch):
    """At 1799 s it is still paused."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))

    clock.advance(seconds=1799)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_t.get(user_id, thread_id)).status == PAUSED


def test_retention_old_turns(monkeypatch):
    """finished turns older than 30 days -> deleted."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))
    asyncio.run(store_t.claim(user_id, thread_id))
    asyncio.run(store_t.finish(user_id, thread_id))

    clock.advance(days=31)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_t.get(user_id, thread_id)) is None


def test_retention_old_turns_not_before_limit(monkeypatch):
    """At 29 days the finished turn is still there."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))
    asyncio.run(store_t.claim(user_id, thread_id))
    asyncio.run(store_t.finish(user_id, thread_id))

    clock.advance(days=29)
    asyncio.run(run_retention(user_id))

    got = asyncio.run(store_t.get(user_id, thread_id))
    assert got is not None and got.status == FINISHED





def test_retention_stuck_resumed_turn(monkeypatch):
    """A resumed turn older than 600 s -> invalidated."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))
    asyncio.run(store_t.claim(user_id, thread_id))      # status = resumed

    clock.advance(seconds=601)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_t.get(user_id, thread_id)).status == T_INVALIDATED


def test_retention_stuck_resumed_not_before_limit(monkeypatch):
    """At 599 s it is still resumed."""
    clock, _, store_t = _memory_stores(monkeypatch)
    user_id = _id()
    session_id = _id()
    thread_id = _id()
    asyncio.run(store_t.pause(
        user_id, session_id, thread_id, request_id=_id(), transcript_enc=b"x",
        state_json={}, expires_at=clock.now + timedelta(minutes=30)))
    asyncio.run(store_t.claim(user_id, thread_id))

    clock.advance(seconds=599)
    asyncio.run(run_retention(user_id))

    assert asyncio.run(store_t.get(user_id, thread_id)).status == RESUMED


# ── Retention PG path with a fake client ──────────────────────────────────

def test_retention_pg_path_one_user_scope_and_all_statements(monkeypatch):
    """Retention on PG: one user_scope, the seven statements in order."""
    conn = _FakeConn()
    pg = _FakePG(conn)
    _install(monkeypatch,
             A.PgActionStore(get_pg_client=lambda: pg),
             T.PgTurnStore(get_pg_client=lambda: pg))

    user_id = _id()
    asyncio.run(run_retention(user_id))

    assert pg.scopes == [user_id]
    assert [sql for sql, _ in conn.executed] == [
        A.SQL_SWEEP_EXPIRED_PROPOSED,
        A.SQL_SWEEP_STALE_APPROVED,
        A.SQL_SWEEP_PAYLOAD_AFTER_FINAL,
        A.SQL_SWEEP_OLD_METADATA,
        T.SQL_SWEEP_STALE_PAUSED,
        T.SQL_SWEEP_OLD_TURNS,
        T.SQL_SWEEP_STUCK_RESUMED,
    ]
    assert conn.executed[1][1] == (user_id, 3600.0)
    assert conn.executed[3][1] == (user_id, 30)
    assert conn.executed[4][1] == (user_id, 1800.0)
    assert conn.executed[5][1] == (user_id, 30)
    assert conn.executed[6][1] == (user_id, 600.0)


# ── PG contract (marked integration) ──────────────────────────────────────

@pytest.mark.integration
async def test_prelude_pg_contract_run_prelude(test_pg_client, monkeypatch):
    """PG contract: run_prelude against the test DSN."""
    async with test_pg_client.transaction() as conn:
        if not await conn.fetchval("SELECT to_regclass('agent_actions')"):
            pytest.skip("migration 021 not applied")

    action_store = A.PgActionStore(get_pg_client=test_pg_client.get_client)
    turn_store = T.PgTurnStore(get_pg_client=test_pg_client.get_client)
    monkeypatch.setattr(P, "get_action_store", lambda: action_store)
    monkeypatch.setattr(P, "get_turn_store", lambda: turn_store)

    invalidated = await run_prelude(_id(), _id())
    assert invalidated == []


@pytest.mark.integration
async def test_prelude_pg_contract_run_retention(test_pg_client, monkeypatch):
    """PG contract: run_retention against the test DSN."""
    async with test_pg_client.transaction() as conn:
        if not await conn.fetchval("SELECT to_regclass('agent_actions')"):
            pytest.skip("migration 021 not applied")

    action_store = A.PgActionStore(get_pg_client=test_pg_client.get_client)
    turn_store = T.PgTurnStore(get_pg_client=test_pg_client.get_client)
    monkeypatch.setattr(P, "get_action_store", lambda: action_store)
    monkeypatch.setattr(P, "get_turn_store", lambda: turn_store)

    await run_retention(_id())      # must not raise