# -*- coding: utf-8 -*-
"""Unit tests for VieNeuClient.synthesize_stream() and POST /synthesize/stream.

All of it runs against a fake model — the real one takes ~30s cold / ~8s
cached to load and does real CPU inference, which has no place in a
millisecond-scale unit suite. See docs/worklogs for the REAL-MODEL check
(timing, click analysis at chunk joins, speaker-similarity) that this file
deliberately does not attempt.

The fake TTS below still exercises the REAL soundfile encode/decode path —
its `infer_stream()` yields genuine sine-wave float32 arrays, not zeros or
mocks, so the Opus/WAV round trip in `_encode_chunk` runs for real. Only
model loading and inference are faked; the audio format handling is not.

`voice_path` is REQUIRED since 12-09-2026: a missing/unreadable reference or
no `voice_path` at all now raises `VoiceResolutionError` instead of falling
back to VieNeu's built-in preset voice (see test_voice_required.py for that
contract in detail). Most tests below exist to exercise coalescing/framing/
encoding, not voice resolution, so they route through the `voice_ref`
fixture — a real file on disk — to get past that check cleanly.
"""

import base64
import io
import json

import numpy as np
import pytest
import soundfile as sf

from src.services.vieneu_client import VieNeuClient, VoiceResolutionError


# ── Fake model ──────────────────────────────────────────────────────────────

def _sine(seconds: float, sample_rate: int = 48000, freq: float = 220.0,
          amplitude: float = 0.2) -> np.ndarray:
    """One piece of real (non-silent, non-random) audio at a given length."""
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False,
                     dtype=np.float32)
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class _FakeStreamingTTS:
    """Stands in for the loaded VieNeu model — loading the real one costs
    ~30s. Implements only what synthesize_stream() and voice enrolment
    actually touch: `sample_rate`, `infer_stream()`, `encode_reference()`.

    `encode_reference`'s signature and return shape (a two-tuple, not a
    single value) track vieneu.v3turbo.V3TurboVieNeuTTS.encode_reference —
    see the identical note on test_voice_required.py's `_FakeTTS`, which this
    mirrors for the same reason.
    """

    def __init__(self, pieces, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self._pieces = pieces
        self.infer_stream_calls = []
        self.encoded = []

    def infer_stream(self, text, voice=None, **kwargs):
        self.infer_stream_calls.append((text, voice))
        for piece in self._pieces:
            yield piece

    def encode_reference(self, path, denoise=True):
        self.encoded.append(path)
        n = len(self.encoded)
        return (f"<emb:{path}:{n}>", f"<codes:{path}:{n}>")


# Mimics infer_stream()'s real shape: short pieces (including silence-length
# gaps between phrases) that sum past the 2.0s coalescing threshold more than
# once, plus a tail that does NOT reach it — so one generator run exercises
# "first chunk immediate", "coalesce to >=2s", and "flush the leftover tail"
# all at once. Durations (s): 0.3, 0.05, 0.8, 0.05, 0.9, 0.05, 0.4, 0.2, 0.3.
#   seq0 = piece0 alone (0.3s, unbuffered)
#   seq1 = pieces 1-6 coalesced (0.05+0.8+0.05+0.9+0.05+0.4 = 2.25s, >=2.0)
#   seq2 = pieces 7-8 flushed at end (0.2+0.3 = 0.5s, < 2.0)
_DEFAULT_DURATIONS = [0.3, 0.05, 0.8, 0.05, 0.9, 0.05, 0.4, 0.2, 0.3]


def _default_pieces(sample_rate=48000):
    return [_sine(d, sample_rate) for d in _DEFAULT_DURATIONS]


@pytest.fixture
def client(tmp_path):
    c = VieNeuClient({"output_dir": str(tmp_path / "out")})
    c._tts = _FakeStreamingTTS(_default_pieces())
    return c


@pytest.fixture
def voice_ref(tmp_path):
    """A real reference .wav on disk, for tests that are not themselves about
    voice resolution. synthesize_stream() now requires a resolvable
    voice_path — a `None` reference raises VoiceResolutionError before
    `start` is ever yielded (see TestVoiceVersion below) — so anything that
    wants to reach chunking/framing/encoding needs a real file to point at.
    """
    ref = tmp_path / "voices" / "ref.wav"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"RIFF-fake-reference-audio-bytes")
    return str(ref)


def _chunks(events):
    return [e for e in events if e["type"] == "chunk"]


# ── Coalescing ────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestCoalescing:

    def test_first_chunk_is_emitted_immediately_unbuffered(self, client, voice_ref):
        """seq 0 is the first infer_stream() piece alone, not merged with
        what follows — this is what keeps first-audio latency low."""
        chunks = _chunks(list(client.synthesize_stream("hello", "en", voice_ref)))

        assert chunks[0]["seq"] == 0
        assert chunks[0]["duration"] == pytest.approx(0.3, abs=0.01)

    def test_middle_chunks_coalesce_to_at_least_two_seconds(self, client, voice_ref):
        chunks = _chunks(list(client.synthesize_stream("hello", "en", voice_ref)))

        assert len(chunks) == 3
        assert chunks[1]["seq"] == 1
        assert chunks[1]["duration"] >= 2.0
        assert chunks[1]["duration"] == pytest.approx(2.25, abs=0.01)

    def test_final_partial_buffer_is_flushed_not_dropped(self, client, voice_ref):
        """The tail (0.5s, under the 2.0s threshold) still has to go out —
        otherwise the end of every answer would be silently lost."""
        chunks = _chunks(list(client.synthesize_stream("hello", "en", voice_ref)))

        assert chunks[-1]["seq"] == 2
        assert chunks[-1]["duration"] == pytest.approx(0.5, abs=0.01)


# ── Framing ───────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestFraming:

    def test_start_and_end_framing_and_counts(self, client, voice_ref):
        events = list(client.synthesize_stream("hello world", "en", voice_ref))

        assert events[0]["type"] == "start"
        assert events[0]["codec"] == client.codec
        assert events[0]["sample_rate"] == client._tts.sample_rate
        # A real content hash (16 hex chars, see _content_hash) — not the
        # old literal "preset" string, which no longer exists as a value.
        assert len(events[0]["voice_version"]) == 16

        assert events[-1]["type"] == "end"
        chunks = _chunks(events)
        assert events[-1]["chunks"] == len(chunks)
        assert events[-1]["duration"] == pytest.approx(
            sum(c["duration"] for c in chunks), abs=0.01
        )
        assert [c["seq"] for c in chunks] == list(range(len(chunks)))

    def test_error_line_emitted_on_mid_stream_exception(self, tmp_path):
        """After `start` has gone out, a failure must produce an `error`
        line rather than letting the HTTP stream just die silently."""

        class _FailingTTS(_FakeStreamingTTS):
            def infer_stream(self, text, voice=None, **kwargs):
                yield self._pieces[0]
                raise RuntimeError("synthetic failure mid-stream")

        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF-fake-reference-audio-bytes")

        c = VieNeuClient({"output_dir": str(tmp_path / "out")})
        c._tts = _FailingTTS([_sine(0.3)])

        events = list(c.synthesize_stream("hello", "en", str(ref)))

        assert events[0]["type"] == "start"
        assert events[-1]["type"] == "error"
        assert "synthetic failure mid-stream" in events[-1]["message"]
        assert not any(e["type"] == "end" for e in events)

    def test_error_line_emitted_when_voice_path_is_missing(self, client):
        """No voice_path at all used to fall back to VieNeu's preset voice —
        silently, in whatever voice the library ships. It now fails the
        whole turn instead: no `start` line, no audio, just `error`."""
        events = list(client.synthesize_stream("hello", "en", None))

        assert len(events) == 1
        assert events[0]["type"] == "error"


# ── voice_version ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestVoiceVersion:

    def test_stable_for_same_file(self, tmp_path):
        wav = tmp_path / "voice.wav"
        wav.write_bytes(b"RIFF-fake-reference-audio-bytes")
        c = VieNeuClient({"output_dir": str(tmp_path / "out")})
        c._tts = _FakeStreamingTTS(_default_pieces())

        first = next(c.synthesize_stream("hi", "en", str(wav)))["voice_version"]
        second = next(c.synthesize_stream("hi", "en", str(wav)))["voice_version"]

        assert first == second
        assert len(first) == 16

    def test_missing_reference_yields_error_not_a_version(self, client):
        """The old preset fallback reported a literal `"preset"` version
        here. A missing reference is now a hard failure before `start` —
        there is no version to report because nothing was resolved."""
        events = list(client.synthesize_stream("hi", "en", None))
        assert events[0]["type"] == "error"
        assert "voice_version" not in events[0]

    def test_changes_when_file_content_changes(self, tmp_path):
        wav_a = tmp_path / "a.wav"
        wav_b = tmp_path / "b.wav"
        wav_a.write_bytes(b"content-A")
        wav_b.write_bytes(b"content-B-different-bytes")
        c = VieNeuClient({"output_dir": str(tmp_path / "out")})
        c._tts = _FakeStreamingTTS(_default_pieces())

        version_a = next(c.synthesize_stream("hi", "en", str(wav_a)))["voice_version"]
        version_b = next(c.synthesize_stream("hi", "en", str(wav_b)))["voice_version"]

        assert version_a != version_b


# ── Encoding ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestChunkEncoding:

    def test_each_opus_chunk_round_trips_through_soundfile(self, client, voice_ref):
        events = list(client.synthesize_stream("hello world", "en", voice_ref))
        chunks = _chunks(events)
        assert chunks  # sanity: there is something to decode

        for c in chunks:
            assert c["codec"] == "opus"
            audio_bytes = base64.b64decode(c["audio"])
            data, sr = sf.read(io.BytesIO(audio_bytes))
            assert sr == client._tts.sample_rate
            assert len(data) > 0

    def test_wav_fallback_codec_round_trips(self, tmp_path):
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF-fake-reference-audio-bytes")

        c = VieNeuClient({"output_dir": str(tmp_path / "out"), "codec": "wav"})
        c._tts = _FakeStreamingTTS(_default_pieces())

        events = list(c.synthesize_stream("hello world", "en", str(ref)))
        assert events[0]["codec"] == "wav"

        for chunk in _chunks(events):
            assert chunk["codec"] == "wav"
            audio_bytes = base64.b64decode(chunk["audio"])
            data, sr = sf.read(io.BytesIO(audio_bytes))
            assert sr == c._tts.sample_rate
            assert len(data) > 0

    def test_flac_codec_round_trips_and_is_reported(self, tmp_path):
        """flac is the configured default (configs/models.yaml) — the owner's
        11-09-2026 decision after hearing clicks on Opus joins and none on
        the uncompressed stream. `start` and every `chunk` line must report
        it accurately."""
        ref = tmp_path / "ref.wav"
        ref.write_bytes(b"RIFF-fake-reference-audio-bytes")

        c = VieNeuClient({"output_dir": str(tmp_path / "out"), "codec": "flac"})
        c._tts = _FakeStreamingTTS(_default_pieces())

        events = list(c.synthesize_stream("hello world", "en", str(ref)))
        assert events[0]["codec"] == "flac"

        chunks = _chunks(events)
        assert chunks  # sanity: there is something to decode
        for chunk in chunks:
            assert chunk["codec"] == "flac"
            audio_bytes = base64.b64decode(chunk["audio"])
            data, sr = sf.read(io.BytesIO(audio_bytes))
            assert sr == c._tts.sample_rate
            assert len(data) > 0

    def test_flac_chunk_is_bit_exact(self, tmp_path):
        """Lossless is the whole point of flac over opus: encoding the SAME
        samples from two INDEPENDENT, freshly-started encoder instances —
        exactly what happens once per chunk in production — must decode
        back to identical int16 sample arrays both times. If it did not,
        that variation IS the seam artefact a real-model check measured as
        audible clicks on opus joins (2/38, 0/13, 1/13 across three runs),
        because a lossy codec's output can depend on encoder state that
        differs between two independently-started instances. flac's
        compression has no such state-dependence: decode(encode(x)) == x
        (quantized to int16) regardless of which encoder instance produced
        it.
        """
        c = VieNeuClient({"output_dir": str(tmp_path / "out"), "codec": "flac"})
        sr = 48000
        audio = _sine(0.8, sr)  # a substantial, non-degenerate piece

        # Two separate calls == two separate soundfile encoder instances,
        # the same independence every real chunk gets.
        first_bytes = c._encode_chunk(audio, sr)
        second_bytes = c._encode_chunk(audio, sr)

        first_data, first_sr = sf.read(io.BytesIO(first_bytes), dtype="int16")
        second_data, second_sr = sf.read(io.BytesIO(second_bytes), dtype="int16")

        assert first_sr == second_sr == sr
        assert np.array_equal(first_data, second_data)
        assert len(first_data) == len(audio)


# ── Endpoint ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSynthesizeStreamEndpoint:

    def test_returns_valid_ndjson(self, tts_client, monkeypatch):
        """POST /synthesize/stream, end to end through FastAPI, still with a
        fake model — proves the route wiring (StreamingResponse, NDJSON
        media type, clean_text_for_tts applied) without touching real
        weights. voice_path names the real committed reference so the
        pre-stream voice-resolution check (see api_server.py) passes."""
        import api_server

        monkeypatch.setattr(
            api_server.vieneu_client, "_tts", _FakeStreamingTTS(_default_pieces())
        )

        response = tts_client.post(
            "/synthesize/stream",
            json={
                "text": "Hello *there*\\n friend",
                "language": "en",
                "voice_path": "voices/anne_vi.wav",
            },
        )

        assert response.status_code == 200
        assert "application/x-ndjson" in response.headers["content-type"]

        lines = [ln for ln in response.text.strip().split("\n") if ln]
        events = [json.loads(ln) for ln in lines]

        assert events[0]["type"] == "start"
        assert events[-1]["type"] == "end"

        chunks = _chunks(events)
        assert len(chunks) == events[-1]["chunks"] > 0
        for c in chunks:
            audio_bytes = base64.b64decode(c["audio"])
            data, sr = sf.read(io.BytesIO(audio_bytes))
            assert len(data) > 0

        # clean_text_for_tts ran server-side: the fake's infer_stream sees
        # the cleaned text, not the raw "*there*\n" markup.
        seen_text, _voice = api_server.vieneu_client._tts.infer_stream_calls[0]
        assert "*" not in seen_text
        assert "\\n" not in seen_text

    def test_llm_mode_with_voice_prompt(self, tts_client, monkeypatch):
        """A voice_prompt object's nested text reaches the model, same as
        simple text mode — this is the request-extraction logic that used to
        be covered by POST /synthesize's TestSynthesizeEndpoint."""
        import api_server

        monkeypatch.setattr(
            api_server.vieneu_client, "_tts", _FakeStreamingTTS(_default_pieces())
        )

        response = tts_client.post(
            "/synthesize/stream",
            json={
                "voice_prompt": {"text": "Do some stretches"},
                "language": "vi",
                "voice_path": "voices/anne_vi.wav",
            },
        )

        assert response.status_code == 200
        seen_text, _voice = api_server.vieneu_client._tts.infer_stream_calls[0]
        assert seen_text == "Do some stretches"

    def test_empty_text_returns_400_before_streaming(self, tts_client):
        """An empty string, not just whitespace-only (see below) — both must
        be rejected before any StreamingResponse is constructed. Checked
        ahead of voice_path, so this still 400s with no voice_path sent."""
        response = tts_client.post("/synthesize/stream", json={"text": ""})
        assert response.status_code == 400
        assert "empty" in response.json()["detail"].lower()

    def test_whitespace_only_text_returns_400(self, tts_client):
        response = tts_client.post("/synthesize/stream", json={"text": "   "})
        assert response.status_code == 400

    def test_no_text_at_all_returns_400(self, tts_client):
        """Neither `text` nor `voice_prompt` given."""
        response = tts_client.post("/synthesize/stream", json={"language": "en"})
        assert response.status_code == 400

    def test_missing_voice_path_returns_422_before_streaming(self, tts_client, monkeypatch):
        """Text is fine but voice_path is absent — the request must fail as
        a normal HTTP error before StreamingResponse is ever constructed,
        not as an in-stream `error` line."""
        import api_server

        monkeypatch.setattr(
            api_server.vieneu_client, "_tts", _FakeStreamingTTS(_default_pieces())
        )

        response = tts_client.post(
            "/synthesize/stream", json={"text": "hello", "language": "en"}
        )

        assert response.status_code == 422
        assert not api_server.vieneu_client._tts.infer_stream_calls

    def test_unresolvable_voice_path_returns_422_before_streaming(self, tts_client, monkeypatch):
        import api_server

        monkeypatch.setattr(
            api_server.vieneu_client, "_tts", _FakeStreamingTTS(_default_pieces())
        )

        response = tts_client.post(
            "/synthesize/stream",
            json={
                "text": "hello",
                "language": "en",
                "voice_path": "voices/nobody_recorded_this_vi.wav",
            },
        )

        assert response.status_code == 422
        assert "nobody_recorded_this_vi.wav" in response.json()["detail"]
        assert not api_server.vieneu_client._tts.infer_stream_calls
