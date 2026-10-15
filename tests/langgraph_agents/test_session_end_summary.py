"""Rolling STM: each conversation keeps one summary, merged forward.

A session compacts in place once its raw part outgrows the budget; the next
compaction folds the previous summary in. housekeep runs the compaction on
ECA's key and awaits it after `done`.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import summarizer as summ

pytestmark = pytest.mark.unit


def _pg(head=(None, 0), rows=()):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetchrow = AsyncMock(return_value={"stm_summary": head[0],
                                          "stm_up_to_seq": head[1]})
    pg.fetch = AsyncMock(return_value=list(rows))
    pg.execute = AsyncMock(return_value="UPDATE 1")
    return pg


@pytest.mark.asyncio
async def test_compaction_merges_forward_from_the_rolling_summary():
    pg = _pg(head=("old", 0), rows=[
        {"seq_id": 1, "role": "user", "content": "q" * 400},
        {"seq_id": 2, "role": "assistant", "content": "a" * 40_000},
        {"seq_id": 3, "role": "user", "content": "q" * 400},
        {"seq_id": 4, "role": "assistant", "content": "a" * 40_000},
    ])
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock(return_value="S")) as roll, \
         patch("asyncio.create_task") as create_task:
        await summ.compact_stm("s-current")
    # One LLM call folding old + covered; written inline, never as a task.
    assert roll.await_args.args[0] == "old"
    assert pg.execute.await_args.args[1:5] == ("s-current", "S", 2, 0)
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_nothing_to_compact_makes_no_llm_call():
    with patch.object(summ, "get_pg_client", return_value=_pg()), \
         patch.object(summ, "_summarize_rolling",
                      new=AsyncMock()) as roll:
        await summ.compact_stm("s-current")
    roll.assert_not_awaited()


@pytest.mark.asyncio
async def test_housekeep_runs_on_eca_key_not_the_users():
    from langgraph_agents.llm_byok import bind_request_llm, current_request_llm

    seen = {}

    async def record(*_args):
        seen["llm"] = current_request_llm()
        return 0

    user_llm = MagicMock()
    bind_request_llm(user_llm)
    try:
        with patch.object(summ, "compact_stm", side_effect=record), \
             patch.object(summ, "get_pg_client", return_value=_pg()):
            await asyncio.create_task(summ.housekeep("user-1", "s-current"))
        assert seen["llm"] is None
        assert current_request_llm() is user_llm      # the request keeps its key
    finally:
        bind_request_llm(None)


@pytest.mark.asyncio
async def test_housekeep_never_raises(caplog):
    with patch.object(summ, "compact_stm",
                      new=AsyncMock(side_effect=RuntimeError("db down"))):
        await summ.housekeep("user-1", "s-current")


@pytest.mark.asyncio
async def test_chat_sends_done_before_housekeeping_finishes_and_still_waits_for_it(monkeypatch):
    """`done` releases the send button; the stream (and so the Lambda
    invocation) stays open until the summaries are written. Driven on the
    generator itself: Starlette's TestClient buffers the whole body."""
    import langgraph_agents.api.main as api_module
    from langgraph_agents.api.schemas import ChatRequest

    async def fake_astream(*_a, **_k):
        yield ("updates", {"synthesizer": {"final_answer": "Xin chào."}})

    graph = MagicMock()
    graph.astream = fake_astream
    monkeypatch.setattr(api_module, "_graph", graph)
    monkeypatch.setattr(api_module, "write_turn_question", AsyncMock())
    monkeypatch.setattr(api_module, "write_turn_answer", AsyncMock(return_value=None))

    log = []

    async def slow_housekeep(user_id, session_id, **_budgets):
        log.append(("started", user_id, session_id))
        await asyncio.sleep(0.3)
        log.append("finished")

    monkeypatch.setattr(api_module, "housekeep", slow_housekeep)
    req = ChatRequest(query="Xin chào", session_id="s-1")
    async for event in api_module._stream_chat(
            req, "r-1", {"configurable": {}}, {}, None, resolved_user_id="u-1"):
        if event.get("event") == "done":
            log.append("done")
    # The generator only returned after the summaries finished, and `done`
    # went out before they did.
    assert ("started", "u-1", "s-1") in log
    assert log[-1] == "finished"
    assert log.index("done") < log.index("finished")


@pytest.mark.asyncio
async def test_no_housekeeping_without_a_signed_in_user(monkeypatch):
    import langgraph_agents.api.main as api_module
    from langgraph_agents.api.schemas import ChatRequest

    async def fake_astream(*_a, **_k):
        yield ("updates", {"synthesizer": {"final_answer": "Xin chào."}})

    graph = MagicMock()
    graph.astream = fake_astream
    monkeypatch.setattr(api_module, "_graph", graph)
    monkeypatch.setattr(api_module, "write_turn_question", AsyncMock())
    monkeypatch.setattr(api_module, "write_turn_answer", AsyncMock(return_value=None))
    hk = AsyncMock()
    monkeypatch.setattr(api_module, "housekeep", hk)
    req = ChatRequest(query="Xin chào", session_id="s-1")
    async for _ in api_module._stream_chat(req, "r-1", {"configurable": {}}, {}, None):
        pass
    hk.assert_not_called()
