"""Grader node — M.3 tag-driven, rule-based, deterministic.

Decisions encoded:
  D6:   Tag 2 loại: safety → template cứng (no retry); quality → retry max 1
  D7:   TAG_RULES = 1 dict nguồn-chân-lý + startup assertion
  D8:   required_outputs=[] → grader skip (handled by routing, D15)
  D31:  Rule-based = lưới chặn "quên hẳn", NOT judge intensity
  D32:  Safety warning = ĐẦU (synthesizer writes, grader verifies);
        Unverified disclaimer = CUỐI (grader appends)
  D33:  Danger detection = PLANNER (1 place); grader only checks presence

Grader scope (D31 — honest about limits):
  - CAN catch: absent marker ("quên hẳn" cảnh báo → has_referral=false → fail)
  - CANNOT catch: intensity ("có nhắc bác sĩ nhưng quá nhẹ" → regex sees "bác sĩ" → pass)
  - Intensity is persona prompt's job; grader is the coarse net.
"""

from __future__ import annotations

import re
import time
from typing import Callable

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from langgraph_agents.state import AgentState
from langgraph_agents.nodes._persona_loader import PersonaError, get_persona
from langgraph_agents.shared.logging import get_logger
from langgraph_agents.tag_contract import (
    DEFAULT_SAFETY_TEMPLATES,
    DEFAULT_SAFETY_TEMPLATES_EN,
    TAG_CONTRACT,
    get_safety_text,
)

logger = get_logger("langgraph.grader")


# ═══════════════════════════════════════════════════════════════════════════
# TAG_RULES — single source of truth (D7)
# ═══════════════════════════════════════════════════════════════════════════

# ── Rule check functions (heuristic marker-based, NOT LLM — D31) ─────────

def _has_danger_warning(text: str) -> bool:
    """Check for a red-flag warning: it names the danger AND says what to do now.

    Either half alone is ordinary text. "Dấu hiệu bạn tập đúng là…",
    "Warning: 5-day streak" and "nên gặp bác sĩ" about a 3-month-old ache all
    passed when any one keyword was enough — 5 of 11 answers without a warning,
    measured 29/09 with scripts/eval_grader_rules.py.

    An emergency instruction (cấp cứu, call 999, A&E) or ⚠ is enough on its own:
    nobody writes those casually, and this tag only runs when the planner has
    already seen a danger sign in the question.

    Task 3 (persona templates): the stop/check commands in Anne's and Bronya's
    EN lines ("Stop there", "go get it checked", "needs a qualified
    professional") are actions, so they sit in `act` and still need the danger
    named — the templates do it with "This sign". On their own they would let
    any "see a doctor" sign-off pass, which is the miss measured above.
    "Stop me/here" and "dừng ở đây" match neither half (bảng âm tính).
    """
    if not text:
        return False
    emergency = [
        r"(?:cấp cứu|gọi\s*(?:số\s*)?115|đến bệnh viện ngay|đi khám ngay)",
        r"(?:(?:call|dial)\s*(?:999|911|112)|\bA&E\b|emergency (?:room|department|services|care)"
        r"|seek (?:urgent|immediate|emergency) (?:medical )?(?:help|care|attention))",
        r"⚠",
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in emergency):
        return True
    serious = (r"(?:nguy hiểm|nghiêm trọng|khẩn cấp|dấu hiệu|triệu chứng"
               r"|danger|serious|warning|red flag|sign of|this sign|these signs|symptom|chest pain)")
    act = (r"(?:(?:ngừng|dừng)\s*(?:tập|ngay|lập tức|lại)|đi khám|gặp bác sĩ|gặp chuyên gia"
           r"|stop\s+(?:training|exercising|immediately|right away|now|there|the exercise)"
           r"|see (?:a |your )?(?:doctor|gp)|(?:go\s+)?get\s+it\s+(?:checked|looked\s+at)"
           r"|needs?\s+a\s+(?:doctor|qualified professional))")
    return bool(re.search(serious, text, re.IGNORECASE) and re.search(act, text, re.IGNORECASE))


def _has_referral(text: str) -> bool:
    """Check for referral/consultation recommendation.

    29/09 (three patterns after the base three): referrals those could not
    see — "nên đi khám" with no doctor after it, "see your GP", "a
    physiotherapist can assess", "go to A&E": 6 of 9 missed, measured with
    scripts/eval_grader_rules.py. A miss here staples a second referral onto an
    answer that already had one.

    Task 3: mỗi mẫu mới đòi CẢ HAI — một hành động/nhu cầu VÀ một đối tượng
    y tế. Nhắc tới bác sĩ thôi ("My doctor friend likes squats") thì không đủ.
    """
    professional = r"(?:doctor|physician|specialist|gp|physio(?:therapist)?|(?:medical|healthcare|health) (?:professional|provider))"
    _MEDICAL_OBJECT = r"(?:doctor|GP|physician|specialist|medical professional|health professional|bác sĩ|chuyên gia y tế)"
    patterns = [
        r"(?:khuyên|nên|hãy)\s*(?:bạn\s*)?(?:đi khám|gặp|hỏi|tham khảo)\s*(?:ý kiến\s*)?(?:bác sĩ|chuyên gia|bác sĩ chuyên khoa|chuyên viên y tế)",
        r"(?:consult|see|visit|refer)\s*(?:a\s*)?(?:doctor|physician|specialist|medical professional)",
        r"(?:không thể|không đủ)\s*(?:chẩn đoán|kê đơn|điều trị)",
        # 29/09 (eval)
        r"đi khám|gặp\s*(?:bác sĩ|chuyên gia|chuyên viên)|khám chuyên khoa|gọi\s*(?:số\s*)?115|cấp cứu",
        rf"(?:consult|see|visit|talk to|speak to|ask)\s+(?:a|an|your|the)\s+{professional}"
        r"|\bA&E\b|(?:call|dial)\s*(?:999|911|112)|emergency (?:department|room|services)|urgent care",
        rf"{professional}\s+(?:can|could|should|will)\s+(?:assess|check|examine|diagnose|help)",
        # Task 3: see/consult/consulting/visit + đối tượng y tế
        # ("see a doctor", "consulting a doctor", "Go see a specialist").
        r"(?:see|consult|consulting|visit)\s+(?:a\s+|the\s+)?" + _MEDICAL_OBJECT,
        # Task 3: examined by / get checked by / go to + đối tượng y tế
        # ("examined by a specialist").
        r"(?:examined by|get checked by|go to)\s+(?:a\s+|the\s+)?" + _MEDICAL_OBJECT,
        # Task 3: nhu cầu (needs) + đối tượng y tế.
        r"needs?\s+(?:a\s+|the\s+)?" + _MEDICAL_OBJECT,
        # Task 3: "specialist diagnosis is required" (Bronya EN) — nhu cầu
        # dưới dạng bị động + chuyên gia.
        r"(?:specialist|doctor|physician|GP)\s+diagnosis\s+is\s+required",
        # Task 3: cần/nên + bác sĩ/chuyên gia (Bronya VI "Cần bác sĩ…").
        r"(?:cần|nên)\s+(?:bác sĩ|chuyên gia)\b",
        # Task 3: gặp/được + bác sĩ/chuyên gia … khám (Hatsune VI "gặp bác sĩ
        # … được khám", Miki VI "được bác sĩ … khám").
        r"(?:gặp|được)\s+(?:bác sĩ|chuyên gia)[\w\s]{0,40}?khám",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _has_disclaimer(text: str) -> bool:
    """Check for wellness scope disclaimer.

    29/09 (three patterns after the base five): "không thay thế lời khuyên của
    bác sĩ", "not medical advice", "mình không thể chẩn đoán" — 4 of 6 missed,
    measured with scripts/eval_grader_rules.py. B2 (the rest): the personas'
    own disclaimer lines, in both languages.
    """
    patterns = [
        r"(?:tư vấn|hướng dẫn)\s*(?:wellness|sức khỏe|thể chất)",
        r"(?:không thay thế|không phải là)\s*(?:cho\s*(?:việc\s*)?)?(?:thăm\s*)?(?:khám|chẩn đoán|điều trị)\s*(?:lâm sàng|y tế|y khoa)",
        r"(?:tham khảo|chỉ mang tính)\s*(?:tham khảo|giáo dục)",
        r"(?:wellness|educational|informational)\s*(?:advice|purpose)",
        r"(?:không phải|không thể)\s*(?:thay thế|coi là)\s*(?:lời khuyên y tế|chẩn đoán)",
        # 29/09 (eval)
        r"(?:chỉ là|chỉ mang tính)\s*(?:thông tin\s*)?tham khảo"
        r"|không thay thế\s*(?:cho\s*)?(?:việc\s*)?(?:thăm\s*)?(?:lời khuyên|ý kiến|khám)[^.\n]{0,30}(?:bác sĩ|y tế|chuyên gia)",
        r"not (?:a substitute for |a replacement for )?(?:professional )?medical advice"
        r"|(?:does not|doesn't|cannot|can't) replace (?:a |your )?(?:doctor|medical|professional)",
        # First person only: "bạn không thể chẩn đoán nếu chỉ dựa vào cảm giác" is advice, not scope.
        r"(?:mình|tôi|em|trợ lý|AI)\s*(?:không thể|không được phép)\s*chẩn đoán|\bI\s*(?:cannot|can't)\s*diagnose",
        # B2: "not (as) a replacement/substitute for … clinical/medical/
        # doctor('s) examination/advice/diagnosis" (Anne/Bronya/Miki EN).
        r"not\s+(?:as\s+)?a\s+(?:replacement|substitute)\s+for\s+"
        r"(?:a\s+|an\s+|the\s+)?[\w\s']{0,40}?"
        r"(?:clinical|medical|doctor(?:'s)?)\s+"
        r"(?:examination|exam|advice|diagnosis|diagnoses|consultation)",
        # B2: "does not replace … medical examination/diagnosis" (default EN).
        r"(?:does not|doesn't|do not|don't|is not|isn't)\s+replace\s+"
        r"[\w\s]{0,40}?(?:clinical|medical|professional)\s+"
        r"(?:medical\s+)?(?:examination|exam|advice|diagnosis|diagnoses)",
        # B2: "can't stand in for a doctor" (Hatsune EN).
        r"(?:can't|cannot|can ?not)\s+stand in for a (?:doctor|physician)",
        # B2: "không thay bác sĩ" (Hatsune VI).
        r"không thay\s+(?:bác sĩ|chuyên gia)",
        # B2: "chẩn đoán của bác sĩ" sau phủ định thay thế (Miki VI).
        r"chẩn đoán của bác sĩ",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _has_sets_reps(text: str) -> bool:
    """Có số hiệp/lần (nửa đầu của _has_sets_reps_frequency — Task 4b)."""
    return bool(re.search(
        r"\d+\s*(?:lần|hiệp|reps?|repetitions?|sets?|lần lặp)",
        text, re.IGNORECASE,
    ))


def _has_frequency(text: str) -> bool:
    """Có tần suất (nửa sau của _has_sets_reps_frequency — Task 4b).

    Frequency must say per day/week. It used to accept a bare "10 lần", so
    "Lặp lại 10 lần" satisfied reps AND frequency from the same two words
    (measured 29/09, scripts/eval_grader_rules.py).
    """
    return bool(re.search(
        r"(?:\d+\s*buổi)|"
        r"(?:\d+\s*(?:lần|times?|ngày|days?)\s*(?:mỗi|một|/|a|per|each)\s*(?:ngày|tuần|day|week))|"
        r"(?:mỗi ngày|hàng ngày|hằng ngày|mỗi tuần|hàng tuần|cách ngày|daily|weekly"
        r"|every (?:day|other day|week)|per week|a week)",
        text, re.IGNORECASE,
    ))


def _has_sets_reps_frequency(text: str) -> bool:
    """Check for exercise protocol: sets + reps + frequency."""
    return _has_sets_reps(text) and _has_frequency(text)


def _has_ordered_steps(text: str) -> bool:
    """Check for ≥2 ordered/enumerated steps."""
    # Match numbered steps anywhere: "1. Step", "1) Step", "Bước 1", "step 1"
    # Use non-multiline pattern to catch steps on the same line
    numbered = len(re.findall(r"(?:^|\s|\.\s*)\d+[\.\)]\s+\S", text))
    if numbered >= 2:
        return True

    # Bullet points (need at least 2)
    bullets = len(re.findall(r"(?m)^\s*[-–—•]\s+\S", text))
    if bullets >= 2:
        return True

    # Explicit step markers: "bước 1", "step 1"
    step_markers = len(re.findall(r"(?:bước|step)\s*\d", text, re.IGNORECASE))
    if step_markers >= 2:
        return True

    # Sequential transition words (at least 2 distinct)
    seq_count = sum(
        1 for p in [r"đầu tiên", r"trước tiên", r"sau đó", r"tiếp theo", r"cuối cùng"]
        if re.search(p, text, re.IGNORECASE)
    )
    return seq_count >= 2


def _has_contraindication(text: str) -> bool:
    """Check that the answer says WHO should not do it: a condition or a group.

    "không nên", "tránh", "avoid", "do not" on their own are technique tips
    ("tránh nín thở", "avoid rushing") and matched almost every answer: 7 of
    12 without a contraindication passed, measured 29/09 with
    scripts/eval_grader_rules.py. Now the prohibition and the condition have
    to be in the same sentence, unless the answer says "chống chỉ định" or
    "not suitable for" outright.
    """
    if not text:
        return False
    explicit = (r"chống chỉ định|contraindicat|không (?:dành|phù hợp)\s*(?:cho|với)?\s*(?:người|ai|trường hợp)"
                r"|not (?:suitable|recommended|safe) for")
    if re.search(explicit, text, re.IGNORECASE):
        return True
    prohibit = (r"không nên|tránh|không tập|thận trọng|cẩn thận|ngừng"
                r"|should ?n[o']t|must not|do not|don't|avoid|be careful|caution|stop")
    condition = (r"người (?:bị|đang|có|mắc|cao tuổi|lớn tuổi)|mang thai|bà bầu|thoát vị|loãng xương"
                 r"|huyết áp|tim mạch|bệnh tim|tiểu đường|viêm|chấn thương|phẫu thuật|gãy|sưng|nhiễm trùng"
                 r"|people with|anyone with|if you (?:have|are)|pregnan|osteoporosis|hernia|blood pressure"
                 r"|heart (?:condition|disease|problem)|diabet|injur|surgery|inflam|arthritis|fracture|symptom")
    return any(
        re.search(prohibit, s, re.IGNORECASE) and re.search(condition, s, re.IGNORECASE)
        for s in re.split(r"[.!?\n]+", text)
    )


# Names that count as a source when they appear. Case-sensitive on purpose: a
# name is capitalised, running text ("who", "bệnh viện") is not. A source
# missing from this list costs one quality retry, never a wrong pass — extend
# it when the knowledge base gains a source.
_NAMED_SOURCE = (
    r"\b(?:NHS|WHO|CDC|NIH|NIA|NICE|ACSM|AHA|HHS|AAOS|APTA)\b"
    r"|National Health Service|World Health Organization|Cochrane|PubMed|Mayo Clinic"
    r"|Bộ Y [Tt]ế|Tổ chức Y tế Thế giới|Physical Activity Guidelines"
    r"|(?:Viện|Hiệp hội|Đại học|Bệnh viện)\s+[A-ZĐ]"
    r"|\b(?:University|Institute|Association|Society|College|Journal) of [A-Z]"
    r"|[A-Z][a-z]+ (?:University|Institute|Association|Society|Journal)\b"
)


def _has_source(text: str) -> bool:
    """Check for a NAMED source: an organisation, document, URL or dated study.

    "theo" is not a citation on its own — it is "tiếp theo", "theo dõi",
    "theo mình thấy" — and "dẫn" matched every "hướng dẫn". With both in the
    list every Vietnamese answer passed: 6 of 6 without a source, measured
    29/09 with scripts/eval_grader_rules.py.
    """
    if not text:
        return False
    patterns = [
        r"(?:https?://|www\.)",                                          # URL
        r"\b[\w-]+(?:\.[\w-]+)*\.(?:gov|org|edu|int|uk|vn|com|net)\b",   # nhs.uk, moh.gov.vn
        r"(?:nguồn|source|references?|trích từ)\s*:\s*\S",              # an explicit "Nguồn: …" label
        r"\[\d+(?:,\s*\d+)*\]",                                          # [1], [1, 2]
        r"\([^()]*\b(?:19|20)\d{2}\)",                                   # (Author 2024)
        r"(?:nghiên cứu|study|trial|meta-analysis)[^.\n]{0,40}\b(?:19|20)\d{2}\b",
    ]
    if any(re.search(p, text, re.IGNORECASE) for p in patterns):
        return True
    return bool(re.search(_NAMED_SOURCE, text))


def _has_motion_fields(text: str) -> bool:
    """Check for motion description: movement + joints mentioned."""
    has_movement = bool(re.search(
        r"(?:động tác|cử động|chuyển động|giơ|nâng|gập|duỗi|xoay|nghiêng|"
        r"movement|motion|raise|lower|bend|extend|rotate|tilt)",
        text, re.IGNORECASE,
    ))
    has_joints = bool(re.search(
        r"(?:khớp|vai|khuỷu|cổ tay|hông|đầu gối|cổ chân|cột sống|lưng|"
        r"joint|shoulder|elbow|wrist|hip|knee|ankle|spine)",
        text, re.IGNORECASE,
    ))
    return has_movement and has_joints


# ── TAG_RULES — single source of truth (D7) ──────────────────────────────

TAG_RULES: dict[str, tuple[str, Callable[[str], bool], str]] = {
    # ── SAFETY (thiếu → chèn TEMPLATE CỨNG, KHÔNG retry — D6) ──
    "red_flag_screen": (
        "safety",
        _has_danger_warning,
        # Template cứng (appended when missing):
        DEFAULT_SAFETY_TEMPLATES["red_flag_screen"],
    ),
    "referral_advice": (
        "safety",
        _has_referral,
        DEFAULT_SAFETY_TEMPLATES["referral_advice"],
    ),
    "scope_disclaimer": (
        "safety",
        _has_disclaimer,
        DEFAULT_SAFETY_TEMPLATES["scope_disclaimer"],
    ),

    # ── QUALITY (thiếu → retry max 1 — D6) ──
    "exercise_protocol": (
        "quality",
        _has_sets_reps_frequency,
        "Missing sets/reps/frequency. Include specific numbers: "
        "\"3 hiệp × 10 lần, 2-3 lần/tuần\".",
    ),
    "exercise_steps": (
        "quality",
        _has_ordered_steps,
        "Missing ordered steps. Provide ≥2 numbered steps or bullet points "
        "describing the movement sequence.",
    ),
    "contraindication": (
        "quality",
        _has_contraindication,
        "Missing contraindications. List conditions where this exercise "
        "should NOT be done (pain, inflammation, specific conditions).",
    ),
    "evidence_citation": (
        "quality",
        _has_source,
        "Missing source citation. Include document title, URL, or reference "
        "for the information provided.",
    ),
    "motion_descriptor": (
        "quality",
        _has_motion_fields,
        "Missing motion description. Describe the movement AND the joints "
        "involved (e.g. \"giơ tay phải lên — khớp vai và khuỷu\").",
    ),
}

# Startup assertion: ensure planner vocabulary ⊆ TAG_RULES (D7)
# Called once at module import — catches drift between planner and grader.
_PLANNER_TAGS = frozenset({
    "red_flag_screen", "referral_advice", "scope_disclaimer",
    "exercise_protocol", "exercise_steps", "contraindication",
    "evidence_citation", "motion_descriptor",
})
assert _PLANNER_TAGS == set(TAG_RULES.keys()), (
    f"TAG_RULES drift detected! "
    f"planner_tags - TAG_RULES = {_PLANNER_TAGS - set(TAG_RULES.keys())}, "
    f"TAG_RULES - planner_tags = {set(TAG_RULES.keys()) - _PLANNER_TAGS}"
)
assert set(TAG_CONTRACT) == set(TAG_RULES)

# ── Warning message for pass_with_warning ─────────────────────────────────
_UNAUTHORIZED_DISCLAIMER = (
    "*Thông tin này chưa được kiểm chứng bởi chuyên gia y tế. "
    "Vui lòng tham khảo ý kiến bác sĩ trước khi áp dụng.*"
)

_UNAUTHORIZED_DISCLAIMER_EN = (
    "*This information has not been verified by a health professional. "
    "Please consult a doctor before acting on it.*"
)


def _unauthorized_disclaimer(lang: str) -> str:
    return _UNAUTHORIZED_DISCLAIMER_EN if lang == "en" else _UNAUTHORIZED_DISCLAIMER


# ── Grader logic ─────────────────────────────────────────────────────────

def _grade_tags(final_answer: str, required_outputs: list[str]) -> dict:
    """Run tag-driven checks against final_answer.

    Returns:
        {result: "pass"|"retry"|"pass_with_warning",
         feedback: str|None,
         safety_missing: list[str],    # safety tags that failed
         quality_missing: list[str]}   # quality tags that failed
    """
    if not final_answer or not final_answer.strip():
        return {
            "result": "retry",
            "feedback": "Empty response. Must produce an answer.",
            "safety_missing": [],
            "quality_missing": [],
        }

    safety_missing = []
    quality_missing = []

    for tag in required_outputs:
        if tag not in TAG_RULES:
            logger.warning("unknown_tag_in_grader", extra={"tag": tag})
            continue

        kind, rule_fn, _ = TAG_RULES[tag]
        if not rule_fn(final_answer):
            if kind == "safety":
                safety_missing.append(tag)
            else:
                quality_missing.append(tag)

    # Safety failures → template cứng (D6: no retry for safety)
    if safety_missing:
        return {
            "result": "pass_with_warning",  # We'll inject templates
            "feedback": None,
            "safety_missing": safety_missing,
            "quality_missing": quality_missing,
        }

    # Quality failures → retry (max 1, D6)
    if quality_missing:
        feedback_parts = []
        for tag in quality_missing:
            _, _, fb = TAG_RULES[tag]
            feedback_parts.append(f"[{tag}] {fb}")
        return {
            "result": "retry",
            "feedback": " ".join(feedback_parts),
            "safety_missing": [],
            "quality_missing": quality_missing,
        }

    # All tags pass
    return {
        "result": "pass",
        "feedback": None,
        "safety_missing": [],
        "quality_missing": [],
    }


# ── Node ─────────────────────────────────────────────────────────────────

def _evidence_text(messages: list) -> str:
    """Văn bản evidence của lượt (Task 4b): nội dung các ToolMessage có nguồn
    is_evidence=True — cùng ngữ nghĩa lọc với synthesizer._evidence_messages
    (tool lạ không rõ nguồn vẫn được tính)."""
    from langgraph_agents.sources import source_for_tool

    parts = []
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        src = source_for_tool(m.name or "")
        if src is not None and not src.is_evidence:
            continue
        parts.append(str(m.content or ""))
    return "\n".join(parts)


def _grade_protocol_parts(answer: str, evidence_text: str) -> list[str]:
    """Phần liều lượng mà evidence CÓ nhưng câu trả lời thiếu (Task 4b).

    Trả [] khi evidence không ghi phần nào (tag không bị kiểm ở lượt đó) hoặc
    khi câu trả lời đã đủ phần evidence có.
    """
    missing = []
    if _has_sets_reps(evidence_text) and not _has_sets_reps(answer):
        missing.append("sets_reps")
    if _has_frequency(evidence_text) and not _has_frequency(answer):
        missing.append("frequency")
    return missing

async def grader_node(state: AgentState, config: RunnableConfig) -> dict:
    """Tag-driven grader node — M.3.

    Reads: required_outputs (tags), final_answer, persona_id (from config)
    Logic:
      - required_outputs=[] → skipped by routing (D8), this node never called
      - safety tag missing → inject persona-aware template (NO retry — D6)
      - quality tag missing → retry max 1 (D6)
      - retry_count >= 1 + still failing quality → pass_with_warning
      - D32: unverified disclaimer appended at END on pass_with_warning
    """
    t0 = time.perf_counter()
    required_outputs = state.get("required_outputs", [])
    final_answer = state.get("final_answer", "")
    retry_count = state.get("retry_count", 0)
    persona_id = config["configurable"].get("persona_id", "anne")

    # Safety: required_outputs=[] should never reach grader (D8 + D15 routing)
    if not required_outputs:
        logger.info("node_skip", extra={
            "node": "grader", "reason": "no_tags",
        })
        return {"grader_result": "pass"}

    result = _grade_tags(final_answer, [t for t in required_outputs
                                      if t != "exercise_protocol"])

    # Task 4b: exercise_protocol chỉ bị đòi phần mà evidence có. Evidence
    # không ghi phần nào → tag không bị kiểm ở lượt đó.
    if "exercise_protocol" in required_outputs:
        evidence_text = _evidence_text(state.get("messages", []))
        ev_sets = _has_sets_reps(evidence_text)
        ev_freq = _has_frequency(evidence_text)
        if not ev_sets and not ev_freq:
            logger.info("protocol_unsupported_by_evidence", extra={
                "node": "grader",
                "request_id": config["configurable"].get("request_id", "-"),
            })
        else:
            missing = _grade_protocol_parts(final_answer, evidence_text)
            if missing:
                if "exercise_protocol" not in result["quality_missing"]:
                    result["quality_missing"].append("exercise_protocol")
                _, _, fb = TAG_RULES["exercise_protocol"]
                piece = f"[exercise_protocol] {fb}"
                result["feedback"] = (
                    f"{result['feedback']} {piece}".strip()
                    if result.get("feedback") else piece
                )
                if result["result"] == "pass":
                    result["result"] = "retry"

    # The site language the user chose, not a guess at the answer's language.
    #
    # This used to call detect_lang(final_answer). Detection reads the reply and
    # is usually right, but "usually" is the problem: a safety warning is the one
    # sentence a reader must not skip, and a declared preference cannot be
    # mis-read the way a two-word answer can.
    #
    # Known consequence, accepted: a Vietnamese question asked on an English site
    # gets a Vietnamese answer with an English warning attached. The reply
    # language still mirrors the question — locale governs inserted text only.
    lang = config["configurable"].get("locale", "en")

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    # ── PASS ──────────────────────────────────────────────────────────
    if result["result"] == "pass":
        logger.info("node_complete", extra={
            "node": "grader", "result": "pass",
            "elapsed_ms": elapsed_ms, "tags": required_outputs,
        })
        return {"grader_result": "pass"}

    # ── SAFETY MISSING → inject template cứng (D6: NO retry) ─────────
    if result["safety_missing"]:
        # Inject persona-aware safety templates at START of answer (D32)
        templates = []
        for tag in result["safety_missing"]:
            templates.append(get_safety_text(tag, persona_id, lang))

        safety_block = "\n\n---\n\n".join(templates)
        modified_answer = f"{safety_block}\n\n---\n\n{final_answer}"

        # Also append unverified disclaimer if quality also failed (D32: CUỐI)
        disclaimer = ""
        if result["quality_missing"]:
            disclaimer = f"\n\n---\n\n{_unauthorized_disclaimer(lang)}"
            modified_answer += disclaimer

        logger.warning("node_complete", extra={
            "node": "grader", "result": "pass_with_warning",
            "elapsed_ms": elapsed_ms,
            "lang": lang,
            "safety_missing": result["safety_missing"],
            "quality_missing": result["quality_missing"],
        })
        return {
            "grader_result": "pass_with_warning",
            "final_answer": modified_answer,
        }

    # ── QUALITY MISSING → retry (max 1) ───────────────────────────────
    if retry_count == 0:
        logger.info("node_complete", extra={
            "node": "grader", "result": "retry",
            "elapsed_ms": elapsed_ms,
            "quality_missing": result["quality_missing"],
        })
        return {
            "grader_result": "retry",
            "retry_count": 1,
            "grader_feedback": result["feedback"],
        }

    # ── RETRY EXHAUSTED → pass with warning (D32: disclaimer cuối) ────
    logger.warning("node_complete", extra={
        "node": "grader", "result": "pass_with_warning",
        "elapsed_ms": elapsed_ms,
        "quality_missing": result["quality_missing"],
        "retry_count": retry_count,
    })
    modified_answer = f"{final_answer}\n\n---\n\n{_unauthorized_disclaimer(lang)}"
    return {
        "grader_result": "pass_with_warning",
        "final_answer": modified_answer,
    }
