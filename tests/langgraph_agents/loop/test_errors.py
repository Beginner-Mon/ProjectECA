"""Tests for `loop.errors`: error kinds, classify, normalize."""

from __future__ import annotations

from langgraph_agents.loop.errors import (
    APPROVAL_UNAVAILABLE, BUDGET, CANCELLED, INVALID_ARGS, REFUSED_BY_POLICY,
    TRANSIENT, ToolResult, classify, normalize,
)


class _FakeSpec:
    def __init__(self, name="kb_search", max_result_chars_per_tool=8000):
        self.name = name
        self.max_result_chars_per_tool = max_result_chars_per_tool


def test_classify_uses_pipeline_mapper():
    exc = RuntimeError("boom")
    result = classify(exc)
    assert "kind" in result and "status" in result


def test_normalize_empty_set_becomes_found_false():
    spec = _FakeSpec()
    r = normalize(spec, "", remaining=1000)
    assert r.ok is True
    assert r.empty is True
    assert r.content == '{"found": false}'
    assert r.kind == "empty"


def test_normalize_list_empty():
    spec = _FakeSpec()
    r = normalize(spec, [], remaining=1000)
    assert r.empty is True
    assert r.content == '{"found": false}'


def test_normalize_error_detection():
    spec = _FakeSpec()
    r = normalize(spec, '{"error": "not found"}', remaining=1000)
    assert r.ok is False
    assert r.kind == "failed"
    assert r.empty is False


def test_normalize_truncates_per_tool():
    spec = _FakeSpec(max_result_chars_per_tool=5)
    long_text = "abcdefghij"
    r = normalize(spec, long_text, remaining=1000)
    assert r.content == long_text[:5]


def test_normalize_turn_remaining_cap():
    spec = _FakeSpec(max_result_chars_per_tool=10000)
    long_text = "x" * 100
    r = normalize(spec, long_text, remaining=10)
    assert r.content == "x" * 10


def test_normalize_source_label():
    spec = _FakeSpec(name="kb_search")
    r = normalize(spec, {"found": True}, remaining=10000)
    assert r.source_label == "kb_search"


def test_error_kinds_are_strings():
    assert INVALID_ARGS == "invalid_args"
    assert CANCELLED == "cancelled"
    assert BUDGET == "budget"
    assert REFUSED_BY_POLICY == "refused_by_policy"
    assert APPROVAL_UNAVAILABLE == "approval_unavailable"
    assert TRANSIENT == "transient"
