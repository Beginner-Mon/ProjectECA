"""Bảng nguồn có kiểu (plan T2): mỗi tool khai báo một lần nó là nguồn gì."""

from langgraph_agents.nodes.retriever_agent import RETRIEVER_BASE_TOOLS
from langgraph_agents.sources import source_for_tool


def _base_tool_names() -> set[str]:
    return {t.name for t in RETRIEVER_BASE_TOOLS}


def test_every_base_tool_has_a_source():
    missing = [n for n in _base_tool_names() if source_for_tool(n) is None]
    assert not missing, f"tool chưa khai nguồn: {missing}"


def test_unknown_tool_returns_none():
    assert source_for_tool("no_such_tool") is None


def test_library_source_shape():
    src = source_for_tool("kb_search")
    assert src.id == "library"
    assert src.is_evidence is True
    assert src.stage_key == "stage_searching"


def test_non_evidence_sources():
    # self và motion không phải evidence (T3/T4 đọc cờ này).
    assert source_for_tool("recall_self").is_evidence is False
    assert source_for_tool("generate_motion").is_evidence is False
    assert source_for_tool("show_movement").is_evidence is False
