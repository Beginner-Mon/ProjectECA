# -*- coding: utf-8 -*-
"""Tests for services/vieneu_tts/text_budget.py (the spoken pieces).

Owner decision 28-09-2026: the whole reply is spoken, one TTS call per
sentence, replacing the 600-char "first sentence only" cap.
"""

import pytest

from langgraph_agents.services.vieneu_tts.text_budget import (
    CHARS_PER_AUDIO_SECOND,
    MAX_SEGMENT_CHARS,
    MIN_SEGMENT_CHARS,
    plan_spoken_text,
    split_sentences,
)


def _joined(pieces):
    return " ".join(pieces)


@pytest.mark.unit
def test_splits_at_full_stops_and_keeps_everything():
    text = (
        "Hôm nay mình tập nhẹ phần vai trong mười phút nhé. "
        "Bạn ngồi thẳng lưng, thả lỏng hai tay xuống. "
        "Sau đó xoay vai ra sau thật chậm mười lần!"
    )
    pieces = split_sentences(text)
    assert pieces == [
        "Hôm nay mình tập nhẹ phần vai trong mười phút nhé.",
        "Bạn ngồi thẳng lưng, thả lỏng hai tay xuống.",
        "Sau đó xoay vai ra sau thật chậm mười lần!",
    ]


@pytest.mark.unit
def test_long_replies_are_spoken_in_full():
    """The old rule spoke only the first sentence of anything over 600 chars."""
    sentence = "Giữ nhịp thở đều và đừng cố quá sức trong tuần đầu tiên."
    text = " ".join([sentence] * 20)
    assert len(text) > 600
    plan = plan_spoken_text(text)
    assert plan.truncated is False
    assert len(plan.segments) == 20
    assert plan.spoken_chars == len(text) - 19  # only the joining spaces


@pytest.mark.unit
def test_short_sentences_join_the_next_one():
    """"Chào bạn." alone is half a second of audio and then a pause."""
    text = "Chào bạn. Mình là Anne. Hôm nay mình tập phần vai nhé, bạn sẵn sàng chưa?"
    pieces = split_sentences(text)
    assert pieces[0] == "Chào bạn. Mình là Anne. Hôm nay mình tập phần vai nhé, bạn sẵn sàng chưa?"
    assert all(len(p) >= MIN_SEGMENT_CHARS for p in pieces)


@pytest.mark.unit
def test_a_short_last_sentence_stays_on_its_own():
    text = "Bạn nhớ khởi động kỹ trước khi bắt đầu bài tập nhé. Chúc bạn khỏe!"
    assert split_sentences(text) == [
        "Bạn nhớ khởi động kỹ trước khi bắt đầu bài tập nhé.",
        "Chúc bạn khỏe!",
    ]


@pytest.mark.unit
def test_numbered_list_marker_is_not_a_sentence_end():
    text = (
        "1. Khởi động khớp vai trong hai phút, xoay tròn đều cả hai bên.\n"
        "2. Kéo giãn cơ cổ nhẹ nhàng sang trái và sang phải."
    )
    assert split_sentences(text) == [
        "1. Khởi động khớp vai trong hai phút, xoay tròn đều cả hai bên.",
        "2. Kéo giãn cơ cổ nhẹ nhàng sang trái và sang phải.",
    ]


@pytest.mark.unit
def test_decimal_point_is_not_a_sentence_end():
    text = "Bạn nên tập 2.5 phút mỗi ngày để cơ thể quen dần với cường độ."
    assert split_sentences(text) == [text]


@pytest.mark.unit
def test_a_number_before_a_full_stop_still_ends_the_sentence():
    text = "Bạn lặp lại động tác kéo giãn này chậm rãi khoảng 10. Sau đó nghỉ ngơi và uống một chút nước."
    pieces = split_sentences(text)
    assert pieces[0].endswith("khoảng 10.")
    assert len(pieces) == 2


@pytest.mark.unit
def test_repeated_marks_and_closing_quotes_stay_with_the_sentence():
    text = 'Bạn làm rất tốt rồi đó, cứ tiếp tục như vậy nhé!?" Giờ mình chuyển sang bài tiếp theo.'
    pieces = split_sentences(text)
    assert pieces[0].endswith('nhé!?"')
    assert pieces[1] == "Giờ mình chuyển sang bài tiếp theo."


@pytest.mark.unit
def test_newlines_end_a_piece_and_ellipsis_is_a_terminator():
    text = "Xin chào bạn, rất vui được gặp lại bạn hôm nay…\nHôm nay mình tập phần lưng dưới nhé."
    assert split_sentences(text) == [
        "Xin chào bạn, rất vui được gặp lại bạn hôm nay…",
        "Hôm nay mình tập phần lưng dưới nhé.",
    ]


@pytest.mark.unit
def test_over_long_sentence_is_cut_at_word_boundaries():
    text = "word " * 200  # 1000 chars, no terminator
    pieces = split_sentences(text)
    assert all(len(p) <= MAX_SEGMENT_CHARS for p in pieces)
    assert _joined(pieces) == text.strip()


@pytest.mark.unit
def test_no_space_cuts_hard_at_max():
    pieces = split_sentences("z" * 900)
    assert pieces == ["z" * 400, "z" * 400, "z" * 100]


@pytest.mark.unit
def test_empty_or_blank_text_has_nothing_to_speak():
    assert split_sentences("") == []
    assert plan_spoken_text("   \n ").segments == ()


@pytest.mark.unit
def test_estimates_per_segment_and_in_total():
    plan = plan_spoken_text(
        "Hôm nay mình tập nhẹ phần vai trong mười phút nhé. Bạn ngồi thẳng lưng và thả lỏng hai tay."
    )
    for seg in plan.segments:
        assert seg.estimated_audio_s == pytest.approx(len(seg.text) / CHARS_PER_AUDIO_SECOND)
    assert plan.estimated_audio_s == pytest.approx(sum(s.estimated_audio_s for s in plan.segments))


@pytest.mark.unit
def test_env_cap_keeps_whole_sentences_that_fit(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "120")
    sentence = "Giữ nhịp thở đều và đừng cố quá sức trong tuần đầu tiên."  # 57
    plan = plan_spoken_text(" ".join([sentence] * 5))
    assert [s.text for s in plan.segments] == [sentence, sentence]
    assert plan.truncated is True


@pytest.mark.unit
def test_env_cap_always_keeps_the_first_sentence(monkeypatch):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "10")
    plan = plan_spoken_text("Giữ nhịp thở đều và đừng cố quá sức trong tuần đầu tiên. Nghỉ ngơi nhé bạn ơi, uống nước nữa.")
    assert len(plan.segments) == 1
    assert plan.truncated is True


@pytest.mark.unit
@pytest.mark.parametrize("value", ["abc", "0", "-5", ""])
def test_bad_or_missing_env_value_means_no_cap(monkeypatch, value):
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", value)
    text = " ".join(["Giữ nhịp thở đều và đừng cố quá sức trong tuần đầu tiên."] * 20)
    assert plan_spoken_text(text).truncated is False
