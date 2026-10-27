"""Guardrail v1 — the layer outside the graph.

No network: every model call is replaced by a fake whose only job is to return
the shape the real chain returns (`{"parsed": ..., "raw": ...}`).
"""

from __future__ import annotations

import asyncio

import pytest

from langgraph_agents.guardrail import policy, stream
from langgraph_agents.guardrail.checks.base import Check
from langgraph_agents.guardrail.directive import directive
from langgraph_agents.guardrail.handoff import clear_cache, current_verdict
from langgraph_agents.guardrail.pipeline import run_input
from langgraph_agents.guardrail.triage import TriageOutput, triage
from langgraph_agents.guardrail.types import (
    CheckContext,
    Decision,
    InputResult,
    Verdict,
)
from langgraph_agents import llm as llm_module

pytestmark = pytest.mark.unit


# ── Fakes ────────────────────────────────────────────────────────────────


class _Structured:
    def __init__(self, *, parsed=None, exc: Exception | None = None):
        self._parsed = parsed
        self._exc = exc

    async def ainvoke(self, _messages):
        if self._exc is not None:
            raise self._exc
        return {"parsed": self._parsed, "raw": None}


class _Model:
    def __init__(self, structured):
        self._structured = structured

    def with_structured_output(self, *_args, **_kwargs):
        return self._structured


def _fake_model(monkeypatch, *, parsed=None, exc: Exception | None = None):
    model = _Model(_Structured(parsed=parsed, exc=exc))
    monkeypatch.setattr(
        "langgraph_agents.guardrail.triage.get_chat_model", lambda role: model)


def _verdict(level="routine", *, out_of_scope=False, health_topic=False,
             source="llm", elapsed_ms=10) -> Verdict:
    return Verdict(level=level, out_of_scope=out_of_scope,
                   health_topic=health_topic, source=source,
                   elapsed_ms=elapsed_ms)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_cache()
    yield
    clear_cache()


# ── 1. JSON parse for all three levels ───────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("EMERGENCY", "emergency"),
    ("SOON", "soon"),
    ("ROUTINE", "routine"),
    ("Soon", "soon"),
    (" routine ", "routine"),
])
async def test_triage_parses_every_level(monkeypatch, raw, expected):
    _fake_model(monkeypatch, parsed=TriageOutput(
        level=raw, out_of_scope=False, health_topic=True))
    verdict = await triage("đau đầu gối")
    assert verdict.level == expected
    assert verdict.source == "llm"
    assert verdict.health_topic is True
    assert verdict.out_of_scope is False


async def test_triage_reads_the_two_booleans(monkeypatch):
    _fake_model(monkeypatch, parsed=TriageOutput(
        level="ROUTINE", out_of_scope=True, health_topic=True))
    verdict = await triage("cho tôi đơn thuốc giảm đau")
    assert verdict.out_of_scope is True
    assert verdict.health_topic is True


# ── 2. Failures fall back to routine, and say so ─────────────────────────


@pytest.mark.parametrize("exc", [
    ValueError("garbage output did not parse"),
    asyncio.TimeoutError(),
    RuntimeError("provider down"),
])
async def test_triage_failure_is_a_routine_fallback(monkeypatch, caplog, exc):
    _fake_model(monkeypatch, exc=exc)
    with caplog.at_level("WARNING", logger="langgraph.guardrail"):
        verdict = await triage("tê bì chân", request_id="req-1")
    assert verdict.level == "routine"
    assert verdict.source == "fallback"
    assert "guardrail_triage_failed" in caplog.text
    assert "guardrail_triage_failed" in [r.message for r in caplog.records]


async def test_triage_unparsed_output_is_a_fallback(monkeypatch, caplog):
    _fake_model(monkeypatch, parsed=None)
    with caplog.at_level("WARNING", logger="langgraph.guardrail"):
        verdict = await triage("đau ngực")
    assert verdict.level == "routine"
    assert verdict.source == "fallback"
    assert "guardrail_triage_failed" in caplog.text


async def test_triage_unknown_level_is_a_fallback(monkeypatch, caplog):
    _fake_model(monkeypatch, parsed=TriageOutput(
        level="CRITICAL", out_of_scope=False, health_topic=False))
    with caplog.at_level("WARNING", logger="langgraph.guardrail"):
        verdict = await triage("đau ngực")
    assert verdict.level == "routine"
    assert verdict.source == "fallback"


# ── 3. safety_keys, one row at a time ────────────────────────────────────


@pytest.mark.parametrize("verdict,planner_tags,expected", [
    # level emergency
    (_verdict("emergency"), [],
     ["emergency_screen", "referral_advice", "scope_disclaimer"]),
    # level soon
    (_verdict("soon"), [],
     ["red_flag_screen", "referral_advice", "scope_disclaimer"]),
    # out_of_scope alone
    (_verdict(out_of_scope=True), [], ["referral_advice"]),
    # health_topic alone
    (_verdict(health_topic=True), [], ["scope_disclaimer"]),
    # any planner quality tag
    (_verdict(), ["exercise_protocol"], ["scope_disclaimer"]),
    (_verdict(), ["evidence_citation"], ["scope_disclaimer"]),
    # transitional: a safety tag the planner still emits keeps its line
    (_verdict(), ["red_flag_screen"], ["red_flag_screen"]),
    (_verdict(), ["referral_advice"], ["referral_advice"]),
    (_verdict(), ["scope_disclaimer"], ["scope_disclaimer"]),
    # a routine turn with nothing to say carries nothing
    (_verdict(), [], []),
])
def test_safety_keys_table(verdict, planner_tags, expected):
    assert stream.safety_keys(verdict, planner_tags) == expected


def test_every_safety_key_is_a_known_template():
    from langgraph_agents.tag_contract import (
        DEFAULT_SAFETY_TEMPLATES, DEFAULT_SAFETY_TEMPLATES_EN,
    )

    for key in stream.SAFETY_KEYS:
        assert key in DEFAULT_SAFETY_TEMPLATES_EN, key
        assert key in DEFAULT_SAFETY_TEMPLATES, key


# ── 4. Head: persona wording first, then the code default ────────────────


def test_head_uses_the_persona_emergency_line(monkeypatch):
    import langgraph_agents.tag_contract as tag_contract

    monkeypatch.setattr(
        tag_contract, "get_persona",
        lambda persona_id, lang: {"safety_templates": {"emergency_screen": "CUSTOM EMERGENCY"}})
    text = stream.head(_verdict("emergency"), "anne", "en")
    assert text == "CUSTOM EMERGENCY\n\n"


def test_head_falls_back_to_the_code_default(monkeypatch):
    import langgraph_agents.tag_contract as tag_contract

    monkeypatch.setattr(
        tag_contract, "get_persona",
        lambda persona_id, lang: {"safety_templates": {}})
    text = stream.head(_verdict("emergency"), "anne", "en")
    assert text == tag_contract.DEFAULT_SAFETY_TEMPLATES_EN["emergency_screen"] + "\n\n"


def test_head_falls_back_to_red_flag_screen_when_emergency_is_empty(monkeypatch):
    import langgraph_agents.tag_contract as tag_contract

    monkeypatch.setattr(
        tag_contract, "get_persona",
        lambda persona_id, lang: {"safety_templates": {"red_flag_screen": "RED FLAG"}})
    monkeypatch.setattr(
        tag_contract, "DEFAULT_SAFETY_TEMPLATES_EN",
        {k: v for k, v in tag_contract.DEFAULT_SAFETY_TEMPLATES_EN.items()
         if k != "emergency_screen"})
    text = stream.head(_verdict("emergency"), "anne", "en")
    assert text == "RED FLAG\n\n"


def test_head_is_soon_line_for_soon_and_empty_for_routine():
    soon = stream.head(_verdict("soon"), "anne", "en")
    assert soon and "stop" in soon.lower()
    assert stream.head(_verdict("routine"), "anne", "en") == ""


# ── 5. Directive ─────────────────────────────────────────────────────────


def test_directive_empty_for_routine():
    assert directive(_verdict("routine")) == ""


def test_directive_present_for_urgent_turns():
    emergency = directive(_verdict("emergency"))
    soon = directive(_verdict("soon"))
    assert emergency and soon
    assert "emergency" in emergency.lower()
    assert "doctor" in soon.lower()
    for text in (emergency, soon):
        assert "Do not give an exercise routine." in text
        assert "do not drop or soften the warning" in text


# ── 6. Extensibility: one module + one policy line ───────────────────────


class _BlockCheck:
    name = "fake_block"
    stage = "input"

    async def run(self, ctx):
        return Decision("block", message="blocked by policy")


class _AnnotateCheck:
    name = "fake_annotate"
    stage = "input"

    async def run(self, ctx):
        return Decision("annotate", {"verdict": _verdict("soon")})


async def test_new_check_blocks_without_touching_other_files(monkeypatch):
    monkeypatch.setattr(policy, "INPUT_CHECKS", [_AnnotateCheck(), _BlockCheck()])
    result = await run_input(CheckContext(message="hello"))
    assert isinstance(result, InputResult)
    assert result.blocked is not None
    assert result.blocked.message == "blocked by policy"
    # A block does not discard the other checks' annotations.
    assert result.verdict.level == "soon"


async def test_first_block_wins(monkeypatch):
    second = _BlockCheck()
    second.name = "fake_block_2"
    monkeypatch.setattr(policy, "INPUT_CHECKS", [_BlockCheck(), second])
    result = await run_input(CheckContext(message="hello"))
    assert result.blocked.message == "blocked by policy"


# ── 7. A broken check never breaks the turn ──────────────────────────────


class _BrokenCheck:
    name = "broken"
    stage = "input"

    async def run(self, ctx):
        raise RuntimeError("boom")


async def test_check_exception_is_logged_and_treated_as_allow(monkeypatch, caplog):
    monkeypatch.setattr(policy, "INPUT_CHECKS", [_BrokenCheck(), _AnnotateCheck()])
    with caplog.at_level("WARNING", logger="langgraph.guardrail"):
        result = await run_input(CheckContext(message="hello", request_id="req-7"))
    assert result.blocked is None
    assert result.verdict.level == "soon"
    assert "guardrail_check_failed" in caplog.text


async def test_no_checks_means_a_routine_verdict(monkeypatch):
    monkeypatch.setattr(policy, "INPUT_CHECKS", [])
    result = await run_input(CheckContext(message="hello"))
    assert result.verdict.level == "routine"
    assert result.verdict.source == "fallback"


# ── 8. Handoff: a provided task, or one inline call per request ──────────


async def test_current_verdict_awaits_the_provided_task():
    expected = _verdict("emergency")

    async def _run():
        return InputResult(expected)

    task = asyncio.create_task(_run())
    config = {"configurable": {"request_id": "req-task", "guardrail_task": task}}
    assert await current_verdict(config) is expected


async def test_current_verdict_runs_inline_once_per_request(monkeypatch):
    calls = []

    async def _fake_run_input(ctx):
        calls.append(ctx.request_id)
        return InputResult(_verdict("soon"))

    monkeypatch.setattr("langgraph_agents.guardrail.handoff.run_input", _fake_run_input)
    config = {"configurable": {
        "request_id": "req-inline", "query": "đau gối", "locale": "vi",
        "persona_id": "anne",
    }}
    first = await current_verdict(config)
    second = await current_verdict(config)
    assert first.level == "soon" and second.level == "soon"
    assert calls == ["req-inline"]


async def test_current_verdict_is_routine_when_the_task_failed():
    async def _boom():
        raise RuntimeError("task died")

    task = asyncio.create_task(_boom())
    config = {"configurable": {"request_id": "req-dead", "guardrail_task": task}}
    verdict = await current_verdict(config)
    assert verdict.level == "routine"
    assert verdict.source == "fallback"


async def test_current_verdict_never_caches_without_a_request_id(monkeypatch):
    """A missing/"-" id cannot tell two turns apart; caching would leak verdicts."""
    calls = []

    async def _fake_run_input(ctx):
        calls.append(ctx.request_id)
        return InputResult(_verdict("soon"))

    monkeypatch.setattr("langgraph_agents.guardrail.handoff.run_input", _fake_run_input)

    config = {"configurable": {"request_id": "-", "query": "đau gối"}}
    first = await current_verdict(config)
    second = await current_verdict(config)
    assert first.level == "soon" and second.level == "soon"
    assert calls == ["-", "-"]


async def test_current_verdict_never_caches_when_request_id_is_absent(monkeypatch):
    calls = []

    async def _fake_run_input(ctx):
        calls.append(ctx.request_id)
        return InputResult(_verdict("routine"))

    monkeypatch.setattr("langgraph_agents.guardrail.handoff.run_input", _fake_run_input)

    config = {"configurable": {"query": "đau gối"}}
    await current_verdict(config)
    await current_verdict(config)
    assert calls == ["-", "-"]


# ── 9. The guardrail never runs on a user's own key ──────────────────────


def test_guardrail_is_eca_only():
    assert "guardrail" in llm_module._ECA_ONLY_ROLES


def test_guardrail_role_timeout_is_ten_seconds():
    """The spike measures gpt-oss-120b's classification, not the 4 s clock."""
    assert llm_module.role_timeout("guardrail") == 10.0


# ── Check protocol sanity ────────────────────────────────────────────────


def test_health_triage_satisfies_the_check_protocol():
    from langgraph_agents.guardrail.checks.health_triage import HealthTriage

    check: Check = HealthTriage()
    assert check.name == "health_triage"
    assert check.stage == "input"
