"""Fixes 1 & 4 (29-09-2026): avatar presence in every persona prompt, and the
evidence summary the synthesizer logs to explain its mode."""

import json

import pytest
from langchain_core.messages import ToolMessage

from langgraph_agents.nodes._persona_loader import build_persona_prompt, get_persona
from langgraph_agents.nodes.synthesizer import _derive_mode, _evidence_summary

pytestmark = pytest.mark.unit


def _tool(name: str, content) -> ToolMessage:
    body = content if isinstance(content, str) else json.dumps(content)
    return ToolMessage(content=body, name=name, tool_call_id=name)


@pytest.mark.parametrize("persona_id", ["anne", "bronya", "hatsune-miku", "miki"])
@pytest.mark.parametrize("mode", ["chat", "clarify", "refuse", "synthesize"])
def test_every_persona_prompt_states_its_presence(persona_id, mode):
    prompt = build_persona_prompt(get_persona(persona_id, "en"), mode)
    assert "## Your presence" in prompt
    assert "Never say you have no body" in prompt


def test_summary_reports_hits_with_best_similarity():
    hits = [{"content": "a", "similarity": 0.41}, {"content": "b", "similarity": 0.72}]
    assert _evidence_summary([_tool("kb_search", hits)]) == [
        {"tool": "kb_search", "status": "hits", "top_similarity": 0.72},
    ]


def test_summary_tells_error_from_empty():
    msgs = [_tool("kb_search", {"error": "timeout"}), _tool("recall", {"found": False})]
    assert [e["status"] for e in _evidence_summary(msgs)] == ["error", "empty"]


def test_summary_is_empty_when_retriever_never_searched():
    assert _evidence_summary([]) == []


def test_summary_agrees_with_mode():
    """An errored search is not evidence: the turn refuses and the log says why."""
    state = {"required_outputs": ["exercise_steps"],
             "messages": [_tool("kb_search", {"error": "timeout"})]}
    assert _derive_mode(state) == "refuse"
    assert _evidence_summary(state["messages"])[0]["status"] == "error"
