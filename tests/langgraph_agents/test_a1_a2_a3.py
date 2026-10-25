"""Acceptance tests — cụm A re-work (A1 clarify, A2 GDPR, A3 user_memory).

R2 required tests from FIX-R1-GDPR-RESUMMARIZE.md.
Conventions:
  - HAS_PG guard + _make_config pattern from test_pr1_memory_fix.py
  - AsyncMock patterns from test_pr2_summarizer.py
  - Markers: unit / integration
"""

from __future__ import annotations

import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.runnables import RunnableConfig

# ── PG availability guard (same pattern as test_pr1_memory_fix.py) ─────────

HAS_PG = False
try:
    import asyncpg
    async def _ping():
        conn = await asyncpg.connect("postgresql://eca:eca_dev@localhost:5433/eca")
        await conn.close()
        return True
    import asyncio
    HAS_PG = asyncio.run(_ping())
except Exception:
    pass


def _make_config(user_id="test-user-a", session_id=None):
    return RunnableConfig(configurable={
        "user_id": user_id,
        "session_id": session_id or str(uuid.uuid4()),
        "query": "test",
        "persona_id": "anne",
        "request_id": "req-test",
    })


# ═══════════════════════════════════════════════════════════════════════════
# A2 — GDPR (integration, real PG)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a2_dirty_window_excludes_from_load_and_search():
    """Dirty chunk excluded from _load_summary_chunks and memory_search (M.8 #4).

    While dirty it is not coverage either: its messages come back as raw history.
    """
    from langgraph_agents.shared import get_pg_client, get_embedding_service
    from langgraph_agents.nodes.memory import _load_raw_history, _load_summary_chunks
    from langgraph_agents.tools.pgvector_tool import memory_search

    pg = get_pg_client()
    await pg.connect()
    embed = get_embedding_service()

    user_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        session_id, user_id,
    )

    try:
        # Insert messages seq 1-10
        for seq in range(1, 11):
            await pg.execute(
                """INSERT INTO messages (session_id, seq_id, role, content, token_count)
                   VALUES ($1, $2, 'user', $3, 10)""",
                session_id, seq, f"message {seq}",
            )

        # Insert active summary chunk covering seq 1-10
        vec = await embed.aembed_passage("Người dùng hỏi về bài tập vai và được khuyên bài tập xoay vai.")
        await pg.execute(
            """INSERT INTO summaries
               (session_id, summary_text, covers_from_seq, covers_up_to_seq, embedding, status)
               VALUES ($1, $2, 1, 10, $3, 'active')
               ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING""",
            session_id,
            "Người dùng hỏi về bài tập vai và được khuyên bài tập xoay vai.",
            vec,
        )

        # Verify chunk is visible before mark-dirty
        chunks_before = await _load_summary_chunks(session_id)
        assert len(chunks_before) == 1, "Chunk should be active before deletion"
        # ... and that its ten messages are covered, so none is loaded raw
        assert await _load_raw_history(session_id) == []

        # Mark chunk dirty (simulate delete_message side-effect)
        await pg.execute(
            "UPDATE summaries SET status = 'dirty' WHERE session_id = $1 AND status = 'active'",
            session_id,
        )

        # Dirty chunk must NOT appear in _load_summary_chunks
        chunks_after = await _load_summary_chunks(session_id)
        assert chunks_after == [], "_load_summary_chunks must exclude dirty chunks"
        # ... and must not hide its messages: they are raw history again
        raw_after = await _load_raw_history(session_id)
        assert [m["content"] for m in raw_after] == [f"message {seq}" for seq in range(1, 11)]

        # Dirty chunk must NOT appear in memory_search results
        search_session = str(uuid.uuid4())
        config = _make_config(user_id=user_id, session_id=search_session)
        result = await memory_search.ainvoke({"query": "bài tập vai"}, config)
        # Either not found or found results must all be active
        if result.get("found"):
            for r in result["results"]:
                assert r["session_id"] != session_id, (
                    "memory_search must not return results from dirty-chunk session"
                )

    finally:
        await pg.execute("DELETE FROM summaries WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM messages WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM conversations WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a2_rebuild_dirty_chunk_round_trip():
    """rebuild_dirty_chunk → chunk active, summary_text updated, embedding changed (M.8 #5,#6)."""
    from langgraph_agents.shared import get_pg_client, get_embedding_service
    from langgraph_agents.nodes.summarizer import rebuild_dirty_chunk

    pg = get_pg_client()
    await pg.connect()
    embed = get_embedding_service()

    user_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        session_id, user_id,
    )

    try:
        # Insert messages seq 1-5 (seq 3 will be "deleted")
        contents = {
            1: "Tôi bị đau lưng",
            2: "Bạn nên tập cầu mông nhẹ",
            # 3 is the deleted message (we simply don't insert it)
            4: "Cảm ơn bác sĩ",
            5: "Chào mừng bạn, chúc tập luyện tốt",
        }
        for seq, content in contents.items():
            await pg.execute(
                """INSERT INTO messages (session_id, seq_id, role, content, token_count)
                   VALUES ($1, $2, 'user', $3, 10)""",
                session_id, seq, content,
            )

        # Insert a dirty summary chunk covering seq 1-5
        old_summary = "Người dùng bị đau lưng và hỏi về bài tập vai (TIN CŨ CẦN XÓA)."
        old_vec = await embed.aembed_passage(old_summary)
        chunk_id = await pg.fetchval(
            """INSERT INTO summaries
               (session_id, summary_text, covers_from_seq, covers_up_to_seq, embedding, status)
               VALUES ($1, $2, 1, 5, $3, 'dirty')
               RETURNING id""",
            session_id, old_summary, old_vec,
        )
        chunk_id = str(chunk_id)

        old_vec_list = old_vec if isinstance(old_vec, list) else list(old_vec)

        # Mock LLM to return a predictable new summary (avoids live LLM call)
        new_summary_text = "Người dùng bị đau lưng, được khuyên tập cầu mông nhẹ."
        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = new_summary_text
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)

        with patch("langgraph_agents.nodes.summarizer.get_chat_model", return_value=mock_llm):
            result = await rebuild_dirty_chunk(session_id, chunk_id)

        assert result is True, "rebuild_dirty_chunk must return True on success"

        # Verify chunk is now active
        row = await pg.fetchrow(
            "SELECT status, summary_text, embedding FROM summaries WHERE id = $1::uuid",
            chunk_id,
        )
        assert row is not None
        assert row["status"] == "active", f"Chunk status must be 'active', got {row['status']}"
        assert row["summary_text"] == new_summary_text, (
            f"summary_text must be updated, got: {row['summary_text']}"
        )
        # Embedding must differ from old (new text → new vector)
        new_vec = list(row["embedding"])
        assert new_vec != old_vec_list, "embedding must change after re-summarize"

    finally:
        await pg.execute("DELETE FROM summaries WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM messages WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM conversations WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a2_empty_chunk_deleted_after_all_messages_removed():
    """Deleting all messages in chunk range → chunk row removed (M.8 #3)."""
    from langgraph_agents.shared import get_pg_client, get_embedding_service
    from langgraph_agents.db.gdpr import delete_message as gdpr_delete_message

    pg = get_pg_client()
    await pg.connect()
    embed = get_embedding_service()

    user_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        session_id, user_id,
    )

    try:
        # Insert exactly 1 message
        msg_id = await pg.fetchval(
            """INSERT INTO messages (session_id, seq_id, role, content, token_count)
               VALUES ($1, 1, 'user', 'single message', 5) RETURNING id""",
            session_id,
        )
        msg_id = str(msg_id)

        # Insert active summary covering only that message
        vec = await embed.aembed_passage("Người dùng gửi 1 tin nhắn duy nhất.")
        await pg.execute(
            """INSERT INTO summaries
               (session_id, summary_text, covers_from_seq, covers_up_to_seq, embedding, status)
               VALUES ($1, $2, 1, 1, $3, 'active')
               ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING""",
            session_id, "Người dùng gửi 1 tin nhắn duy nhất.", vec,
        )

        # Delete the only message → chunk should be removed (empty chunk)
        result = await gdpr_delete_message(msg_id, session_id)
        assert result["deleted"] is True

        # Chunk must no longer exist
        remaining = await pg.fetchval(
            "SELECT COUNT(*) FROM summaries WHERE session_id = $1",
            session_id,
        )
        assert remaining == 0, (
            f"Empty chunk must be deleted by gdpr_delete_message, found {remaining} rows"
        )

    finally:
        await pg.execute("DELETE FROM summaries WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM messages WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM conversations WHERE session_id = $1", session_id)
        await pg.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a2_ownership_other_user_cannot_delete_message():
    """User B cannot delete User A's message — returns not_found (M.8 ownership)."""
    from langgraph_agents.shared import get_pg_client
    from langgraph_agents.db.gdpr import delete_message as gdpr_delete_message

    pg = get_pg_client()
    await pg.connect()

    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())
    session_a = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_a)
    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_b)
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        session_a, user_a,
    )

    try:
        msg_id = await pg.fetchval(
            """INSERT INTO messages (session_id, seq_id, role, content, token_count)
               VALUES ($1, 1, 'user', 'secret message', 5) RETURNING id""",
            session_a,
        )
        msg_id = str(msg_id)

        # User B tries to delete via a different session_id they don't own
        session_b_fake = str(uuid.uuid4())
        result = await gdpr_delete_message(msg_id, session_b_fake)

        # Must return not_found (wrong session_id → no row match)
        assert result["deleted"] is False, (
            "Deleting another user's message via wrong session must return deleted=False"
        )

        # Original message must still exist
        still_there = await pg.fetchval(
            "SELECT COUNT(*) FROM messages WHERE id = $1::uuid",
            msg_id,
        )
        assert still_there == 1, "Message must still exist after failed cross-user delete"

    finally:
        await pg.execute("DELETE FROM messages WHERE session_id = $1", session_a)
        await pg.execute("DELETE FROM conversations WHERE session_id = $1", session_a)
        await pg.execute("DELETE FROM users WHERE id = ANY($1)", [user_a, user_b])


@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a2_delete_user_cascades_all_data():
    """DELETE /users/{id} → 0 rows in conversations/messages/summaries/user_memory (M.8)."""
    from langgraph_agents.shared import get_pg_client, get_embedding_service
    from langgraph_agents.db.gdpr import delete_user

    pg = get_pg_client()
    await pg.connect()
    embed = get_embedding_service()

    user_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
    await pg.execute(
        "INSERT INTO conversations (session_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        session_id, user_id,
    )
    await pg.execute(
        """INSERT INTO messages (session_id, seq_id, role, content, token_count)
           VALUES ($1, 1, 'user', 'hello', 5)""",
        session_id,
    )
    vec = await embed.aembed_passage("Hello summary.")
    await pg.execute(
        """INSERT INTO summaries
           (session_id, summary_text, covers_from_seq, covers_up_to_seq, embedding, status)
           VALUES ($1, $2, 1, 1, $3, 'active')
           ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING""",
        session_id, "Hello summary.", vec,
    )
    await pg.execute(
        "INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id
    )
    await pg.execute(
        """INSERT INTO user_memory (user_id, fact_text)
           VALUES ($1::uuid, 'bị đau lưng')""",
        user_id,
    )

    result = await delete_user(user_id)
    assert result["deleted"] is True

    # All rows must be gone (cascade)
    conv_count = await pg.fetchval(
        "SELECT COUNT(*) FROM conversations WHERE user_id = $1::uuid", user_id,
    )
    msg_count = await pg.fetchval(
        "SELECT COUNT(*) FROM messages WHERE session_id = $1", session_id,
    )
    sum_count = await pg.fetchval(
        "SELECT COUNT(*) FROM summaries WHERE session_id = $1", session_id,
    )
    mem_count = await pg.fetchval(
        "SELECT COUNT(*) FROM user_memory WHERE user_id = $1::uuid", user_id,
    )

    assert conv_count == 0, f"conversations must be 0 after delete_user, got {conv_count}"
    assert msg_count == 0, f"messages must be 0 after delete_user, got {msg_count}"
    assert sum_count == 0, f"summaries must be 0 after delete_user, got {sum_count}"
    assert mem_count == 0, f"user_memory must be 0 after delete_user, got {mem_count}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a2_delete_covered_message_resets_the_rolling_summary():
    """Deleting a message at or below stm_up_to_seq clears the summary, which
    described a message that is gone. An uncovered delete leaves it alone."""
    from contextlib import asynccontextmanager
    from langgraph_agents.db import gdpr as gdpr_mod

    class _Conn:
        def __init__(self, seq, up_to):
            self._seq, self._up_to = seq, up_to
            self.writes = []

        async def fetchrow(self, sql, *args):
            if sql.strip().startswith("SELECT seq_id"):
                return {"seq_id": self._seq} if self._seq else None
            return {"stm_up_to_seq": self._up_to}

        async def execute(self, sql, *args):
            self.writes.append(" ".join(sql.split()))
            return "UPDATE 1"

        @asynccontextmanager
        async def transaction(self):
            yield self

    class _Pool:
        def __init__(self, conn):
            self._conn = conn

        @asynccontextmanager
        async def acquire(self):
            yield self._conn

    async def _delete(seq, up_to):
        conn = _Conn(seq, up_to)
        pg = MagicMock()
        pg.connect = AsyncMock()
        pg._pool = _Pool(conn)
        with patch.object(gdpr_mod, "get_pg_client", return_value=pg):
            result = await gdpr_mod.delete_message("m-1", "s-1")
        return result, conn

    result, conn = await _delete(seq=5, up_to=10)
    assert result == {"deleted": True, "stm_reset": True}
    assert any("stm_summary = NULL, stm_up_to_seq = 0" in w for w in conn.writes)
    assert any(w.startswith("DELETE FROM messages") for w in conn.writes)

    result, conn = await _delete(seq=11, up_to=10)
    assert result == {"deleted": True, "stm_reset": False}
    assert all("stm_summary" not in w for w in conn.writes)

    result, _ = await _delete(seq=None, up_to=10)
    assert result == {"deleted": False, "reason": "not_found"}


# ═══════════════════════════════════════════════════════════════════════════
# A1 — clarify động (unit)
# ═══════════════════════════════════════════════════════════════════════════
# NOTE (16/10/2026): resume_last_session is gone — the two tests pinning its
# 24h ambiguity window were deleted with it. What remains is the synthesizer
# invariant: no tool flag ever forces the whole turn into clarify.

@pytest.mark.unit
def test_a1_tool_ambiguous_never_forces_clarify():
    """Tool ambiguous mà planner không đòi hỏi lại → mode khác 'clarify'.

    Một tool kéo cả lượt sang chế độ hỏi lại đã làm mất phần bài tập của câu gộp
    (16/10/2026). Mode clarify chỉ khi planner trả needs_clarification.
    """
    from langgraph_agents.nodes.synthesizer import _derive_mode
    from langchain_core.messages import ToolMessage

    ambiguous_result = json.dumps({
        "found": True,
        "ambiguous": True,
        "summary_chunks": ["talked about squats"],
        "recent_messages": [{"role": "user", "content": "hello again"}],
        "candidates": [{"preview": "...", "session_id": "s1"}],
    })

    state = {
        "needs_clarification": False,
        "required_outputs": [],
        "messages": [
            ToolMessage(content=ambiguous_result, tool_call_id="call-1",
                        name="kb_search"),
        ],
    }

    mode = _derive_mode(state)
    assert mode != "clarify", (
        f"Tool ambiguous must not force clarify, got '{mode}'"
    )
    assert mode == "synthesize"


@pytest.mark.unit
def test_a1_derive_mode_not_clarify_when_no_ambiguous():
    """synthesizer._derive_mode → not 'clarify' when tool result has no ambiguous flag."""
    from langgraph_agents.nodes.synthesizer import _derive_mode
    from langchain_core.messages import ToolMessage

    normal_result = json.dumps({
        "found": True,
        "results": [{"summary_text": "Bài tập squat", "session_id": "s1", "similarity": 0.9}],
    })

    state = {
        "needs_clarification": False,
        "required_outputs": ["exercise_protocol"],
        "messages": [
            ToolMessage(content=normal_result, tool_call_id="call-1", name="kb_search"),
        ],
    }

    mode = _derive_mode(state)
    assert mode in ("synthesize", "chat"), (
        f"Expected synthesize/chat without ambiguous, got '{mode}'"
    )
    assert mode != "clarify"


# ═══════════════════════════════════════════════════════════════════════════
# A3 — user_memory (integration)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a3_user_memory_fact_reaches_the_synthesizer_prompt():
    """POST user_memory fact → memory node loads it → synthesizer's facts block.

    Restrictions come first whatever their age: prompt budgets drop facts from
    the end of the list.
    """
    from langgraph_agents.shared import get_pg_client
    from langgraph_agents.nodes.memory import _load_user_facts
    from langgraph_agents.nodes.synthesizer import _build_user_facts

    pg = get_pg_client()
    await pg.connect()

    user_id = str(uuid.uuid4())
    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)

    try:
        # Insert facts directly (simulating POST /me/memory), oldest first
        for minutes_ago, fact_text, category in (
            (30, "Không được nâng tay quá đầu", "restriction"),
            (20, "Tôi bị đau lưng mãn tính", "condition"),
            (10, "Thích tập buổi sáng", "preference"),
        ):
            await pg.execute(
                """INSERT INTO user_memory (user_id, fact_text, category, created_at)
                   VALUES ($1::uuid, $2, $3, now() - make_interval(mins => $4))""",
                user_id, fact_text, category, minutes_ago,
            )

        # _load_user_facts must return them, safety first
        facts = await _load_user_facts(user_id)
        assert facts == [
            "Không được nâng tay quá đầu",
            "Tôi bị đau lưng mãn tính",
            "Thích tập buổi sáng",
        ]

        # The synthesizer's block must carry them
        block = _build_user_facts(facts)
        assert "## What this user has told you about themselves" in block
        assert "- Tôi bị đau lưng mãn tính" in block

    finally:
        await pg.execute("DELETE FROM user_memory WHERE user_id = $1::uuid", user_id)
        await pg.execute("DELETE FROM users WHERE id = $1", user_id)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_PG, reason="PostgreSQL not available on port 5433")
@pytest.mark.asyncio
async def test_a3_user_memory_ownership_other_user_gets_404():
    """User B deleting User A's fact returns DELETE 0 (not found / not owned)."""
    from langgraph_agents.shared import get_pg_client

    pg = get_pg_client()
    await pg.connect()

    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_a)
    await pg.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT DO NOTHING", user_b)

    try:
        # User A inserts a fact
        fact_id = await pg.fetchval(
            "INSERT INTO user_memory (user_id, fact_text) VALUES ($1::uuid, $2) RETURNING id",
            user_a, "Bí mật của user A",
        )

        # User B tries to delete User A's fact
        result = await pg.execute(
            "DELETE FROM user_memory WHERE id = $1::uuid AND user_id = $2::uuid",
            fact_id, user_b,
        )
        assert result == "DELETE 0", (
            f"User B must not be able to delete User A's fact, got result: {result}"
        )

        # Fact must still exist
        still_there = await pg.fetchval(
            "SELECT COUNT(*) FROM user_memory WHERE id = $1::uuid",
            fact_id,
        )
        assert still_there == 1, "Fact must still exist after cross-user delete attempt"

    finally:
        await pg.execute("DELETE FROM user_memory WHERE user_id = ANY($1)", [user_a, user_b])
        await pg.execute("DELETE FROM users WHERE id = ANY($1)", [user_a, user_b])


@pytest.mark.unit
def test_a3_user_memory_create_validates_empty_fact_text():
    """UserMemoryCreate schema: fact_text='' → Pydantic ValidationError (min_length=1)."""
    from pydantic import ValidationError
    from langgraph_agents.api.schemas import UserMemoryCreate

    with pytest.raises(ValidationError):
        UserMemoryCreate(fact_text="")


@pytest.mark.unit
def test_a3_user_memory_create_validates_too_long_fact_text():
    """UserMemoryCreate schema: fact_text >500 chars → Pydantic ValidationError (max_length=500)."""
    from pydantic import ValidationError
    from langgraph_agents.api.schemas import UserMemoryCreate

    with pytest.raises(ValidationError):
        UserMemoryCreate(fact_text="x" * 501)


@pytest.mark.unit
def test_a3_user_memory_create_accepts_valid_fact_text():
    """UserMemoryCreate schema: valid fact_text → no error."""
    from langgraph_agents.api.schemas import UserMemoryCreate

    item = UserMemoryCreate(fact_text="Tôi bị đau lưng mãn tính", category="health")
    assert item.fact_text == "Tôi bị đau lưng mãn tính"
    assert item.category == "health"
