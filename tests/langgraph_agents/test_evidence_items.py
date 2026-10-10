"""Mục evidence dùng chung (grader-contract T1)."""
import json

import pytest
from langchain_core.messages import ToolMessage

from langgraph_agents.evidence import (
    classify_tool_result, evidence_items, named_items, render_evidence,
)
from langgraph_agents.nodes.synthesizer import _extract_tool_results

pytestmark = pytest.mark.unit


def _tm(name, payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id=f"tc-{name}", name=name)


_KB = [
    {"content": "Hold 30 seconds, 3 sets.", "similarity": 0.9,
     "source_type": "exercise_db", "document_title": "Elbow plank", "chunk_index": 0},
    {"content": "Stay active.", "similarity": 0.8,
     "source_type": "nhs_uk", "document_title": "NHS — Back pain", "chunk_index": 0},
]
_MEM = {"found": True, "results": [{"summary_text": "talked about squats"}]}
_WEB = [{"title": "Guide", "url": "https://x.example", "content": "..."}]


def test_render_matches_extract_tool_results():
    msgs = [_tm("kb_search", _KB), _tm("memory_search", _MEM), _tm("search_medical", _WEB)]
    assert render_evidence(evidence_items(msgs)) == _extract_tool_results(msgs)


def test_kb_items_carry_title_and_cite_key():
    items = evidence_items([_tm("kb_search", _KB)])
    assert [(i.cite_key, i.title) for i in items] == [
        ("exercise_db", "Elbow plank"), ("nhs_uk", "NHS — Back pain")]
    assert items[0].header == "[From ECA's exercise library: Elbow plank]"


def test_memory_has_no_cite_key_and_web_has_one():
    items = evidence_items([_tm("memory_search", _MEM), _tm("search_medical", _WEB)])
    assert [i.cite_key for i in items] == [None, "web"]


def test_motion_and_self_are_not_evidence():
    msgs = [_tm("generate_motion", {"state": "queued"}),
            _tm("recall_self", {"found": True, "results": []})]
    assert evidence_items(msgs) == []


def test_named_items_matches_title_case_insensitively():
    items = evidence_items([_tm("kb_search", _KB)])
    named = named_items(items, "Bài elbow PLANK giữ 30 giây.")
    assert [i.title for i in named] == ["Elbow plank"]


def test_named_items_never_returns_untitled_items():
    items = evidence_items([_tm("memory_search", _MEM)])
    assert named_items(items, "anything at all") == []


def test_classify_tool_result():
    assert classify_tool_result("[]") == "empty"
    assert classify_tool_result('{"found": false}') == "empty"
    assert classify_tool_result('{"error": "TimeoutError"}') == "error"
    assert classify_tool_result('[{"content": "x"}]') == "hits"
