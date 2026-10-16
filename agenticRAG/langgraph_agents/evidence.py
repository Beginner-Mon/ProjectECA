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


def _when(date: str, title: str | None = None) -> str:
    """Date and title, as `2026-10-12 — Knee talk`; either alone; empty when neither is known."""
    day = str(date or "")[:10]
    return f"{day} — {title}" if day and title else (day or title or "")


def _item(header: str, *parts: str) -> EvidenceItem:
    text = "\n".join(p for p in parts if p)
    return EvidenceItem(header=header, text=text, cite_key=None, title="")


def _resumed_items(data, header: str) -> list[EvidenceItem]:
    """resume_last_session: the newest conversation as readable text, then (when
    the two newest were close in time) the others the user could have meant.

    The tool always returns the newest conversation normally; the candidates
    are there so the answer can name which one it used in one sentence.
    """
    if not isinstance(data, dict) or not data.get("found"):
        return []
    lines = []
    for m in data.get("recent_messages") or []:
        if isinstance(m, dict) and m.get("content"):
            lines.append(f"{'User' if m.get('role') == 'user' else 'You'}: {m['content']}")
    items = [_item(header, _when(data.get("updated_at", ""), data.get("title")),
                   *[str(c) for c in data.get("summary_chunks") or []], *lines)]
    candidates = [c for c in data.get("candidates") or [] if isinstance(c, dict)]
    if data.get("ambiguous") and candidates:
        listed = [f"- {_when(c.get('updated_at', ''))}: {c.get('preview', '')}"
                  for c in candidates]
        items.append(_item(header, "Using the most recent conversation; "
                           "if the user meant a different one, "
                           "they can say which:", *listed))
    return items


def _memory_items(data, header: str) -> list[EvidenceItem]:
    """Readable items for the resume tool's result.

    Never raw JSON: the answering model would repeat our key names and ids.
    """
    return _resumed_items(data, header)


def _message_items(m: ToolMessage) -> list[EvidenceItem]:
    name = m.name or ""
    src = source_for_tool(name)
    header = "[From another source]" if src is None else f"[From {src.label}]"
    cite_key = src.id if src is not None and src.id in _CITABLE_SOURCE_IDS else None
    whole = [EvidenceItem(header=header, text=str(m.content), cite_key=cite_key, title="")]
    if name not in ("kb_search", "resume_last_session"):
        return whole
    try:
        data = json.loads(str(m.content))
    except (json.JSONDecodeError, TypeError):
        return whole
    if name != "kb_search":
        return _memory_items(data, header)
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
        if classify_tool_result(str(m.content)) == "error":
            continue
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
