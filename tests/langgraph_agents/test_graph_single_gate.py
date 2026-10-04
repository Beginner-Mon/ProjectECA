"""Kiểm 1 sau tools (grader-contract T2): tools lỗi → quay lại retriever_agent.

Trước T2, sau tools graph không bao giờ quay lại retriever_agent. Từ T2,
tool lỗi được quay lại retriever_agent tối đa một lần (route_after_tools);
kết quả rỗng không phải lỗi (D24) nên vẫn đi tiếp.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage


def _edge_pairs(graph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in graph.get_graph().edges}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tools_can_route_back_to_retriever():
    from langgraph_agents.graph import build_graph_async

    graph = await build_graph_async()
    assert ("tools", "retriever_agent") in _edge_pairs(graph)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tools_routes_to_kimodo_or_synthesizer():
    from langgraph_agents.graph import build_graph_async

    graph = await build_graph_async()
    targets = {t for s, t in _edge_pairs(graph) if s == "tools"}
    assert targets <= {"retriever_agent", "kimodo", "synthesizer", "error_handler"}
    assert "synthesizer" in targets


@pytest.mark.unit
@pytest.mark.asyncio
async def test_grader_retry_path_intact():
    """Đường retry của grader (grader -> retriever_agent) giữ nguyên: thay đổi
    T7 chỉ bỏ lượt agent thừa sau tools, không bỏ retry."""
    from langgraph_agents.graph import build_graph_async

    graph = await build_graph_async()
    assert ("grader", "retriever_agent") in _edge_pairs(graph)


# ── Đếm số lần chạy retriever_agent trên graph thật (LLM + pg giả) ──────

def _mock_planner(plan):
    mock_struct = MagicMock()
    mock_struct.ainvoke = AsyncMock(return_value=plan)
    mock_llm = MagicMock()
    mock_llm.with_structured_output = MagicMock(return_value=mock_struct)
    return mock_llm


def _mock_retriever_llm(counter):
    ai = AIMessage(content="", tool_calls=[
        {"name": "kb_search", "args": {"query": "dau lung"}, "id": "tc1",
         "type": "tool_call"},
    ])
    mock_bound = MagicMock()
    mock_bound.ainvoke = AsyncMock(return_value=ai)

    def get_model(role):
        counter[role] = counter.get(role, 0) + 1
        m = MagicMock()
        m.bind_tools = MagicMock(return_value=mock_bound)
        return m

    return get_model


async def _run_turn(needs_retrieval: bool) -> dict:
    """Chạy một lượt qua graph thật; planner tags [] để grader bỏ qua."""
    import uuid

    from langgraph_agents.graph import build_graph_async
    from langgraph_agents.nodes.planner import PlanOutput
    from langgraph_agents.tools import pgvector_tool

    plan = PlanOutput(required_outputs=[], resolved_query="dau lung",
                      needs_retrieval=needs_retrieval,
                      needs_clarification=False)
    counter: dict = {}

    mock_pg = AsyncMock()
    mock_pg.connect = AsyncMock()
    mock_pg.fetch = AsyncMock(return_value=[{
        "content": "Cat-cow.", "chunk_index": 0, "similarity": 0.9,
        "source_type": "exercise_db", "title": "Back",
    }])
    mock_embed = MagicMock()
    mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 384)

    synth_llm = MagicMock()
    synth_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content="ok"))

    node_runs: dict = {}
    graph = await build_graph_async()
    state = {"messages": [], "errors": [], "retry_count": 0, "total_tokens": 0}
    config = {"configurable": {
        "user_id": "u1", "session_id": str(uuid.uuid4()), "query": "dau lung",
        "persona_id": "anne", "request_id": "r-t7", "web_search": False,
        "locale": "en",
    }}
    with patch("langgraph_agents.nodes.planner.get_chat_model",
               return_value=_mock_planner(plan)), \
         patch("langgraph_agents.nodes.retriever_agent.get_chat_model",
               side_effect=_mock_retriever_llm(counter)), \
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
                for node_name in payload:
                    node_runs[node_name] = node_runs.get(node_name, 0) + 1
    return {"node_runs": node_runs, "retriever_llm_calls": counter.get("retriever", 0)}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_single_tool_round_runs_retriever_once():
    res = await _run_turn(needs_retrieval=True)
    assert res["node_runs"].get("retriever_agent", 0) == 1
    assert res["retriever_llm_calls"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_chat_turn_runs_retriever_zero_times():
    res = await _run_turn(needs_retrieval=False)
    assert res["node_runs"].get("retriever_agent", 0) == 0
    assert res["retriever_llm_calls"] == 0
