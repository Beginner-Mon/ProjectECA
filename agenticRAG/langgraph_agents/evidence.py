"""Evidence của một lượt: một nơi dựng, ba nơi đọc (synthesizer, grader, dòng nguồn)."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from langchain_core.messages import ToolMessage

from langgraph_agents.shared.context import budget_chars
from langgraph_agents.sources import kb_segment_label, source_for_tool

EVIDENCE_PER_MESSAGE_CAP = 1500
_CITABLE_SOURCE_IDS = ("web", "video")


@dataclass(frozen=True)
class EvidenceItem:
    header: str           # "[From ECA's exercise library: Elbow plank]"
    text: str             # nội dung, đã cắt theo EVIDENCE_PER_MESSAGE_CAP
    cite_key: str | None  # "exercise_db" | "nhs_uk" | "web" | "video" | None
    title: str            # document_title; "" khi không có


def classify_tool_result(content: str) -> str:
    """'empty' | 'error' | 'hits'."""
    if content in ("", "[]", "{}", '{"found": false}'):
        return "empty"
    if '"error"' in content:
        return "error"
    return "hits"


def evidence_messages(messages: list) -> list:
    """ToolMessage được tính là evidence tra cứu (bỏ nguồn is_evidence=False)."""
    out = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        src = source_for_tool(m.name or "")
        if src is not None and not src.is_evidence:
            continue
        out.append(m)
    return out


def _message_items(m: ToolMessage) -> list[EvidenceItem]:
    name = m.name or ""
    src = source_for_tool(name)
    header = "[From another source]" if src is None else f"[From {src.label}]"
    cite_key = src.id if src is not None and src.id in _CITABLE_SOURCE_IDS else None
    whole = [EvidenceItem(header=header, text=str(m.content), cite_key=cite_key, title="")]
    if name != "kb_search":
        return whole
    try:
        data = json.loads(str(m.content))
    except (json.JSONDecodeError, TypeError):
        return whole
    if not isinstance(data, list) or not data:
        return whole
    items = []
    for seg in data:
        if not isinstance(seg, dict):
            continue
        source_type = seg.get("source_type", "")
        label = kb_segment_label(source_type)
        if label is None:
            continue
        doc = seg.get("document_title") or ""
        seg_header = f"[From {label}]" if not doc else f"[From {label}: {doc}]"
        items.append(EvidenceItem(header=seg_header, text=str(seg.get("content", "")),
                                  cite_key=source_type, title=doc))
    return items


def evidence_items(messages: list) -> list[EvidenceItem]:
    """Các mục evidence đưa cho synthesizer: mới nhất được giữ trước, trả theo thứ tự thời gian."""
    pieces: list[EvidenceItem] = []
    for m in evidence_messages(messages):
        pieces.extend(_message_items(m))
    kept: list[EvidenceItem] = []
    used = 0
    budget = budget_chars("evidence")
    for item in reversed(pieces):
        text = item.text[:EVIDENCE_PER_MESSAGE_CAP]
        if kept and used + len(text) > budget:
            break
        kept.append(replace(item, text=text))
        used += len(text)
    kept.reverse()
    return kept


def render_evidence(items: list[EvidenceItem]) -> str:
    return "\n\n".join(f"{i.header}\n{i.text}" for i in items)


def named_items(items: list[EvidenceItem], answer: str) -> list[EvidenceItem]:
    """Các mục có tên xuất hiện trong câu trả lời (không phân biệt hoa thường)."""
    low = (answer or "").lower()
    return [i for i in items if i.title and i.title.lower() in low]
