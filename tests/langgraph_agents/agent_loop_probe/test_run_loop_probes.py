"""Tests for `run_loop_probes.py`, the runner that measures the real loop.

No network, no database, no key: models are scripted fakes, and the one test
that goes through the real `AgentModel` replaces `openai.AsyncOpenAI`.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from langgraph_agents.local_tests.agent_loop import run_loop_probes as rlp
from langgraph_agents.local_tests.agent_loop.probes import probe_by_id
from tests.langgraph_agents.loop.fakes import AgentReply, FakeAgentModel

pytestmark = pytest.mark.unit


def _call(i: int, name: str, **args) -> dict:
    return {"id": f"c{i}", "name": name, "arguments": json.dumps(args)}


def _run(probe_id: str, replies: list[AgentReply], repeat: int = 1) -> dict:
    probe = probe_by_id(probe_id)
    return asyncio.run(rlp.run_probe(
        probe, repeat, agent_model=FakeAgentModel(replies), mode=rlp.MODE_DRY))


def _outcomes(row: dict) -> list[tuple[str, str]]:
    return [(c["tool"], c["outcome"]) for c in row["call_log"] + row["refused"]]


# ── dry runs through the real graph ──────────────────────────────────────

def test_executed_call_is_logged_and_scored():
    row = _run("P1", [
        AgentReply(tool_calls=[_call(1, "find_exercise", condition="knee pain")]),
        AgentReply(content="Wall Slide (quadriceps) helps knee pain."),
    ])
    assert row["call_log"] == [{
        "round": 1, "tool": "find_exercise", "args": {"condition": "knee pain"},
        "outcome": "executed", "ok": True}]
    assert row["refused"] == []
    assert row["rounds"] == 2 and row["tool_calls"] == 1
    assert row["stop_reason"] == "completed"
    assert row["persona"] == "passthrough" and row["mode"] == "dry-run"
    # check_probe decided on that call: P1 expects find_exercise(knee)
    assert row["failed_check"] is None and row["pass"] is True
    assert row["final_answer"] == "Wall Slide (quadriceps) helps knee pain."
    assert len(row["per_round_latency"]) == 2


def test_check_probe_fails_when_the_expected_call_is_missing():
    row = _run("P1", [AgentReply(content="Try wall slides.")])
    assert row["call_log"] == []
    assert row["failed_check"] == "expected_calls" and row["pass"] is False


def test_p10_shape_saved_detail_is_refused_then_clean_call_executes():
    row = _run("P10", [
        AgentReply(tool_calls=[_call(1, "web_search",
                                     query="knee osteoarthritis exercises")]),
        AgentReply(tool_calls=[_call(2, "web_search", query="gentle knee exercises")]),
        AgentReply(content="Gentle knee exercises: wall slides."),
    ])
    assert _outcomes(row) == [("web_search", "executed"),
                              ("web_search", "refused_by_policy")]
    assert row["call_log"][0]["args"] == {"query": "gentle knee exercises"}
    assert row["call_log"][0]["round"] == 2
    assert row["refused"][0]["round"] == 1
    # taint rule 2 saw the probe's USER_FACTS (the memory node never ran)
    assert "osteoarthritis" in json.dumps(row["refused"][0]["args"])
    # only the executed call is scored and recorded as sent
    assert row["user_facts_in_args"] == []
    assert row["pass"] is True


def test_p9_shape_after_a_personal_read_egress_and_writes_need_approval():
    row = _run("P9", [
        AgentReply(tool_calls=[_call(1, "read_inbox")]),
        AgentReply(tool_calls=[_call(2, "web_search", query="clinic hours"),
                               _call(3, "create_reminder", title="pay invoice",
                                     date="2026-11-04", time="09:00")]),
        AgentReply(content="The clinic moved your appointment to 2026-11-05."),
    ])
    assert _outcomes(row) == [
        ("read_inbox", "executed"),
        ("web_search", "approval_unavailable"),
        ("create_reminder", "approval_unavailable"),
    ]
    assert row["reminders"] == []
    assert row["failed_check"] is None and row["pass"] is True


def test_write_alone_is_refused_before_stage_four():
    row = _run("P3", [
        AgentReply(tool_calls=[_call(1, "create_reminder", title="wall slide",
                                     date="2026-11-02", time="07:00")]),
        AgentReply(content="I could not set it."),
    ])
    assert _outcomes(row) == [("create_reminder", "approval_unavailable")]
    assert row["call_log"] == [] and row["reminders"] == []


def test_offers_only_the_probe_tools():
    model = FakeAgentModel([AgentReply(content="Bye.")])
    asyncio.run(rlp.run_probe(probe_by_id("P5"), 1, agent_model=model, mode="dry-run"))
    names = [t["function"]["name"] for t in model.calls[0]["tools"]]
    assert names == ["find_exercise", "create_reminder"]


def test_script_exhausted_is_a_failed_row_not_a_crash():
    row = _run("P1", [AgentReply(tool_calls=[_call(1, "find_exercise",
                                                   condition="knee")])])
    # the agent node turns the model failure into a stop note
    assert row["pass"] is False
    assert row["error"] and "script exhausted" in row["error"]


# ── the database is never reached ────────────────────────────────────────

def test_dry_run_completes_with_get_pg_client_raising(monkeypatch):
    import langgraph_agents.shared as shared
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("the real database was reached")

    monkeypatch.setattr(shared, "get_pg_client", boom)
    row = _run("P2", [AgentReply(content="See you tomorrow.")])
    assert row["pass"] is True and row["error"] is None
    assert calls == []                       # the runner's own refusal answered
    assert shared.get_pg_client is boom      # and the patch was restored after


def test_no_database_refuses_and_counts_then_restores():
    import langgraph_agents.shared as shared
    original = shared.get_pg_client
    with rlp.no_database() as attempts:
        with pytest.raises(RuntimeError):
            shared.get_pg_client()
    assert attempts == ["get_pg_client"]
    assert shared.get_pg_client is original


# ── production does not set the probe hooks ──────────────────────────────

def test_production_wiring_does_not_set_the_hooks():
    import langgraph_agents.api.main as api_main
    from langgraph_agents.loop.graph import build_loop_graph
    from langgraph_agents.loop.tools.registry import build_turn_tools

    src = inspect.getsource(api_main)
    assert "turn_tools" not in src
    import re
    calls = re.findall(r"build_loop_graph\(([^)]*)\)", src)
    assert calls and all(c.strip() == "" for c in calls)   # always the default

    assert inspect.signature(build_loop_graph).parameters["memory"].default is None
    # without `turn_tools` the registry returns the builtins
    names = {s.name for s in build_turn_tools({"web_search": False})}
    assert {"kb_search", "recall_self", "load_skill"} <= names


def test_default_graph_uses_the_real_memory_node(monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    marker = object()
    seen = {}

    class _Builder:
        def __init__(self, *_a, **_k):
            pass

        def add_node(self, name, fn):
            seen[name] = fn

        def add_edge(self, *_a, **_k):
            pass

        def add_conditional_edges(self, *_a, **_k):
            pass

        def compile(self):
            return SimpleNamespace(with_config=lambda **_k: marker)

    monkeypatch.setattr(graph_mod, "StateGraph", _Builder)
    assert graph_mod.build_loop_graph() is marker
    assert seen["memory"] is graph_mod.memory_node
    graph_mod.build_loop_graph(memory=rlp.make_seed_memory([]))
    assert seen["memory"] is not graph_mod.memory_node


# ── recording model ──────────────────────────────────────────────────────

def test_tpm_429_waits_and_resends_outside_the_latency(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    class _Inner:
        n = 0

        async def complete(self, messages, tools=None, *, tool_choice="auto"):
            _Inner.n += 1
            if _Inner.n == 1:
                raise RuntimeError("Rate limit reached for tokens per minute (TPM). "
                                   "Please try again in 2.4s")
            return AgentReply(content="ok", usage={"prompt_tokens": 7,
                                                   "completion_tokens": 3,
                                                   "reasoning_tokens": 2})

    rec = rlp.RecordingModel(_Inner(), sleep=fake_sleep)
    reply = asyncio.run(rec.complete([{"role": "user", "content": "hi"}]))
    assert reply.content == "ok"
    assert slept == [pytest.approx(3.4)]
    assert rec.waits == 1 and rec.wait_seconds == pytest.approx(3.4)
    assert len(rec.latency) == 1
    assert rec.prompt_tokens == [7] and rec.reasoning_tokens == [2]


def test_other_errors_propagate():
    class _Inner:
        async def complete(self, *a, **k):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(rlp.RecordingModel(_Inner()).complete([]))


# ── the production AgentModel, with the SDK replaced ─────────────────────

class _SDKMessage:
    def __init__(self, content="", tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model_extra = {"reasoning": reasoning} if reasoning else {}


def _sdk_response(message, usage=(10, 4, 14, 3)):
    u = SimpleNamespace(
        prompt_tokens=usage[0], completion_tokens=usage[1], total_tokens=usage[2],
        completion_tokens_details=SimpleNamespace(reasoning_tokens=usage[3]))
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=u)


def test_live_path_runs_the_real_agent_model_against_groq_endpoint(monkeypatch):
    import langgraph_agents.loop.client as client_mod

    sent = []
    inits = []
    tool_call = SimpleNamespace(
        id="call_1", function=SimpleNamespace(
            name="find_exercise", arguments='{"condition": "knee pain"}'))
    responses = [
        _sdk_response(_SDKMessage(tool_calls=[tool_call], reasoning="think a")),
        _sdk_response(_SDKMessage(content="Wall Slide (quadriceps) helps.")),
    ]

    class _Completions:
        async def create(self, **kwargs):
            sent.append(kwargs)
            return responses.pop(0)

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            inits.append(kwargs)
            self.chat = SimpleNamespace(completions=_Completions())

    monkeypatch.setattr(client_mod.openai, "AsyncOpenAI", _FakeAsyncOpenAI)
    model = rlp.make_live_model("test-key", "low")
    row = asyncio.run(rlp.run_probe(probe_by_id("P1"), 1, agent_model=model,
                                    mode=rlp.MODE_LIVE))

    assert inits == [{"api_key": "test-key", "base_url": rlp.GROQ["base_url"],
                      "timeout": 20.0, "max_retries": 0}]
    assert [k["model"] for k in sent] == [rlp.GROQ["model"]] * 2
    assert sent[0]["extra_body"]["reasoning_effort"] == "low"
    # the second request carries the transcript as wire dicts, with the tool
    # call in OpenAI shape and the reasoning echoed on the assistant message
    second = sent[1]["messages"]
    assert all(isinstance(m, dict) for m in second)
    assistant = [m for m in second if m.get("tool_calls")
                 and m["tool_calls"][0]["id"] == "call_1"][0]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["function"] == {
        "name": "find_exercise", "arguments": '{"condition": "knee pain"}'}
    assert assistant["reasoning"] == "think a"
    tool_msg = [m for m in second if m["role"] == "tool" and m["tool_call_id"] == "call_1"][0]
    assert "Wall Slide" in tool_msg["content"]

    assert row["mode"] == "live" and row["pass"] is True
    assert row["per_round_prompt_tokens"] == [10, 10]
    assert row["per_round_reasoning_tokens"] == [3, 3]
    assert row["reasoning_chars_received"] == len("think a")
    assert row["reasoning_chars_sent"] == len("think a")   # sent once, in round 2
    assert row["prompt_tokens"] == 20 and row["completion_tokens"] == 8


# ── client hook ──────────────────────────────────────────────────────────

def test_agent_model_defaults_have_no_explicit_endpoint():
    from langgraph_agents.loop.client import AgentModel
    assert AgentModel()._explicit_endpoint is None
    ep = AgentModel(base_url="http://x/v1", api_key="k", model="m",
                    extra_body={"a": 1})._resolve_endpoint()
    assert ep["base_url"] == "http://x/v1" and ep["api_key"] == "k"
    assert ep["model"] == "m" and ep["extra_body"] == {"a": 1}


# ── CLI ──────────────────────────────────────────────────────────────────

def test_cli_requires_exactly_one_mode():
    with pytest.raises(SystemExit):
        rlp.main(["--probes", "P2"])
    with pytest.raises(SystemExit):
        rlp.main(["--probes", "P2", "--dry-run", "--live"])


def test_cli_live_without_key_exits_3_before_any_call(monkeypatch, capsys):
    monkeypatch.setattr(rlp, "groq_keys", lambda: [])
    assert rlp.main(["--live", "--probes", "P2"]) == 3
    assert "GROQ_API_KEY" in capsys.readouterr().err


def test_cli_dry_run_writes_rows_and_prints_overall(tmp_path, capsys):
    code = rlp.main(["--dry-run", "--probes", "P6,P7", "--repeats", "2",
                     "--label", "t", "--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    rows = [json.loads(l) for l in (Path(tmp_path) / "t.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["probe"] for r in rows] == ["P6", "P7", "P6", "P7"]
    assert "overall: 4/4 runs passed" in out
    assert "per probe: P6 2/2, P7 2/2" in out
