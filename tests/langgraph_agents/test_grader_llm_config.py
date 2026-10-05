"""Role grader khong suy nghi ngam (grader-contract T8b)."""

import pytest

pytestmark = pytest.mark.unit


def _model(role, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    from langgraph_agents import llm as llm_mod

    llm_mod.get_chat_model.cache_clear()
    try:
        return llm_mod.get_chat_model(role)
    finally:
        llm_mod.get_chat_model.cache_clear()


def test_grader_disables_hidden_reasoning(monkeypatch):
    m = _model("grader", monkeypatch)
    assert m.extra_body == {"thinking": {"type": "disabled"}}
    assert m.request_timeout == 5.0
    assert m.max_retries == 0


def test_planner_keeps_default_thinking(monkeypatch):
    m = _model("planner", monkeypatch)
    assert m.extra_body is None
    assert m.request_timeout == 20.0
    assert m.max_retries == 1
