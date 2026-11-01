"""Scripted fakes for the agent-loop test suite.

Stage 0 scaffold (PLAN.md, Part II, stage 0): every loop test runs against
scripted models — no LLM API call is ever made (II.1). `FakeAgentModel` replays
a prepared list of replies, in order, and records each call so tests can assert
on what the agent node sent.
"""

from __future__ import annotations

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
        self.calls.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
        if not self.script:
            raise ScriptExhausted(
                f"FakeAgentModel: script exhausted after {len(self.calls)} call(s)"
            )
        return self.script.pop(0)
