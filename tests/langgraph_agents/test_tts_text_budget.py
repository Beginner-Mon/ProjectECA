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
    opener = "Hôm nay mình tập nhẹ phần vai trong mười phút."
    text = opener + " " + "w" * 700
    plan = plan_spoken_text(text)
    assert plan.text == opener
    assert plan.estimated_audio_s == pytest.approx(
        len(opener) / CHARS_PER_AUDIO_SECOND
    )


@pytest.mark.unit
def test_env_override_changes_limit(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "200")
    opener = "Mình bắt đầu bằng một bài khởi động vai thật nhẹ."
    text = opener + " " + "q" * 300
    assert len(text) > 200
    plan = plan_spoken_text(text)
    assert plan.text == opener
    assert plan.truncated is True


# ── Regression: what counts as "the first sentence" ─────────────────
# Found in review 23-09-2026. "Earliest terminator wins" cut these replies
# to 2 and 14 characters — the user heard "một chấm" and nothing else, with
# no error anywhere. Numbered lists and decimals are routine in exercise
# instructions, so this was going to fire on ordinary traffic.

_TAIL = "Giữ nhịp thở đều và đừng cố quá sức trong tuần đầu tiên. " * 12


@pytest.mark.unit
def test_numbered_list_marker_is_not_a_sentence_end():
    opener = "1. Khởi động khớp vai trong hai phút, xoay tròn đều cả hai bên."
    plan = plan_spoken_text(opener + " " + _TAIL)
    assert plan.text == opener
    assert plan.truncated is True


@pytest.mark.unit
def test_decimal_point_is_not_a_sentence_end():
    opener = "Bạn nên tập 2.5 phút mỗi ngày để cơ thể quen dần với cường độ."
    plan = plan_spoken_text(opener + " " + _TAIL)
    assert plan.text == opener


@pytest.mark.unit
def test_heading_line_too_short_keeps_looking():
    text = "Bài tập vai\n1. Khởi động khớp vai trong hai phút rồi nghỉ. " + _TAIL
    plan = plan_spoken_text(text)
    assert plan.text.endswith("rồi nghỉ.")
    assert len(plan.text) > 40


@pytest.mark.unit
def test_short_opening_sentences_accumulate_to_a_whole_sentence():
    """Never cut mid-sentence: keep adding whole sentences until it is worth
    speaking. "Chào bạn." alone is 9 characters of audio nobody wants."""
    text = "Chào bạn. Mình là Anne. Hôm nay mình tập phần vai nhé. " + _TAIL
    plan = plan_spoken_text(text)
    assert plan.text == "Chào bạn. Mình là Anne. Hôm nay mình tập phần vai nhé."


@pytest.mark.unit
def test_bad_env_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "abc")
    text = "k" * (SPOKEN_CHAR_LIMIT - 1)
    plan = plan_spoken_text(text)
    assert plan.truncated is False
