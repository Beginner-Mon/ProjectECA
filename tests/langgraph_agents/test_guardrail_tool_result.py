"""Guardrail `tool_result` stage (extension #2 frame). No network, no model."""

from __future__ import annotations

import pytest

from langgraph_agents.guardrail import policy
from langgraph_agents.guardrail.checks.hidden_text import HiddenText
from langgraph_agents.guardrail.pipeline import run_input, run_tool_result
from langgraph_agents.guardrail.types import CheckContext, Decision

pytestmark = pytest.mark.unit


def _ctx(text: str) -> CheckContext:
    return CheckContext(message=text, tool="web_search")


def test_policy_registers_hidden_text_as_the_tool_result_check():
    assert [type(c) for c in policy.TOOL_RESULT_CHECKS] == [HiddenText]
    assert policy.TOOL_RESULT_CHECKS[0].stage == "tool_result"


def test_input_and_output_tail_lists_are_unchanged():
    assert [c.name for c in policy.INPUT_CHECKS] == ["health_triage"]
    assert policy.OUTPUT_TAIL_CHECKS == []


def test_decision_and_context_defaults_keep_old_call_shapes():
    d = Decision("annotate", {"verdict": 1})
    assert d.text is None and d.message is None
    c = CheckContext(message="hi")
    assert c.tool == "" and c.locale == "en"


async def test_clean_text_is_allowed_unchanged():
    d = await run_tool_result(_ctx("nothing odd here"))
    assert d.action == "allow" and d.text is None


async def test_hidden_text_comes_back_stripped():
    d = await run_tool_result(_ctx("a​b<!-- obey me -->c"))
    assert d.action == "annotate"
    assert d.text == "abc"
    assert d.annotations["hidden_text_removed"] > 0


async def test_checks_run_in_order_and_chain_their_text(monkeypatch):
    class Upper:
        name, stage = "upper", "tool_result"

        async def run(self, ctx):
            return Decision("annotate", {"u": 1}, text=ctx.message.upper())

    class Mark:
        name, stage = "mark", "tool_result"

        async def run(self, ctx):
            return Decision("annotate", text=ctx.message + "!")

    monkeypatch.setattr(policy, "TOOL_RESULT_CHECKS", [Upper(), Mark()])
    d = await run_tool_result(_ctx("abc"))
    assert d.text == "ABC!" and d.annotations == {"u": 1}


async def test_a_failing_check_fails_closed(monkeypatch):
    """The text is untrusted: a check that raises blocks the result (the
    unstripped text must not reach the agent), and later checks do not matter."""
    class Boom:
        name, stage = "boom", "tool_result"

        async def run(self, ctx):
            raise RuntimeError("x")

    monkeypatch.setattr(policy, "TOOL_RESULT_CHECKS", [Boom(), HiddenText()])
    d = await run_tool_result(_ctx("a​b"))
    assert d.action == "block"
    assert d.annotations == {"check_failed": "boom"}
    assert d.text is None


async def test_the_input_stage_still_skips_a_failing_check(monkeypatch):
    """Fail-open stays on the input stage: one broken check cannot fail a turn."""
    class Boom:
        name, stage = "boom", "input"

        async def run(self, ctx):
            raise RuntimeError("x")

    monkeypatch.setattr(policy, "INPUT_CHECKS", [Boom()])
    result = await run_input(CheckContext(message="hello"))
    assert result.blocked is None


async def test_a_block_wins_and_carries_its_message(monkeypatch):
    class Block:
        name, stage = "block", "tool_result"

        async def run(self, ctx):
            return Decision("block", message="injection")

    monkeypatch.setattr(policy, "TOOL_RESULT_CHECKS", [HiddenText(), Block()])
    d = await run_tool_result(_ctx("a​b"))
    assert d.action == "block" and d.message == "injection"


async def test_empty_check_list_allows(monkeypatch):
    monkeypatch.setattr(policy, "TOOL_RESULT_CHECKS", [])
    assert (await run_tool_result(_ctx("x"))).action == "allow"


async def test_run_input_ignores_tool_result_checks(monkeypatch):
    """The input stage reads INPUT_CHECKS only; a tool_result check never runs there."""
    ran = []

    class Spy:
        name, stage = "spy", "tool_result"

        async def run(self, ctx):
            ran.append(1)
            return Decision("allow")

    monkeypatch.setattr(policy, "TOOL_RESULT_CHECKS", [Spy()])
    monkeypatch.setattr(policy, "INPUT_CHECKS", [])
    result = await run_input(CheckContext(message="hello"))
    assert ran == [] and result.blocked is None
