"""Tests for `loop.mode.select_mode` (PLAN.md §II.2)."""

from langgraph_agents.loop.config import LoopSettings, load_settings
from langgraph_agents.loop.mode import select_mode


def test_pipeline_default():
    s = LoopSettings()
    assert select_mode((), s) == "pipeline"
    assert select_mode(("loop-testers",), s) == "pipeline"


def test_loop_mode_everyone():
    s = LoopSettings(agent_mode="loop")
    assert select_mode((), s) == "loop"
    assert select_mode(("admin",), s) == "loop"


def test_group_mode_membership():
    s = LoopSettings(agent_mode="group", loop_group="loop-testers")
    assert select_mode(("loop-testers",), s) == "loop"
    assert select_mode(("admin",), s) == "pipeline"
    assert select_mode((), s) == "pipeline"


def test_disabled_kill_switch():
    s = LoopSettings(agent_mode="loop", disabled=True)
    assert select_mode(("loop-testers",), s) == "pipeline"
    assert select_mode((), s) == "pipeline"


def test_load_settings_defaults_agent_mode_pipeline(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "pipeline")
    load_settings.cache_clear()
    s = load_settings()
    assert s.agent_mode == "pipeline"
