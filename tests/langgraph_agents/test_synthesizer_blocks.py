"""Evidence theo nguồn (plan T3) + khối trạng thái cơ thể (plan T4)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from langchain_core.messages import ToolMessage

from langgraph_agents.nodes.synthesizer import (
    _REFUSE_TASK,
    _build_about_you,
    _build_body_state_note,
    _build_tag_instructions,
    _check_tool_ambiguous,
    _extract_tool_results,
    _has_tool_results,
)
from langgraph_agents.shared.context import budget_chars


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


_NHS = [{"content": "NHS advice on back pain.", "similarity": 0.85,
         "source_type": "nhs_uk", "document_title": "NHS Back",
         "chunk_index": 0}]


def test_nhs_segment_never_titled_eca_library():
    """B5: đoạn nhs_uk mang nhãn NHS, không bao giờ nhãn thư viện ECA."""
    ev = _extract_tool_results([_tm("kb_search", _NHS)])
    assert "NHS health guidance" in ev
    assert "ECA's exercise library" not in ev


def test_library_segment_never_titled_nhs():
    """B5: và ngược lại."""
    ev = _extract_tool_results([_tm("kb_search", _KB)])
    assert "ECA's exercise library" in ev
    assert "NHS health guidance" not in ev


def test_mixed_kb_segments_get_two_titles():
    """B5: một lượt có cả hai loại thì có hai tiêu đề khác nhau."""
    ev = _extract_tool_results([_tm("kb_search", _KB + _NHS)])
    assert "ECA's exercise library" in ev
    assert "NHS health guidance" in ev
    assert "NHS Back" in ev  # document_title kèm theo đoạn


def test_strange_source_type_stays_excluded():
    """B5: source_type ngoài hai loại vẫn bị loại khỏi evidence."""
    weird = [{"content": "Random wiki.", "similarity": 0.9,
              "source_type": "wikipedia", "document_title": "Wiki",
              "chunk_index": 0}]
    ev = _extract_tool_results([_tm("kb_search", weird)])
    assert "Random wiki." not in ev
    assert "Wiki" not in ev


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


def test_voice_line_alone():
    note = _build_body_state_note([], speaks_aloud=True)
    assert "## Your body this turn" in note
    assert "You are speaking this reply aloud in your own voice; the user hears you." in note


def test_voice_line_with_motion():
    note = _build_body_state_note(
        [_motion_tm({"state": "queued", "prompt": "squat"})],
        speaks_aloud=True,
    )
    assert note.count("## Your body this turn") == 1
    assert 'You are about to show "squat"' in note
    assert "You are speaking this reply aloud in your own voice; the user hears you." in note


def test_no_voice_line_by_default():
    assert _build_body_state_note([]) == ""
    assert _build_body_state_note([], speaks_aloud=False) == ""


def test_motion_only_output_unchanged():
    note = _build_body_state_note([_motion_tm({"state": "unavailable"})])
    assert "speaking this reply aloud" not in note


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


# ── B3: hướng dẫn theo đúng tag của lượt ──────────────────────────────

def test_tag_instructions_only_lists_turn_tags():
    assert "sets, reps" in _build_tag_instructions(["exercise_protocol"])
    assert "sets, reps" not in _build_tag_instructions(["exercise_steps"])
    assert "sets, reps" not in _build_tag_instructions([])
    assert "ordered steps" in _build_tag_instructions(["exercise_steps"])
    assert "ordered steps" not in _build_tag_instructions(["exercise_protocol"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synthesizer_prompt_omits_protocol_without_tag():
    """Lượt không có exercise_protocol: prompt không chứa sets/reps."""
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {
        "messages": [_tm("kb_search", _KB)],
        "resolved_query": "dau lung duoi thi tap gi",
        "required_outputs": ["scope_disclaimer", "contraindication",
                             "evidence_citation"],
        "needs_clarification": False,
        "total_tokens": 0,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "dau lung",
        "locale": "vi",
    }}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)

    assert captured.get("msgs"), "synthesizer never called the LLM"
    prompt = "\n".join(
        str(getattr(m, "content", "")) for m in captured["msgs"])
    assert "For exercise_protocol" not in prompt
    assert "mention sources" not in prompt  # dòng nguồn do code phát (T4)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synthesizer_prompt_includes_protocol_with_tag():
    """Lượt có exercise_protocol: prompt chứa sets/reps."""
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {
        "messages": [_tm("kb_search", _KB)],
        "resolved_query": "bai do tap may hiep",
        "required_outputs": ["scope_disclaimer", "exercise_protocol",
                             "evidence_citation"],
        "needs_clarification": False,
        "total_tokens": 0,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "may hiep",
        "locale": "vi",
    }}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)

    assert captured.get("msgs"), "synthesizer never called the LLM"
    prompt = "\n".join(
        str(getattr(m, "content", "")) for m in captured["msgs"])
    assert "sets, reps" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_protocol_instruction_forbids_own_numbers():
    """Task 4a: dòng protocol không có ví dụ số, cấm tự đặt số."""
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {
        "messages": [_tm("kb_search", _KB)],
        "resolved_query": "bai do tap may hiep",
        "required_outputs": ["scope_disclaimer", "exercise_protocol",
                             "evidence_citation"],
        "needs_clarification": False,
        "total_tokens": 0,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "may hiep",
        "locale": "vi",
    }}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)

    assert captured.get("msgs"), "synthesizer never called the LLM"
    prompt = "\n".join(
        str(getattr(m, "content", "")) for m in captured["msgs"])
    assert "Do not supply numbers of your own" in prompt
    assert "3 sets of 10 reps" not in prompt


# ── T8f: khối About you ─────────────────────────────────────────────

def _self_tm(payload) -> ToolMessage:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id="tc-self",
                       name="recall_self")


def test_about_you_first_person_never_looked_up():
    block = _build_about_you([_self_tm(
        {"found": True, "results": [
            {"title": "Appearance", "kind": "sheet",
             "content": "Short hair.", "similarity": 0.9}]})])
    assert "## About you" in block
    assert "first person" in block
    assert "Never say you looked it up" in block
    assert "Short hair." in block


def test_about_you_caps_at_budget_chars():
    block = _build_about_you([_self_tm(
        {"found": True, "results": [
            {"title": "T", "kind": "sheet",
             "content": "x" * 2000, "similarity": 0.9}]})])
    assert "## About you" in block
    assert block.count("x") <= budget_chars("about_you")


def test_about_you_empty_without_usable_result():
    assert _build_about_you([]) == ""
    assert _build_about_you([_self_tm({"found": False})]) == ""
    assert _build_about_you([_self_tm("{not json")]) == ""
    assert _build_about_you([_tm("kb_search", _KB)]) == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synthesizer_prompt_carries_about_you():
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {
        "messages": [_self_tm(
            {"found": True, "results": [
                {"title": "Appearance", "kind": "sheet",
                 "content": "Short hair.", "similarity": 0.9}]})],
        "resolved_query": "who are you",
        "required_outputs": [],
        "needs_clarification": False,
        "total_tokens": 0,
    }
    config = {"configurable": {
        "request_id": "r", "persona_id": "anne", "query": "who are you",
        "locale": "en",
    }}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)

    assert captured.get("msgs"), "synthesizer never called the LLM"
    prompt = "\n".join(
        str(getattr(m, "content", "")) for m in captured["msgs"])
    assert "## About you" in prompt
    assert "Short hair." in prompt
    assert "Never say you looked it up" in prompt
