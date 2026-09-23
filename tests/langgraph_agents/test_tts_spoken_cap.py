# -*- coding: utf-8 -*-
"""Tests for the spoken-length cap at the single choke point.

`api/main.py::_stream_speech` serves BOTH /chat and POST /tts: long replies
reach SpeechLLm as their first sentence only, and `speech_start` carries
`truncated` / `spoken_chars` / `estimated_audio_s` so the frontend can
schedule gapless playback. A fake TTS client stands in for SpeechLLm and
records exactly what text it was asked to synthesize.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest


class _FakeTtsClient:
    def __init__(self):
        self.calls = []

    async def synthesize_stream(self, text, voice_path=None, language=None):
        self.calls.append(
            {"text": text, "voice_path": voice_path, "language": language}
        )
        yield {"type": "start", "codec": "opus",
               "sample_rate": 24000, "voice_version": "vv1"}
        yield {"type": "chunk", "seq": 0, "codec": "opus",
               "audio": "QQ==", "duration": 1.0}
        yield {"type": "end", "chunks": 1, "duration": 1.0}


@pytest.fixture
def fake_tts(monkeypatch):
    import langgraph_agents.api.main as main_module

    fake = _FakeTtsClient()
    monkeypatch.setattr(
        main_module, "get_vieneu_tts_client", lambda: fake)
    return fake


async def _collect(text, **kwargs):
    import langgraph_agents.api.main as main_module

    return [e async for e in main_module._stream_speech(text, **kwargs)]


def _data(events, name):
    for e in events:
        if e["event"] == name:
            return json.loads(e["data"])
    raise AssertionError(f"no {name} event in {[e['event'] for e in events]}")


@pytest.mark.unit
async def test_long_text_sends_first_sentence_only(fake_tts):
    text = "Câu mở đầu. " + "x" * 700
    events = await _collect(text, voice_path="voices/anne_vi.wav",
                            language="vi")
    assert fake_tts.calls[0]["text"] == "Câu mở đầu."
    start = _data(events, "speech_start")
    assert start["truncated"] is True
    assert start["spoken_chars"] == len("Câu mở đầu.")
    assert start["estimated_audio_s"] == pytest.approx(
        len("Câu mở đầu.") / 17.9)
    assert [e["event"] for e in events] == [
        "speech_start", "speech_chunk", "speech_end"]


@pytest.mark.unit
async def test_short_text_passes_verbatim(fake_tts):
    text = "Xin chào, hôm nay bạn thế nào?"
    events = await _collect(text, voice_path="voices/anne_vi.wav",
                            language="vi")
    assert fake_tts.calls[0]["text"] == text
    start = _data(events, "speech_start")
    assert start["truncated"] is False
    assert start["spoken_chars"] == len(text)
    assert start["estimated_audio_s"] == pytest.approx(len(text) / 17.9)


@pytest.mark.unit
async def test_truncation_does_not_log_content(fake_tts, monkeypatch, caplog):
    import langgraph_agents.api.main as main_module
    import logging

    records = []

    class _Spy:
        def info(self, msg, extra=None):
            records.append((msg, extra))

    monkeypatch.setattr(main_module, "get_logger", lambda name: _Spy())
    with caplog.at_level(logging.INFO):
        await _collect("Đầu. " + "y" * 700, voice_path="v", language="vi")
    assert records, "expected a speech_truncated log line"
    msg, extra = records[0]
    assert msg == "speech_truncated"
    assert set(extra) == {"total_chars", "spoken_chars"}
    assert "y" * 10 not in json.dumps(records)


@pytest.mark.unit
def test_chat_and_tts_share_the_choke_point():
    """Both call sites must route through _stream_speech — if anyone splits
    them (per-route truncation logic), this fails on purpose. Read as source
    on purpose: driving both HTTP routes needs the full graph, while the
    invariant here is purely structural (one def, two call sites)."""
    src = Path("agenticRAG/langgraph_agents/api/main.py").read_text(
        encoding="utf-8")
    code_uses = re.findall(
        r"(?:async def _stream_speech\(|"
        r"_stream_speech\(final_answer, voice_path, speech_language\)|"
        r"_stream_speech\(body\.text, voice_path, language\))",
        src,
    )
    assert len(code_uses) == 3, (
        "expected 1 def + 2 call sites (/chat, /tts), found: "
        f"{code_uses}"
    )
    assert src.count("plan_spoken_text(") == 1, (
        "the budget must be applied in exactly one place"
    )


@pytest.mark.unit
def test_speech_start_shape_is_inspectable():
    """Documents the new speech_start fields for the frontend contract."""
    sig_src = inspect.getsource(
        __import__("langgraph_agents.api.main",
                   fromlist=["_stream_speech"])._stream_speech)
    for field in ("truncated", "spoken_chars", "estimated_audio_s"):
        assert f'"{field}"' in sig_src
