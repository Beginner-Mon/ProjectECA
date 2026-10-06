"""LLM chấm thay regex trong grader (grader-contract T5)."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage

pytestmark = pytest.mark.unit


def _kb_msg(chunks=None):
    if chunks is None:
        chunks = [{"content": "Hold 30 seconds, 3 sets a week.", "similarity": 0.9,
                   "source_type": "exercise_db", "document_title": "Back",
                   "chunk_index": 0}]
    return ToolMessage(content=json.dumps(chunks), tool_call_id="tc-kb",
                       name="kb_search")


def _config(**kw):
    base = {"persona_id": "anne", "locale": "en", "query": "squat",
            "request_id": "tj"}
    base.update(kw)
    return {"configurable": base}


def _state(tags, final="Tập Elbow plank nhé.", messages=None, retry_count=0):
    return {"required_outputs": tags, "final_answer": final,
            "messages": messages if messages is not None else [_kb_msg()],
            "retry_count": 0 if retry_count is None else retry_count,
            "total_tokens": 0, "errors": []}


def _judge_result(items):
    """Fake parsed output của LLM chấm: {"parsed": ns(items=[...])}."""
    ns_items = [SimpleNamespace(item=item, in_reply=ir, in_evidence=ie)
                for item, ir, ie in items]
    return {"parsed": SimpleNamespace(items=ns_items)}


def _patch_judge(monkey_ret):
    """Patch grader.get_chat_model; trả (structured_mock)."""
    from langgraph_agents.nodes import grader as grader_mod

    mock_llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(return_value=monkey_ret)
    mock_llm.with_structured_output = MagicMock(return_value=structured)
    p = patch.object(grader_mod, "get_chat_model", return_value=mock_llm)
    p.start()
    return p, structured


@pytest.mark.asyncio
async def test_missing_but_in_evidence_retries():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([
        ("exercise_protocol.amount", False, True),
        ("exercise_protocol.frequency", True, True),
    ]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_protocol"]), _config())
    finally:
        p.stop()
    assert result["grader_result"] == "retry"
    assert result["retry_count"] == 1
    assert result["grader_feedback"] == "- how many sets or reps, or how long to hold"
    assert result["grader_detail"]["exercise_protocol.amount"] == "synth_missed"
    assert "final_answer" not in result


@pytest.mark.asyncio
async def test_missing_and_source_silent_passes():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([
        ("exercise_protocol.amount", False, False),
        ("exercise_protocol.frequency", False, False),
    ]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_protocol"]), _config())
    finally:
        p.stop()
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {
        "exercise_protocol.amount": "source_silent",
        "exercise_protocol.frequency": "source_silent",
    }
    assert "retry_count" not in result


@pytest.mark.asyncio
async def test_all_present_passes():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([
        ("exercise_protocol.amount", True, True),
        ("exercise_protocol.frequency", True, False),
    ]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_protocol"]), _config())
    finally:
        p.stop()
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {
        "exercise_protocol.amount": "ok",
        "exercise_protocol.frequency": "ok",
    }


@pytest.mark.asyncio
async def test_judge_not_called_without_model_tags():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([]))
    try:
        result = await grader_mod.grader_node(
            _state(["scope_disclaimer", "evidence_citation"]), _config())
    finally:
        p.stop()
    structured.ainvoke.assert_not_called()
    assert result["grader_result"] == "pass"


@pytest.mark.asyncio
async def test_judge_not_called_without_evidence():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_steps"], messages=[]), _config())
    finally:
        p.stop()
    structured.ainvoke.assert_not_called()
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {"exercise_steps": "no_evidence"}


@pytest.mark.asyncio
async def test_judge_not_called_when_only_empty_results():
    from langgraph_agents.nodes import grader as grader_mod

    empty = ToolMessage(content="[]", tool_call_id="tc1", name="kb_search")
    p, structured = _patch_judge(_judge_result([]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_steps"], messages=[empty]), _config())
    finally:
        p.stop()
    structured.ainvoke.assert_not_called()
    assert result["grader_detail"] == {"exercise_steps": "no_evidence"}


@pytest.mark.asyncio
async def test_judge_error_passes_and_logs(caplog):
    import logging

    from langgraph_agents.nodes import grader as grader_mod

    mock_llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=TimeoutError("judge down"))
    mock_llm.with_structured_output = MagicMock(return_value=structured)
    with patch.object(grader_mod, "get_chat_model", return_value=mock_llm):
        with caplog.at_level(logging.WARNING, logger="langgraph.grader"):
            result = await grader_mod.grader_node(
                _state(["exercise_steps"]), _config())
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {}
    assert "grader_judge_failed" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("judge", [
    {"side_effect": TimeoutError("judge down")},
    {"return_value": {"raw": None, "parsed": None}},
], ids=["raises", "unparsed"])
async def test_judge_failure_keeps_closing_safety_lines(judge):
    """Lượt cho qua khi LLM chấm hỏng chỉ an toàn vì dòng kết do code nối, không qua LLM chấm."""
    from langgraph_agents.nodes import grader as grader_mod
    from langgraph_agents.tag_contract import get_safety_text

    mock_llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(**judge)
    mock_llm.with_structured_output = MagicMock(return_value=structured)
    tags = ["exercise_steps", "referral_advice", "scope_disclaimer"]
    with patch.object(grader_mod, "get_chat_model", return_value=mock_llm):
        result = await grader_mod.grader_node(_state(tags), _config())
    structured.ainvoke.assert_awaited_once()
    referral = get_safety_text("referral_advice", "anne", "en")
    disclaimer = get_safety_text("scope_disclaimer", "anne", "en")
    assert result["grader_result"] == "pass"
    assert result["final_answer"] == f"Tập Elbow plank nhé.\n\n{referral}\n\n{disclaimer}"


@pytest.mark.asyncio
async def test_judge_that_never_answers_is_cut_at_the_deadline(caplog):
    """Timeout của model chỉ tính từng lần đọc; wait_for giới hạn cả lời gọi."""
    import asyncio
    import logging
    import time

    from langgraph_agents.nodes import grader as grader_mod

    async def never_answers(*_args, **_kwargs):
        await asyncio.sleep(30)

    mock_llm = MagicMock()
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=never_answers)
    mock_llm.with_structured_output = MagicMock(return_value=structured)
    t0 = time.perf_counter()
    with patch.object(grader_mod, "get_chat_model", return_value=mock_llm), \
         patch.object(grader_mod, "role_timeout", return_value=0.05):
        with caplog.at_level(logging.WARNING, logger="langgraph.grader"):
            result = await grader_mod.grader_node(_state(["exercise_steps"]), _config())
    assert time.perf_counter() - t0 < 5
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {}
    assert any(getattr(r, "error", None) == "TimeoutError" for r in caplog.records)


@pytest.mark.asyncio
async def test_unparsed_output_passes(caplog):
    import logging

    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge({"raw": None, "parsed": None})
    try:
        with caplog.at_level(logging.WARNING, logger="langgraph.grader"):
            result = await grader_mod.grader_node(
                _state(["exercise_steps"]), _config())
    finally:
        p.stop()
    assert result["grader_result"] == "pass"
    assert "grader_judge_failed" in caplog.text


@pytest.mark.asyncio
async def test_unknown_and_missing_items_are_ok():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([("something_else", False, True)]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_steps"]), _config())
    finally:
        p.stop()
    assert result["grader_result"] == "pass"
    assert result["grader_detail"] == {"exercise_steps": "ok"}


@pytest.mark.asyncio
async def test_prompt_holds_only_this_turns_checks():
    from langgraph_agents.nodes import grader as grader_mod
    from langgraph_agents.tag_contract import get_safety_text

    opening = get_safety_text("red_flag_screen", "anne", "vi")
    final = opening + "\n\n" + "Thân bài."
    p, structured = _patch_judge(_judge_result([("contraindication", True, True)]))
    try:
        result = await grader_mod.grader_node(
            _state(["contraindication", "scope_disclaimer", "red_flag_screen"],
                   final=final),
            _config(locale="vi"))
    finally:
        p.stop()
    assert result["grader_result"] == "pass"
    user_text = structured.ainvoke.call_args[0][0][1][1]
    assert "- contraindication: who should not do this exercise, or should take care" in user_text
    assert "exercise_steps" not in user_text
    assert "Thân bài." in user_text
    assert opening not in user_text


@pytest.mark.asyncio
async def test_empty_body_retries_without_judge():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_steps"], final=""), _config())
    finally:
        p.stop()
    structured.ainvoke.assert_not_called()
    assert result["grader_result"] == "retry"
    assert result["retry_count"] == 1
    assert result["grader_feedback"] is None


@pytest.mark.asyncio
async def test_second_run_does_not_judge():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([]))
    try:
        result = await grader_mod.grader_node(
            _state(["exercise_steps", "scope_disclaimer"],
                   final="Tập nhẹ thôi.", retry_count=1), _config())
    finally:
        p.stop()
    structured.ainvoke.assert_not_called()
    assert result["grader_result"] == "pass_with_warning"
    assert "final_answer" in result


@pytest.mark.asyncio
async def test_grader_node_calls_no_regex_rule():
    from langgraph_agents.nodes import grader as grader_mod

    p, structured = _patch_judge(_judge_result([("exercise_steps", True, True)]))
    mocks = {}
    patches = []
    for name in ("_has_danger_warning", "_has_referral", "_has_disclaimer",
                 "_has_sets_reps", "_has_frequency", "_has_sets_reps_frequency",
                 "_has_ordered_steps", "_has_contraindication", "_has_source",
                 "_has_motion_fields"):
        m = MagicMock()
        mocks[name] = m
        patches.append(patch.object(grader_mod, name, m))
    try:
        for pt in patches:
            pt.start()
        result = await grader_mod.grader_node(
            _state(["exercise_steps"], final="Bước 1 đứng. Bước 2 ngồi."),
                   _config())
    finally:
        for pt in patches:
            pt.stop()
        p.stop()
    assert result["grader_result"] == "pass"
    for name, m in mocks.items():
        assert m.call_count == 0, name
