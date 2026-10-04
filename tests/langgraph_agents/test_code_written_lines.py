"""Câu an toàn và dòng nguồn do code phát (grader-contract T4)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from langgraph_agents.tag_contract import get_safety_text

pytestmark = pytest.mark.unit


def _config(**kw):
    base = {"persona_id": "anne", "locale": "vi", "query": "đau ngực",
            "request_id": "t"}
    base.update(kw)
    return {"configurable": base}


def _state(tags, messages=None):
    return {"messages": messages or [], "resolved_query": "đau ngực",
            "required_outputs": tags, "needs_clarification": False,
            "total_tokens": 0, "errors": [], "retry_count": 0}


def _tm(name, payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id=f"tc-{name}", name=name)


_KB = [{"content": "Cat-cow for back pain.", "similarity": 0.9,
        "source_type": "exercise_db", "document_title": "Back", "chunk_index": 0}]


def _astream(*parts):
    async def gen(_msgs):
        for p in parts:
            yield AIMessageChunk(content=p)
    return gen


def _streaming_llm(captured, *parts):
    mock_llm = MagicMock()

    async def gen(msgs):
        captured.append(msgs)
        for p in parts:
            yield AIMessageChunk(content=p)
    mock_llm.astream = gen
    return mock_llm


def _opening():
    return get_safety_text("red_flag_screen", "anne", "vi")


@pytest.mark.asyncio
async def test_opening_is_streamed_first():
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    tags = ["red_flag_screen", "referral_advice"]
    with patch.object(syn_mod, "get_chat_model",
                       return_value=_streaming_llm(captured, "Đau ngực cần khám.")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        result = await syn_mod.synthesizer_node(_state(tags), _config())
    opening = _opening()
    contents = [p["content"] for p in sent if "content" in p]
    assert contents[0] == opening + "\n\n"
    assert result["final_answer"] == opening + "\n\n" + "Đau ngực cần khám."


@pytest.mark.asyncio
async def test_prompt_tells_model_what_is_already_said():
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    tags = ["red_flag_screen", "referral_advice"]
    with patch.object(syn_mod, "get_chat_model",
                       return_value=_streaming_llm(captured, "Đau ngực cần khám.")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        await syn_mod.synthesizer_node(_state(tags), _config())
    opening = _opening()
    system = captured[0][0].content
    assert "## Already on the user's screen" in system
    assert opening in system
    assert "## Added after your reply" in system
    assert "a referral to a doctor" in system
    assert "Your FIRST sentence must warn" not in system


@pytest.mark.asyncio
async def test_no_contract_blocks_without_code_tags():
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    with patch.object(syn_mod, "get_chat_model",
                       return_value=_streaming_llm(captured, "ok")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        await syn_mod.synthesizer_node(_state(["exercise_steps"]), _config())
    system = captured[0][0].content
    assert "## Already on the user's screen" not in system
    assert "## Added after your reply" not in system


@pytest.mark.asyncio
async def test_deliverables_list_only_model_tags():
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    tags = ["scope_disclaimer", "exercise_steps", "evidence_citation"]
    with patch.object(syn_mod, "get_chat_model",
                       return_value=_streaming_llm(captured, "ok")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        await syn_mod.synthesizer_node(_state(tags, [_tm("kb_search", _KB)]),
                                       _config())
    system = captured[0][0].content
    marker = "## Required deliverables (tags)"
    after = system.split(marker, 1)[1].strip().splitlines()[0].strip()
    assert after == "exercise_steps"


@pytest.mark.asyncio
async def test_number_rule_in_synthesize_and_refuse():
    from langgraph_agents.nodes import synthesizer as syn_mod

    for tags, msgs in ((["exercise_steps"], [_tm("kb_search", _KB)]),
                       (["exercise_steps"], [])):
        captured: list = []
        sent: list = []
        with patch.object(syn_mod, "get_chat_model",
                           return_value=_streaming_llm(captured, "ok")), \
             patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
            await syn_mod.synthesizer_node(_state(tags, msgs), _config())
        assert "come only from the evidence" in captured[0][0].content


@pytest.mark.asyncio
async def test_fallback_runs_when_primary_fails_at_first_byte():
    from langgraph_agents.nodes import synthesizer as syn_mod

    async def boom(_msgs):
        raise RuntimeError("primary down")
        yield  # pragma: no cover

    fallback = MagicMock()
    fallback.ainvoke = AsyncMock(return_value=AIMessage(content="ok"))
    sent: list = []
    with patch.object(syn_mod, "get_chat_model") as mock_llm, \
         patch.object(syn_mod, "get_fallback_chat_model",
                      return_value=fallback) as mock_fb, \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        mock_llm.return_value.astream = boom
        result = await syn_mod.synthesizer_node(
            _state(["red_flag_screen"]), _config())
    assert mock_fb.call_count == 1
    opening = _opening()
    assert result["final_answer"].startswith(opening)
    assert result["final_answer"].endswith("ok")


@pytest.mark.asyncio
async def test_total_failure_keeps_opening():
    from langgraph_agents.nodes import synthesizer as syn_mod

    async def boom(_msgs):
        raise RuntimeError("primary down")
        yield  # pragma: no cover

    with patch.object(syn_mod, "get_chat_model") as mock_llm, \
         patch.object(syn_mod, "get_fallback_chat_model", return_value=None), \
         patch.object(syn_mod, "get_stream_writer", return_value=lambda p: None):
        mock_llm.return_value.astream = boom
        result = await syn_mod.synthesizer_node(
            _state(["red_flag_screen"]), _config())
    assert result["final_answer"].startswith(_opening())
    assert any(e.get("severity") == "critical" for e in result.get("errors", []))


def _kb_elbow():
    return _tm("kb_search", [{"content": "Hold 30 seconds.", "similarity": 0.9,
                              "source_type": "exercise_db",
                              "document_title": "Elbow plank", "chunk_index": 0}])


@pytest.mark.asyncio
async def test_grader_appends_closing_lines_once():
    from langgraph_agents.nodes import grader as grader_mod

    tags = ["referral_advice", "evidence_citation", "scope_disclaimer"]
    state = {"required_outputs": tags, "final_answer": "Tập Elbow plank nhé.",
             "messages": [_kb_elbow()], "retry_count": 0, "total_tokens": 0}
    sent: list = []
    with patch.object(grader_mod, "get_stream_writer", return_value=sent.append):
        result = await grader_mod.grader_node(state, _config())
    referral = get_safety_text("referral_advice", "anne", "vi")
    disclaimer = get_safety_text("scope_disclaimer", "anne", "vi")
    source = "*Nguồn: thư viện bài tập của ECA — Elbow plank.*"
    expected = "Tập Elbow plank nhé." + "".join(f"\n\n{line}" for line in
                                               (referral, source, disclaimer))
    assert result["final_answer"] == expected
    tail = "".join(f"\n\n{line}" for line in (referral, source, disclaimer))
    assert sent == [{"content": tail}]
    for line in (referral, source, disclaimer):
        assert result["final_answer"].count(line) == 1


@pytest.mark.asyncio
async def test_no_unverified_line_anywhere():
    from langgraph_agents.nodes import grader as grader_mod

    state = {"required_outputs": ["exercise_steps"],
             "final_answer": "Tập nhẹ nhàng thôi.",
             "messages": [], "retry_count": 1, "total_tokens": 0}
    with patch.object(grader_mod, "get_stream_writer",
                      return_value=lambda p: None):
        result = await grader_mod.grader_node(state, _config())
    assert "has not been verified" not in result["final_answer"]
    assert "chưa được kiểm chứng" not in result["final_answer"]


@pytest.mark.asyncio
async def test_safety_tags_never_trigger_template_insertion():
    from langgraph_agents.nodes import grader as grader_mod

    state = {"required_outputs": ["referral_advice"],
             "final_answer": "Tập nhẹ thôi.",
             "messages": [], "retry_count": 0, "total_tokens": 0}
    with patch.object(grader_mod, "get_stream_writer",
                      return_value=lambda p: None):
        result = await grader_mod.grader_node(state, _config())
    assert result["final_answer"].startswith("Tập nhẹ thôi.")
