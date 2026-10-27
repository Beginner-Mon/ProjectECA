# -*- coding: utf-8 -*-
"""Safety text is language-correct, decided here — never by the model.

So an English answer used to receive a Vietnamese warning stapled to the front
of it. That is worst on red_flag_screen: the one line the reader must not skip.

The grader is gone (Phase 3e): the guardrail now owns the safety lines. These
tests keep checking that every persona resolves both languages and that the
defaults fall back correctly.
"""

import pytest

from langgraph_agents.tag_contract import (
    DEFAULT_SAFETY_TEMPLATES,
    DEFAULT_SAFETY_TEMPLATES_EN,
    get_safety_text,
)

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
