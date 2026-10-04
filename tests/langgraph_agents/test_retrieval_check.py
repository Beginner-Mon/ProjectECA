"""Kiểm 1: lỗi của vòng retriever vừa xong (grader-contract T2)."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langgraph_agents.routing import (
    failed_tool_names, retrieval_fault, route_after_retriever,
)

pytestmark = pytest.mark.unit


def _call(name="kb_search"):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": {"query": "x"}, "id": "tc1", "type": "tool_call"}])


def _tool(content, name="kb_search"):
    return ToolMessage(content=content, tool_call_id="tc1", name=name)


def test_no_tool_called():
    state = {"messages": [HumanMessage("hi"), AIMessage(content="")]}
    assert retrieval_fault(state) == "no_tool_called"


def test_tool_error():
    state = {"messages": [_call(), _tool('{"error": "TimeoutError"}')]}
    assert retrieval_fault(state) == "tool_error"
    assert failed_tool_names(state) == ["kb_search"]


def test_empty_result_is_not_a_fault():
    assert retrieval_fault({"messages": [_call(), _tool("[]")]}) is None


def test_hits_is_not_a_fault():
    assert retrieval_fault({"messages": [_call(), _tool('[{"content": "x"}]')]}) is None


def test_no_ai_message_is_not_a_fault():
    assert retrieval_fault({"messages": [HumanMessage("hi")]}) is None


def test_route_goes_back_once_when_no_tool_called():
    base = {"messages": [AIMessage(content="")], "errors": [], "required_outputs": []}
    assert route_after_retriever({**base, "retriever_rounds": 1}) == "retriever_agent"
    assert route_after_retriever({**base, "retriever_rounds": 2}) == "synthesizer"


def test_route_runs_tools_on_second_round():
    state = {"messages": [_call()], "errors": [], "required_outputs": [],
             "retriever_rounds": 2}
    assert route_after_retriever(state) == "tools"


# ── Graph thật: kiểm 1 quay lại đúng node, tối đa một lần ──────────────

from unittest.mock import AsyncMock, MagicMock, patch


def _mock_planner(plan):
    mock_struct = MagicMock()
    mock_struct.ainvoke = AsyncMock(return_value=plan)
    mock_llm = MagicMock()
    mock_llm.with_structured_output = MagicMock(return_value=mock_struct)
    return mock_llm


def _kb_call(call_id="tc1"):
    return AIMessage(content="", tool_calls=[
        {"name": "kb_search", "args": {"query": "dau lung"}, "id": call_id,
         "type": "tool_call"},
    ])


async def _run_graph(retriever_ais, pg_fetch):
    """Graph thật; planner tags [] để grader bỏ qua; ghi prompt từng vòng."""
    import uuid

    from langgraph_agents.graph import build_graph_async
    from langgraph_agents.nodes.planner import PlanOutput
    from langgraph_agents.tools import pgvector_tool

    plan = PlanOutput(required_outputs=[], resolved_query="dau lung",
                      needs_retrieval=True, needs_clarification=False)

    retriever_prompts: list[str] = []
    synth_prompts: list[str] = []

    mock_bound = MagicMock()
    async def _retr_ainvoke(msgs):
        retriever_prompts.append(str(msgs[0].content) if msgs else "")
        ai = retriever_ais[min(len(retriever_prompts) - 1, len(retriever_ais) - 1)]
        return ai
    mock_bound.ainvoke = AsyncMock(side_effect=_retr_ainvoke)

    def _get_retriever(role):
        m = MagicMock()
        m.bind_tools = MagicMock(return_value=mock_bound)
        return m

    synth_llm = MagicMock()
    async def _synth_ainvoke(msgs):
        synth_prompts.append("\n".join(str(m.content) for m in msgs))
        return AIMessage(content="ok")
    synth_llm.ainvoke = AsyncMock(side_effect=_synth_ainvoke)

    mock_pg = AsyncMock()
    mock_pg.connect = AsyncMock()
    mock_pg.fetch = pg_fetch
    mock_embed = MagicMock()
    mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 384)

    node_runs: dict = {}
    final_state: dict = {}
    graph = await build_graph_async()
    state = {"messages": [], "errors": [], "retry_count": 0, "total_tokens": 0}
    config = {"configurable": {
        "user_id": "u1", "session_id": str(uuid.uuid4()), "query": "dau lung",
        "persona_id": "anne", "request_id": "r-t2", "web_search": False,
        "locale": "en",
    }}
    with patch("langgraph_agents.nodes.planner.get_chat_model",
               return_value=_mock_planner(plan)), \
         patch("langgraph_agents.nodes.retriever_agent.get_chat_model",
               side_effect=_get_retriever), \
         patch("langgraph_agents.nodes.synthesizer.get_chat_model",
               return_value=synth_llm), \
         patch("langgraph_agents.nodes.synthesizer.get_stream_writer",
               side_effect=RuntimeError("no stream")), \
         patch.object(pgvector_tool, "get_pg_client", return_value=mock_pg), \
         patch.object(pgvector_tool, "get_embedding_service",
                      return_value=mock_embed):
        async for mode, payload in graph.astream(
                state, config, stream_mode=["updates"]):
            if isinstance(payload, dict):
                for node_name, node_output in payload.items():
                    node_runs[node_name] = node_runs.get(node_name, 0) + 1
                    if isinstance(node_output, dict):
                        final_state.update(node_output)
    return {"node_runs": node_runs, "retriever_prompts": retriever_prompts,
            "synth_prompts": synth_prompts, "final_state": final_state}


def _pg_ok():
    return AsyncMock(return_value=[{
        "content": "Cat-cow.", "chunk_index": 0, "similarity": 0.9,
        "source_type": "exercise_db", "title": "Back",
    }])


@pytest.mark.asyncio
async def test_no_tool_then_tool():
    res = await _run_graph([AIMessage(content=""), _kb_call()], _pg_ok())
    assert res["node_runs"].get("retriever_agent", 0) == 2
    assert res["node_runs"].get("tools", 0) == 1
    assert res["node_runs"].get("synthesizer", 0) == 1
    assert len(res["retriever_prompts"]) == 2
    assert "## Second attempt" not in res["retriever_prompts"][0]
    assert "## Second attempt" in res["retriever_prompts"][1]
    assert "called no tool" in res["retriever_prompts"][1]


@pytest.mark.asyncio
async def test_tool_raises_then_succeeds():
    pg_fetch = AsyncMock(side_effect=[
        RuntimeError("boom"),
        [{
            "content": "Cat-cow.", "chunk_index": 0, "similarity": 0.9,
            "source_type": "exercise_db", "title": "Back",
        }],
    ])
    res = await _run_graph([_kb_call("tc1"), _kb_call("tc2")], pg_fetch)
    assert res["node_runs"].get("retriever_agent", 0) == 2
    assert res["node_runs"].get("tools", 0) == 2
    assert len(res["retriever_prompts"]) == 2
    assert "These tool calls failed: kb_search." in res["retriever_prompts"][1]
    assert res["synth_prompts"] and "Cat-cow." in res["synth_prompts"][0]
    assert "RuntimeError" not in res["synth_prompts"][0]


@pytest.mark.asyncio
async def test_tool_raises_twice_still_answers():
    pg_fetch = AsyncMock(side_effect=RuntimeError("boom"))
    res = await _run_graph([_kb_call("tc1"), _kb_call("tc2")], pg_fetch)
    assert res["final_state"].get("final_answer")
    assert res["node_runs"].get("retriever_agent", 0) == 2
    assert res["node_runs"].get("synthesizer", 0) == 1


@pytest.mark.asyncio
async def test_no_tool_twice_goes_on():
    res = await _run_graph([AIMessage(content=""), AIMessage(content="")],
                           _pg_ok())
    assert res["node_runs"].get("retriever_agent", 0) == 2
    assert res["node_runs"].get("tools", 0) == 0
    assert res["node_runs"].get("synthesizer", 0) == 1


@pytest.mark.asyncio
async def test_empty_result_no_second_round():
    pg_fetch = AsyncMock(return_value=[])
    res = await _run_graph([_kb_call()], pg_fetch)
    assert res["node_runs"].get("retriever_agent", 0) == 1


@pytest.mark.asyncio
async def test_untagged_turn_is_checked():
    res = await _run_graph([AIMessage(content=""), _kb_call()], _pg_ok())
    assert res["node_runs"].get("retriever_agent", 0) == 2
