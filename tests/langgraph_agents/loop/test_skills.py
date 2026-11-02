"""Loop skills (stage 2 / GĐ3, plan II.2 `skills/` + II.3 stage 2).

- skills are listed in the `load_skill` tool description;
- a skill body loads only when the skill is called;
- a file edited without a re-pin is skipped, with a warning;
- a scripted turn loads a skill and then follows it;
- the pin CLI round-trips.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import types
from pathlib import Path

import pytest

from tests.langgraph_agents.loop.fakes import FakeAgentModel, AgentReply
from langgraph_agents.loop.skills import discover, file_sha256, load_skill
from langgraph_agents.loop.skills import loader as loader_mod
from langgraph_agents.loop.skills.pin import compute_pins, write_pins
from langgraph_agents.loop.tools import builtin as builtin_mod
from langgraph_agents.loop.tools.registry import build_turn_tools


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


def _msg_tool_call_id(m):
    if isinstance(m, dict):
        return m.get("tool_call_id")
    return getattr(m, "tool_call_id", None)


@pytest.fixture()
def fake_retriever_tools(monkeypatch):
    monkeypatch.setattr(builtin_mod, "_retriever_tools",
                        lambda: {"kb_search": _FakeLCTool(),
                                 "recall_self": _FakeLCTool(),
                                 "youtube_transcript": _FakeLCTool()})


def _packaged_skills_dir() -> Path:
    return Path(loader_mod.SKILLS_DIR)


def _tmp_skills_dir(tmp_path: Path) -> Path:
    """Copy the packaged skills into tmp_path and pin there unchanged."""
    src = _packaged_skills_dir()
    for child in sorted(src.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            shutil.copytree(child, tmp_path / child.name)
    write_pins(compute_pins(tmp_path), tmp_path)
    return tmp_path


def _load_skill_spec():
    specs = build_turn_tools({})
    return next(s for s in specs if s.name == "load_skill")


# ── skills listed in the tool description ─────────────────────────────────

def test_skills_listed_in_tool_description(fake_retriever_tools):
    specs = build_turn_tools({})
    spec = next(s for s in specs if s.name == "load_skill")
    # Internal tool, always on together with the internal retrievers.
    assert spec.risk == "internal"
    assert spec.clinical is False
    ids = [info.id for info in discover()]
    assert ids == ["explain-exercise", "weekly-plan"]
    # Names + one-line descriptions in the description.
    assert "load_skill" == spec.name
    assert "weekly-plan" in spec.description
    assert "explain-exercise" in spec.description
    assert "7-day" in spec.description
    # The BODIES stay out: only the call loads them (plan §8).
    assert "Day N: <focus>" not in spec.description
    assert "Work through these steps in order" not in spec.description
    assert "Do not describe the exercise from memory" not in spec.description


# ── body loads only when the skill is called ──────────────────────────────

def test_skill_body_loads_only_on_call(fake_retriever_tools):
    spec = _load_skill_spec()
    # Description has no body text.
    assert "# Building a 7-day exercise plan" not in spec.description
    # The call returns the body, nothing else.
    out = asyncio.run(spec.call({"name": "weekly-plan"}, {}))
    assert out["loaded"] is True
    assert "# Building a 7-day exercise plan" in out["content"]
    assert "Day N: <focus>" in out["content"]
    # Hash matches the pin that ships with the package.
    pins = compute_pins(_packaged_skills_dir())
    assert pins["weekly-plan"] == file_sha256(
        _packaged_skills_dir() / "weekly-plan" / "SKILL.md")


# ── edited without a re-pin → skipped, with a warning ─────────────────────

def test_unpinned_skill_is_skipped_with_warning(tmp_path):
    root = _tmp_skills_dir(tmp_path)
    # Edit one file without re-pinning.
    path = root / "weekly-plan" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nsome new line\n",
                    encoding="utf-8")
    out = load_skill("weekly-plan", skills_dir=root)
    assert out["loaded"] is False
    assert out["content"] == ""
    assert out["warning"] is not None
    assert "changed since it was pinned" in out["warning"]
    assert "Re-pin" in out["warning"]
    # A skill whose pin still matches loads normally.
    ok = load_skill("explain-exercise", skills_dir=root)
    assert ok["loaded"] is True


def test_missing_pins_fail_closed(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    shutil.copytree(_packaged_skills_dir() / "weekly-plan",
                    root / "weekly-plan")
    # No PINS.json in the dir at all.
    out = load_skill("weekly-plan", skills_dir=root)
    assert out["loaded"] is False
    assert "not pinned" in (out["warning"] or "")


def test_unknown_skill_warns():
    out = load_skill("no-such-skill")
    assert out["loaded"] is False
    assert "does not exist" in (out["warning"] or "")


# ── the tool answers "skipped" through the gate-less call path too ────────

def test_load_skill_call_reports_skip(fake_retriever_tools, tmp_path):
    spec = _load_skill_spec()
    root = _tmp_skills_dir(tmp_path)
    path = root / "explain-exercise" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nedited\n",
                    encoding="utf-8")
    # _load_skill_call goes through the packaged SKILLS_DIR by default, so
    # point the loader's module default at the temp dir for this call.
    saved = loader_mod.SKILLS_DIR
    try:
        loader_mod.SKILLS_DIR = root
        out = asyncio.run(spec.call({"name": "explain-exercise"}, {}))
    finally:
        loader_mod.SKILLS_DIR = saved
    assert out["loaded"] is False
    assert "changed since it was pinned" in (out["warning"] or "")


# ── pin CLI round trip ────────────────────────────────────────────────────

def test_pin_module_round_trip(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    shutil.copytree(_packaged_skills_dir() / "weekly-plan",
                    root / "weekly-plan")
    shutil.copytree(_packaged_skills_dir() / "explain-exercise",
                    root / "explain-exercise")
    pins = compute_pins(root)
    assert set(pins) == {"weekly-plan", "explain-exercise"}
    assert pins["weekly-plan"] == file_sha256(root / "weekly-plan" / "SKILL.md")
    write_pins(pins, root)
    stored = json.loads((root / "PINS.json").read_text(encoding="utf-8"))
    assert stored == pins
    # After re-pinning the edited file, the skill loads again.
    (root / "weekly-plan" / "SKILL.md").write_text(
        (root / "weekly-plan" / "SKILL.md").read_text(encoding="utf-8")
        + "\nsecond edit\n", encoding="utf-8")
    assert load_skill("weekly-plan", skills_dir=root)["loaded"] is False
    write_pins(compute_pins(root), root)
    assert load_skill("weekly-plan", skills_dir=root)["loaded"] is True


# ── scripted turn: load a skill, then follow it ───────────────────────────

def _run_graph(graph, agent_model, persona_model, query):
    from langgraph_agents.loop.limits import new_turn
    config = {"configurable": {
        "turn_ctx": new_turn(),
        "agent_model": agent_model,
        "persona_model": persona_model,
        "user_id": "u1",
        "session_id": "s1",
        "query": query,
        "locale": "en",
        "request_id": "r1",
        "persona_id": "anne",
    }}
    return asyncio.run(graph.ainvoke(
        {"transcript": [], "errors": [], "total_tokens": 0}, config))


def test_scripted_turn_loads_skill_then_follows_it(fake_retriever_tools,
                                                   monkeypatch):
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(
        persona_mod, "current_verdict",
        lambda config: types.SimpleNamespace(level="routine"))

    from langgraph_agents.loop.graph import build_loop_graph
    graph = build_loop_graph()

    agent_model = FakeAgentModel([
        AgentReply(content="", tool_calls=[
            {"id": "t1", "name": "load_skill",
             "arguments": '{"name": "weekly-plan"}'}]),
        AgentReply(
            content=("Day 1: core — bird dog (3 × 10). Day 2: rest. "
                     "Day 3: core — dead bug (3 × 8). "
                     "How many days per week can you train, and any "
                     "equipment or pain limits?")),
    ])
    persona_model = FakeAgentModel([AgentReply(
        content=("Here is your week: Day 1: core — bird dog (3 × 10), "
                 "Day 2: rest, Day 3: core — dead bug (3 × 8). "
                 "How many days can you train?"))])

    final = _run_graph(graph, agent_model, persona_model, "make a weekly plan")

    # Round 1: the model has tools but NOT the body; round 2 it does.
    assert len(agent_model.calls) == 2
    first_msgs = agent_model.calls[0]["messages"]
    assert all("Building a 7-day exercise plan" not in _msg_content(m)
               for m in first_msgs)
    second_msgs = agent_model.calls[1]["messages"]
    assert any("Building a 7-day exercise plan" in _msg_content(m)
               and _msg_role(m) == "tool" and _msg_tool_call_id(m) == "t1"
               for m in second_msgs)

    # Exactly one tool message for the load_skill call, with the body.
    tool_msgs = [m for m in final["transcript"]
                 if _msg_role(m) == "tool" and _msg_tool_call_id(m) == "t1"]
    assert len(tool_msgs) == 1
    content = _msg_content(tool_msgs[0])
    assert "# Building a 7-day exercise plan" in content
    assert "Day N: <focus>" in content

    # The agent followed the skill's prescribed format (and asked for the
    # missing details), and the persona carried it out.
    assert "Day 1: core" in (final.get("agent_result") or "")
    assert "Day 1: core" in (final.get("final_answer") or "")
