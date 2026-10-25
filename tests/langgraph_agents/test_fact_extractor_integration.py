"""Fact extraction against a real Postgres (memory v2, C1b).

Runs only against the development database, on the same guard as
test_turn_index_integration.py: skipped unless ECA_TEST_PG_DSN is set AND the
application is pointed at that same database (VVA_PG_DSN equal to it). The
project's own `.env` points VVA_PG_DSN at production, and these tests write rows.

    python <scratchpad>/pytest_on_branch.py <repo> \\
        tests/langgraph_agents/test_fact_extractor_integration.py -m integration

The model is a script: each call is answered with the next scripted list of
operations. Everything else is real — the statements, the transaction, the
row-level security. Every test makes its own users with random UUIDs through
the application's own client and deletes them at the end (their conversations,
messages and facts go with them by cascade).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


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


async def _conversation(pg, user_id: str) -> str:
    session_id = str(uuid.uuid4())
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2)",
        session_id, user_id,
    )
    return session_id


async def _message(pg, session_id: str, role: str, content: str) -> int:
    return await pg.fetchval(
        "INSERT INTO messages (session_id, role, content) VALUES ($1, $2, $3) "
        "RETURNING seq_id",
        session_id, role, content,
    )


async def _typed_fact(pg, user_id: str, text: str, memory_type: str = "profile"):
    """A fact as POST /me/memory writes it: `source` left to its default."""
    return await pg.fetchval(
        "INSERT INTO user_memory (user_id, fact_text, memory_type) VALUES ($1, $2, $3) "
        "RETURNING id",
        user_id, text, memory_type,
    )


async def _facts(pg) -> dict[str, dict]:
    """Every fact row the bound user can see, by its text."""
    rows = await pg.fetch(
        "SELECT id, fact_text, memory_type, project, source, valid, source_session_id, "
        "       last_seen_session_id, updated_at > created_at AS refreshed "
        "FROM user_memory")
    return {r["fact_text"]: dict(r) for r in rows}


async def _marker(pg, session_id: str) -> int:
    return await pg.fetchval(
        "SELECT ltm_up_to_seq FROM conversations WHERE session_id = $1", session_id)


class _Script:
    """Stands in for the model. A reply is a list of operations, a callable
    that builds one from {fact text: id shown to the model}, or an exception."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.seen: list[str] = []

    async def ainvoke(self, messages):
        from langgraph_agents.nodes.fact_extractor import Operations

        user_message = messages[1][1]
        self.seen.append(user_message)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            reply = reply({f["fact"]: f["id"] for f in _stored_in(user_message)})
        return {"raw": None, "parsed": Operations(operations=reply), "parsing_error": None}


def _stored_in(user_message: str) -> list[dict]:
    """The stored rows the model was shown, both tiers, in prompt order."""
    rows = []
    for line in user_message.splitlines():
        line = line.strip()
        if line.startswith('{"id": "f'):
            shown = json.loads(line)
            rows.append({"id": shown["id"], "fact": shown["fact"],
                         "type": shown.get("type"), "topic": shown.get("topic"),
                         "locked": shown.get("locked", False)})
    return rows


async def _extract(user_id: str, current: str, script: _Script):
    from langgraph_agents.nodes import fact_extractor as fe

    with patch.object(fe, "extraction_llm", return_value=script):
        return await fe.extract_user_facts(user_id, current)


_ACL = "Had ACL reconstruction on the left knee in March 2025"
_BACK = "Has lower back pain when sitting for long"
_KNEE_NOW = "Left knee is pain-free when walking (ACL reconstruction in March 2025)"


@pytest.mark.asyncio
async def test_add_then_a_later_update_and_delete_and_the_marker_follows(pg):
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    user = await _user(pg)
    first = await _conversation(client, user)
    await _message(client, first, "user", "I had my left ACL rebuilt in March 2025 and my "
                                          "lower back hurts when I sit for long.")
    answered = await _message(client, first, "assistant", "Thanks, I will keep that in mind.")

    script = _Script([{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": _ACL},
                      {"op": "ADD", "type": "profile",
                          "topic": "health", "fact": _BACK}])
    summary = await _extract(user, first, script)

    assert (summary["llm_calls"], summary["added"]) == (1, 2)
    assert re.match(r"Conversation date: \d{4}-\d{2}-\d{2}\n", script.seen[0])
    assert "[user] I had my left ACL rebuilt" in script.seen[0]
    facts = await _facts(client)
    assert set(facts) == {_ACL, _BACK}
    for fact in facts.values():
        assert fact["source"] == "extracted" and fact["valid"] is True
        assert str(fact["source_session_id"]) == first and fact["memory_type"] == "profile"
        assert fact["refreshed"] is False
    assert await _marker(client, first) == answered

    # The same text again is not a second row, whatever the caller checked.
    assert await client.execute(
        fe._ADD_SQL, user, _ACL, "profile", "fact", 2, "active", "health", 1,
        [""], first) == "INSERT 0 0"

    # Days later, in another conversation, the user corrects both.
    second = await _conversation(client, user)
    said = await _message(client, second, "user", "My back is fine now, and the knee no "
                                                  "longer hurts when I walk.")
    script = _Script(lambda ids: [
        {"op": "UPDATE", "id": ids[_ACL], "type": "profile", "topic": "health", "fact": _KNEE_NOW},
        {"op": "DELETE", "id": ids[_BACK]},
    ])
    summary = await _extract(user, second, script)

    assert (summary["updated"], summary["deleted"], summary["added"]) == (1, 1, 0)
    assert [(f["fact"], f["locked"]) for f in _stored_in(script.seen[0])] in (
        [(_ACL, False), (_BACK, False)], [(_BACK, False), (_ACL, False)])
    facts = await _facts(client)
    assert set(facts) == {_KNEE_NOW, _BACK}
    assert facts[_KNEE_NOW]["valid"] is True and facts[_KNEE_NOW]["refreshed"] is True
    assert str(facts[_KNEE_NOW]["last_seen_session_id"]) == second
    # The origin conversation still owns the row; the update only restamped it.
    assert str(facts[_KNEE_NOW]["source_session_id"]) == first
    # Retired, not removed, and still tied to the conversation it came from.
    assert facts[_BACK]["valid"] is False and facts[_BACK]["refreshed"] is True
    assert str(facts[_BACK]["source_session_id"]) == first
    assert await _marker(client, second) == said
    assert await _marker(client, first) == answered


@pytest.mark.asyncio
async def test_a_second_run_with_nothing_new_reads_nothing_and_calls_no_model(pg):
    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    last = await _message(client, session, "user", "I work night shifts as a nurse.")
    await _extract(user, session, _Script(
        [{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": "Works night shifts as a nurse"}]))

    idle = _Script()
    summary = await _extract(user, session, idle)

    assert summary == {"conversations": 0, "llm_calls": 0, "failed": 0,
                       "added": 0, "updated": 0, "deleted": 0, "repeated": 0,
                       "purged": 0}
    assert idle.seen == []
    assert await _marker(client, session) == last
    assert len(await _facts(client)) == 1


@pytest.mark.asyncio
async def test_long_backlog_is_read_over_several_runs_and_an_answer_alone_needs_no_model(pg):
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    first = await _message(client, session, "assistant", "a" * 5000)
    await _message(client, session, "user", "I avoid running. " + "u" * 5000)
    last = await _message(client, session, "assistant", "Understood.")

    # Run 1: the second message would pass 8,000 characters, so the slice is the
    # assistant message alone, and that needs no model.
    script = _Script([{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": "Avoids running"}])
    summary = await _extract(user, session, script)
    assert (summary["conversations"], summary["llm_calls"]) == (1, 0)
    assert script.seen == [] and await _marker(client, session) == first

    # A run that started reading before that one finished finds its place taken.
    assert await client.execute(fe._MARK_SQL, session, user, 0, last) == "UPDATE 0"
    assert await _marker(client, session) == first

    # Run 2 picks up after the marker and reads to the end.
    summary = await _extract(user, session, script)
    assert (summary["llm_calls"], summary["added"]) == (1, 1)
    assert "a" * 5000 not in script.seen[0]
    assert "[user] I avoid running. " in script.seen[0]
    assert script.seen[0].rstrip().endswith("Reply with the JSON object only.")
    assert "[assistant] Understood." in script.seen[0]
    assert await _marker(client, session) == last


@pytest.mark.asyncio
async def test_conversations_are_read_most_recently_updated_first(pg):
    client, _ = pg
    user = await _user(pg)
    oldest = await _conversation(client, user)
    await _message(client, oldest, "user", "said in the oldest")
    middle = await _conversation(client, user)
    await _message(client, middle, "user", "said in the middle one")
    newest = await _conversation(client, user)
    await _message(client, newest, "user", "said in the newest")

    # The current conversation reads like any other: most recently updated
    # first, at most two per run.
    script = _Script([], [])
    summary = await _extract(user, oldest, script)
    assert summary["conversations"] == 2
    assert "said in the newest" in script.seen[0]
    assert "said in the middle one" in script.seen[1]
    assert await _marker(client, oldest) == 0

    # The user says more in the newest one; it and the oldest are read next.
    said_later = await _message(client, newest, "user", "said later in the newest")
    script = _Script([], [])
    summary = await _extract(user, newest, script)
    assert summary["conversations"] == 2
    assert "said later in the newest" in script.seen[0]
    assert "said in the oldest" in script.seen[1]
    assert await _marker(client, newest) == said_later

    assert (await _extract(user, newest, _Script()))["conversations"] == 0


@pytest.mark.asyncio
async def test_a_fact_the_user_typed_ignores_an_update_and_a_delete_hides_it(pg):
    """D8: an UPDATE aimed at a typed item is dropped; a DELETE is kept."""
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    user = await _user(pg)
    typed_text = "Prefers low-impact exercise only"
    typed_id = await _typed_fact(client, user, typed_text, "preference")
    session = await _conversation(client, user)
    await _message(client, session, "user", "I do HIIT three times a week now.")

    script = _Script(lambda ids: [
        {"op": "UPDATE", "id": ids[typed_text], "type": "profile", "topic": "health",
         "fact": "Does HIIT"},
        {"op": "DELETE", "id": ids[typed_text]},
    ])
    summary = await _extract(user, session, script)

    assert _stored_in(script.seen[0]) == [
        {"id": "f1", "fact": typed_text, "type": "preference", "topic": None,
         "locked": True}]
    assert (summary["updated"], summary["deleted"]) == (0, 1)

    facts = await _facts(client)
    assert set(facts) == {typed_text}          # the text was never rewritten
    kept = facts[typed_text]
    assert (kept["source"], kept["memory_type"]) == ("user", "preference")
    assert kept["valid"] is False and kept["source_session_id"] is None

    # The statement itself refuses an update of a typed row, without the check
    # in front of it.
    assert await client.execute(
        fe._UPDATE_SQL, typed_id, user, "Does HIIT", "profile", "health",
        "fact", 2, "active", session) == "UPDATE 0"
    assert set(await _facts(client)) == {typed_text}


@pytest.mark.asyncio
async def test_user_b_is_neither_read_nor_changed_when_running_as_user_a(pg):
    from langgraph_agents.db.postgres import bind_request_user
    from langgraph_agents.nodes import fact_extractor as fe

    client, _ = pg
    b = await _user(pg)
    b_session = await _conversation(client, b)
    await _message(client, b_session, "user", "B said: I have a slipped disc.")
    b_typed = "B typed: allergic to latex"
    await _typed_fact(client, b, b_typed, "profile")
    b_extracted = "B extracted: has a slipped disc"
    b_extracted_id = await client.fetchval(
        "INSERT INTO user_memory (user_id, fact_text, memory_type, source, source_session_id) "
        "VALUES ($1, $2, 'profile', 'extracted', $3) RETURNING id",
        b, b_extracted, b_session)

    a = await _user(pg)                       # binds A
    a_session = await _conversation(client, a)
    await _message(client, a_session, "user", "A said: I sprained my ankle last week.")

    script = _Script([{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": "A: sprained ankle"}])
    summary = await _extract(a, a_session, script)
    assert (summary["conversations"], summary["added"]) == (1, 1)
    assert _stored_in(script.seen[0]) == []
    assert "B said" not in script.seen[0] and "B typed" not in script.seen[0]
    assert set(await _facts(client)) == {"A: sprained ankle"}

    # Asked for B's work while signed in as A: nothing to read, nothing to write.
    idle = _Script()
    assert (await _extract(b, b_session, idle))["conversations"] == 0
    assert idle.seen == []
    assert await client.execute(
        fe._UPDATE_SQL, b_extracted_id, a, "changed by A", "profile", "health",
        "fact", 2, "active", a_session) == "UPDATE 0"
    assert await client.execute(
        fe._UPDATE_SQL, b_extracted_id, b, "changed by A", "profile", "health",
        "fact", 2, "active", a_session) == "UPDATE 0"
    assert await client.execute(fe._HIDE_SQL, b_extracted_id, b) == "UPDATE 0"
    assert await client.execute(fe._MARK_SQL, b_session, b, 0, 999) == "UPDATE 0"

    bind_request_user(b)
    facts = await _facts(client)
    assert set(facts) == {b_typed, b_extracted}
    assert all(f["valid"] is True and f["refreshed"] is False for f in facts.values())
    assert await _marker(client, b_session) == 0


@pytest.mark.asyncio
async def test_deleting_a_conversation_removes_its_facts_and_keeps_the_typed_ones(pg):
    client, _ = pg
    user = await _user(pg)
    typed_text = "Has a standing desk at home"
    await _typed_fact(client, user, typed_text, "profile")
    kept_session = await _conversation(client, user)
    await _message(client, kept_session, "user", "I want to run 5 km by spring.")
    doomed = await _conversation(client, user)
    await _message(client, doomed, "user", "My right shoulder clicks when I lift.")

    script = _Script([{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": "Right shoulder clicks when lifting"}],
                     [{"op": "ADD", "type": "area",
                          "topic": "health", "fact": "Wants to run 5 km by spring"}])
    summary = await _extract(user, doomed, script)
    assert summary["added"] == 2
    assert len(await _facts(client)) == 3

    await client.execute("DELETE FROM conversations WHERE session_id = $1", doomed)

    assert set(await _facts(client)) == {typed_text, "Wants to run 5 km by spring"}


@pytest.mark.asyncio
async def test_after_a_failed_call_the_marker_has_not_moved_and_the_next_run_retries(pg):
    client, _ = pg
    user = await _user(pg)
    session = await _conversation(client, user)
    last = await _message(client, session, "user", "I had my left hip replaced in 2024.")

    summary = await _extract(user, session, _Script(RuntimeError("provider is down")))
    assert (summary["llm_calls"], summary["failed"], summary["added"]) == (1, 1, 0)
    assert await _marker(client, session) == 0
    assert await _facts(client) == {}

    script = _Script([{"op": "ADD", "type": "profile",
                          "topic": "health", "fact": "Had a left hip replacement in 2024"}])
    summary = await _extract(user, session, script)
    assert (summary["failed"], summary["added"]) == (0, 1)
    assert "[user] I had my left hip replaced in 2024." in script.seen[0]
    assert await _marker(client, session) == last


@pytest.mark.asyncio
async def test_the_memory_node_loads_tier1_instructions_only_newest_first(pg):
    from langgraph_agents.nodes.memory import _load_user_instructions

    client, _ = pg
    user = await _user(pg)
    typed = "Must not lift more than 5 kg (doctor's advice)"
    await client.execute(
        "INSERT INTO user_memory (user_id, fact_text, memory_type, kind, project, "
        "                         tier, status) "
        "VALUES ($1, $2, 'instruction', 'instruction', 'lifting', 1, 'active')",
        user, typed)
    session = await _conversation(client, user)
    await _message(client, session, "user", "A few things about me.")

    await _extract(user, session, _Script([
        {"op": "ADD", "type": "instruction", "topic": "answer style",
         "fact": "Prefers short answers"},
        {"op": "ADD", "type": "profile", "topic": "health",
         "fact": "Has knee osteoarthritis"},
        {"op": "ADD", "type": "instruction", "topic": "movement",
         "fact": "Avoids deep squats"},
        {"op": "ADD", "type": "area", "topic": "hiking",
         "fact": "Wants to hike again"},
    ]))
    # One of them is hidden by a later conversation and must stop being loaded.
    later = await _conversation(client, user)
    await _message(client, later, "user", "Long answers are fine now.")
    await _extract(user, later, _Script(
        lambda ids: [{"op": "DELETE", "id": ids["Prefers short answers"]}]))

    loaded = await _load_user_instructions(user)

    # Every line is "[YYYY-MM-DD] (topic) fact".
    assert all(re.match(r"\[\d{4}-\d{2}-\d{2}\] \(", line) for line in loaded)
    undated = [re.sub(r"^\[[\d-]+\] ", "", line) for line in loaded]
    # Tier 1 holds instructions only, newest first. The tier-2 rows (the health
    # fact, the area) and the hidden row are not loaded.
    assert undated == ["(movement) Avoids deep squats",
                       f"(lifting) {typed}"]
