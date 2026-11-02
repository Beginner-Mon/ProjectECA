"""Probe set G (stage 3C) and the runner flags `--skills` / `--plugins`.

No network, no database, no key: models are scripted fakes; the one real-plugin
test stubs the plugin turn.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from langgraph_agents.local_tests.agent_loop import run_loop_probes as rlp
from langgraph_agents.local_tests.agent_loop.probes import (
    ALL_PROBES, PROBES, PROBES_G, check_probe, effective_probe, probe_by_id,
)
from tests.langgraph_agents.loop.fakes import AgentReply, FakeAgentModel

pytestmark = pytest.mark.unit

DEEPWIKI = ["deepwiki__read_wiki_structure", "deepwiki__read_wiki_contents",
            "deepwiki__ask_question"]


def _call(i: int, tool: str, **args) -> dict:
    return {"id": f"c{i}", "name": tool, "arguments": json.dumps(args)}


def _run(probe_id: str, replies: list[AgentReply], **flags) -> dict:
    return asyncio.run(rlp.run_probe(
        probe_by_id(probe_id), 1, agent_model=FakeAgentModel(replies),
        mode=rlp.MODE_DRY, **flags))


# ── the set ──────────────────────────────────────────────────────────────

def test_set_g_has_five_probes_and_all_stays_p1_to_p13():
    assert [p["id"] for p in PROBES_G] == ["G1", "G2", "G3", "G4", "G5"]
    assert [p["id"] for p in PROBES] == [f"P{i}" for i in range(1, 14)]
    assert [p["id"] for p in ALL_PROBES] == [p["id"] for p in PROBES + PROBES_G]
    assert probe_by_id("G3")["category"] == "plugin: deepwiki"


def test_set_g_covers_two_skills_one_plugin_and_two_unrelated_tasks():
    cats = [p["category"] for p in PROBES_G]
    assert cats == ["skill: weekly-plan", "skill: explain-exercise", "plugin: deepwiki",
                    "unrelated: small talk", "unrelated: plain KB"]
    wanted = {e["args_contains"]["name"]
              for p in PROBES_G[:2] for e in p["checks"]["expected_calls"]
              if e["tool"] == "load_skill"}
    assert wanted == {"weekly-plan", "explain-exercise"}


# ── effective_probe ──────────────────────────────────────────────────────

def test_everything_on_changes_nothing():
    probe = probe_by_id("G1")
    assert effective_probe(probe, True, ("deepwiki",)) is probe


def test_skills_off_removes_load_skill_and_its_expected_call():
    eff = effective_probe(probe_by_id("G1"), skills=False, plugins=("deepwiki",))
    assert "load_skill" not in eff["tools"]
    assert [e["tool"] for e in eff["checks"]["expected_calls"]] == ["kb_search"]
    assert eff["checks"]["forbid_calls"] == DEEPWIKI       # forbids stay


def test_no_plugins_removes_the_plugin_tools_and_any_of_calls():
    eff = effective_probe(probe_by_id("G3"), skills=True, plugins=())
    assert eff["tools"] == ["load_skill", "kb_search"]
    assert "any_of_calls" not in eff["checks"]
    assert eff["checks"]["forbid_calls"] == ["load_skill"]


def test_a_plugin_id_only_enables_its_own_tools():
    eff = effective_probe(probe_by_id("G3"), plugins=("other",))
    assert not any(t.startswith("deepwiki__") for t in eff["tools"])


def test_the_original_probe_is_not_mutated():
    probe = probe_by_id("G3")
    before = json.dumps(probe, sort_keys=True)
    effective_probe(probe, skills=False, plugins=())
    assert json.dumps(probe, sort_keys=True) == before


# ── the checks ───────────────────────────────────────────────────────────

def test_g3_passes_on_any_deepwiki_tool_with_the_repo():
    log = [{"round": 1, "tool": "deepwiki__read_wiki_contents",
            "args": {"repoName": "langchain-ai/langgraph"}, "outcome": "executed"}]
    assert check_probe(probe_by_id("G3"), log, "x") is None


def test_g3_fails_without_a_call_or_with_the_wrong_repo():
    probe = probe_by_id("G3")
    assert check_probe(probe, [], "x") == "any_of_calls"
    wrong = [{"round": 1, "tool": "deepwiki__ask_question",
              "args": {"repoName": "someone/else", "question": "q"}}]
    assert check_probe(probe, wrong, "x") == "any_of_calls"


def test_g4_fails_on_any_tool_call():
    log = [{"round": 1, "tool": "kb_search", "args": {"query": "thanks"}}]
    assert check_probe(probe_by_id("G4"), log, "x") == "forbid_calls"


def test_g5_forbids_a_skill_load_for_a_plain_question():
    log = [{"round": 1, "tool": "load_skill", "args": {"name": "weekly-plan"}},
           {"round": 2, "tool": "kb_search", "args": {"query": "shoulder pain"}}]
    assert check_probe(probe_by_id("G5"), log, "x") == "forbid_calls"


# ── the runner ───────────────────────────────────────────────────────────

def test_the_skill_probe_loads_the_real_skill_file():
    row = _run("G1", [
        AgentReply(tool_calls=[_call(1, "load_skill", name="weekly-plan")]),
        AgentReply(tool_calls=[_call(2, "kb_search", query="knee")]),
        AgentReply(content="Plan."),
    ])
    assert [c["tool"] for c in row["call_log"]] == ["load_skill", "kb_search"]
    assert row["pass"] is True and row["failed_check"] is None
    assert row["skills"] == "on" and row["plugins"] == []


def test_skills_off_does_not_offer_load_skill_and_still_scores():
    row = _run("G1", [
        AgentReply(tool_calls=[_call(1, "kb_search", query="knee")]),
        AgentReply(content="Plan."),
    ], skills=False)
    assert row["skills"] == "off"
    assert "load_skill" not in row["offered_tools"]
    assert row["pass"] is True


def test_a_model_that_asks_for_an_unoffered_skill_tool_is_refused():
    row = _run("G1", [
        AgentReply(tool_calls=[_call(1, "load_skill", name="weekly-plan")]),
        AgentReply(content="done"),
    ], skills=False)
    assert row["call_log"] == []
    assert row["refused"] and row["refused"][0]["tool"] == "load_skill"


def test_plugin_tools_are_offered_only_for_a_listed_plugin():
    off = _run("G3", [AgentReply(content="I do not know.")])
    assert off["plugins"] == [] and not any(t.startswith("deepwiki__")
                                            for t in off["offered_tools"])
    on = _run("G3", [
        AgentReply(tool_calls=[_call(1, "deepwiki__ask_question",
                                     repoName="langchain-ai/langgraph",
                                     question="checkpointers")]),
        AgentReply(content="They store state."),
    ], plugins=("deepwiki",))
    assert on["plugins"] == ["deepwiki"]
    assert "deepwiki__ask_question" in on["offered_tools"]
    assert on["pass"] is True


def test_g4_passes_with_no_call_and_fails_with_one():
    assert _run("G4", [AgentReply(content="Gặp lại nhé!")])["pass"] is True
    row = _run("G4", [AgentReply(tool_calls=[_call(1, "kb_search", query="x")]),
                      AgentReply(content="ok")])
    assert row["failed_check"] == "forbid_calls"


def test_cli_g_runs_the_set_and_records_the_flags(tmp_path, capsys):
    code = rlp.main(["--dry-run", "--probes", "G", "--skills", "off",
                     "--plugins", "deepwiki", "--label", "g", "--out-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    rows = [json.loads(l) for l in (Path(tmp_path) / "g.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["probe"] for r in rows] == ["G1", "G2", "G3", "G4", "G5"]
    assert {r["skills"] for r in rows} == {"off"}
    assert {tuple(r["plugins"]) for r in rows} == {("deepwiki",)}
    assert all("load_skill" not in r["offered_tools"] for r in rows)
    assert "overall: 5/5 runs passed" in out
    assert "skills off" in out and "plugins ['deepwiki']" in out


def test_cli_all_is_still_p1_to_p13(tmp_path, capsys):
    rlp.main(["--dry-run", "--probes", "all", "--label", "a", "--out-dir", str(tmp_path)])
    rows = [json.loads(l) for l in (Path(tmp_path) / "a.jsonl").read_text(
        encoding="utf-8").splitlines()]
    assert [r["probe"] for r in rows] == [f"P{i}" for i in range(1, 14)]


def test_cli_rejects_a_bad_skills_value():
    with pytest.raises(SystemExit):
        rlp.main(["--dry-run", "--skills", "maybe"])


def test_live_with_an_inactive_plugin_stops_before_any_model_call(monkeypatch, capsys):
    monkeypatch.setattr(rlp, "groq_keys", lambda: ["k"])

    async def _turn(ids, request_id):
        return SimpleNamespace(statuses={"deepwiki": "definition_changed"},
                               specs=[], close=_noop)

    async def _noop():
        return None

    monkeypatch.setattr(rlp, "real_plugin_turn", _turn)

    def _boom(*a, **k):
        raise AssertionError("no model may be built")

    monkeypatch.setattr(rlp, "make_live_model", _boom)
    assert rlp.main(["--live", "--probes", "G3", "--plugins", "deepwiki"]) == 3
    assert "definition_changed" in capsys.readouterr().err


def test_live_mode_swaps_the_fake_plugin_tools_for_the_real_ones(monkeypatch):
    """Only `--live` uses the real catalog entry; its specs replace the fakes
    of the same plugin, the turn carries the plugin turn (so the gate sees it)
    and the sessions are closed at the end."""
    from langgraph_agents.loop.tools.registry import ToolSpec

    closed: list[bool] = []
    seen: dict = {}

    async def _real_call(args, config=None):
        seen["args"] = dict(args)
        return {"answer": "REAL"}

    real = ToolSpec(
        name="deepwiki__ask_question", description="real", parameters={
            "type": "object", "properties": {"repoName": {"type": "string"},
                                              "question": {"type": "string"}},
            "required": ["repoName", "question"]},
        risk="egress_read", source="plugin", call=_real_call, plugin_id="deepwiki")

    from langgraph_agents.loop.plugins.runtime import PluginTurn

    class _PT(PluginTurn):
        async def close(self):
            closed.append(True)

    def _make_pt():
        pt = _PT("r")
        pt.specs = [real]
        pt.statuses = {"deepwiki": "active"}
        return pt

    async def _turn(ids, request_id):
        return _make_pt()

    monkeypatch.setattr(rlp, "real_plugin_turn", _turn)
    row = asyncio.run(rlp.run_probe(
        probe_by_id("G3"), 1, mode=rlp.MODE_LIVE, plugins=("deepwiki",),
        agent_model=FakeAgentModel([
            AgentReply(tool_calls=[_call(1, "deepwiki__ask_question",
                                         repoName="langchain-ai/langgraph",
                                         question="checkpointers")]),
            AgentReply(content="They store state."),
        ])))
    assert row["refused"] == [] and row["error"] is None, row
    assert seen["args"]["repoName"] == "langchain-ai/langgraph"
    assert row["plugin_status"] == {"deepwiki": "active"}
    assert row["offered_tools"].count("deepwiki__ask_question") == 1
    assert row["pass"] is True
    assert closed and all(closed)   # the persona node and the runner both close it
