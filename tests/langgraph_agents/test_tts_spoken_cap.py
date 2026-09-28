# -*- coding: utf-8 -*-
"""Tests for sentence-by-sentence speech at the single choke point.

`api/main.py::_stream_speech` serves BOTH /chat and POST /tts: the reply is
split into sentences (text_budget.py) and each one is a separate SpeechLLm
call, streamed to the browser as ONE clip — one speech_start, a
speech_sentence marker per sentence, chunk seqs continuing across sentences,
one speech_end. A fake TTS client stands in for SpeechLLm and records exactly
what text it was asked to synthesize.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest


class _FakeTtsClient:
    """Two chunks per call. `fail_on` = call index that errors after one chunk,
    `raise_on` = call index that raises before yielding anything."""

    def __init__(self, fail_on=None, raise_on=None):
        self.calls = []
        self.fail_on = fail_on
        self.raise_on = raise_on

    async def synthesize_stream(self, text, voice_path=None, language=None):
        index = len(self.calls)
        self.calls.append(
            {"text": text, "voice_path": voice_path, "language": language}
        )
        if index == self.raise_on:
            from langgraph_agents.services.exceptions import ServiceUnavailableError
            raise ServiceUnavailableError("vieneu_tts", "connection refused")
        yield {"type": "start", "codec": "opus",
               "sample_rate": 24000, "voice_version": "vv1"}
        yield {"type": "chunk", "seq": 0, "codec": "opus",
               "audio": f"A{index}==", "duration": 1.0}
        if index == self.fail_on:
            yield {"type": "error", "message": "model crashed"}
            return
        yield {"type": "chunk", "seq": 1, "codec": "opus",
               "audio": f"B{index}==", "duration": 1.0}
        yield {"type": "end", "chunks": 2, "duration": 2.0}


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


S1 = "Hôm nay mình tập nhẹ phần vai trong mười phút nhé."
S2 = "Bạn ngồi thẳng lưng, thả lỏng hai tay xuống."
S3 = "Sau đó xoay vai ra sau thật chậm mười lần!"
TEXT = f"{S1} {S2} {S3}"


def _all(events, name):
    return [json.loads(e["data"]) for e in events if e["event"] == name]


@pytest.mark.unit
async def test_one_call_per_sentence_in_order(fake_tts):
    events = await _collect(TEXT, voice_path="voices/anne_vi.wav", language="vi")
    assert [c["text"] for c in fake_tts.calls] == [S1, S2, S3]
    assert all(c["language"] == "vi" for c in fake_tts.calls)


@pytest.mark.unit
async def test_streams_as_one_clip_with_sentence_markers(fake_tts):
    events = await _collect(TEXT, voice_path="v", language="vi")
    assert [e["event"] for e in events] == [
        "speech_start",
        "speech_sentence", "speech_chunk", "speech_chunk",
        "speech_sentence", "speech_chunk", "speech_chunk",
        "speech_sentence", "speech_chunk", "speech_chunk",
        "speech_end",
    ]
    # Seqs continue across sentences, so the clip has no gaps or repeats.
    assert [c["seq"] for c in _all(events, "speech_chunk")] == [0, 1, 2, 3, 4, 5]
    assert [c["audio"] for c in _all(events, "speech_chunk")][:3] == ["A0==", "B0==", "A1=="]
    sentences = _all(events, "speech_sentence")
    assert [s["index"] for s in sentences] == [0, 1, 2]
    assert [s["first_seq"] for s in sentences] == [0, 2, 4]
    assert sentences[1]["estimated_audio_s"] == pytest.approx(len(S2) / 17.9)
    assert _data(events, "speech_end") == {"chunks": 6}


@pytest.mark.unit
async def test_speech_start_carries_whole_reply_totals(fake_tts):
    events = await _collect(TEXT, voice_path="v", language="vi")
    start = _data(events, "speech_start")
    spoken = len(S1) + len(S2) + len(S3)
    assert start["truncated"] is False
    assert start["spoken_chars"] == spoken
    assert start["estimated_audio_s"] == pytest.approx(spoken / 17.9)
    assert start["sentences"] == 3
    assert start["lang"] == "vi"


@pytest.mark.unit
async def test_long_replies_are_spoken_in_full(fake_tts):
    text = " ".join([S1] * 20)
    assert len(text) > 600
    events = await _collect(text, voice_path="v", language="vi")
    assert len(fake_tts.calls) == 20
    assert _data(events, "speech_start")["truncated"] is False


@pytest.mark.unit
async def test_failure_before_any_audio_is_one_speech_failed(monkeypatch):
    import langgraph_agents.api.main as main_module

    fake = _FakeTtsClient(raise_on=0)
    monkeypatch.setattr(main_module, "get_vieneu_tts_client", lambda: fake)
    events = await _collect(TEXT, voice_path="v", language="vi")
    assert [e["event"] for e in events] == ["speech_failed"]
    assert len(fake.calls) == 1  # does not go on to the next sentence


@pytest.mark.unit
async def test_failure_after_some_audio_keeps_it_as_a_partial_clip(monkeypatch):
    import langgraph_agents.api.main as main_module

    fake = _FakeTtsClient(fail_on=1)  # sentence 2 errors after one chunk
    monkeypatch.setattr(main_module, "get_vieneu_tts_client", lambda: fake)
    events = await _collect(TEXT, voice_path="v", language="vi")
    kinds = [e["event"] for e in events]
    assert "speech_failed" not in kinds
    assert _data(events, "speech_end") == {"chunks": 3, "partial": True}
    assert len(fake.calls) == 2  # sentence 3 is not attempted


@pytest.mark.unit
async def test_transport_failure_mid_reply_is_partial_too(monkeypatch):
    import langgraph_agents.api.main as main_module

    fake = _FakeTtsClient(raise_on=2)
    monkeypatch.setattr(main_module, "get_vieneu_tts_client", lambda: fake)
    events = await _collect(TEXT, voice_path="v", language="vi")
    assert _data(events, "speech_end") == {"chunks": 4, "partial": True}


@pytest.mark.unit
async def test_blank_text_is_speech_failed_without_calling_tts(fake_tts):
    events = await _collect("   ", voice_path="v", language="vi")
    assert [e["event"] for e in events] == ["speech_failed"]
    assert fake_tts.calls == []


@pytest.mark.unit
async def test_env_cap_truncation_does_not_log_content(fake_tts, monkeypatch):
    import langgraph_agents.api.main as main_module

    records = []

    class _Spy:
        def info(self, msg, extra=None):
            records.append((msg, extra))

    monkeypatch.setattr(main_module, "get_logger", lambda name: _Spy())
    monkeypatch.setenv("TTS_MAX_SPOKEN_CHARS", "60")
    await _collect(TEXT, voice_path="v", language="vi")
    assert records, "expected a speech_truncated log line"
    msg, extra = records[0]
    assert msg == "speech_truncated"
    assert set(extra) == {"total_chars", "spoken_chars"}
    assert "xoay vai" not in json.dumps(records, ensure_ascii=False)


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
        "the sentence plan must be made in exactly one place"
    )


@pytest.mark.unit
def test_speech_start_shape_is_inspectable():
    """Documents the speech_start / speech_sentence fields of the frontend contract."""
    sig_src = inspect.getsource(
        __import__("langgraph_agents.api.main",
                   fromlist=["_stream_speech"])._stream_speech)
    for field in ("truncated", "spoken_chars", "estimated_audio_s", "sentences", "first_seq", "partial"):
        assert f'"{field}"' in sig_src
