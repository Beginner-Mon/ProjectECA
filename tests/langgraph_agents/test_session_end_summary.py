"""Long-term memory: a session is summarised once the user has moved on (12/10).

Prod before this: no session reached the 10k-token threshold (largest ~2,800,
median ~400), and the few summaries that did trigger ran as fire-and-forget
tasks that Lambda froze after the response — 26 `summarizer_trigger_failed`
("the underlying connection is closed") against 5 `summarizer_done` since 01/09.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import summarizer as summ

pytestmark = pytest.mark.unit


def _pg(rows):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=rows)
    return pg


@pytest.mark.asyncio
async def test_finished_sessions_are_summarised_from_where_their_summary_stops():
    pg = _pg([{"session_id": "s-new", "covered": 0},
              {"session_id": "s-old", "covered": 41}])
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run:
        n = await summ.summarize_finished_sessions("user-1", "s-current")
    assert n == 2
    assert [c.args for c in run.await_args_list] == [("s-new", 0), ("s-old", 41)]
    sql, *args = pg.fetch.await_args.args
    assert args == ["user-1", "s-current", summ._MIN_FINISHED_MESSAGES,
                    summ._MAX_FINISHED_PER_TURN]
    assert summ._MIN_FINISHED_MESSAGES == 4      # two turns
    assert "session_id <> $2" in sql


@pytest.mark.asyncio
async def test_nothing_to_summarise_makes_no_llm_call():
    with patch.object(summ, "get_pg_client", return_value=_pg([])), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run:
        assert await summ.summarize_finished_sessions("user-1", "s-current") == 0
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_current_session_long_summary_runs_inline_not_as_a_task():
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetchval = AsyncMock(return_value=0)
    # Three long messages: only the newest fits the window, 1–2 fell out.
    pg.fetch = AsyncMock(return_value=[
        {"seq_id": i, "role": r, "content": "x" * 3200}
        for i, r in ((1, "user"), (2, "assistant"), (3, "user"))])
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.nodes.memory.history_budget", return_value=1000), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run, \
         patch("asyncio.create_task") as create_task:
        await summ.maybe_summarize("s-current")
    run.assert_awaited_once_with("s-current", 0, up_to_seq=2)
    create_task.assert_not_called()


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
        with patch.object(summ, "summarize_finished_sessions", side_effect=record), \
             patch.object(summ, "maybe_summarize", new=AsyncMock()):
            await asyncio.create_task(summ.housekeep("user-1", "s-current"))
        assert seen["llm"] is None
        assert current_request_llm() is user_llm      # the request keeps its key
    finally:
        bind_request_llm(None)


@pytest.mark.asyncio
async def test_housekeep_never_raises(caplog):
    with patch.object(summ, "summarize_finished_sessions",
                      new=AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(summ, "maybe_summarize", new=AsyncMock()) as current:
        await summ.housekeep("user-1", "s-current")
    current.assert_awaited_once_with("s-current")


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
    monkeypatch.setattr(api_module, "write_session_turn", AsyncMock(return_value=None))

    log = []

    async def slow_housekeep(user_id, session_id):
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
    monkeypatch.setattr(api_module, "write_session_turn", AsyncMock(return_value=None))
    hk = AsyncMock()
    monkeypatch.setattr(api_module, "housekeep", hk)
    req = ChatRequest(query="Xin chào", session_id="s-1")
    async for _ in api_module._stream_chat(req, "r-1", {"configurable": {}}, {}, None):
        pass
    hk.assert_not_called()
