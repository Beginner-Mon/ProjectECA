"""Bảng tag và các dòng do code phát (grader-contract T3)."""
import pytest

from langgraph_agents.evidence import EvidenceItem
from langgraph_agents.tag_contract import (
    TAG_CONTRACT, check_items, closing_items_note, closing_lines, get_safety_text,
    model_tags, opening_line, source_line,
)

pytestmark = pytest.mark.unit

ALL_TAGS = ["red_flag_screen", "referral_advice", "scope_disclaimer", "exercise_protocol",
            "exercise_steps", "contraindication", "evidence_citation", "motion_descriptor"]


def _lib(title, text="x"):
    return EvidenceItem(header=f"[From ECA's exercise library: {title}]", text=text,
                        cite_key="exercise_db", title=title)


_NHS = EvidenceItem(header="[From NHS health guidance: NHS — Back pain]", text="x",
                    cite_key="nhs_uk", title="NHS — Back pain")
_MEM = EvidenceItem(header="[From your earlier conversations with this user]", text="x",
                    cite_key=None, title="")


def test_contract_covers_the_eight_tags():
    assert set(TAG_CONTRACT) == set(ALL_TAGS)


def test_model_tags_and_check_items():
    assert model_tags(ALL_TAGS) == ["exercise_protocol", "exercise_steps",
                                    "contraindication", "motion_descriptor"]
    assert [c.key for c in check_items(["exercise_protocol", "scope_disclaimer"])] == [
        "exercise_protocol.amount", "exercise_protocol.frequency"]


@pytest.mark.parametrize("slug", ["anne", "bronya", "hatsune-miku", "miki"])
@pytest.mark.parametrize("locale", ["vi", "en"])
def test_every_persona_has_an_opening_line(slug, locale):
    assert opening_line(["red_flag_screen"], slug, locale) == get_safety_text(
        "red_flag_screen", slug, locale)
    assert opening_line(["red_flag_screen"], slug, locale)


def test_no_opening_without_red_flag():
    assert opening_line(["referral_advice", "scope_disclaimer"], "anne", "vi") == ""


def test_unknown_locale_falls_back_to_english():
    assert opening_line(["red_flag_screen"], "anne", "ja") == get_safety_text(
        "red_flag_screen", "anne", "en")
    assert source_line([_lib("Elbow plank")], "Do the Elbow plank.", "ja").startswith("*Source:")


def test_closing_order_is_referral_source_disclaimer():
    lines = closing_lines(["scope_disclaimer", "evidence_citation", "referral_advice"],
                          "anne", "vi", [_lib("Elbow plank")], "Tập Elbow plank nhé.")
    assert lines == [
        get_safety_text("referral_advice", "anne", "vi"),
        "*Nguồn: thư viện bài tập của ECA — Elbow plank.*",
        get_safety_text("scope_disclaimer", "anne", "vi"),
    ]


def test_source_line_two_sources():
    line = source_line([_lib("Lower Back Curl"), _NHS],
                       "Lower Back Curl is a stretch. The NHS says stay active.", "en")
    assert line == "*Source: ECA's exercise library — Lower Back Curl; NHS health guidance.*"


def test_source_line_caps_titles_at_three():
    items = [_lib(t) for t in ("A one", "B two", "C three", "D four")]
    line = source_line(items, "A one, B two, C three, D four", "en")
    assert line == "*Source: ECA's exercise library — A one, B two, C three.*"


def test_no_source_line_when_library_lacks_the_answer():
    assert source_line([_lib("Rower")], "Cartwheel không có trong thư viện.", "vi") == ""


def test_no_source_line_for_memory_only():
    assert source_line([_MEM], "Hôm trước mình nói về squat.", "vi") == ""


def test_no_source_line_without_citation_tag():
    assert closing_lines(["scope_disclaimer"], "anne", "en",
                         [_lib("Elbow plank")], "Elbow plank.") == [
        get_safety_text("scope_disclaimer", "anne", "en")]


def test_closing_items_note():
    assert closing_items_note(["referral_advice", "evidence_citation", "scope_disclaimer"]) == (
        "a referral to a doctor, the source line, a scope disclaimer")
    assert closing_items_note(["exercise_steps"]) == ""
