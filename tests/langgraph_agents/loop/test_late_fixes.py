"""Late stage-1 fixes (card stage1-late.md): L1 builtin specs, L2 web_search
offered, L3 `actions` accumulates, L4 the fake speaks the wire format."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.langgraph_agents.loop.fakes import (
    AgentReply, FakeAgentModel, FakeLCTool, assert_wire_format,
)
from langgraph_agents.loop.graph import build_loop_graph
from langgraph_agents.loop.limits import new_turn
from langgraph_agents.loop.tools.registry import build_turn_tools


@pytest.fixture(autouse=True)
def _memory_rate_store():
    from langgraph_agents.loop.rate import MemoryRateCounter, set_store
    set_store(MemoryRateCounter())
    yield


def _names(ctx) -> set[str]:
    return {s.name for s in build_turn_tools(ctx)}


# ── L1 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["kb_search", "recall_self", "youtube_transcript"])
def test_wrapped_builtin_describes_itself(name):
    """name, description and parameters are the LangChain tool's own."""
    import langgraph_agents.loop.tools.builtin as builtin
    tool = builtin._retriever_tools()[name]
    spec = getattr(builtin, f"{name}_spec")()
    assert spec.name == tool.name
    assert spec.description == tool.description
    schema = tool.get_input_jsonschema()
    assert spec.parameters["properties"] == schema["properties"]
    assert spec.parameters["required"] == schema["required"]


def test_youtube_parameter_is_url_as_the_tool_declares():
    import langgraph_agents.loop.tools.builtin as builtin
    assert list(builtin.youtube_transcript_spec().parameters["properties"]) == ["url"]


def test_recall_self_is_not_personal_and_no_builtin_is():
    import langgraph_agents.loop.tools.builtin as builtin
    assert builtin.recall_self_spec().data_class == "none"
    specs = build_turn_tools({"persona_id": "anne", "web_search": True})
    assert [s.name for s in specs if s.data_class == "personal"] == []


def test_recall_self_offered_only_when_persona_has_a_sheet():
    from langgraph_agents.nodes._persona_loader import _persona_has_sheet
    assert _persona_has_sheet("anne") and not _persona_has_sheet("bronya")
    assert "recall_self" in _names({"persona_id": "anne"})
    assert "recall_self" not in _names({"persona_id": "bronya"})
    assert "recall_self" not in _names({})


# ── graph helpers ────────────────────────────────────────────────────────

def _script(replies):
    return FakeAgentModel([AgentReply(content=r.get("content", ""),
                                      tool_calls=r.get("tool_calls", []))
                           for r in replies])


def _patch_graph(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.tools.builtin as builtin_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(builtin_mod, "_retriever_tools", lambda: {
        n: FakeLCTool(n) for n in ("kb_search", "recall_self", "youtube_transcript")})
    monkeypatch.setattr(builtin_mod, "_web_search_call",
                        lambda args, config: [{"title": "x", "text": "y"}])


def _run(agent_model, persona_model, **extra):
    cfg = {"turn_ctx": new_turn(), "agent_model": agent_model,
           "persona_model": persona_model, "user_id": "u1", "session_id": "s1",
           "query": "q", "locale": "en", "request_id": "r1",
           "persona_id": "anne",
           "verdict_getter": lambda config: SimpleNamespace(level="routine")}
    cfg.update(extra)
    state = {"transcript": [], "errors": [], "total_tokens": 0}
    return asyncio.run(build_loop_graph().ainvoke(state, {"configurable": cfg}))


def _tool_msgs(state):
    out = {}
    for m in state["transcript"]:
        role = getattr(m, "type", None) or m.get("role")
        tid = getattr(m, "tool_call_id", None) or (
            m.get("tool_call_id") if isinstance(m, dict) else None)
        if role == "tool" and tid != "load_context":
            out[tid] = getattr(m, "content", None) or m.get("content", "")
    return out


def test_recall_self_then_web_search_is_allowed(monkeypatch):
    """No builtin is personal, so taint rule 1 does not refuse the egress call."""
    _patch_graph(monkeypatch)
    agent = _script([
        {"tool_calls": [{"id": "t1", "name": "recall_self",
                         "arguments": '{"query": "hair"}'}]},
        {"tool_calls": [{"id": "t2", "name": "web_search",
                         "arguments": '{"query": "news"}'}]},
        {"content": "Done."},
    ])
    state = _run(agent, _script([{"content": "Done."}]), web_search=True)
    msgs = _tool_msgs(state)
    assert set(msgs) == {"t1", "t2"}
    assert '"refused"' not in msgs["t2"]


# ── L3 ───────────────────────────────────────────────────────────────────

def test_actions_accumulate_over_rounds(monkeypatch):
    _patch_graph(monkeypatch)
    agent = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                         "arguments": '{"query": "a"}'}]},
        {"tool_calls": [{"id": "t2", "name": "kb_search",
                         "arguments": '{"query": "b"}'}]},
        {"content": "Done."},
    ])
    state = _run(agent, _script([{"content": "Done."}]))
    assert [a["tool_call_id"] for a in state["actions"]] == ["t1", "t2"]
    assert state["loop_trace"][-1]["actions"] == 2


def test_merge_actions_skips_repeats():
    from langgraph_agents.loop.state import merge_actions
    a = {"tool_call_id": "t1", "tool": "x"}
    b = {"tool_call_id": "t2", "tool": "y"}
    assert merge_actions([a], [a, b]) == [a, b]
    assert merge_actions(None, [a]) == [a]


# ── L4 ───────────────────────────────────────────────────────────────────

def test_wire_check_rejects_bad_transcripts():
    with pytest.raises(AssertionError):
        assert_wire_format(
            [{"role": "tool", "content": "x", "tool_call_id": "never-called"}])
    with pytest.raises(AssertionError):
        assert_wire_format([{"role": "ai", "content": "x"}])


def test_fake_model_checks_round_two_of_a_real_graph_turn(monkeypatch):
    """Round 2 carries LangChain messages; the fake converts and validates."""
    _patch_graph(monkeypatch)
    agent = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                         "arguments": '{"query": "a"}'}]},
        {"content": "Done."},
    ])
    _run(agent, _script([{"content": "Done."}]))
    assert len(agent.calls) == 2
    kinds = {type(m).__name__ for m in agent.calls[1]["messages"]}
    assert kinds - {"dict"}, "round 2 should hold message objects, not dicts"
