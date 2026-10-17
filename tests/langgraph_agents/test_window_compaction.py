"""The current session is summarised as it falls out of the window (D13, 17/10).

Before: the recent-raw window held ~1,000 tokens (`context_budgets.history`)
but a summary waited for 10,000 unsummarised tokens, so between the two the
agent saw neither the messages nor a summary of them. Now the summary follows
the window, on whole messages, and never ends on an unanswered question.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import summarizer as summ

pytestmark = pytest.mark.unit

BUDGET = 1000            # tokens, as in config/langgraph.yaml
CHARS_PER_TOKEN = 4      # memory._token_estimate


def _msgs(sizes_in_tokens, first_seq=1, first_role="user"):
    roles = ["user", "assistant"] if first_role == "user" else ["assistant", "user"]
    return [
        {"seq_id": first_seq + i, "role": roles[i % 2], "content": "x" * (t * CHARS_PER_TOKEN)}
        for i, t in enumerate(sizes_in_tokens)
    ]


async def _maybe(rows, last_covered=0):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetchval = AsyncMock(return_value=last_covered)
    pg.fetch = AsyncMock(return_value=rows)
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.nodes.memory.history_budget", return_value=BUDGET), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run:
        await summ.maybe_summarize("s-1")
    return run


@pytest.mark.asyncio
async def test_everything_fits_in_the_window_so_nothing_is_summarised():
    run = await _maybe(_msgs([200, 300, 200, 250]))      # 950 tokens, all shown
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_what_fell_out_is_summarised_up_to_its_last_message():
    # Window keeps the newest 900 tokens (msgs 5–7: 300×3); 1–4 are out (800 tokens).
    rows = _msgs([200, 200, 200, 200, 300, 300, 300])
    run = await _maybe(rows)
    run.assert_awaited_once_with("s-1", 0, up_to_seq=4)


@pytest.mark.asyncio
async def test_never_ends_on_a_question_its_answer_comes_along():
    # Out of window: msgs 1–3 (user, assistant, user). Message 3 is a question,
    # so the summary takes its answer (msg 4) too, though 4 is still shown.
    rows = _msgs([250, 250, 250, 300, 300, 300])
    run = await _maybe(rows)
    assert rows[2]["role"] == "user"
    run.assert_awaited_once_with("s-1", 0, up_to_seq=4)


@pytest.mark.asyncio
async def test_a_little_out_of_window_waits_for_more():
    rows = _msgs([100, 100, 300, 300, 300])               # only msg 1 (100 tokens) is out
    run = await _maybe(rows)
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_continues_from_the_last_summary_not_from_the_start():
    rows = _msgs([200, 200, 200, 300, 300, 300], first_seq=11)
    run = await _maybe(rows, last_covered=10)
    run.assert_awaited_once_with("s-1", 10, up_to_seq=14)


@pytest.mark.asyncio
async def test_the_twenty_message_cap_counts_too():
    # 40 messages of 45 tokens: the budget alone would show 22 (990 tokens),
    # but the memory node shows only the last 20, so 1–20 are out.
    rows = _msgs([45] * 40)
    run = await _maybe(rows)
    run.assert_awaited_once_with("s-1", 0, up_to_seq=20)


@pytest.mark.asyncio
async def test_one_huge_message_pushes_everything_older_out():
    # The newest message alone is over budget, so the window is empty and all
    # three are summarised — the last one too, though it has no answer yet.
    rows = _msgs([300, 300, 2000])
    run = await _maybe(rows)
    run.assert_awaited_once_with("s-1", 0, up_to_seq=3)


def test_window_matches_the_memory_node():
    """The two must agree, or a message is summarised while shown, or neither."""
    from langgraph_agents.nodes.memory import _select_recent_raw
    rows = _msgs([200, 200, 200, 200, 300, 300, 300])
    with patch("langgraph_agents.nodes.memory.history_budget", return_value=BUDGET):
        start = summ._window_start(rows)
    shown = _select_recent_raw([{"role": r["role"], "content": r["content"]} for r in rows], BUDGET)
    assert [r["content"] for r in rows[start:]] == [m["content"] for m in shown]


@pytest.mark.asyncio
async def test_run_summarize_stops_at_up_to_seq():
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=[])
    with patch.object(summ, "get_pg_client", return_value=pg):
        await summ._run_summarize("s-1", 10, up_to_seq=14)
    sql, *args = pg.fetch.await_args.args
    assert "seq_id <= $3" in sql and args == ["s-1", 10, 14]
