# -*- coding: utf-8 -*-
"""T6 — static clips come from ui_strings, keyed per contract C.

No hardcoded phrases: _greeting_texts reads ui_strings.greeting slots off
the record's own persona (the same projection the catalog column serves),
missing slots are dropped (never filled from the frontend fallback bundle),
and every entry carries key/sha256/text_sha256 with text_sha256 hashed per
contract E over EXACTLY the string sent to SpeechLLm.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import upload_characters_to_s3 as up


def _persona_with(greeting_vi: dict, greeting_en: dict) -> dict:
    return {
        "locales": {
            "vi": {"ui_strings": {"greeting": greeting_vi}},
            "en": {"ui_strings": {"greeting": greeting_en}},
        },
    }


_FULL_VI = {
    "morning": "Chào buổi sáng! Hôm nay mình giúp gì cho bạn?",
    "afternoon": "Chào buổi chiều! Bạn đang thế nào rồi?",
    "evening": "Chào buổi tối! Mình ở đây với bạn.",
    "night": "Khuya rồi, bạn vẫn chưa ngủ à? Mình ở đây nhé.",
}
_FULL_EN = {
    "morning": "Good morning! What can I help you with today?",
    "afternoon": "Good afternoon! How are you doing?",
    "evening": "Good evening! I am here with you.",
    "night": "Still up at this hour? I am here if you need me.",
}


@pytest.mark.unit
def test_greeting_texts_come_from_ui_strings_verbatim():
    texts = up._greeting_texts(_persona_with(_FULL_VI, _FULL_EN))

    assert set(texts) == {"morning", "afternoon", "evening", "night"}
    assert texts["morning"] == {"vi": _FULL_VI["morning"], "en": _FULL_EN["morning"]}
    assert texts["night"]["vi"] == _FULL_VI["night"]


@pytest.mark.unit
def test_missing_slot_is_dropped_never_filled_from_a_fallback():
    vi = {"morning": _FULL_VI["morning"]}  # only morning authored
    texts = up._greeting_texts(_persona_with(vi, {}))

    assert set(texts) == {"morning"}
    assert texts["morning"] == {"vi": _FULL_VI["morning"]}
    # An empty persona yields no clips at all — never a fallback sentence.
    assert up._greeting_texts({}) == {}
    assert up._greeting_texts({"locales": {}}) == {}
    # The hardcoded phrase table is gone (its name must not even resolve).
    assert not hasattr(up, "STATIC_PHRASES")


@pytest.mark.unit
def test_text_sha256_matches_the_precomputed_vietnamese_value():
    """Contract E, both languages, against independently computed digests."""
    assert (
        up._text_sha256("Chào buổi sáng! Hôm nay mình giúp gì cho bạn?")
        == "6d4d8e320d2a1eb08dbea2ba55ffeedb038eaaf013a29c7e59899efaae6b3939"
    )
    assert (
        up._text_sha256("Good morning! What can I help you with today?")
        == "d425cfae741503fb9253d8f742e0ebe7928135fac73ff308014d2094a81de1aa"
    )


@pytest.mark.unit
def test_dry_run_writes_contract_c_shape_with_real_text_hashes():
    """--dry-run renders nothing: keys preview from the text hash, the text
    hash itself is real (what T7 compares), audio sha256 waits for a render."""
    records = [{
        "slug": "anne",
        "display_name": "Anne",
        "persona": _persona_with(_FULL_VI, _FULL_EN),
    }]
    voice_keys = {"anne": {"vi": "voices/anne_vi_aaaa1111.wav"}}

    static_map = up.build_static_audio(
        records, voice_keys, None, "http://localhost:5000", dry_run=True,
    )

    # vi renders (voice present), en skips (no voice) — with a warning, not silence.
    morning_vi = static_map["anne"]["greeting.morning"]["vi"]
    assert set(morning_vi) == {"key", "sha256", "text_sha256"}
    assert morning_vi["key"].startswith("characters/anne/audio/")
    assert morning_vi["key"].endswith(".ogg")
    assert morning_vi["sha256"] is None
    assert morning_vi["text_sha256"] == up._text_sha256(_FULL_VI["morning"])
    assert "en" not in static_map["anne"]["greeting.morning"]
    # All four slots attempted for vi.
    assert {k for k in static_map["anne"]} == {
        "greeting.morning",
        "greeting.afternoon",
        "greeting.evening",
        "greeting.night",
    }


@pytest.mark.unit
def test_dry_run_with_no_voices_renders_nothing_but_warns(capsys):
    records = [{
        "slug": "anne",
        "display_name": "Anne",
        "persona": _persona_with(_FULL_VI, _FULL_EN),
    }]

    static_map = up.build_static_audio(
        records, {}, None, "http://localhost:5000", dry_run=True,
    )

    assert static_map == {}
    out = capsys.readouterr().out
    assert "no uploaded voice" in out
