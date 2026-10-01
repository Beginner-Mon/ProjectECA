"""Evidence theo nguồn (plan T3): motion không phải evidence, tiêu đề theo nguồn."""

import json

from langchain_core.messages import ToolMessage

from langgraph_agents.nodes.synthesizer import (
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
