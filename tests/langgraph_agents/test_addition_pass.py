"""Viết thêm phần thiếu (grader-contract T6): retry chỉ nối phần thiếu."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

pytestmark = pytest.mark.unit


def _mock_planner(plan):
    mock_struct = MagicMock()
    mock_struct.ainvoke = AsyncMock(return_value=plan)
    mock_llm = MagicMock()
    mock_llm.with_structured_output = MagicMock(return_value=mock_struct)
    return mock_llm


class _SynthFake:
    """LLM synthesizer giả: mỗi lần gọi dùng một kịch bản (list chunk hoặc lỗi).

    Lỗi đặt giữa list chunk: stream các chunk trước nó rồi mới ném.
    """

    def __init__(self, scripts):
        self.scripts = scripts
        self.calls: list = []

    def _script(self):
        return self.scripts[min(len(self.calls) - 1, len(self.scripts) - 1)]

    async def astream(self, msgs):
        self.calls.append(msgs)
        script = self._script()
        if isinstance(script, Exception):
            raise script
        for p in script:
            if isinstance(p, Exception):
                raise p
            yield AIMessageChunk(content=p)

    async def ainvoke(self, msgs):
        self.calls.append(msgs)
        script = self._script()
        if isinstance(script, Exception):
            raise script
        return AIMessage(content="".join(script))


def _judge_fake(amount_in_reply=False):
    from langgraph_agents.nodes import grader as grader_mod

    mock_llm = MagicMock()
    structured = MagicMock()
    parsed = SimpleNamespace(items=[
        SimpleNamespace(item="exercise_protocol.amount",
                        in_reply=amount_in_reply, in_evidence=True),
        SimpleNamespace(item="exercise_protocol.frequency",
                        in_reply=True, in_evidence=True),
    ])
    structured.ainvoke = AsyncMock(return_value={"parsed": parsed})
    mock_llm.with_structured_output = MagicMock(return_value=structured)
    return patch.object(grader_mod, "get_chat_model", return_value=mock_llm)


async def _run_turn(synth_scripts):
    """Graph thật; planner tags [red_flag, protocol, scope]; pg một hàng."""
    import uuid

    from langgraph_agents.graph import build_graph_async
    from langgraph_agents.nodes.planner import PlanOutput
    from langgraph_agents.tools import pgvector_tool

    plan = PlanOutput(
        required_outputs=["red_flag_screen", "exercise_protocol",
                          "scope_disclaimer"],
        resolved_query="elbow plank", needs_retrieval=True,
        needs_clarification=False)

    synth = _SynthFake(synth_scripts)
    sent: list = []

    mock_pg = AsyncMock()
    mock_pg.connect = AsyncMock()
    mock_pg.fetch = AsyncMock(return_value=[{
        "content": "Hold 30 seconds, 3 sets.", "chunk_index": 0,
        "similarity": 0.9, "source_type": "exercise_db", "title": "Elbow plank",
    }])
    mock_embed = MagicMock()
    mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 384)

    kb_ai = AIMessage(content="", tool_calls=[
        {"name": "kb_search", "args": {"query": "elbow plank"}, "id": "tc1",
         "type": "tool_call"},
    ])
    mock_bound = MagicMock()
    mock_bound.ainvoke = AsyncMock(return_value=kb_ai)

    def _get_retriever(role):
        m = MagicMock()
        m.bind_tools = MagicMock(return_value=mock_bound)
        return m

    node_runs: dict = {}
    final_state: dict = {}
    judge_calls = {"n": 0}
    graph = await build_graph_async()
    state = {"messages": [], "errors": [], "retry_count": 0, "total_tokens": 0}
    config = {"configurable": {
        "user_id": "u1", "session_id": str(uuid.uuid4()),
        "query": "elbow plank", "persona_id": "anne", "request_id": "r-t6",
        "web_search": False, "locale": "vi",
    }}
    with patch("langgraph_agents.nodes.planner.get_chat_model",
               return_value=_mock_planner(plan)), \
         patch("langgraph_agents.nodes.retriever_agent.get_chat_model",
               side_effect=_get_retriever), \
         patch("langgraph_agents.nodes.synthesizer.get_chat_model",
               return_value=synth), \
         patch("langgraph_agents.nodes.synthesizer.get_stream_writer",
               return_value=sent.append), \
         patch("langgraph_agents.nodes.grader.get_stream_writer",
               return_value=sent.append), \
         _judge_fake(), \
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
    return {"node_runs": node_runs, "final_state": final_state,
            "synth_calls": synth.calls, "sent": sent}


def _opening():
    from langgraph_agents.tag_contract import get_safety_text
    return get_safety_text("red_flag_screen", "anne", "vi")


def _disclaimer():
    from langgraph_agents.tag_contract import get_safety_text
    return get_safety_text("scope_disclaimer", "anne", "vi")


def _referral():
    from langgraph_agents.tag_contract import get_safety_text
    return get_safety_text("referral_advice", "anne", "vi")


_DRAFT = "Elbow plank: chống khuỷu, giữ thẳng lưng."
_ADD = "Thư viện ghi 3 hiệp, giữ 30 giây."


@pytest.mark.asyncio
async def test_addition_is_appended():
    res = await _run_turn([[_DRAFT], [_ADD]])
    assert res["node_runs"].get("retriever_agent", 0) == 1
    assert res["node_runs"].get("synthesizer", 0) == 2
    assert res["node_runs"].get("grader", 0) == 2
    final = res["final_state"].get("final_answer", "")
    opening = _opening()
    # Planner thêm referral_advice khi có red_flag_screen (D33).
    expected = (opening + "\n\n" + _DRAFT + "\n\n" + _ADD
                + "\n\n" + _referral() + "\n\n" + _disclaimer())
    assert final == expected
    assert final.count(opening) == 1
    streamed = "".join(p["content"] for p in res["sent"] if "content" in p)
    assert streamed == final


@pytest.mark.asyncio
async def test_second_prompt_has_the_block():
    res = await _run_turn([[_DRAFT], [_ADD]])
    assert len(res["synth_calls"]) == 2
    first_text = "\n".join(str(m.content) for m in res["synth_calls"][0])
    assert "## Add what is missing" not in first_text
    second = res["synth_calls"][1]
    ai_text = "\n".join(str(m.content) for m in second
                        if isinstance(m, AIMessage))
    assert _DRAFT in ai_text
    last_system = second[-1].content
    assert "## Add what is missing" in last_system
    assert "- how many sets or reps, or how long to hold" in last_system


@pytest.mark.asyncio
async def test_addition_failure_keeps_answer():
    res = await _run_turn([[_DRAFT], RuntimeError("synth down")])
    final = res["final_state"].get("final_answer", "")
    opening = _opening()
    assert final == (opening + "\n\n" + _DRAFT + "\n\n" + _referral()
                     + "\n\n" + _disclaimer())
    assert "error_unavailable" not in final
    errors = res["final_state"].get("errors", []) or []
    assert not [e for e in errors if e.get("severity") == "critical"]
    assert res["final_state"].get("grader_result") == "pass_with_warning"


@pytest.mark.asyncio
async def test_addition_cut_midway_keeps_what_was_shown():
    part = "Thư viện ghi 3 hiệp"
    res = await _run_turn([[_DRAFT], [part, RuntimeError("connection reset")]])
    final = res["final_state"].get("final_answer", "")
    streamed = "".join(p["content"] for p in res["sent"] if "content" in p)
    assert streamed == final
    assert final == (_opening() + "\n\n" + _DRAFT + "\n\n" + part
                     + "\n\n" + _referral() + "\n\n" + _disclaimer())
    assert res["final_state"].get("grader_result") == "pass_with_warning"


@pytest.mark.asyncio
async def test_empty_first_body_is_a_normal_rewrite():
    res = await _run_turn([[], ["Xong."]])
    assert len(res["synth_calls"]) == 2
    second_text = "\n".join(str(m.content) for m in res["synth_calls"][1])
    assert "## Add what is missing" not in second_text
    final = res["final_state"].get("final_answer", "")
    assert "Xong." in final
    assert final.count(_opening()) == 1
