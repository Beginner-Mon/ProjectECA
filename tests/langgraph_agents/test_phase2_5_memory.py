"""Memory unit tests — M.5 raw history, token estimate, mock DB.

What the node hands on (facts, summaries, the history after the last summary)
is covered end to end in test_memory_v2_stm.py; this file keeps the loaders.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langgraph_agents.state import AgentState


@pytest.mark.unit
class TestContextAssembly:
    """Verify _recent_raw_to_messages."""

    def test_recent_raw_to_messages(self):
        from langgraph_agents.nodes.memory import _recent_raw_to_messages
        from langchain_core.messages import HumanMessage, AIMessage
        raw = [
            {"role": "user", "content": "xin chao"},
            {"role": "assistant", "content": "Chao ban!"},
        ]
        msgs = _recent_raw_to_messages(raw)
        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)
        assert msgs[0].content == "xin chao"

    def test_recent_raw_skips_empty_content(self):
        from langgraph_agents.nodes.memory import _recent_raw_to_messages
        raw = [{"role": "user", "content": ""}]
        msgs = _recent_raw_to_messages(raw)
        assert msgs[0].content == ""

    def test_recent_raw_unknown_role_skipped(self):
        from langgraph_agents.nodes.memory import _recent_raw_to_messages
        raw = [{"role": "system", "content": "beep"}]
        msgs = _recent_raw_to_messages(raw)
        # system role is not user or assistant → not converted
        assert len(msgs) == 0


@pytest.mark.unit
class TestTokenBudget:
    """Verify _token_estimate."""

    def test_token_estimate_empty(self):
        from langgraph_agents.nodes.memory import _token_estimate
        assert _token_estimate("") == 0

    def test_token_estimate_vietnamese(self):
        from langgraph_agents.nodes.memory import _token_estimate
        # ~40 chars → ~10 tokens
        assert _token_estimate("Xin chao ban, toi can bai tap cho dau lung") == 10


@pytest.mark.unit
class TestMemoryLoaderMocks:
    """Test async loader functions with mocked PostgreSQL."""

    @pytest.mark.asyncio
    async def test_load_user_instructions_returns_tier1_newest_first(self):
        from langgraph_agents.nodes.memory import _load_user_instructions
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"fact_text": "65 tuổi", "project": None, "said_at": "2026-10-02"},
            {"fact_text": "đau lưng mãn tính", "project": None, "said_at": "2026-10-01"},
        ]

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            facts = await _load_user_instructions("test-user-id")
            assert len(facts) == 2
            assert "[2026-10-02] 65 tuổi" in facts
            sql = " ".join(mock_pg.fetch.await_args.args[0].split())
            assert "tier = 1" in sql and "status = 'active'" in sql
            assert "ORDER BY said_at DESC" in sql
            assert "safety" not in sql

    @pytest.mark.asyncio
    async def test_load_user_instructions_handles_db_error(self):
        from langgraph_agents.nodes.memory import _load_user_instructions
        mock_pg = AsyncMock()
        mock_pg.fetch.side_effect = RuntimeError("DB connection failed")

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            facts = await _load_user_instructions("test-user-id")
            assert facts == []  # graceful degradation

    @pytest.mark.asyncio
    async def test_load_stm_returns_summary_and_uncovered_history(self):
        from langgraph_agents.nodes.memory import _load_stm
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"stm_summary": "User asked about squat exercises",
             "role": "assistant", "content": "Chào bạn!"},
            {"stm_summary": "User asked about squat exercises",
             "role": "user", "content": "xin chào"},
        ]

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            summary, rows = await _load_stm("test-session")
            assert summary == ["User asked about squat exercises"]
            # Asked newest first, handed on oldest first.
            assert rows == [
                {"role": "user", "content": "xin chào"},
                {"role": "assistant", "content": "Chào bạn!"},
            ]

    @pytest.mark.asyncio
    async def test_load_stm_handles_db_error(self):
        from langgraph_agents.nodes.memory import _load_stm
        mock_pg = AsyncMock()
        mock_pg.fetch.side_effect = RuntimeError("DB down")

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            assert await _load_stm("test-session") == ([], [])

    @pytest.mark.asyncio
    async def test_load_stm_empty_without_summary_or_history(self):
        from langgraph_agents.nodes.memory import _load_stm
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"stm_summary": None, "role": None, "content": None},
        ]

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            assert await _load_stm("test-session") == ([], [])

    @pytest.mark.asyncio
    async def test_load_raw_history_hands_on_oldest_first(self):
        from langgraph_agents.nodes.memory import _load_stm
        mock_pg = AsyncMock()
        # The statement asks for the newest rows first: its row limit must
        # keep the recent end of a long session.
        mock_pg.fetch.return_value = [
            {"stm_summary": None, "role": "assistant", "content": "Chào bạn!"},
            {"stm_summary": None, "role": "user", "content": "xin chào"},
        ]

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            summary, result = await _load_stm("test-session")
            assert summary == []
            assert result == [
                {"role": "user", "content": "xin chào"},
                {"role": "assistant", "content": "Chào bạn!"},
            ]

    @pytest.mark.asyncio
    async def test_load_raw_history_error_returns_empty(self):
        from langgraph_agents.nodes.memory import _load_stm
        mock_pg = AsyncMock()
        mock_pg.fetch.side_effect = RuntimeError("DB down")

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg):
            result = await _load_stm("test-session")
            assert result == ([], [])

    @pytest.mark.asyncio
    async def test_memory_node_reads_postgres_not_the_cache(self):
        """The short-term cache is off in production and on the developer
        machine, so the node no longer asks it: PostgreSQL is the only read.
        """
        from langgraph_agents.nodes.memory import memory_node
        from langchain_core.runnables import RunnableConfig

        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = []
        config = RunnableConfig(configurable={
            "user_id": "test-user", "session_id": "test-sess",
            "query": "xin chao", "persona_id": "anne",
            "request_id": "req-001",
        })

        with patch("langgraph_agents.nodes.memory.get_pg_client", return_value=mock_pg), \
             patch("langgraph_agents.shared.stm.get_stm") as mock_get_stm, \
             patch("langgraph_agents.nodes.memory._load_tier2_facts",
                   new=AsyncMock(return_value=([], None, None, None))):
            await memory_node({"messages": []}, config)

        mock_get_stm.assert_not_called()
        assert mock_pg.fetch.await_count == 2


@pytest.mark.unit
class TestMemoryNode:
    """Test memory_node orchestration (mocked)."""

    @pytest.mark.asyncio
    async def test_memory_node_assembles_context(self):
        from langgraph_agents.nodes.memory import memory_node
        from langchain_core.messages import AIMessage, HumanMessage
        from langchain_core.runnables import RunnableConfig

        config = RunnableConfig(configurable={
            "user_id": "test-user", "session_id": "test-sess",
            "query": "xin chao", "persona_id": "anne",
            "request_id": "req-001",
        })
        state: AgentState = {"messages": [], "errors": [], "total_tokens": 0,
                             "retry_count": 0}

        with patch("langgraph_agents.nodes.memory._load_user_instructions") as mock_facts, \
             patch("langgraph_agents.nodes.memory._load_stm") as mock_stm, \
             patch("langgraph_agents.nodes.memory._load_tier2_facts") as mock_tier2:
            mock_facts.return_value = ["65 tuoi"]
            mock_stm.return_value = (
                ["Session: squat"],
                [
                    {"role": "user", "content": "xin chao"},
                    {"role": "assistant", "content": "Chao ban!"},
                ],
            )
            mock_tier2.return_value = (["- [2026-10-14] di bo"], 0.9, None, 5)

            result = await memory_node(state, config)
            # Facts and summaries are fields of their own, not a SystemMessage.
            assert result["user_instructions"] == ["65 tuoi"]
            assert result["user_facts"] == ["- [2026-10-14] di bo"]
            assert result["session_summary"] == ["Session: squat"]
            assert [type(m) for m in result["messages"]] == [HumanMessage, AIMessage]

    @pytest.mark.asyncio
    async def test_memory_node_empty_context_returns_empty(self):
        from langgraph_agents.nodes.memory import memory_node
        from langchain_core.runnables import RunnableConfig

        config = RunnableConfig(configurable={
            "user_id": "test-user", "session_id": "test-sess",
            "query": "xin chao", "persona_id": "anne",
            "request_id": "req-001",
        })
        state: AgentState = {"messages": [], "errors": [], "total_tokens": 0,
                             "retry_count": 0}

        with patch("langgraph_agents.nodes.memory._load_user_instructions") as mock_facts, \
             patch("langgraph_agents.nodes.memory._load_stm") as mock_stm, \
             patch("langgraph_agents.nodes.memory._load_tier2_facts") as mock_tier2:
            mock_facts.return_value = []
            mock_stm.return_value = ([], [])
            mock_tier2.return_value = ([], None, None, None)

            result = await memory_node(state, config)
            assert result == {"messages": [], "user_instructions": [],
                              "user_facts": [], "session_summary": []}
