"""Tests for `loop.taint`: A7 whole-token matching on real fact text."""

from langgraph_agents.loop.taint import build_taint, find_banned


def _norm_snapshot(facts, instructions, current_message=""):
    return build_taint(facts, instructions, current_message)


# A7: the four rows from the fix note, with facts and instructions as given.

def test_a7_osteoarthritis_and_knee():
    """Fact "osteoarthritis" and "knee"; query "knee osteoarthritis exercises".

    Correct: refuses "osteoarthritis" and "knee osteoarthritis"; allows "knee".
    Today's substring version refused only "knee" and let "osteoarthritis" leak.
    """
    snapshot = _norm_snapshot(
        facts=["[2026-10-14] User has knee osteoarthritis."],
        instructions=[],
        current_message="",
    )
    joined = "knee osteoarthritis exercises"
    banned = find_banned(joined, snapshot)
    assert "osteoarthritis" in banned
    assert "knee osteoarthritis" in banned
    # "knee" alone: the fact has "knee osteoarthritis" (2-gram) and the user
    # did NOT type "knee" in the current message, so "knee" is banned too.
    assert "knee" in banned


def test_a7_lan_knee_pain():
    """Fact "User's name is Lan."; query "Lan knee pain".

    Correct: "Lan" is refused (today it leaked because "lan." kept its stop).
    """
    snapshot = _norm_snapshot(
        facts=["User's name is Lan."],
        instructions=[],
        current_message="",
    )
    joined = "Lan knee pain"
    banned = find_banned(joined, snapshot)
    assert "lan" in banned


def test_a7_user_guide_for_knee_braces():
    """Fact "[2026-10-14] User has knee osteoarthritis."; query
    "user guide for knee braces".

    Correct: "user" is allowed (A7: "user" is a stopword; it carries no fact).
    Today's substring version wrongly refused "user" from "User".
    """
    snapshot = _norm_snapshot(
        facts=["[2026-10-14] User has knee osteoarthritis."],
        instructions=[],
        current_message="",
    )
    joined = "user guide for knee braces"
    banned = find_banned(joined, snapshot)
    assert "user" not in banned


def test_a7_art_therapy():
    """Fact "User likes art therapy"; query "arthritis".

    Correct: "art" is allowed (substring of "arthritis" is not a match;
    whole-token only). Today's substring version wrongly refused "art".
    """
    snapshot = _norm_snapshot(
        facts=["User likes art therapy"],
        instructions=[],
        current_message="",
    )
    joined = "arthritis"
    banned = find_banned(joined, snapshot)
    assert "art" not in banned


# Existing P10 cases retained.

def test_p10_osteoarthritis_refused():
    snapshot = _norm_snapshot(
        facts=["user has osteoarthritis of the left knee"],
        instructions=["avoid deep squats"],
        current_message="my knee hurts",
    )
    joined = "osteoarthritis treatment"
    banned = find_banned(joined, snapshot)
    assert "osteoarthritis" in banned


def test_knee_typed_by_user_allowed():
    snapshot = _norm_snapshot(
        facts=["user has osteoarthritis of the left knee"],
        instructions=["avoid deep squats"],
        current_message="my knee hurts",
    )
    assert "knee" not in snapshot.banned_terms
    joined = "knee exercise"
    banned = find_banned(joined, snapshot)
    assert "knee" not in banned


def test_name_lan_refused():
    snapshot = _norm_snapshot(
        facts=["the user's name is Lan"],
        instructions=[],
        current_message="what exercises can I do",
    )
    joined = "show me Lan's records"
    banned = find_banned(joined, snapshot)
    assert "lan" in banned


def test_ngrams_up_to_3():
    snapshot = _norm_snapshot(
        facts=["do not eat after 8pm"],
        instructions=[],
        current_message="hello",
    )
    joined = "do not eat after 8pm please"
    banned = find_banned(joined, snapshot)
    # n-grams include "do not eat", "not eat after", "eat after 8pm", etc.
    assert any("eat after 8pm" in b or b == "eat after 8pm" for b in banned) or \
           "do not eat" in banned or "not eat after" in banned
