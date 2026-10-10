# -*- coding: utf-8 -*-
"""The grader injects safety text VERBATIM — it never calls an LLM.

So the language of that text is decided here, not by the model, and an English
answer used to receive a Vietnamese warning stapled to the front of it. That is
worst on red_flag_screen: the one line the reader must not skip.
"""

import pytest

from langgraph_agents.nodes.grader import (
    DEFAULT_SAFETY_TEMPLATES,
    DEFAULT_SAFETY_TEMPLATES_EN,
    get_safety_text,
    grader_node,
)
from langgraph_agents.state import AgentState

CHARACTERS = ["anne", "bronya", "miki", "hatsune-miku"]
# eca_default was deleted on 04-09: the product has four characters, and a
# persona that fails to load now raises instead of standing in for one.
SAFETY_TAGS = ["red_flag_screen", "referral_advice", "scope_disclaimer"]


@pytest.mark.unit
@pytest.mark.parametrize("slug", CHARACTERS)
@pytest.mark.parametrize("tag", SAFETY_TAGS)
def test_every_character_has_both_languages(slug, tag):
    vi = get_safety_text(tag, slug, "vi")
    en = get_safety_text(tag, slug, "en")
    assert vi and en
    assert vi != en, f"{slug}.{tag}: the English variant is just the Vietnamese one"


@pytest.mark.unit
@pytest.mark.parametrize("slug", CHARACTERS)
@pytest.mark.parametrize("tag", SAFETY_TAGS)
def test_language_variants_do_not_contain_the_other_alphabet(slug, tag):
    """A cheap smoke test that catches a copy-paste of the wrong line."""
    from langgraph_agents.shared.lang import _VN_SET

    en = get_safety_text(tag, slug, "en")
    assert not any(c in _VN_SET for c in en), f"{slug}.{tag}.en still reads Vietnamese"


@pytest.mark.unit
@pytest.mark.parametrize("slug", CHARACTERS)
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_persona_disclaimer_recognized_by_grader(slug, lang):
    """B2: câu scope_disclaimer của mọi persona/mọi ngôn ngữ phải qua
    _has_disclaimer — nếu không, lượt EN bị chèn disclaimer lần hai và mất
    kiểm tag chất lượng."""
    from langgraph_agents.nodes.grader import _has_disclaimer

    text = get_safety_text("scope_disclaimer", slug, lang)
    assert _has_disclaimer(text), f"{slug}.{lang}: disclaimer not recognized"


@pytest.mark.unit
def test_default_disclaimers_recognized_by_grader():
    """B2: tương tự cho hai mẫu mặc định."""
    from langgraph_agents.nodes.grader import _has_disclaimer

    assert _has_disclaimer(DEFAULT_SAFETY_TEMPLATES["scope_disclaimer"])
    assert _has_disclaimer(DEFAULT_SAFETY_TEMPLATES_EN["scope_disclaimer"])


@pytest.mark.unit
@pytest.mark.parametrize("slug", CHARACTERS)
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_persona_red_flag_recognized_by_grader(slug, lang):
    """B2: tương tự cho red_flag_screen với hàm kiểm của nó."""
    from langgraph_agents.nodes.grader import _has_danger_warning

    text = get_safety_text("red_flag_screen", slug, lang)
    assert _has_danger_warning(text), f"{slug}.{lang}: red flag not recognized"


@pytest.mark.unit
@pytest.mark.parametrize("slug", CHARACTERS)
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_persona_referral_recognized_by_grader(slug, lang):
    """B2: tương tự cho referral_advice với hàm kiểm của nó."""
    from langgraph_agents.nodes.grader import _has_referral

    text = get_safety_text("referral_advice", slug, lang)
    assert _has_referral(text), f"{slug}.{lang}: referral not recognized"


@pytest.mark.unit
@pytest.mark.parametrize("tag,checker", [
    ("red_flag_screen", "_has_danger_warning"),
    ("referral_advice", "_has_referral"),
    ("scope_disclaimer", "_has_disclaimer"),
])
@pytest.mark.parametrize("lang", ["vi", "en"])
def test_default_templates_recognized_by_grader(tag, checker, lang):
    """Task 3: cả các mẫu mặc định hai ngôn ngữ cũng phải qua hàm kiểm."""
    import langgraph_agents.nodes.grader as grader_mod

    fn = getattr(grader_mod, checker)
    defaults = (DEFAULT_SAFETY_TEMPLATES if lang == "vi"
                else DEFAULT_SAFETY_TEMPLATES_EN)
    assert fn(defaults[tag]), f"default.{lang}.{tag}: not recognized"


# ── Bảng âm tính (Task 3): nhắc tới bác sĩ/dừng lại thôi thì chưa đủ ──────

_REFERRAL_NEGATIVES = [
    "I'm not a doctor.",
    "Mình không phải bác sĩ.",
    "I have no medical training.",
    "Bác sĩ của bạn đã cho tập lại chưa?",
    "My doctor friend likes squats.",
]

_DANGER_NEGATIVES = [
    "Stop me if I'm going too fast.",
    "Let's stop here for today.",
    "Hôm nay dừng ở đây nhé.",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _REFERRAL_NEGATIVES)
def test_referral_rejects_mere_mentions(text):
    """Task 3: thiếu hành động-nhu cầu hoặc thiếu đối tượng y tế → không qua."""
    from langgraph_agents.nodes.grader import _has_referral

    assert not _has_referral(text), f"false positive: {text!r}"


@pytest.mark.unit
@pytest.mark.parametrize("text", _DANGER_NEGATIVES)
def test_danger_rejects_mere_stops(text):
    """Task 3: dừng chuyện/dừng hôm nay (không phải dừng tập/khám) → không qua."""
    from langgraph_agents.nodes.grader import _has_danger_warning

    assert not _has_danger_warning(text), f"false positive: {text!r}"


@pytest.mark.unit
def test_default_falls_back_when_a_persona_defines_nothing():
    """A persona with no templates at all must still warn, in both languages."""
    for tag in SAFETY_TAGS:
        assert get_safety_text(tag, "does-not-exist", "vi") == DEFAULT_SAFETY_TEMPLATES[tag]
        assert get_safety_text(tag, "does-not-exist", "en") == DEFAULT_SAFETY_TEMPLATES_EN[tag]


@pytest.mark.unit
def test_persona_customisation_survives_a_missing_en_variant():
    """Resolution is `<tag>.<lang>` → `<tag>` → default.

    The persona's own line sits ABOVE the generic English default on purpose: a
    persona that customised its warning did so for a reason, and silently
    replacing it with boilerplate would drop that.
    """
    import langgraph_agents.nodes._persona_loader as loader

    loader._persona_cache["_probe"] = {
        "persona_id": "_probe",
        "locales": {
            "en": {
                "voice": "", "examples": "", "ui_strings": {},
                "safety_templates": {"red_flag_screen": "CUSTOM ONLY"},
            }
        },
    }
    try:
        assert get_safety_text("red_flag_screen", "_probe", "en") == "CUSTOM ONLY"
        assert get_safety_text("referral_advice", "_probe", "en") == \
            DEFAULT_SAFETY_TEMPLATES_EN["referral_advice"]
    finally:
        loader._persona_cache.pop("_probe", None)


@pytest.mark.unit
def test_default_lang_keeps_existing_callers_on_vietnamese():
    """`lang` defaults to "vi" so nothing that predates this change moves."""
    assert get_safety_text("red_flag_screen", "anne") == \
        get_safety_text("red_flag_screen", "anne", "vi")


@pytest.mark.asyncio
async def test_english_answer_gets_an_english_warning_injected():
    """End to end through the node — dòng kết đúng locale, nằm ở cuối."""
    state: AgentState = {
        "messages": [], "errors": [], "retry_count": 0, "total_tokens": 0,
        "required_outputs": ["referral_advice"],
        "final_answer": (
            "Try three sets of ten repetitions and stop if the pain goes above "
            "4 out of 10, then rest for a full day before the next session."
        ),
    }
    # locale, not detection: the grader stopped guessing on 04-09.
    config = {
        "configurable": {
            "persona_id": "anne",
            "query": "What stretches should I do?",
            "locale": "en",
        }
    }
    result = await grader_node(state, config)

    assert result["grader_result"] == "pass"
    assert result["final_answer"].endswith(
        get_safety_text("referral_advice", "anne", "en"))
    assert get_safety_text("referral_advice", "anne", "vi") not in result["final_answer"]


@pytest.mark.asyncio
async def test_vietnamese_answer_still_gets_the_vietnamese_warning():
    state: AgentState = {
        "messages": [], "errors": [], "retry_count": 0, "total_tokens": 0,
        "required_outputs": ["referral_advice"],
        "final_answer": "Bạn nên tập bài kéo giãn cơ lưng dưới, mỗi hiệp 10 lần, thở đều.",
    }
    config = {
        "configurable": {
            "persona_id": "anne",
            "query": "Tôi nên tập bài nào?",
            "locale": "vi",
        }
    }
    result = await grader_node(state, config)

    assert result["final_answer"].endswith(
        get_safety_text("referral_advice", "anne", "vi"))


@pytest.mark.asyncio
async def test_node_works_without_a_locale_in_config():
    """An older client that sends no locale still gets a closing line, not a crash.

    It gets the ENGLISH one, because that is the declared default everywhere."""
    state: AgentState = {
        "messages": [], "errors": [], "retry_count": 0, "total_tokens": 0,
        "required_outputs": ["referral_advice"],
        "final_answer": "Bạn nên ngừng tập và đi khám bác sĩ ngay.",
    }
    result = await grader_node(state, {"configurable": {"persona_id": "anne"}})
    assert result["grader_result"] == "pass"
    assert result["final_answer"].endswith(
        get_safety_text("referral_advice", "anne", "en"))
