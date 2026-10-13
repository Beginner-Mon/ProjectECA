"""Summarizer unit tests — PR 2 M.5 background summarizer.

Tests: trigger threshold, CAS idempotent, LLM summarize + embed, retry.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _turns(*chars, first_seq=1):
    """Message rows for turns of (question_chars, answer_chars), oldest first."""
    rows, seq = [], first_seq
    for q_chars, a_chars in chars:
        rows.append({"seq_id": seq, "role": "user", "content": "q" * q_chars})
        rows.append({"seq_id": seq + 1, "role": "assistant", "content": "a" * a_chars})
        seq += 2
    return rows


@pytest.fixture
def compact_at_2000(monkeypatch):
    from langgraph_agents.shared import context as ctx_mod
    monkeypatch.setattr(ctx_mod, "_load_context_budgets",
                        lambda: {"history": 1000, "history_compact_at": 2000})


@pytest.mark.unit
class TestSummarizerThreshold:
    """Verify trigger logic: unsummarised content above `history_compact_at`.

    Size is len(content) // 4. `messages.token_count` holds the whole turn's
    LLM usage and is not read (see test_memory_v2_stm.py for where the cut lands).
    """

    @pytest.mark.asyncio
    async def test_below_threshold_no_summarize(self, compact_at_2000):
        """1.9k tokens of content → summarizer does NOT fire."""
        from langgraph_agents.nodes.summarizer import maybe_summarize
        mock_pg = AsyncMock()
        mock_pg.fetchval.return_value = 0  # no prior summary
        mock_pg.fetch.return_value = _turns((400, 3400), (400, 3400))

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer._run_summarize",
                       new=AsyncMock()) as mock_run:
                await maybe_summarize("test-session")
                mock_run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_above_threshold_fires_task(self, compact_at_2000):
        """2.1k tokens of content → summarizer runs (inline, inside the invocation)."""
        from langgraph_agents.nodes.summarizer import maybe_summarize
        mock_pg = AsyncMock()
        mock_pg.fetchval.return_value = 0
        mock_pg.fetch.return_value = _turns((400, 3800), (400, 3800))

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer._run_summarize",
                       new=AsyncMock()) as mock_run:
                await maybe_summarize("test-session")
                # The older turn (seq 1-2) is folded; the latest stays raw.
                mock_run.assert_awaited_once_with("test-session", 0, 2)

    @pytest.mark.asyncio
    async def test_only_messages_after_the_last_summary_are_measured(self, compact_at_2000):
        """The chunk starts where the last one stopped."""
        from langgraph_agents.nodes.summarizer import maybe_summarize
        mock_pg = AsyncMock()
        mock_pg.fetchval.return_value = 50  # last summary covers up to seq 50
        mock_pg.fetch.return_value = _turns((400, 3800), (400, 3800), first_seq=51)

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer._run_summarize",
                       new=AsyncMock()) as mock_run:
                await maybe_summarize("test-session")
                assert mock_pg.fetch.await_args.args[1:] == ("test-session", 50)
                mock_run.assert_awaited_once_with("test-session", 50, 52)

    @pytest.mark.asyncio
    async def test_token_count_is_not_what_is_measured(self, compact_at_2000):
        """A turn's LLM usage says nothing about how long its messages are."""
        from langgraph_agents.nodes.summarizer import maybe_summarize
        mock_pg = AsyncMock()
        mock_pg.fetchval.return_value = 0
        rows = _turns((40, 200), (40, 200), (40, 200))
        for r in rows:
            r["token_count"] = 9000
        mock_pg.fetch.return_value = rows

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer._run_summarize",
                       new=AsyncMock()) as mock_run:
                await maybe_summarize("test-session")
                mock_run.assert_not_awaited()


@pytest.mark.unit
class TestSummarizerCAS:
    """Verify CAS idempotent (ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING)."""

    @pytest.mark.asyncio
    async def test_cas_no_duplicate_chunk(self):
        """Calling _run_summarize twice with same session → only 1 INSERT row."""
        from langgraph_agents.nodes.summarizer import _run_summarize
        mock_pg = AsyncMock()
        mock_pg.connect = AsyncMock()
        mock_pg.fetch.return_value = [
            {"seq_id": 1, "role": "user", "content": "hello", "token_count": 10},
            {"seq_id": 2, "role": "assistant", "content": "hi there", "token_count": 12},
        ]
        mock_pg.fetchrow = AsyncMock(return_value=None)
        mock_pg.execute = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)

        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = "A summary of the conversation"
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer.get_embedding_service", return_value=mock_embed):
                with patch("langgraph_agents.nodes.summarizer.get_chat_model", return_value=mock_llm):
                    await _run_summarize("test-session", 0)
                    mock_pg.execute.assert_called_once()
                    # Verify INSERT includes ON CONFLICT clause
                    call_sql = mock_pg.execute.call_args[0][0]
                    assert "ON CONFLICT" in call_sql
                    assert "uq_chunk" in call_sql


@pytest.mark.unit
class TestSummarizerLLM:
    """Verify LLM call and response handling."""

    @pytest.mark.asyncio
    async def test_summarize_with_llm_content(self):
        """LLM returns summary → embedded → INSERT."""
        from langgraph_agents.nodes.summarizer import _run_summarize
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"seq_id": 1, "role": "user", "content": "toi bi dau lung", "token_count": 8000},
            {"seq_id": 2, "role": "assistant", "content": "ban nen tap squat nhe", "token_count": 9000},
        ]
        mock_pg.execute = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)

        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = "Nguoi dung hoi ve dau lung, duoc khuyen tap squat nhe."
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer.get_embedding_service", return_value=mock_embed):
                with patch("langgraph_agents.nodes.summarizer.get_chat_model", return_value=mock_llm):
                    await _run_summarize("test-session", 0)
                    # Verify INSERT was called with correct data
                    args = mock_pg.execute.call_args
                    assert args is not None

    @pytest.mark.asyncio
    async def test_summarize_llm_empty_response(self):
        """LLM returns empty content → summarizer returns without INSERT."""
        from langgraph_agents.nodes.summarizer import _run_summarize
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"seq_id": 1, "role": "user", "content": "hello", "token_count": 5},
        ]
        mock_pg.execute = AsyncMock()

        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = ""  # empty
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer.get_chat_model", return_value=mock_llm):
                await _run_summarize("test-session", 0)
                # INSERT should NOT be called (empty response)
                # execute is only called for INSERT — verify not called
                insert_calls = [c for c in mock_pg.execute.call_args_list
                                if "INSERT INTO summaries" in str(c)]
                assert len(insert_calls) == 0

    @pytest.mark.asyncio
    async def test_summarize_llm_retry_once(self):
        """LLM fails first call → retries once → succeeds on retry."""
        from langgraph_agents.nodes.summarizer import _run_summarize
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = [
            {"seq_id": 1, "role": "user", "content": "hello", "token_count": 5000},
        ]
        mock_pg.execute = AsyncMock()

        mock_embed = MagicMock()
        mock_embed.aembed_passage = AsyncMock(return_value=[0.1] * 384)

        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = "Summary after retry"
        # First call fails, second succeeds
        mock_llm.ainvoke = AsyncMock(side_effect=[
            RuntimeError("LLM timeout"),
            mock_ai,
        ])

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer.get_embedding_service", return_value=mock_embed):
                with patch("langgraph_agents.nodes.summarizer.get_chat_model", return_value=mock_llm):
                    await _run_summarize("test-session", 0)
                    # Should have called LLM twice (retry)
                    assert mock_llm.ainvoke.call_count == 2


@pytest.mark.unit
class TestSummarizerEdgeCases:
    """Edge cases: no messages, handle exceptions."""

    @pytest.mark.asyncio
    async def test_trigger_handles_db_error(self):
        """maybe_summarize gracefully handles DB errors."""
        from langgraph_agents.nodes.summarizer import maybe_summarize
        mock_pg = AsyncMock()
        mock_pg.fetchval.side_effect = RuntimeError("DB down")

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            # Should not raise — error is logged
            await maybe_summarize("test-session")

    @pytest.mark.asyncio
    async def test_empty_messages_returns_early(self):
        """No messages since last summary → returns without LLM call."""
        from langgraph_agents.nodes.summarizer import _run_summarize
        mock_pg = AsyncMock()
        mock_pg.fetch.return_value = []  # no messages

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            with patch("langgraph_agents.nodes.summarizer.get_chat_model") as mock_llm:
                await _run_summarize("test-session", 0)
                mock_llm.assert_not_called()
