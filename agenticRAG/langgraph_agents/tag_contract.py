"""Bảng tag (grader-contract T3): ai viết, vị trí, mục kiểm; câu cố định do code phát."""

from __future__ import annotations

from dataclasses import dataclass

from langgraph_agents.evidence import named_items
from langgraph_agents.nodes._persona_loader import PersonaError, get_persona
from langgraph_agents.sources import CITATIONS, SOURCE_LINE_PREFIX

# ── Mẫu an toàn mặc định ─────────────────────────────────────────────────

DEFAULT_SAFETY_TEMPLATES: dict[str, str] = {
    "red_flag_screen": (
        "**Cảnh báo quan trọng:** Triệu chứng bạn mô tả có thể là dấu hiệu "
        "của một tình trạng nghiêm trọng. Bạn nên NGỪNG tập luyện ngay và đi "
        "khám bác sĩ chuyên khoa để được chẩn đoán chính xác."
    ),
    "referral_advice": (
        "**Lưu ý:** Với câu hỏi này, tôi khuyên bạn nên tham khảo ý kiến "
        "bác sĩ hoặc chuyên gia y tế. Tôi chỉ có thể cung cấp thông tin tham "
        "khảo về wellness, không thay thế chẩn đoán lâm sàng."
    ),
    "scope_disclaimer": (
        "*Thông tin này chỉ mang tính tham khảo về wellness và không thay "
        "thế cho việc khám và chẩn đoán y tế chuyên nghiệp.*"
    ),
}

# English counterparts. These are injected VERBATIM — the grader is rule-based
# and never calls an LLM — so a user who asked in English used to receive a
# perfectly English answer with a Vietnamese safety warning stapled to it. That
# is worst precisely where it matters most: the red-flag text is the one
# sentence the reader must not skip.
#
# No emoji, matching the Vietnamese set: emphasis is carried by bold text, so a
# terminal or screen reader that drops the glyph loses nothing.
DEFAULT_SAFETY_TEMPLATES_EN: dict[str, str] = {
    "red_flag_screen": (
        "**Important warning:** the symptoms you describe may indicate a "
        "serious condition. Please STOP exercising now and see a doctor for a "
        "proper diagnosis."
    ),
    "referral_advice": (
        "**Note:** for this question I recommend consulting a doctor or a "
        "qualified health professional. I can only offer general wellness "
        "information, which does not replace a clinical diagnosis."
    ),
    "scope_disclaimer": (
        "*This is general wellness information and does not replace "
        "professional medical examination or diagnosis.*"
    ),
}


def get_safety_text(tag: str, persona_id: str, lang: str = "vi") -> str:
    """The safety line for one tag, in the character's own words, in `lang`.

    Resolution order, most specific first:
        persona overlay for `lang` → module default for `lang` → ""

    The `<tag>.en` suffix convention is gone. It existed because a persona was a
    single flat file that had to carry two languages at once; now each language
    is its own overlay file (`personas/<slug>/<lang>.md`) and the key is just
    `<tag>` in both. A persona that has no overlay for `lang` gets the neutral
    default rather than a warning in the wrong language.

    Falls back rather than raising, unlike `get_persona`: every caller is already
    injecting text into a reply that is about to ship, and a missing template
    must degrade to the generic warning, never to silence.
    """
    lang = lang if lang in ("vi", "en") else "en"
    try:
        templates = get_persona(persona_id, lang).get("safety_templates") or {}
    except PersonaError:
        templates = {}
    custom = templates.get(tag)
    if custom:
        return custom
    defaults = DEFAULT_SAFETY_TEMPLATES_EN if lang == "en" else DEFAULT_SAFETY_TEMPLATES
    return defaults.get(tag, "")


# ── Bảng tag ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckItem:
    key: str          # "exercise_protocol.amount"
    definition: str   # tiếng Anh; dùng cho LLM chấm và lời nhắc viết thêm


@dataclass(frozen=True)
class TagSpec:
    writer: str       # "code" | "model"
    position: str     # "opening" | "body" | "closing"
    checks: tuple = ()


TAG_CONTRACT: dict[str, TagSpec] = {
    "red_flag_screen": TagSpec("code", "opening"),
    "referral_advice": TagSpec("code", "closing"),
    "evidence_citation": TagSpec("code", "closing"),
    "scope_disclaimer": TagSpec("code", "closing"),
    "exercise_protocol": TagSpec("model", "body", (
        CheckItem("exercise_protocol.amount", "how many sets or reps, or how long to hold"),
        CheckItem("exercise_protocol.frequency", "how often, per day or per week"),
    )),
    "exercise_steps": TagSpec("model", "body", (
        CheckItem("exercise_steps", "the movement as two or more ordered steps"),)),
    "contraindication": TagSpec("model", "body", (
        CheckItem("contraindication", "who should not do this exercise, or should take care"),)),
    "motion_descriptor": TagSpec("model", "body", (
        CheckItem("motion_descriptor", "how the body moves and which joints are involved"),)),
}

_CLOSING_NOTE = {
    "referral_advice": "a referral to a doctor",
    "evidence_citation": "the source line",
    "scope_disclaimer": "a scope disclaimer",
}


def model_tags(required_outputs: list) -> list[str]:
    """Các tag do model viết, theo thứ tự của bảng."""
    return [t for t, s in TAG_CONTRACT.items() if s.writer == "model" and t in required_outputs]


def check_items(required_outputs: list) -> list[CheckItem]:
    return [c for t in model_tags(required_outputs) for c in TAG_CONTRACT[t].checks]


def opening_line(required_outputs: list, persona_id: str, locale: str) -> str:
    if "red_flag_screen" not in required_outputs:
        return ""
    return get_safety_text("red_flag_screen", persona_id, locale)


def closing_items_note(required_outputs: list) -> str:
    return ", ".join(text for tag, text in _CLOSING_NOTE.items() if tag in required_outputs)


def source_line(items: list, answer: str, locale: str) -> str:
    """Dòng nguồn; "" khi không nguồn nào được nhận ra trong câu trả lời."""
    lang = locale if locale in SOURCE_LINE_PREFIX else "en"
    parts = []
    for key, cite in CITATIONS.items():
        of_source = [i for i in items if i.cite_key == key]
        if not of_source:
            continue
        if cite.cite_when == "entry_named":
            titles = list(dict.fromkeys(i.title for i in named_items(of_source, answer)))
            if titles:
                parts.append(f"{cite.label[lang]} — {', '.join(titles[:3])}")
        elif cite.cite_when == "name_mentioned":
            if cite.name in (answer or ""):
                parts.append(cite.label[lang])
        else:
            parts.append(cite.label[lang])
    if not parts:
        return ""
    return f"*{SOURCE_LINE_PREFIX[lang]}: {'; '.join(parts)}.*"


def closing_lines(required_outputs: list, persona_id: str, locale: str,
                  items: list, answer: str) -> list[str]:
    """Các dòng kết theo thứ tự: khuyên gặp bác sĩ, dòng nguồn, disclaimer."""
    lines = []
    if "referral_advice" in required_outputs:
        lines.append(get_safety_text("referral_advice", persona_id, locale))
    if "evidence_citation" in required_outputs:
        lines.append(source_line(items, answer, locale))
    if "scope_disclaimer" in required_outputs:
        lines.append(get_safety_text("scope_disclaimer", persona_id, locale))
    return [line for line in lines if line]
