"""Turn index against a real Postgres (memory v2, B1b).

Runs only against the development database: the test is skipped unless
ECA_TEST_PG_DSN is set AND the application is pointed at that same database
(VVA_PG_DSN equal to it). That equality is the guard — the project's own `.env`
points VVA_PG_DSN at production, and these tests write rows.

    python <scratchpad>/pytest_on_branch.py <repo> \\
        tests/langgraph_agents/test_turn_index_integration.py -m integration

Every test makes its own users, conversations and messages with random UUIDs,
through the application's own client, and deletes the users at the end (their
conversations, messages and index rows go with them by cascade).
"""

from __future__ import annotations

import os
import uuid

import pytest
from langchain_core.runnables import RunnableConfig

pytestmark = pytest.mark.integration


@pytest.fixture
async def pg(monkeypatch):
    from langgraph_agents.shared.env import load_env

    load_env()
    test_dsn = os.getenv("ECA_TEST_PG_DSN", "").strip()
    if not test_dsn:
        pytest.skip("ECA_TEST_PG_DSN not set")
    if os.getenv("VVA_PG_DSN", "").strip() != test_dsn:
        pytest.skip("the application is not pointed at the test database")

    from langgraph_agents.db.postgres import bind_request_user
    from langgraph_agents.shared import get_pg_client

    monkeypatch.setenv("VVA_HISTORY_INDEX", "1")
    client = get_pg_client()
    await client.connect()
    created: list[str] = []
    yield client, created
    # Cleanup runs pass or fail. A user's own scope is needed to see its rows.
    for user_id in created:
        try:
            bind_request_user(user_id)
            await client.execute("DELETE FROM users WHERE id = $1", user_id)
        except Exception as exc:                              # noqa: BLE001
            print(f"cleanup of {user_id} failed: {exc}")
    bind_request_user(None)


async def _user(pg_and_created) -> str:
    from langgraph_agents.db.postgres import bind_request_user

    pg, created = pg_and_created
    user_id = str(uuid.uuid4())
    created.append(user_id)
    bind_request_user(user_id)
    await pg.execute("INSERT INTO users (id) VALUES ($1)", user_id)
    return user_id


async def _conversation(pg, user_id: str, title: str | None = None) -> str:
    session_id = str(uuid.uuid4())
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id, title) VALUES ($1, $2, $3)",
        session_id, user_id, title,
    )
    return session_id


async def _message(pg, session_id: str, role: str, content: str):
    return await pg.fetchval(
        "INSERT INTO messages (session_id, role, content) VALUES ($1, $2, $3) RETURNING id",
        session_id, role, content,
    )


async def _turn(pg, session_id: str, question: str, answer: str):
    q = await _message(pg, session_id, "user", question)
    a = await _message(pg, session_id, "assistant", answer)
    return q, a


async def _count(pg, session_id: str) -> int:
    return await pg.fetchval("SELECT count(*) FROM turn_index WHERE session_id = $1", session_id)


def _config(user_id: str, current_session: str, words: str) -> RunnableConfig:
    return RunnableConfig(configurable={
        "user_id": user_id, "session_id": current_session, "query": words,
        "request_id": "it-1",
    })


_KNEE_Q = "My knee hurts when I climb stairs, which exercises can help?"
_KNEE_A = ("Try gentle quadriceps strengthening: straight leg raises, mini squats and "
           "step-ups on a low step, three sets of ten, and stop if the pain sharpens.")
_SHOULDER_Q = "I have a stiff shoulder after sleeping badly, what should I do?"
_SHOULDER_A = ("Do pendulum swings and gentle cross-body stretches for the shoulder, "
               "hold each for thirty seconds, twice a day.")


@pytest.mark.asyncio
async def test_indexer_writes_one_row_per_answered_turn_and_is_idempotent(pg):
    from langgraph_agents.db.turn_index import index_new_turns

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    await _turn(client, session, _KNEE_Q, _KNEE_A)
    await _turn(client, session, _SHOULDER_Q, _SHOULDER_A)

    assert await index_new_turns(user) == 2
    assert await _count(client, session) == 2
    assert await index_new_turns(user) == 0
    assert await _count(client, session) == 2

    row = await client.fetchrow(
        "SELECT t.content, t.turn_at, a.created_at AS answered "
        "FROM turn_index t JOIN messages a ON a.id = t.assistant_message_id "
        "WHERE t.content LIKE 'My knee%'", )
    assert row["content"] == f"{_KNEE_Q}\n{_KNEE_A}"
    assert row["turn_at"] == row["answered"]


@pytest.mark.asyncio
async def test_a_question_without_an_answer_is_not_a_turn(pg):
    from langgraph_agents.db.turn_index import index_new_turns

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    await _message(client, session, "user", "A question nobody answered")
    assert await index_new_turns(user) == 0
    # Two questions in a row: the answer pairs with the nearer one.
    await _message(client, session, "user", _KNEE_Q)
    await _message(client, session, "assistant", _KNEE_A)
    assert await index_new_turns(user) == 1
    content = await client.fetchval("SELECT content FROM turn_index WHERE session_id = $1", session)
    assert content.startswith(_KNEE_Q) and "nobody answered" not in content


@pytest.mark.asyncio
async def test_user_b_cannot_read_user_a_rows_nor_find_them(pg):
    from langgraph_agents.db.postgres import bind_request_user
    from langgraph_agents.db.turn_index import index_new_turns
    from langgraph_agents.tools.pgvector_tool import memory_search

    client, _ = pg
    a = await _user(pg)
    a_session = await _conversation(client, a)
    await _turn(client, a_session, _KNEE_Q, _KNEE_A)
    assert await index_new_turns(a) == 1

    b = await _user(pg)                       # binds B
    b_session = await _conversation(client, b)
    assert await _count(client, a_session) == 0
    assert await client.fetchval("SELECT count(*) FROM turn_index") == 0
    result = await memory_search.ainvoke(
        {"query": "knee exercises for stairs"}, _config(b, b_session, _KNEE_Q))
    assert result == {"found": False}

    bind_request_user(a)
    assert await _count(client, a_session) == 1


@pytest.mark.asyncio
async def test_deleting_the_conversation_removes_its_rows(pg):
    from langgraph_agents.db.turn_index import index_new_turns

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    await _turn(client, session, _KNEE_Q, _KNEE_A)
    assert await index_new_turns(user) == 1
    await client.execute("DELETE FROM conversations WHERE session_id = $1", session)
    assert await _count(client, session) == 0


@pytest.mark.asyncio
async def test_deleting_either_message_of_a_turn_removes_that_turns_row(pg):
    from langgraph_agents.db.turn_index import index_new_turns

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    q1, _a1 = await _turn(client, session, _KNEE_Q, _KNEE_A)
    _q2, a2 = await _turn(client, session, _SHOULDER_Q, _SHOULDER_A)
    assert await index_new_turns(user) == 2

    await client.execute("DELETE FROM messages WHERE id = $1", q1)
    assert await _count(client, session) == 1
    await client.execute("DELETE FROM messages WHERE id = $1", a2)
    assert await _count(client, session) == 0


@pytest.mark.asyncio
async def test_memory_search_finds_the_turn_and_leaves_out_the_current_conversation(pg):
    from langgraph_agents.db.turn_index import index_new_turns
    from langgraph_agents.tools.pgvector_tool import memory_search

    client, _ = pg
    user = await _user(pg)
    old = await _conversation(client, user, title="Knee and shoulder")
    await _turn(client, old, _KNEE_Q, _KNEE_A)
    await _turn(client, old, _SHOULDER_Q, _SHOULDER_A)
    current = await _conversation(client, user)
    await _turn(client, current, "Which exercises help my knee on the stairs?", "Try step-ups.")
    assert await index_new_turns(user) == 3

    result = await memory_search.ainvoke(
        {"query": "knee exercises recommended for climbing stairs"},
        _config(user, current, "Which exercises did you recommend for my knee on the stairs?"))
    assert result.get("found") is True, result
    top = result["results"][0]
    assert top["snippet"].startswith(_KNEE_Q)
    assert top["session_id"] == old and top["session_title"] == "Knee and shoulder"
    assert all(r["session_id"] != current for r in result["results"])
    assert len(top["date"]) == 10 and top["similarity"] >= 0.87

    # Same query from inside the old conversation: nothing else holds it.
    again = await memory_search.ainvoke(
        {"query": "knee exercises recommended for climbing stairs"},
        _config(user, old, "Which exercises did you recommend for my knee on the stairs?"))
    assert all(r["session_id"] != old for r in again.get("results", []))


@pytest.mark.asyncio
async def test_memory_search_since_days_filters_on_turn_at(pg):
    from langgraph_agents.db.turn_index import index_new_turns
    from langgraph_agents.tools.pgvector_tool import memory_search

    client, _ = pg
    user = await _user(pg)
    old = await _conversation(client, user)
    q, a = await _turn(client, old, _KNEE_Q, _KNEE_A)
    await client.execute(
        "UPDATE messages SET created_at = now() - interval '30 days' WHERE id = $1", a)
    current = await _conversation(client, user)
    assert await index_new_turns(user) == 1
    words = "Which exercises did you recommend for my knee on the stairs?"
    args = {"query": "knee exercises recommended for climbing stairs"}
    assert (await memory_search.ainvoke(args, _config(user, current, words)))["found"] is True
    assert await memory_search.ainvoke(
        {**args, "since_days": 7}, _config(user, current, words)) == {"found": False}


@pytest.mark.asyncio
async def test_memory_search_unrelated_query_finds_nothing(pg):
    from langgraph_agents.db.turn_index import index_new_turns
    from langgraph_agents.tools.pgvector_tool import memory_search

    client, _ = pg
    user = await _user(pg)
    old = await _conversation(client, user)
    await _turn(client, old, _KNEE_Q, _KNEE_A)
    current = await _conversation(client, user)
    assert await index_new_turns(user) == 1
    off_topic = "what is a good recipe for chocolate cake with strawberries"
    result = await memory_search.ainvoke(
        {"query": off_topic}, _config(user, current, off_topic))
    assert result == {"found": False}
