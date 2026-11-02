"""Tests for `loop.agent`: A8 (reasoning out of content) and A9 (total_tokens
per round), scripted through the real agent node."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests.langgraph_agents.loop.fakes import FakeAgentModel, AgentReply
from langgraph_agents.loop.graph import build_loop_graph
from langgraph_agents.loop.limits import new_turn


class _FakeLCTool:
    """Fake LangChain tool whose ainvoke returns empty."""
    async def ainvoke(self, args, config):
        return {"found": False}


def _msg_role(m):
    if isinstance(m, dict):
        role = m.get("role")
    else:
        role = getattr(m, "role", None) or getattr(m, "type", None)
    return {"human": "user", "ai": "assistant"}.get(role, role)


def _msg_content(m):
    if isinstance(m, dict):
        return m.get("content", "") or ""
    return getattr(m, "content", "") or ""


def _config(agent_model, persona_model, session_id="s1", user_id="u1", query="hi"):
    turn = new_turn()
    return {
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


def _script(replies):
    return FakeAgentModel([AgentReply(content=r.get("content", ""),
                                      tool_calls=r.get("tool_calls", []),
                                      reasoning_field=r.get("reasoning_field"),
                                      reasoning_text=r.get("reasoning_text"),
                                      usage=r.get("usage"))
                           for r in replies])


async def _run(graph, agent_model, persona_model, query="hi"):
    state = {"transcript": [], "errors": [], "total_tokens": 0}
    config = _config(agent_model, persona_model, query=query)
    final = await graph.ainvoke(state, config)
    return final


# A8: reasoning goes into its own field, never into content / agent_result.
def test_reasoning_not_in_content(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.tools.builtin as builtin_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(builtin_mod, "_retriever_tools",
                        lambda: {"kb_search": _FakeLCTool(),
                                 "recall_self": _FakeLCTool(),
                                 "youtube_transcript": _FakeLCTool()})

    graph = build_loop_graph()
    agent_model = _script([
        {"content": "The answer.", "reasoning_field": "reasoning",
         "reasoning_text": "chain of thought here",
         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
    ])
    persona_model = _script([{"content": "The answer."}])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="what is x"))

    # Transcript assistant message: content has no reasoning; reasoning key holds it.
    assistants = [m for m in state.get("transcript", [])
                  if _msg_role(m) == "assistant" and _msg_content(m) == "The answer."]
    assert assistants, "assistant message with content 'The answer.' not found"
    msg = assistants[0]
    # Reasoning may be on the dict or in AIMessage.additional_kwargs.
    reasoning = (msg.get("reasoning")
                 if isinstance(msg, dict)
                 else getattr(msg, "additional_kwargs", {}).get("reasoning"))
    assert reasoning == "chain of thought here"
    assert "chain of thought" not in _msg_content(msg)
    # agent_result is content only.
    assert state.get("agent_result") == "The answer."
    assert "chain of thought" not in state.get("agent_result", "")
    # total_tokens charged from this round.
    assert state.get("total_tokens") == 15


# A9: two rounds, usage summed into total_tokens.
def test_total_tokens_summed_across_rounds(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.tools.builtin as builtin_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(builtin_mod, "_retriever_tools",
                        lambda: {"kb_search": _FakeLCTool(),
                                 "recall_self": _FakeLCTool(),
                                 "youtube_transcript": _FakeLCTool()})

    graph = build_loop_graph()
    agent_model = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                         "arguments": '{"query": "x"}'}],
         "content": "",
         "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
        {"content": "Done.", "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}},
    ])
    persona_model = _script([{"content": "Done."}])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="x"))

    # Two agent calls: total_tokens is the model usages only (14 + 11 = 25),
    # persona usage is 0 here; tool-message text is charged to the NEXT
    # agent round's prompt_tokens, never a second time (S2).
    assert len(agent_model.calls) == 2
    assert state.get("total_tokens") == 25


# S2: tool results are sent to the model in the next agent round and charged
# there as prompt_tokens. tools_node must not charge them a second time:
# only nodes that call a model (agent, persona, reused memory/vision)
# return total_tokens.
def test_tool_results_not_double_charged(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.tools.builtin as builtin_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(builtin_mod, "_retriever_tools",
                        lambda: {"kb_search": _FakeLCTool(),
                                 "recall_self": _FakeLCTool(),
                                 "youtube_transcript": _FakeLCTool()})

    graph = build_loop_graph()
    agent_model = _script([
        {"tool_calls": [{"id": "t1", "name": "kb_search",
                         "arguments": '{"query": "x"}'}],
         "content": "",
         "usage": {"prompt_tokens": 60, "completion_tokens": 40,
                   "total_tokens": 100}},
        {"content": "Done.",
         "usage": {"prompt_tokens": 90, "completion_tokens": 60,
                   "total_tokens": 150}},
    ])
    persona_model = _script([{"content": "Done.",
                              "usage": {"prompt_tokens": 4,
                                        "completion_tokens": 3,
                                        "total_tokens": 7}}])
    state = asyncio.run(_run(graph, agent_model, persona_model, query="x"))

    assert len(agent_model.calls) == 2
    # 100 (agent round 1) + 150 (agent round 2) + 7 (persona). The tool
    # result text is NOT added again — double charging would exceed this.
    assert state.get("total_tokens") == 250 + 7
