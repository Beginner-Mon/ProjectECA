"""Nguồn có kiểu (plan T2) — một nơi duy nhất nói mỗi tool là nguồn gì.

Ba nơi đọc bảng này, không tự đặt tên riêng:
  - nhãn UI (`stage_key`, dùng ở api/main.py — T6),
  - tiêu đề evidence (`label`, dùng ở nodes/synthesizer.py — T3),
  - cách dẫn nguồn trong prompt (persona đọc `label` — T3).

Thêm tool mới: thêm một dòng ở TOOL_SOURCES. Không thêm cờ/tag planner.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str            # "library" | "memory" | "web" | "video" | "self" | "motion"
    label: str         # tên gọi trong prompt, tiếng Anh
    stage_key: str | None   # khóa UI string, hoặc None
    is_evidence: bool  # False với self và motion


TOOL_SOURCES: dict[str, Source] = {
    "kb_search": Source(
        id="library",
        label="ECA's exercise library",
        stage_key="stage_searching",
        is_evidence=True,
    ),
    "memory_search": Source(
        id="memory",
        label="your earlier conversations with this user",
        stage_key="stage_recalling",
        is_evidence=True,
    ),
    "resume_last_session": Source(
        id="memory",
        label="your earlier conversations with this user",
        stage_key="stage_recalling",
        is_evidence=True,
    ),
    "search_medical": Source(
        id="web",
        label="the web",
        stage_key="stage_web",
        is_evidence=True,
    ),
    "youtube_transcript": Source(
        id="video",
        label="the video the user sent",
        stage_key="stage_video",
        is_evidence=True,
    ),
    # Kiến thức về bản thân (T8) — không phải evidence tra cứu.
    "recall_self": Source(
        id="self",
        label="About you",
        stage_key=None,
        is_evidence=False,
    ),
    # Trạng thái cơ thể (T4, T9) — không phải evidence tra cứu.
    "generate_motion": Source(
        id="motion",
        label="body state",
        stage_key=None,
        is_evidence=False,
    ),
    "show_movement": Source(
        id="motion",
        label="body state",
        stage_key=None,
        is_evidence=False,
    ),
}


def source_for_tool(name: str) -> Source | None:
    """Trả Source của tool, hoặc None với tool lạ (tiêu đề evidence dự phòng)."""
    return TOOL_SOURCES.get(name)


# ── Nhãn theo từng đoạn KB (B5) ──────────────────────────────────────────
# kb_search trả cả exercise_db và nhs_uk; tiêu đề evidence đặt theo
# source_type của ĐOẠN (nodes/synthesizer._split_message_parts), kèm
# document_title — không gọi NHS là "thư viện ECA".

KB_SOURCE_TYPE_LABELS: dict[str, str] = {
    "exercise_db": "ECA's exercise library",
    "nhs_uk": "NHS health guidance",
}


def kb_segment_label(source_type: str) -> str | None:
    """Nhãn evidence cho một đoạn KB; None = loại lạ, vẫn bị loại (B5)."""
    return KB_SOURCE_TYPE_LABELS.get(source_type or "")
