# -*- coding: utf-8 -*-
"""Regression test for the 11-09 "send button stuck in stop" latency bug.

Owner's vva.log: after the answer text was fully shown, the send button
stayed in "stop" for 6.5-7.9s on TEXT-mode turns (no TTS involved). Root
cause: write_session_turn (db/session_store.py) made THREE separate
`PostgresClient.execute()` calls, and each one opens its OWN transaction —
BEGIN, `SELECT set_config('app.user_id', ...)` for row-level security,
the query, COMMIT — 4 round trips per call, each paying full
Vietnam-to-us-east-1 latency (~1s). Up to 12 sequential round trips for one
turn's persistence.

The fix holds ONE `pg.transaction()` (one connection, one BEGIN, one
set_config, one COMMIT) across TWO queries instead of three separate
transactions. This test verifies that shape against a fully mocked
PostgresClient — no live database, no network — by counting how many times
`.transaction()` and the yielded connection's `.execute()` are called, and by
checking the two queries' SQL text is in the right order.

NOT combined into a single WITH-CTE statement covering users + conversations
+ messages together, even though that would get to a literal "one query" —
see write_session_turn's own docstring in db/session_store.py for why: the
`messages` row-level-security WITH CHECK policy subqueries `conversations`,
and PostgreSQL's documented CTE-snapshot-sharing means that subquery would
not see conversations' own row from the SAME statement for a brand-new
session's first turn. Two statements in one transaction avoids that; this
test pins exactly that shape so a future "optimize this to one CTE" doesn't
reintroduce the RLS bug silently.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


class _FakeConn:
    """Stands in for the asyncpg Connection yielded by PostgresClient.transaction()."""

    def __init__(self):
        self.execute_calls: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args):
        self.execute_calls.append((query, args))
        return "INSERT 0 1"


class _FakeTransactionCM:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePg:
    """Stands in for get_pg_client()'s return value.

    Deliberately does NOT implement `.execute()` / `.executemany()` at all —
    if write_session_turn regresses to calling those (the old per-statement
    round-trip shape) instead of holding one `.transaction()`, this raises
    AttributeError rather than silently passing.
    """

    def __init__(self):
        self.conn = _FakeConn()
        self.connect_calls = 0
        self.transaction_calls = 0

    async def connect(self):
        self.connect_calls += 1

    def transaction(self):
        self.transaction_calls += 1
        return _FakeTransactionCM(self.conn)


@pytest.fixture
def fake_pg_and_stm(monkeypatch):
    from langgraph_agents.db import session_store

    fake_pg = _FakePg()
    monkeypatch.setattr(session_store, "get_pg_client", lambda: fake_pg)

    # _append_stm's STM write is unrelated to this test's concern (DB round
    # trips) — stub it so this test doesn't also depend on shared/stm.py's
    # backend selection.
    fake_stm = MagicMock()
    fake_stm.get = AsyncMock(return_value=[])
    fake_stm.set = AsyncMock()
    monkeypatch.setattr(session_store, "get_stm", lambda: fake_stm)

    return fake_pg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_session_turn_holds_one_transaction_two_queries(fake_pg_and_stm):
    """One `pg.transaction()` (was: up to three separate pg.execute() calls,
    each opening its own), and within it exactly two queries — not three."""
    from langgraph_agents.db.session_store import write_session_turn

    fake_pg = fake_pg_and_stm

    await write_session_turn(
        user_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        user_query="hello",
        assistant_answer="hi there",
        total_tokens=5,
    )

    assert fake_pg.transaction_calls == 1, (
        "write_session_turn must hold ONE transaction, not open a new one "
        "per statement (that is the 6.5-7.9s bug this guards)"
    )
    assert len(fake_pg.conn.execute_calls) == 2, (
        "expected exactly 2 queries inside the held transaction "
        "(users+conversations CTE, then messages)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_session_turn_query_order_and_conflict_clauses(fake_pg_and_stm):
    """First query upserts users + conversations (ON CONFLICT preserved for
    both); second inserts the messages pair, user row before assistant row
    in the VALUES list text — BIGSERIAL seq_id is assigned in VALUES order,
    so this is what keeps the user's row at the lower seq_id."""
    from langgraph_agents.db.session_store import write_session_turn

    fake_pg = fake_pg_and_stm

    await write_session_turn(
        user_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        user_query="hello",
        assistant_answer="hi there",
        total_tokens=5,
    )

    (users_conv_sql, users_conv_args), (messages_sql, messages_args) = fake_pg.conn.execute_calls

    assert "INSERT INTO users" in users_conv_sql
    assert "ON CONFLICT (id) DO NOTHING" in users_conv_sql
    assert "INSERT INTO conversations" in users_conv_sql
    assert "ON CONFLICT (session_id) DO UPDATE" in users_conv_sql
    assert "updated_at = now()" in users_conv_sql

    assert "INSERT INTO messages" in messages_sql
    assert "'user'" in messages_sql and "'assistant'" in messages_sql
    assert messages_sql.index("'user'") < messages_sql.index("'assistant'"), (
        "user row must appear before the assistant row in the VALUES list — "
        "BIGSERIAL seq_id follows VALUES order, and the user's turn must "
        "sort first"
    )
    # created_at is not mentioned at all in the messages INSERT — still left
    # to the column DEFAULT (now()), exactly as before. An ISO string bound
    # to a timestamptz parameter fails under asyncpg's binary protocol, which
    # is why this was never passed as a parameter either.
    assert "created_at" not in messages_sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_write_session_turn_motion_job_id_on_assistant_row_only(fake_pg_and_stm):
    """extras JSONB (motion.job_id) must be bound for the assistant row only,
    same as before this change — the user row's extras stays NULL."""
    from langgraph_agents.db.session_store import write_session_turn

    fake_pg = fake_pg_and_stm

    await write_session_turn(
        user_id="11111111-1111-1111-1111-111111111111",
        session_id="22222222-2222-2222-2222-222222222222",
        user_query="show me a stretch",
        assistant_answer="here's one",
        total_tokens=7,
        motion_job_id="job-abc-123",
    )

    _, (messages_sql, messages_args) = fake_pg.conn.execute_calls
    # $1=session_id, $2=user_query, $3=assistant_answer, $4=total_tokens, $5=extras
    assert messages_args[-1] is not None
    assert "job-abc-123" in messages_args[-1]
