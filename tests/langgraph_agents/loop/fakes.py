"""Scripted fakes for the agent-loop test suite.

Stage 0 scaffold (PLAN.md, Part II, stage 0): every loop test runs against
scripted models — no LLM API call is ever made (II.1). `FakeAgentModel` replays
a prepared list of replies, in order, and records each call so tests can assert
on what the agent node sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

try:  # the real reply shape arrives with loop/client.py in stage 1
    from langgraph_agents.loop.client import AgentReply

except ImportError:  # stage 0: client.py does not exist yet
    @dataclass
    class AgentReply:  # type: ignore[no-redef]
        """Placeholder matching the shape planned for `loop/client.py` (II.2)."""

        content: str = ""
        tool_calls: list[dict[str, Any]] = field(default_factory=list)
        reasoning_field: str | None = None
        reasoning_text: str | None = None
        usage: dict[str, Any] | None = None


class FakeLCTool:
    """Fake LangChain tool: describes itself like the real ones, ainvoke
    returns empty. `name` defaults to the tool's registry name."""

    description = "Fake tool description."

    def __init__(self, name: str = "fake") -> None:
        self.name = name

    def get_input_jsonschema(self) -> dict:
        return {"description": "docstring", "title": self.name,
                "type": "object",
                "properties": {"query": {"title": "Query", "type": "string"}},
                "required": ["query"]}

    async def ainvoke(self, args, config):
        return {"found": False}


def assert_wire_format(wire: list[dict[str, Any]]) -> None:
    """The messages are plain OpenAI chat dicts the provider would accept."""
    json.dumps(wire)  # serialisable, or it raises
    ids: set[str] = set()
    for m in wire:
        assert isinstance(m, dict), f"not a dict: {type(m)!r}"
        assert m.get("role") in ("system", "user", "assistant", "tool"), m
        assert isinstance(m.get("content"), (str, list, type(None))), m
        if m["role"] == "assistant":
            for tc in m.get("tool_calls") or []:
                assert tc["type"] == "function", tc
                assert isinstance(tc["id"], str) and tc["id"], tc
                fn = tc["function"]
                assert isinstance(fn["name"], str) and fn["name"], tc
                assert isinstance(fn["arguments"], str), tc
                json.loads(fn["arguments"])
                ids.add(tc["id"])
        if m["role"] == "tool":
            assert isinstance(m.get("tool_call_id"), str), m
            assert m["tool_call_id"] in ids, (
                f"tool message answers unknown call id {m['tool_call_id']!r}")


class ScriptExhausted(RuntimeError):
    """Raised when the loop keeps calling after the script ran out.

    A scripted model has nothing else to give. Raising at once turns a
    non-terminating loop (plan §14, "chắc chắn dừng") into a pytest failure,
    instead of a hang or a hidden LLM call.
    """


class FakeAgentModel:
    """Scripted stand-in for `AgentModel` (`loop/client.py`, stage 1).

    `complete(messages, tools, *, tool_choice="auto")` pops the next reply from
    the script and returns it. Empty replies are legitimate — the loop must
    recognise them and stop; only an *extra* call after the script ran out
    raises.
    """

    def __init__(self, script: list[AgentReply]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | None = "auto",
    ) -> AgentReply:
        from langgraph_agents.loop.client import to_wire_messages

        # Same conversion as the real AgentModel; what the provider would get
        # must be JSON-serialisable OpenAI wire format.
        assert_wire_format(to_wire_messages(list(messages)))
        self.calls.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
        if not self.script:
            raise ScriptExhausted(
                f"FakeAgentModel: script exhausted after {len(self.calls)} call(s)"
            )
        return self.script.pop(0)
