# -*- coding: utf-8 -*-
"""Tests for services/vieneu_tts/text_budget.py (spoken-length cap).

Owner decision: replies over SPOKEN_CHAR_LIMIT (600) chars speak only the
first sentence; shorter replies pass through byte-identical.
"""

import pytest

from langgraph_agents.services.vieneu_tts.text_budget import (
    CHARS_PER_AUDIO_SECOND,
    SPOKEN_CHAR_LIMIT,
    plan_spoken_text,
)


@pytest.mark.unit
def test_599_chars_passes_through_untouched():
    text = "x" * 599
    plan = plan_spoken_text(text)
    assert plan.text == text
    assert plan.truncated is False


@pytest.mark.unit
def test_600_chars_boundary_does_not_trigger():
    text = "y" * 600
    plan = plan_spoken_text(text)
    assert plan.text == text
    assert plan.truncated is False


@pytest.mark.unit
def test_601_chars_keeps_only_first_sentence():
    text = "Câu đầu tiên. " + "z" * 587
    assert len(text) == 601
    plan = plan_spoken_text(text)
    assert plan.text == "Câu đầu tiên."
    assert plan.truncated is True


@pytest.mark.unit
def test_vietnamese_ellipsis_and_newline_cut_cleanly():
    text = "Xin chào…\n" + "v" * 700
    plan = plan_spoken_text(text)
    assert plan.text == "Xin chào…"
    assert plan.truncated is True
    # No broken code points from slicing.
    plan.text.encode("utf-8")


@pytest.mark.unit
def test_long_first_sentence_cuts_at_space_before_400():
    text = "word " * 180 + ". tail that never gets spoken"
    plan = plan_spoken_text(text)
    assert len(plan.text) < 400
    assert plan.text == text[:399].rstrip()
    assert not plan.text.endswith(" ")
    assert plan.truncated is True


@pytest.mark.unit
def test_no_terminator_no_space_cuts_hard_at_400():
    text = "z" * 700
    plan = plan_spoken_text(text)
    assert plan.text == "z" * 400
    assert plan.truncated is True


@pytest.mark.unit
def test_estimate_uses_spoken_length_not_original():
    text = "Ngắn. " + "w" * 700
    plan = plan_spoken_text(text)
    assert plan.text == "Ngắn."
    assert plan.estimated_audio_s == pytest.approx(
        len("Ngắn.") / CHARS_PER_AUDIO_SECOND
    )


@pytest.mark.unit
def test_env_override_changes_limit(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "200")
    text = "Một câu. " + "q" * 300
    assert len(text) > 200
    plan = plan_spoken_text(text)
    assert plan.text == "Một câu."
    assert plan.truncated is True


@pytest.mark.unit
def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "abc")
    text = "k" * (SPOKEN_CHAR_LIMIT - 1)
    plan = plan_spoken_text(text)
    assert plan.truncated is False
