"""A scripted turn through the real loop graph with a plugin tool.

The production path: `load_context` prepares the plugins (store, cached
definitions, MCP listing), `build_turn_tools` offers the tool, the gate and the
tools node run the call, `normalize` runs the guardrail `tool_result` stage, and
the persona node closes the MCP session. Only the model and the memory/vision
nodes are fakes; the MCP server is in-process.
"""

from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.graph import build_loop_graph
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.limits import new_turn
from langgraph_agents.loop.mcp_client import McpClient, definitions_sha256
from langgraph_agents.loop.plugins import runtime
from langgraph_agents.loop.plugins.manifest import Manifest
from langgraph_agents.loop.plugins.store import MemoryPluginStore, set_store as set_plugin_store
from langgraph_agents.loop.rate import MemoryRateCounter, set_store as set_rate_store
from tests.langgraph_agents.loop.fakes import AgentReply, FakeAgentModel
from tests.langgraph_agents.loop.mcp_fakes import SERVER_HOST, SERVER_URL, ZERO_WIDTH, Fake

SECRET = "knee-pain-protocol-7731"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    runtime.reset_runtime()
    set_plugin_store(MemoryPluginStore())
    set_rate_store(MemoryRateCounter())
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: SimpleNamespace(level="routine"))
    yield
    runtime.reset_runtime()
    set_plugin_store(None)


def _manifest(pin: str) -> Manifest:
    return Manifest.model_validate({
        "id": "fake", "name": "Fake", "version": "1", "publisher": "tests",
        "mcp_server": {"url": SERVER_URL, "allowed_hosts": [SERVER_HOST]},
        "tools": {"echo": {"egress": True, "risk": "read"}},
        "reviewed": {"by": "test", "tool_definitions_sha256": pin},
        "enabled_by_default": True,
    })


async def _pin() -> str:
    fake = Fake()
    async with fake.running():
        c = McpClient(SERVER_URL, [SERVER_HOST], policy=AddressPolicy.for_tests(),
                      transport=httpx.ASGITransport(app=fake.app))
        try:
            return definitions_sha256(await c.list_tools())
        finally:
            await c.close()


def _script(*replies):
    return FakeAgentModel([AgentReply(content=r.get("content", ""),
                                      tool_calls=r.get("tool_calls", []))
                           for r in replies])


def _config(turn, agent_model, persona_model, manifest, **extra):
    cfg = {
        "turn_ctx": turn, "agent_model": agent_model, "persona_model": persona_model,
        "user_id": "u1", "session_id": "s1", "query": "tell me about knees",
        "locale": "en", "request_id": "req-plugin-1", "persona_id": "anne",
        "loop_settings": LoopSettings(plugins_disabled=False),
        "plugin_catalog": [manifest], "web_search": False,
    }
    cfg.update(extra)
    return {"configurable": cfg}


async def test_the_agent_calls_a_plugin_tool_and_the_result_arrives_stripped(caplog):
    pin = await _pin()
    manifest = _manifest(pin)
    fake = Fake()
    turn = new_turn()
    agent = _script(
        {"tool_calls": [{"id": "t1", "name": "fake__echo",
                         "arguments": json.dumps({"text": SECRET})}]},
        {"content": "Here is what the plugin said."},
    )
    persona = _script({"content": "Here is what the plugin said."})
    graph = build_loop_graph()

    async with fake.running():
        runtime.set_client_overrides({"fake": {
            "transport": httpx.ASGITransport(app=fake.app),
            "policy": AddressPolicy.for_tests()}})
        with caplog.at_level(logging.INFO):
            state = await graph.ainvoke(
                {"transcript": [], "errors": [], "total_tokens": 0},
                _config(turn, agent, persona, manifest))

    # 1. the model was offered the plugin tool, with the D4 name
    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert "fake__echo" in offered

    # 2. the call reached the server and the result is in the transcript, with
    #    the zero-width character stripped by the tool_result stage
    assert fake.calls == 1
    tool_msgs = [m for m in state["transcript"]
                 if (m.get("role") if isinstance(m, dict) else m.type) == "tool"
                 and (m.get("tool_call_id") if isinstance(m, dict) else m.tool_call_id) == "t1"]
    assert len(tool_msgs) == 1
    content = tool_msgs[0]["content"] if isinstance(tool_msgs[0], dict) else tool_msgs[0].content
    assert content.startswith(SECRET)
    assert ZERO_WIDTH not in content

    # 3. the action record names the plugin tool and is ok
    act = [a for a in state["actions"] if a["tool_call_id"] == "t1"][0]
    assert act["status"] == "ok" and act["tool"] == "fake__echo"

    # 4. one audit line, with a hash of the arguments and no text
    audit = [r for r in caplog.records if r.message == "agent_loop_plugin_call"]
    assert len(audit) == 1
    rec = audit[0]
    assert rec.request_id == "req-plugin-1"
    assert rec.plugin_id == "fake" and rec.tool == "fake__echo" and rec.outcome == "ok"
    assert rec.result_chars == len(SECRET) and rec.duration_ms >= 0
    expected = hashlib.sha256(json.dumps(
        {"text": SECRET}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert rec.args_sha256 == expected
    for r in caplog.records:      # neither the argument nor the result is logged
        if r.name.startswith("langgraph.loop"):
            assert SECRET not in json.dumps(r.__dict__, default=str)

    # 5. the MCP session was closed when the turn ended
    assert turn.plugins.open_sessions == 0


async def test_plugins_are_off_by_default_and_no_store_is_read():
    """With the shipped default (plugins_disabled=True) no plugin tool is offered
    and no store is read."""
    pin = await _pin()
    manifest = _manifest(pin)
    turn = new_turn()
    agent = _script({"content": "Hello."})
    persona = _script({"content": "Hello."})
    graph = build_loop_graph()
    await graph.ainvoke({"transcript": [], "errors": [], "total_tokens": 0},
                        _config(turn, agent, persona, manifest,
                                loop_settings=LoopSettings()))
    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert not any(n.startswith("fake__") for n in offered)
    assert turn.plugins.specs == []


async def test_a_dead_plugin_does_not_fail_the_turn(monkeypatch):
    import asyncio

    class _Hang(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            await asyncio.sleep(3600)

    monkeypatch.setattr(runtime, "LIST_TIMEOUT_S", 0.2)
    manifest = _manifest("x")
    turn = new_turn()
    agent = _script({"content": "No plugin today."})
    persona = _script({"content": "No plugin today."})
    runtime.set_client_overrides({"fake": {"transport": _Hang(),
                                           "policy": AddressPolicy.for_tests()}})
    state = await build_loop_graph().ainvoke(
        {"transcript": [], "errors": [], "total_tokens": 0},
        _config(turn, agent, persona, manifest))

    assert "No plugin today." in state["final_answer"]
    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert "fake__echo" not in offered
    # the trace says which plugin was left out
    assert state["loop_trace"][-1]["plugins_unavailable"] == ["fake"]
