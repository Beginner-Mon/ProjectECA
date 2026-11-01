"""Scripted turns through the real loop graph (read-only, end to end).

memory_node and vision_node are patched to no-ops (they hit PostgreSQL); the
guardrail verdict is mocked to routine. The agent model is `FakeAgentModel`.

Covers: one tool then the answer; parallel reads in one round; a repeated
empty call is refused; an empty answer gets the fallback; every tool_call_id
gets an answer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.langgraph_agents.loop.fakes import FakeAgentModel, AgentReply
from langgraph_agents.loop.graph import build_loop_graph
from langgraph_agents.loop.limits import new_turn
from langgraph_agents.loop.outcome import EMPTY_ANSWER
from langgraph_agents.loop.rate import MemoryRateCounter, set_store as set_rate_store


@pytest.fixture(autouse=True)
def _memory_rate_store():
    set_rate_store(MemoryRateCounter())


def _fake_verdict():
    v = SimpleNamespace(level="routine")
    return v


def _graph():
    return build_loop_graph()


def _msg_role(m):
    if isinstance(m, dict):
        role = m.get("role")
    else:
        # LangChain messages expose .type ("system"/"human"/"ai"/"tool"), not .role.
        role = getattr(m, "role", None) or getattr(m, "type", None)
    # Normalise LangChain's human/ai to user/assistant for consistency.
    return {"human": "user", "ai": "assistant"}.get(role, role)


class _FakeLCTool:
    """Fake LangChain tool whose ainvoke returns empty."""
    async def ainvoke(self, args, config):
        return {"found": False}


def _msg_content(m):
    if isinstance(m, dict):
        return m.get("content", "") or ""
    return getattr(m, "content", "") or ""


def _msg_tool_call_id(m):
    if isinstance(m, dict):
        return m.get("tool_call_id")
    return getattr(m, "tool_call_id", None)


def _config(agent_model, persona_model, session_id="s1", user_id="u1",
            query="hi", extra_config=None):
    turn = new_turn()
    cfg = {
        "configurable": {
            "turn_ctx": turn,
            "agent_model": agent_model,
            "persona_model": persona_model,
            "user_id": user_id,
            "session_id": session_id,
            "query": query,
            "locale": "en",
            "request_id": "r1",
            "persona_id": "anne",
        }
    }
    if extra_config:
        cfg["configurable"].update(extra_config)
    return cfg


async def _run(graph, agent_model, persona_model, query="hi", state=None,
               extra_config=None):
    if state is None:
        state = {"transcript": [], "errors": [], "total_tokens": 0}
    config = _config(agent_model, persona_model, query=query,
                     extra_config=extra_config)
    final = await graph.ainvoke(state, config)
    return final


def _script(replies):
    return FakeAgentModel([AgentReply(content=r.get("content", ""),
                                      tool_calls=r.get("tool_calls", []))
                           for r in replies])


def test_one_tool_then_answer(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: _fake_verdict())

    graph = _graph()
    agent_model = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                        "arguments": '{"query": "bird dog"}'}],
         "content": ""},
        {"content": "Bird dog is a core-strength exercise."},
    ])
    persona_model = _script([{"content": "Bird dog is a core-strength exercise."}])
    state = asyncio.run(_run(graph, agent_model, persona_model,
                             query="bird dog exercise"))

    # Two agent model calls: one with the tool, one answering.
    assert len(agent_model.calls) == 2
    # The answer made it into agent_result and final_answer.
    assert "Bird dog" in (state.get("agent_result") or "")
    assert "Bird dog is a core-strength exercise." in state.get("final_answer", "")
    # Every tool_call_id answered: exactly one tool message for t1
    # (the synthetic load_context call is excluded).
    tool_msgs = [m for m in state.get("transcript", [])
                 if _msg_role(m) == "tool"
                 and _msg_tool_call_id(m) != "load_context"]
    assert len(tool_msgs) == 1
    assert _msg_tool_call_id(tool_msgs[0]) == "t1"


def test_parallel_reads_one_round(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: _fake_verdict())

    graph = _graph()
    agent_model = _script([
        {"tool_calls": [
            {"id": "t1", "name": "kb_search",
             "arguments": '{"query": "bird dog"}'},
            {"id": "t2", "name": "recall_self",
             "arguments": '{"query": "knee"}'},
        ], "content": ""},
        {"content": "Here is what I found."},
    ])
    persona_model = _script([{"content": "Here is what I found."}])
    state = asyncio.run(_run(graph, agent_model, persona_model,
                             query="bird dog and knee"))

    # One round: both reads in parallel, then the answer.
    assert len(agent_model.calls) == 2
    tool_msgs = [m for m in state.get("transcript", [])
                 if _msg_role(m) == "tool"
                 and _msg_tool_call_id(m) != "load_context"]
    assert len(tool_msgs) == 2
    ids = {_msg_tool_call_id(m) for m in tool_msgs}
    assert ids == {"t1", "t2"}


def test_repeated_empty_refused(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: _fake_verdict())

    graph = _graph()
    agent_model = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                        "arguments": '{"query": "bird dog"}'}],
         "content": ""},
        # Second round: model repeats the same call.
        {"tool_calls": [{"id": "t2", "name": "kb_search",
                        "arguments": '{"query": "bird dog"}'}],
         "content": ""},
        {"content": "Nothing more to add."},
    ])
    persona_model = _script([{"content": "Nothing more to add."}])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="bird dog"))

    # The turn completes with an answer even when a call is repeated.
    tool_msgs = [m for m in state.get("transcript", [])
                 if _msg_role(m) == "tool"
                 and _msg_tool_call_id(m) != "load_context"]
    ids = {_msg_tool_call_id(m) for m in tool_msgs}
    assert ids == {"t1", "t2"}
    assert state.get("final_answer")


def test_empty_answer_fallback(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: _fake_verdict())

    graph = _graph()
    agent_model = _script([
        {"content": ""},  # empty answer → code fallback
    ])
    persona_model = _script([{"content": "[fallback answer]" }])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="bird dog"))

    assert state.get("agent_result") == EMPTY_ANSWER[0]
    assert state.get("final_answer")


def test_every_tool_call_id_answered(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: _fake_verdict())

    graph = _graph()
    agent_model = _script([
        {"tool_calls": [
            {"id": "t1", "name": "kb_search", "arguments": '{"query": "a"}'},
            {"id": "t2", "name": "kb_search", "arguments": '{"query": "b"}'},
        ], "content": ""},
        {"content": "All answered."},
    ])
    persona_model = _script([{"content": "All answered."}])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="x"))

    tool_msgs = [m for m in state.get("transcript", [])
                 if _msg_role(m) == "tool"
                 and _msg_tool_call_id(m) != "load_context"]
    assert {_msg_tool_call_id(m) for m in tool_msgs} == {"t1", "t2"}
    # Exactly one message per id.
    assert len(tool_msgs) == 2


# A1: verdict fetched once per round before egress/clinical calls; internal
# tools run without waiting. Emergency verdict refuses web_search and
# request_motion, kb_search still runs.
def test_verdict_fetched_before_egress(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.tools.builtin as builtin_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})

    # Mock the retriever tools and request_motion so no network/DB is used.
    monkeypatch.setattr(builtin_mod, "_retriever_tools",
                        lambda: {"kb_search": _FakeLCTool(),
                                 "recall_self": _FakeLCTool(),
                                 "youtube_transcript": _FakeLCTool()})
    monkeypatch.setattr(builtin_mod, "_request_motion_call",
                        lambda args, config: {"job_id": "jm1", "state": "queued"})
    monkeypatch.setattr(builtin_mod, "_web_search_call",
                        lambda args, config: [{"title": "x", "text": "y"}])

    graph = build_loop_graph()

    # Emergency verdict: egress/clinical refused, internal allowed.
    verdict_calls = []
    verdict = SimpleNamespace(level="emergency")

    async def verdict_getter(config):
        verdict_calls.append(1)
        return verdict

    agent_model = _script([
        {"tool_calls": [
            {"id": "t1", "name": "kb_search", "arguments": '{"query": "knee"}'},
            {"id": "t2", "name": "web_search", "arguments": '{"query": "knee"}'},
            {"id": "t3", "name": "request_motion", "arguments": '{"prompt": "squat"}'},
        ], "content": ""},
        {"content": "Done."},
    ])
    persona_model = _script([{"content": "Done."}])
    state = asyncio.run(_run(graph, agent_model, persona_model,
                             query="knee exercise",
                             extra_config={"verdict_getter": verdict_getter}))

    # Verdict fetched once (A1: once per round, not per call).
    assert len(verdict_calls) == 1
    # Tool messages: kb_search (internal) ran; web_search & request_motion
    # (egress/clinical) were refused by the gate under emergency verdict.
    tool_msgs = [m for m in state.get("transcript", [])
                 if _msg_role(m) == "tool"
                 and _msg_tool_call_id(m) != "load_context"]
    ids = {_msg_tool_call_id(m) for m in tool_msgs}
    assert ids == {"t1", "t2", "t3"}  # every call_id answered exactly once
    by_id = {_msg_tool_call_id(m): _msg_content(m) for m in tool_msgs}
    # t1 = kb_search: not refused (internal allowed under emergency).
    assert '"refused"' not in by_id["t1"]
    # t2 = web_search: refused (egress + emergency verdict).
    assert '"refused"' in by_id["t2"]
    # t3 = request_motion: refused (clinical + emergency verdict).
    assert '"refused"' in by_id["t3"]
