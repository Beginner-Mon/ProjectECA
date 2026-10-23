"""Tier storage and reads against a real Postgres (memory-v2 tiers, step 7).

Runs only against the development database, on the same guard as
test_fact_extractor_integration.py: skipped unless ECA_TEST_PG_DSN is set AND
the application is pointed at that same database (VVA_PG_DSN equal to it).
Migration 016 must be applied there (K applies it once benchmark A is done).

Every test makes its own users with random UUIDs and deletes them at the end
(their conversations, messages and facts go with them by cascade).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

_VEC = [1.0] + [0.0] * 383


@pytest.fixture
async def pg():
    from langgraph_agents.shared.env import load_env

    load_env()
    test_dsn = os.getenv("ECA_TEST_PG_DSN", "").strip()
    if not test_dsn:
        pytest.skip("ECA_TEST_PG_DSN not set")
    if os.getenv("VVA_PG_DSN", "").strip() != test_dsn:
        pytest.skip("the application is not pointed at the test database")

    from langgraph_agents.db.postgres import bind_request_user
    from langgraph_agents.shared import get_pg_client

    client = get_pg_client()
    await client.connect()
    created: list[str] = []
    yield client, created
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


async def _conversation(pg, user_id: str) -> str:
    session_id = str(uuid.uuid4())
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2)",
        session_id, user_id,
    )
    return session_id


async def _tier_row(pg, user_id: str, fact: str, tier: int = 2,
                    kind: str = "fact", status: str = "active",
                    project=None, vector=None, last_seen=None,
                    source: str = "extracted", source_session=None):
    return await pg.fetchval(
        """INSERT INTO user_memory
           (user_id, fact_text, kind, tier, status, project, source,
            source_session_id, last_seen_session_id, embedding)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           RETURNING id""",
        user_id, fact, kind, tier, status, project, source,
        source_session, last_seen, vector,
    )


@pytest.mark.asyncio
async def test_tier_rows_are_invisible_to_another_user(pg):
    from langgraph_agents.db.postgres import bind_request_user
    from langgraph_agents.nodes.memory import (
        _load_tier2_facts, _load_user_instructions)

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    await _tier_row(client, user, "No squats", tier=1, kind="fact",
                    source_session=session)
    await _tier_row(client, user, "Runs 5km", tier=2, vector=_VEC)

    other = await _user(pg)  # binds other
    assert await _load_user_instructions(other) == []
    embed = MagicMock()
    embed.aembed_query = AsyncMock(return_value=list(_VEC))
    with patch("langgraph_agents.shared.get_embedding_service",
               return_value=embed):
        lines, _, _, _ = await _load_tier2_facts(other, session, "chay bo")
    assert lines == []

    bind_request_user(user)
    assert await _load_user_instructions(user) == ["No squats"]


@pytest.mark.asyncio
async def test_editing_text_or_project_clears_the_embedding(pg):
    client, _ = pg
    user = await _user(pg)
    row_id = await _tier_row(client, user, "Runs 5km", vector=list(_VEC))
    got = await client.fetchval(
        "SELECT embedding IS NOT NULL FROM user_memory WHERE id = $1", row_id)
    assert got is True

    await client.execute(
        "UPDATE user_memory SET fact_text = $2 WHERE id = $1",
        row_id, "Runs 10km")
    assert await client.fetchval(
        "SELECT embedding IS NULL FROM user_memory WHERE id = $1", row_id) is True

    await client.execute(
        "UPDATE user_memory SET embedding = $2 WHERE id = $1",
        row_id, _VEC)
    await client.execute(
        "UPDATE user_memory SET project = 'Run' WHERE id = $1", row_id)
    assert await client.fetchval(
        "SELECT embedding IS NULL FROM user_memory WHERE id = $1", row_id) is True

    # Touching anything else leaves the vector alone.
    await client.execute(
        "UPDATE user_memory SET embedding = $2 WHERE id = $1",
        row_id, _VEC)
    await client.execute(
        "UPDATE user_memory SET category = 'goal' WHERE id = $1", row_id)
    assert await client.fetchval(
        "SELECT embedding IS NOT NULL FROM user_memory WHERE id = $1", row_id) is True


@pytest.mark.asyncio
async def test_stm_compare_and_set_leaves_one_winner(pg):
    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    first = await client.execute(
        "UPDATE conversations SET stm_summary = $2, stm_up_to_seq = $3 "
        "WHERE session_id = $1 AND stm_up_to_seq = $4",
        session, "one", 10, 0)
    assert first == "UPDATE 1"
    again = await client.execute(
        "UPDATE conversations SET stm_summary = $2, stm_up_to_seq = $3 "
        "WHERE session_id = $1 AND stm_up_to_seq = $4",
        session, "two", 20, 0)
    assert again == "UPDATE 0"
    assert await client.fetchval(
        "SELECT stm_summary FROM conversations WHERE session_id = $1",
        session) == "one"


@pytest.mark.asyncio
async def test_extractor_writes_the_decided_tier(pg):
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    await client.execute(
        "INSERT INTO messages (session_id, role, content) VALUES ($1, 'user', $2)",
        session, "Dung goi y bai cho lung cua toi.")

    class _Script:
        async def ainvoke(self, messages):
            return {"raw": None,
                    "parsed": fe.Operations(operations=[
                         {"op": "ADD", "type": "instruction",
                          "topic": "exercise", "fact": "No exercise for the back",
                          "safety": True}]),
                    "parsing_error": None}

    with patch.object(fe, "extraction_llm", return_value=_Script()):
        summary = await fe.extract_user_facts(user, session)
    assert (summary["added"], summary["failed"]) == (1, 0)
    row = await client.fetchrow(
        "SELECT kind, tier, status FROM user_memory WHERE user_id = $1", user)
    assert (row["kind"], row["tier"], row["status"]) == ("instruction", 1, "active")


@pytest.mark.asyncio
async def test_tier2_skips_the_current_conversation_live(pg):
    from langgraph_agents.nodes.memory import _load_tier2_facts

    client, _ = pg
    user = await _user(pg)
    current = await _conversation(client, user)
    other = await _conversation(client, user)
    await _tier_row(client, user, "Runs 5km", vector=list(_VEC),
                    last_seen=current)
    await _tier_row(client, user, "Swims daily", vector=list(_VEC),
                    last_seen=other)

    embed = MagicMock()
    embed.aembed_query = AsyncMock(return_value=list(_VEC))
    with patch("langgraph_agents.shared.get_embedding_service",
               return_value=embed):
        lines, _, _, _ = await _load_tier2_facts(user, current, "the duc")
    assert [line for line in lines if "Runs 5km" in line] == []
    assert any("Swims daily" in line for line in lines)


@pytest.mark.asyncio
async def test_unlabelled_hit_pulls_no_project_along_live(pg):
    from langgraph_agents.nodes.memory import _load_tier2_facts

    client, _ = pg
    user = await _user(pg)
    other = await _conversation(client, user)
    await _tier_row(client, user, "Likes pho", vector=list(_VEC),
                    last_seen=other)
    await _tier_row(client, user, "Likes hiking", vector=list(_VEC),
                    last_seen=other)

    embed = MagicMock()
    embed.aembed_query = AsyncMock(return_value=list(_VEC))
    with patch("langgraph_agents.shared.get_embedding_service",
               return_value=embed):
        lines, _, _, _ = await _load_tier2_facts(
            user, str(uuid.uuid4()), "an uong")
    assert len(lines) == 2
    assert all(line.startswith("- [") and "(" not in line for line in lines)


@pytest.mark.asyncio
async def test_project_label_longer_than_60_is_rejected(pg):
    import asyncpg

    client, _ = pg
    user = await _user(pg)
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await _tier_row(client, user, "Too long a label", project="P" * 61)


@pytest.mark.asyncio
async def test_purge_deletes_old_rows_beyond_the_newest_shown(pg):
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    user = await _user(pg)
    old = datetime.now(timezone.utc) - timedelta(days=61)
    # More old candidates than the model is ever shown at once.
    await client.executemany(
        """INSERT INTO user_memory
           (user_id, fact_text, category, tier, status, source, said_at)
           VALUES ($1, $2, 'profile', 2, 'candidate', 'extracted', $3)""",
        [(user, f"old candidate {n}", old) for n in range(61)])
    hidden_id = await client.fetchval(
        """INSERT INTO user_memory
           (user_id, fact_text, category, source, valid, updated_at)
           VALUES ($1, 'hidden fact', 'profile', 'extracted', false,
                   now() - make_interval(days => 31))
           RETURNING id""", user)
    fresh = await _tier_row(client, user, "Fresh fact")

    policy = {"candidate_ttl_days": 60, "forget_purge_days": 30}
    purged = await fe._purge_user(client, user, policy)
    assert purged == 62
    assert await client.fetchval(
        "SELECT COUNT(*) FROM user_memory WHERE user_id = $1", user) == 1
    assert await client.fetchval(
        "SELECT id FROM user_memory WHERE user_id = $1", user) == fresh
    assert hidden_id is not None


@pytest.mark.asyncio
async def test_deleting_the_source_session_removes_its_facts(pg):
    client, _ = pg
    user = await _user(pg)
    source = await _conversation(client, user)
    later = await _conversation(client, user)
    row_id = await _tier_row(client, user, "Repeated elsewhere",
                             source_session=source, last_seen=later)

    await client.execute(
        "DELETE FROM conversations WHERE session_id = $1", source)
    assert await client.fetchval(
        "SELECT COUNT(*) FROM user_memory WHERE id = $1", row_id) == 0


@pytest.mark.asyncio
async def test_deleting_only_the_last_seen_session_keeps_the_fact(pg):
    client, _ = pg
    user = await _user(pg)
    source = await _conversation(client, user)
    later = await _conversation(client, user)
    row_id = await _tier_row(client, user, "Seen again later",
                             source_session=source, last_seen=later)

    await client.execute(
        "DELETE FROM conversations WHERE session_id = $1", later)
    row = await client.fetchrow(
        "SELECT valid, last_seen_session_id FROM user_memory WHERE id = $1",
        row_id)
    assert row["valid"] is True
    assert row["last_seen_session_id"] is None
