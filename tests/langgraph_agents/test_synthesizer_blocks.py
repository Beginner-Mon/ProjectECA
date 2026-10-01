"""Evidence theo nguồn (plan T3) + khối trạng thái cơ thể (plan T4)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.messages import ToolMessage

from langgraph_agents.nodes.synthesizer import (
    _REFUSE_TASK,
    _build_body_state_note,
    _check_tool_ambiguous,
    _extract_tool_results,
    _has_tool_results,
)


def _tm(name: str, payload) -> ToolMessage:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id=f"tc-{name}", name=name)


_KB = [{"content": "Cat-cow for back pain.", "similarity": 0.9,
        "source_type": "exercise_db", "document_title": "Back", "chunk_index": 0}]
_MEM = {"found": True, "results": [{"summary_text": "talked about squats",
                                    "similarity": 0.8}]}
_WEB = [{"title": "Back pain guide", "url": "https://x.example", "content": "..."}]
_MOTION = {"state": "queued", "job_id": "j1", "prompt": "squat"}


def test_motion_message_absent_from_evidence():
    msgs = [_tm("kb_search", _KB), _tm("generate_motion", _MOTION)]
    ev = _extract_tool_results(msgs)
    assert "squat" not in ev or "ECA's exercise library" in ev
    assert "generate_motion" not in ev
    assert "queued" not in ev


def test_motion_only_means_no_tool_results():
    assert _has_tool_results([_tm("generate_motion", _MOTION)]) is False


def test_memory_result_titled_by_source_not_library():
    ev = _extract_tool_results([_tm("memory_search", _MEM)])
    assert "your earlier conversations" in ev
    assert "library" not in ev.lower()


def test_kb_and_web_have_distinct_titles():
    ev = _extract_tool_results([_tm("kb_search", _KB),
                                _tm("search_medical", _WEB)])
    assert "ECA's exercise library" in ev
    assert "the web" in ev


def test_unknown_tool_gets_generic_title():
    ev = _extract_tool_results([_tm("mystery_tool", {"found": True})])
    assert "[From another source]" in ev


def test_ambiguity_ignores_non_evidence():
    assert _check_tool_ambiguous(
        [_tm("generate_motion", {"ambiguous": True})]) is False
    assert _check_tool_ambiguous(
        [_tm("kb_search", {"ambiguous": True})]) is True


# ── T4: khối trạng thái cơ thể ──────────────────────────────────────────

_BANNED_TECHNICAL = ["kimodo", "engine", "module", "queue", "gpu"]


def _motion_tm(payload) -> ToolMessage:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id="kimodo_motion",
                       name="generate_motion")


def test_body_note_queued_names_the_movement_and_eta():
    note = _build_body_state_note([_motion_tm(
        {"state": "queued", "job_id": "j1", "prompt": "squat",
         "eta_seconds": 5})])
    assert "## Your body this turn" in note
    assert "squat" in note
    assert "5" in note
    lowered = note.lower()
    assert not any(w in lowered for w in _BANNED_TECHNICAL)


def test_body_note_without_eta_drops_the_time_clause():
    note = _build_body_state_note([_motion_tm(
        {"state": "cache_hit", "job_id": "j1", "prompt": "squat"})])
    assert "## Your body this turn" in note
    assert "squat" in note
    assert "second" not in note.lower()


def test_body_note_unavailable_gives_no_technical_reason():
    for state in ("unavailable", "busy"):
        note = _build_body_state_note([_motion_tm({"state": state})])
        assert "## Your body this turn" in note
        assert "not able to show" in note
        lowered = note.lower()
        assert not any(w in lowered for w in _BANNED_TECHNICAL), state


def test_body_note_empty_without_motion_or_on_broken_json():
    assert _build_body_state_note([]) == ""
    assert _build_body_state_note([_tm("kb_search", _KB)]) == ""
    assert _build_body_state_note([_motion_tm("{not json")]) == ""


def test_refuse_task_is_narrow_not_whole_turn():
    assert "You cannot answer this one" not in _REFUSE_TASK
    assert "that part only" in _REFUSE_TASK


class _FakeAI:
    content = "ok"
    usage_metadata = None
    response_metadata = {}


def _capturing_llm(captured: dict) -> MagicMock:
    async def fake_ainvoke(msgs):
        captured["msgs"] = msgs
        return _FakeAI()

    llm = MagicMock()
    llm.ainvoke = fake_ainvoke
    return llm


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refuse_with_queued_motion_carries_body_note():
    """tags motion + thư viện rỗng + motion queued: prompt có khối cơ thể,
    không có câu từ chối cả lượt."""
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {
        "messages": [_motion_tm({"state": "queued", "job_id": "j1",
                                 "prompt": "cartwheel", "eta_seconds": 5})],
        "resolved_query": "show me a cartwheel",
        "required_outputs": ["scope_disclaimer", "motion_descriptor"],
        "needs_clarification": False,
        "total_tokens": 0,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "show me a cartwheel",
        "locale": "en",
    }}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)

    assert captured.get("msgs"), "synthesizer never called the LLM"
    prompt = "\n".join(
        str(getattr(m, "content", "")) for m in captured["msgs"])
    assert "## Your body this turn" in prompt
    assert "cartwheel" in prompt
    assert "You cannot answer this one" not in prompt
