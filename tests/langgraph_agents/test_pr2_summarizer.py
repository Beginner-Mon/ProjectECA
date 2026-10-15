"""Summarizer unit tests — rolling STM compaction (memory-v2 tiers).

One summary per conversation (conversations.stm_summary), merged forward and
written compare-and-set on stm_up_to_seq. No embedding: the summary is read
by session id, never searched.

Tests: trigger threshold, CAS idempotent, rolling LLM call + retry.
"""

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


def _pg(head=(None, 0), rows=()):
    """Fake pg client: fetchrow answers the stm head, fetch the messages."""
    pg = AsyncMock()
    pg.fetchrow = AsyncMock(return_value={"stm_summary": head[0],
                                          "stm_up_to_seq": head[1]})
    pg.fetch = AsyncMock(return_value=list(rows))
    pg.execute = AsyncMock(return_value="UPDATE 1")
    return pg


@pytest.fixture
def compact_at_2000(monkeypatch):
    from langgraph_agents.shared import context as ctx_mod
    monkeypatch.setattr(ctx_mod, "_load_context_budgets",
                        lambda: {"history": 1000, "history_compact_at": 2000})


@pytest.mark.unit
class TestCompactionThreshold:
    """Verify trigger logic: unsummarised content above `history_compact_at`.

    Size is len(content) // 4. `messages.token_count` holds the whole turn's
    LLM usage and is not read (see test_memory_v2_stm.py for where the cut lands).
    """

    @pytest.mark.asyncio
    async def test_below_threshold_no_summarize(self, compact_at_2000):
        """1.9k tokens of content → compactor does NOT fire."""
        from langgraph_agents.nodes import summarizer as summ_mod
        pg = _pg(rows=_turns((400, 3400), (400, 3400)))

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock()) as mock_roll:
            await summ_mod.compact_stm("test-session")
            mock_roll.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_above_threshold_folds_oldest_part(self, compact_at_2000):
        """2.1k tokens of content → compactor merges, leaving the latest raw."""
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = _turns((400, 3800), (400, 3800))
        pg = _pg(rows=rows)

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock(return_value="S")) as mock_roll:
            await summ_mod.compact_stm("test-session")
            # The older turn (seq 1-2) is folded; the latest stays raw.
            mock_roll.assert_awaited_once()
            assert mock_roll.await_args.args[1] == [
                {"seq_id": 1, "role": "user", "content": rows[0]["content"]},
                {"seq_id": 2, "role": "assistant", "content": rows[1]["content"]},
            ]
            sql, *args = pg.execute.await_args.args
            assert "stm_up_to_seq = $4" in " ".join(sql.split())
            assert args == ["test-session", "S", 2, 0]

    @pytest.mark.asyncio
    async def test_only_messages_after_the_summary_are_measured(self, compact_at_2000):
        """Compaction starts where the rolling summary stopped."""
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = _turns((400, 3800), (400, 3800), first_seq=51)
        pg = _pg(head=("old", 50), rows=rows)

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock(return_value="S")) as mock_roll:
            await summ_mod.compact_stm("test-session")
            assert pg.fetch.await_args.args[1:] == ("test-session", 50)
            # The new summary folds the old one in.
            assert mock_roll.await_args.args[0] == "old"
            sql, *args = pg.execute.await_args.args
            assert args == ["test-session", "S", 52, 50]

    @pytest.mark.asyncio
    async def test_token_count_is_not_what_is_measured(self, compact_at_2000):
        """A turn's LLM usage says nothing about how long its messages are."""
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = _turns((40, 200), (40, 200), (40, 200))
        for r in rows:
            r["token_count"] = 9000
        pg = _pg(rows=rows)

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock()) as mock_roll:
            await summ_mod.compact_stm("test-session")
            mock_roll.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_conversation_row_compacts_nothing(self, compact_at_2000):
        """A session with no conversations row is not ours to summarise."""
        from langgraph_agents.nodes import summarizer as summ_mod
        pg = AsyncMock()
        pg.fetchrow = AsyncMock(return_value=None)
        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock()) as mock_roll:
            await summ_mod.compact_stm("test-session")
            mock_roll.assert_not_awaited()
            pg.fetch.assert_not_awaited()


@pytest.mark.unit
class TestRollingMerge:
    """The second compaction folds the first summary in (plan step 3)."""

    @pytest.mark.asyncio
    async def test_second_compaction_merges_the_first(self, compact_at_2000):
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = _turns((400, 3800), (400, 3800))
        pg = _pg(head=("first summary", 0), rows=rows)

        seen = {}

        async def fake_roll(previous, covered, session_id, usage=None):
            seen["previous"] = previous
            return (previous or "") + " + more"

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling", side_effect=fake_roll):
            await summ_mod.compact_stm("test-session")

        assert seen["previous"] == "first summary"
        sql, *args = pg.execute.await_args.args
        assert args[1] == "first summary + more"
        assert args[2:] == [2, 0]


@pytest.mark.unit
class TestCompactionCAS:
    """Compare-and-set: two compactions racing write the same range once."""

    @pytest.mark.asyncio
    async def test_concurrent_compactions_write_once(self, compact_at_2000, caplog):
        """The loser sees UPDATE 0 and logs superseded instead of writing."""
        import asyncio
        import logging
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = _turns((400, 3800), (400, 3800))

        pg = _pg(rows=rows)
        pg.execute = AsyncMock(side_effect=["UPDATE 1", "UPDATE 0"])

        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "_summarize_rolling",
                          new=AsyncMock(return_value="S")):
            with caplog.at_level(logging.DEBUG):
                await asyncio.gather(
                    summ_mod.compact_stm("test-session"),
                    summ_mod.compact_stm("test-session"),
                )
        assert pg.execute.await_count == 2
        assert [r.getMessage() for r in caplog.records].count("stm_compacted") == 1
        assert [r.getMessage() for r in caplog.records].count(
            "stm_compaction_superseded") == 1


@pytest.mark.unit
class TestRollingLLM:
    """Verify rolling LLM call and response handling."""

    def _llm(self, content):
        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = content
        mock_ai.usage_metadata = {"input_tokens": 7, "output_tokens": 3}
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)
        return mock_llm

    @pytest.mark.asyncio
    async def test_rolling_prompt_folds_previous_and_keeps_instructions(self):
        """The prompt carries the old summary plus the new messages, keeps
        instructions/facts, caps at ~250 words, same language."""
        from langgraph_agents.nodes import summarizer as summ_mod
        rows = [{"seq_id": 1, "role": "user", "content": "tra loi ngan thoi"}]
        with patch.object(summ_mod, "get_chat_model",
                          return_value=self._llm("S")) as get_model:
            text = await summ_mod._summarize_rolling("old", rows, "s-1")
        get_model.assert_called_once_with("memory")
        assert text == "S"

    @pytest.mark.asyncio
    async def test_rolling_prompt_shape(self):
        from langgraph_agents.nodes import summarizer as summ_mod
        llm = self._llm("S")
        rows = [{"seq_id": 1, "role": "user", "content": "my knee hurts"}]
        with patch.object(summ_mod, "get_chat_model", return_value=llm):
            await summ_mod._summarize_rolling("old summary", rows, "s-1")
        prompt = llm.ainvoke.await_args.args[0]
        assert "old summary" in prompt
        assert "User: my knee hurts" in prompt
        assert "250 words" in prompt
        assert "SAME language" in prompt
        assert "instructions" in prompt

    @pytest.mark.asyncio
    async def test_rolling_without_previous_summarises_messages_only(self):
        from langgraph_agents.nodes import summarizer as summ_mod
        llm = self._llm("S")
        rows = [{"seq_id": 1, "role": "user", "content": "hi"}]
        with patch.object(summ_mod, "get_chat_model", return_value=llm):
            await summ_mod._summarize_rolling(None, rows, "s-1")
        prompt = llm.ainvoke.await_args.args[0]
        assert "Previous summary" not in prompt

    @pytest.mark.asyncio
    async def test_rolling_llm_empty_response_writes_nothing(self):
        """LLM returns empty content → compactor returns without UPDATE."""
        from langgraph_agents.nodes import summarizer as summ_mod
        pg = _pg(rows=_turns((400, 3800), (400, 3800)))
        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = ""
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)
        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "get_chat_model", return_value=mock_llm):
            await summ_mod.compact_stm("test-session")
            mock_llm.ainvoke.assert_awaited()
            pg.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rolling_llm_retry_once(self):
        """LLM fails first call → retries once → succeeds on retry."""
        from langgraph_agents.nodes import summarizer as summ_mod
        mock_llm = MagicMock()
        mock_ai = MagicMock()
        mock_ai.content = "Summary after retry"
        mock_ai.usage_metadata = {}
        mock_llm.ainvoke = AsyncMock(side_effect=[RuntimeError("LLM timeout"), mock_ai])
        with patch.object(summ_mod, "get_chat_model", return_value=mock_llm):
            text = await summ_mod._summarize_rolling(
                None, [{"seq_id": 1, "role": "user", "content": "hi"}], "s-1")
            assert text == "Summary after retry"
            assert mock_llm.ainvoke.call_count == 2


@pytest.mark.unit
class TestCompactionEdgeCases:
    """Edge cases: no messages, handle exceptions."""

    @pytest.mark.asyncio
    async def test_trigger_handles_db_error(self):
        """compact_stm gracefully handles DB errors."""
        from langgraph_agents.nodes.summarizer import compact_stm
        mock_pg = AsyncMock()
        mock_pg.fetchrow = AsyncMock(side_effect=RuntimeError("DB down"))

        with patch("langgraph_agents.nodes.summarizer.get_pg_client", return_value=mock_pg):
            # Should not raise — error is logged
            await compact_stm("test-session")

    @pytest.mark.asyncio
    async def test_empty_messages_returns_early(self):
        """No messages after the summary → returns without LLM call."""
        from langgraph_agents.nodes import summarizer as summ_mod
        pg = _pg(rows=[])
        with patch.object(summ_mod, "get_pg_client", return_value=pg), \
             patch.object(summ_mod, "get_chat_model") as mock_llm:
            await summ_mod.compact_stm("test-session")
            mock_llm.assert_not_called()
