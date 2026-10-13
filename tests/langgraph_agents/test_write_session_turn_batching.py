# -*- coding: utf-8 -*-
"""How many database round trips a turn's two writes cost, and their shape.

The reason this file exists is latency. Every `PostgresClient.execute()` opens
its OWN transaction — BEGIN, `SELECT set_config('app.user_id', ...)` for
row-level security, the query, COMMIT — 4 round trips, each paying full
Vietnam-to-us-east-1 latency (~1s). On 11-09 a turn's persistence made three
such calls and the send button stayed in "stop" for 6.5-7.9s after the answer
was fully shown (owner's vva.log, text-mode turns, no TTS involved).

A turn is written in two parts (db/session_store.py):

  write_turn_question  as the turn starts, beside the graph: ONE
                       `pg.transaction()` holding two statements
                       (users+conversations upsert, then the user row)
  write_turn_answer    once there is an answer, in front of `done`: ONE
                       `pg.transaction()` holding ONE statement

Both are checked against a fully mocked PostgresClient — no live database, no
network — by counting `.transaction()` calls and the statements run on the
connection it yields, and by reading the SQL text.

The question's two statements are NOT one WITH-CTE, even though that would be
a literal "one query" — see write_turn_question's docstring: the `messages`
row-level-security WITH CHECK policy subqueries `conversations`, and sibling
sub-statements of one WITH do not see each other's rows, so the check would
fail on the first turn of every new session. This file pins the two-statement
shape so a future "optimize this to one CTE" does not reintroduce that.
"""

from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

_USER = "11111111-1111-1111-1111-111111111111"
_SESSION = "22222222-2222-2222-2222-222222222222"
_QUESTION_ID = "33333333-3333-3333-3333-333333333333"
_ANSWER_ID = "44444444-0000-0000-0000-000000000004"


class _FakeConn:
    """Stands in for the asyncpg Connection yielded by PostgresClient.transaction()."""

    def __init__(self):
        # Every statement, in order: (method, sql, args).
        self.statements: list[tuple[str, str, tuple]] = []

    async def execute(self, query: str, *args):
        self.statements.append(("execute", query, args))
        return "INSERT 0 1"

    async def fetchval(self, query: str, *args):
        """The assistant INSERT ... RETURNING id — one value, as asyncpg gives it."""
        self.statements.append(("fetchval", query, args))
        return _ANSWER_ID


class _FakeTransactionCM:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePg:
    """Stands in for get_pg_client()'s return value.

    Deliberately does NOT implement `.execute()` / `.fetchval()` and friends —
    if either write regresses to calling those (one transaction PER statement,
    the old round-trip shape) instead of holding one `.transaction()`, this
    raises AttributeError rather than silently passing.
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
def fake_pg(monkeypatch):
    from langgraph_agents.db import session_store

    pg = _FakePg()
    monkeypatch.setattr(session_store, "get_pg_client", lambda: pg)
    return pg


@pytest.fixture
def fake_stm(monkeypatch):
    """The short-term cache write is not this file's concern (DB round trips);
    stubbed so these tests do not depend on shared/stm.py's backend selection."""
    from langgraph_agents.db import session_store

    stm = MagicMock()
    stm.get = AsyncMock(return_value=[])
    stm.set = AsyncMock()
    monkeypatch.setattr(session_store, "get_stm", lambda: stm)
    return stm


async def _write_question(**over):
    from langgraph_agents.db.session_store import write_turn_question

    kwargs = dict(user_id=_USER, session_id=_SESSION, message_id=_QUESTION_ID,
                  user_query="hello")
    kwargs.update(over)
    return await write_turn_question(**kwargs)


async def _write_answer(**over):
    from langgraph_agents.db.session_store import write_turn_answer

    kwargs = dict(session_id=_SESSION, user_query="hello",
                  assistant_answer="hi there", total_tokens=5)
    kwargs.update(over)
    return await write_turn_answer(**kwargs)


# ── The question, as the turn starts ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_write_holds_one_transaction_two_statements(fake_pg, fake_stm):
    await _write_question()

    assert fake_pg.transaction_calls == 1, (
        "the question write must hold ONE transaction, not open a new one per "
        "statement (that is the 6.5-7.9s bug this guards)"
    )
    assert [method for method, _, _ in fake_pg.conn.statements] == ["execute", "execute"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_write_upserts_owner_rows_then_inserts_the_user_row(fake_pg, fake_stm):
    """First statement upserts users + conversations (ON CONFLICT on both);
    the second inserts the user row. Two statements, in that order: the
    messages policy has to find the conversation the first one made."""
    await _write_question(user_query="my knee hurts")

    (_, owners_sql, owners_args), (_, message_sql, message_args) = fake_pg.conn.statements

    assert "INSERT INTO users" in owners_sql
    assert "ON CONFLICT (id) DO NOTHING" in owners_sql
    assert "INSERT INTO conversations" in owners_sql
    assert "ON CONFLICT (session_id) DO UPDATE" in owners_sql
    assert "updated_at = now()" in owners_sql
    assert "messages" not in owners_sql
    assert owners_args == (_USER, _SESSION)

    assert "INSERT INTO messages" in message_sql
    assert "'user'" in message_sql and "'assistant'" not in message_sql
    assert "conversations" not in message_sql
    assert message_args == (_QUESTION_ID, _SESSION, "my knee hurts")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_row_takes_its_id_from_the_caller(fake_pg, fake_stm):
    """The memory node leaves the running turn's question out by this id, so
    the row cannot be given one by the database."""
    await _write_question(message_id="aaaaaaaa-0000-0000-0000-00000000000a")

    _, message_sql, message_args = fake_pg.conn.statements[1]
    assert "INSERT INTO messages (id, session_id, role, content)" in " ".join(message_sql.split())
    assert message_args[0] == "aaaaaaaa-0000-0000-0000-00000000000a"
    # Written a second time (the caller retries once), the same id is a no-op.
    assert "ON CONFLICT (id) DO NOTHING" in message_sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_row_leaves_created_at_to_the_database(fake_pg, fake_stm):
    """An ISO string bound to a timestamptz parameter fails under asyncpg's
    binary protocol, which is why it is never passed as one."""
    await _write_question()

    _, message_sql, _ = fake_pg.conn.statements[1]
    assert "created_at" not in message_sql
    assert "token_count" not in message_sql and "extras" not in message_sql


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_write_does_not_touch_the_short_term_cache(fake_pg, fake_stm):
    """The cache holds question/answer pairs; half a turn is not one."""
    await _write_question()

    fake_stm.get.assert_not_awaited()
    fake_stm.set.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_question_write_accepts_a_non_uuid_user_id(fake_pg, fake_stm):
    from langgraph_agents.db.session_store import _to_uuid

    await _write_question(user_id="anonymous")

    assert fake_pg.conn.statements[0][2] == (_to_uuid("anonymous"), _SESSION)


# ── The answer, once there is one ────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_write_holds_one_transaction_one_statement(fake_pg, fake_stm):
    """This one sits between the last token and `done`."""
    assistant_id = await _write_answer()

    assert fake_pg.transaction_calls == 1
    assert [method for method, _, _ in fake_pg.conn.statements] == ["fetchval"]
    assert assistant_id == _ANSWER_ID, "must return the id from the RETURNING clause"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_statement_inserts_the_assistant_row_and_bumps_the_conversation(
        fake_pg, fake_stm):
    await _write_answer(assistant_answer="try heel slides", total_tokens=7)

    _, sql, args = fake_pg.conn.statements[0]
    flat = " ".join(sql.split())
    assert "UPDATE conversations SET updated_at = now()" in flat
    assert "INSERT INTO messages" in flat
    assert "'assistant'" in flat and "'user'" not in flat
    assert flat.endswith("RETURNING id")
    # The question is not written again, and no row is created for its owner.
    assert "INSERT INTO users" not in flat and "INSERT INTO conversations" not in flat
    assert "created_at" not in flat
    # $1 session, $2 content, $3 token_count, $4 extras
    assert args == (_SESSION, "try heel slides", 7, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_write_returns_none_when_no_row_came_back(fake_pg, fake_stm, monkeypatch):
    monkeypatch.setattr(fake_pg.conn, "fetchval", AsyncMock(return_value=None))

    assert await _write_answer() is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_extras_are_null_without_motion_or_meta(fake_pg, fake_stm):
    await _write_answer()

    assert fake_pg.conn.statements[0][2][-1] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_extras_carry_the_motion_job_id(fake_pg, fake_stm):
    await _write_answer(motion_job_id="job-abc-123")

    extras = json.loads(fake_pg.conn.statements[0][2][-1])
    assert extras == {"motion": {"job_id": "job-abc-123"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_extras_carry_the_motion_prompt_when_given(fake_pg, fake_stm):
    """motion.prompt is the Kimodo resolved_query, stored beside the job id."""
    await _write_answer(motion_job_id="job-abc-123", motion_prompt="squat movement")

    extras = json.loads(fake_pg.conn.statements[0][2][-1])
    assert extras == {"motion": {"job_id": "job-abc-123", "prompt": "squat movement"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_extras_carry_meta_beside_motion(fake_pg, fake_stm):
    """`meta` (turn context snapshot) lands next to "motion" in the same JSONB."""
    meta = {"request_id": "req-1", "persona_id": "anne", "latency_ms": 42}
    await _write_answer(motion_job_id="job-abc-123", meta=meta)

    extras = json.loads(fake_pg.conn.statements[0][2][-1])
    assert extras == {"motion": {"job_id": "job-abc-123"}, "meta": meta}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_extras_carry_meta_without_any_motion(fake_pg, fake_stm):
    """extras is not gated on motion_job_id being present."""
    await _write_answer(meta={"request_id": "req-2"})

    extras = json.loads(fake_pg.conn.statements[0][2][-1])
    assert extras == {"meta": {"request_id": "req-2"}}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_answer_write_appends_the_pair_to_the_short_term_cache(fake_pg, fake_stm):
    await _write_answer(user_query="my knee hurts", assistant_answer="try heel slides")

    fake_stm.set.assert_awaited_once()
    session_id, turns = fake_stm.set.await_args.args
    assert session_id == _SESSION
    assert [(t["q"], t["a"]) for t in turns] == [("my knee hurts", "try heel slides")]


# ── The pair ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_whole_turn_costs_two_transactions_and_three_statements(fake_pg, fake_stm):
    """Question then answer: the question row is inserted first, so its
    BIGSERIAL seq_id is the lower of the two and it sorts first."""
    await _write_question()
    await _write_answer()

    assert fake_pg.transaction_calls == 2
    statements = [" ".join(sql.split()) for _, sql, _ in fake_pg.conn.statements]
    assert len(statements) == 3
    inserts = [s for s in statements if "INSERT INTO messages" in s]
    assert "'user'" in inserts[0] and "'assistant'" in inserts[1]


@pytest.mark.unit
def test_the_single_turn_write_is_gone():
    from langgraph_agents.db import session_store

    assert not hasattr(session_store, "write_session_turn")
