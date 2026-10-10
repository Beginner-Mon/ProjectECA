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
    voice_keys = {"anne": {"vi": "voices/anne_vi.wav"}}

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


# ── Seeding a brand-new character ────────────────────────────────────────────


class _FakePg:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self.connected = False

    async def connect(self):
        self.connected = True

    async def execute(self, sql, *args):
        self.calls.append((sql, args))


def _record(**over):
    rec = {
        "slug": "mei",
        "display_name": "Mei",
        "description": None,
        "vrm_url": "https://cdn/models/mei/abcd1234.vrm",
        "vrm_metadata": {"joint_count": 1},
        "avatar_profile": {},
        "persona": _persona_with(_FULL_VI, _FULL_EN),
        "voice_language": "vi",
        "sort_order": 0,
    }
    rec.update(over)
    return rec


def _run_upsert(monkeypatch, records, writer):
    import asyncio

    import sync_personas_to_db as sync

    app_pg = _FakePg()
    monkeypatch.setattr(sync, "_writer_client", lambda: writer)
    import langgraph_agents.shared as shared

    monkeypatch.setattr(shared, "get_pg_client", lambda: app_pg)
    asyncio.run(up.upsert(records))
    return app_pg


@pytest.mark.unit
def test_upsert_writes_ui_strings_in_insert_and_update(monkeypatch):
    import json

    writer = _FakePg()
    rec = _record()
    _run_upsert(monkeypatch, [rec], writer)

    sql, args = writer.calls[0]
    insert_cols, _, rest = sql.partition("VALUES")
    assert "ui_strings" in insert_cols
    assert "ui_strings = EXCLUDED.ui_strings" in " ".join(rest.split())
    expected = json.dumps(up._ui_strings_for(rec["persona"]), ensure_ascii=False)
    assert expected in args
    assert expected != "{}"


@pytest.mark.unit
def test_upsert_new_row_gets_next_sort_order_and_existing_row_keeps_its_own(monkeypatch):
    writer = _FakePg()
    _run_upsert(monkeypatch, [_record(sort_order=0)], writer)

    sql, args = writer.calls[0]
    flat = " ".join(sql.split())
    assert "COALESCE((SELECT MAX(sort_order) + 1 FROM characters), 0)" in flat
    on_conflict = flat.split("ON CONFLICT")[1]
    assert "sort_order" not in on_conflict
    # The file-index sort_order is no longer sent as a parameter: sort_order dropped, ui_strings added.
    assert len(args) == 12


@pytest.mark.unit
def test_upsert_uses_the_writer_client_when_available(monkeypatch):
    writer = _FakePg()
    app_pg = _run_upsert(monkeypatch, [_record()], writer)

    assert writer.connected and len(writer.calls) == 1
    assert not app_pg.connected and app_pg.calls == []


@pytest.mark.unit
def test_upsert_falls_back_to_the_app_client_without_a_writer_dsn(monkeypatch):
    app_pg = _run_upsert(monkeypatch, [_record()], None)

    assert app_pg.connected and len(app_pg.calls) == 1


# ── Voice keys: the name the chat path asks for ──────────────────────────────


class _FakeS3:
    def __init__(self, existing=True):
        self.existing = existing
        self.uploads: list[tuple[str, str, str, dict]] = []

    def head_object(self, **kw):  # pragma: no cover - must not be consulted
        if not self.existing:
            raise Exception("404")
        return {}

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.uploads.append((path, bucket, key, ExtraArgs))


@pytest.mark.unit
def test_voice_key_is_the_unhashed_name_and_matches_the_chat_path(tmp_path, monkeypatch):
    from langgraph_agents.services.vieneu_tts.voice import voice_key

    (tmp_path / "anne_vi.wav").write_bytes(b"RIFF....")
    monkeypatch.setattr(up, "VOICES_DIR", tmp_path)
    import boto3

    s3 = _FakeS3(existing=True)
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: s3)

    keys = up.upload_voices([{"slug": "anne"}], "voice-bucket")

    assert keys == {"anne": {"vi": "voices/anne_vi.wav"}}
    assert keys["anne"]["vi"] == voice_key("anne", "vi")


@pytest.mark.unit
def test_voice_is_uploaded_even_when_the_object_already_exists(tmp_path, monkeypatch):
    (tmp_path / "anne_vi.wav").write_bytes(b"RIFF....")
    monkeypatch.setattr(up, "VOICES_DIR", tmp_path)
    import boto3

    s3 = _FakeS3(existing=True)
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: s3)

    up.upload_voices([{"slug": "anne"}], "voice-bucket")

    assert len(s3.uploads) == 1
    _path, bucket, key, extra = s3.uploads[0]
    assert (bucket, key) == ("voice-bucket", "voices/anne_vi.wav")
    assert extra["CacheControl"] == "private, no-cache"


# ── --models-dir and slug validation ─────────────────────────────────────────


@pytest.mark.unit
def test_models_dir_is_required_and_the_constant_is_gone(monkeypatch, capsys):
    assert not hasattr(up, "MODELS_DIR")
    monkeypatch.setattr(sys, "argv", ["upload_characters_to_s3.py", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        up.main()
    assert exc.value.code == 2
    assert "--models-dir" in capsys.readouterr().err


@pytest.mark.unit
@pytest.mark.parametrize("name", ["Mei.vrm", "hatsune_miku.vrm", "mei 2.vrm"])
def test_build_records_rejects_a_model_whose_name_is_not_a_slug(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")

    with pytest.raises(SystemExit) as exc:
        up.build_records("", tmp_path)

    msg = str(exc.value)
    assert name in msg
    assert "rename" in msg.lower()
