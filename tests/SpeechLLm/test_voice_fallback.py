# -*- coding: utf-8 -*-
"""A missing reference .wav must degrade to the preset voice, never raise.

SpeechLLm is the only place in the system that can answer "does that file
exist?" — the LangGraph service builds `voices/<slug>_<lang>.wav` in another
process and, deployed, on another host. So the check and the fallback both
belong here, and a character with no recording yet still has to be able to
speak.
"""

import logging

import pytest

from src.services.vieneu_client import VieNeuClient, _resolve_ref


class _FakeTTS:
    """Stands in for the GGUF model — loading the real one costs ~30s.

    ``encode_reference`` is kept to the REAL signature —
    ``encode_reference(self, path, denoise=...)`` returning a two-tuple
    ``(speaker_emb, ref_codes)`` — deliberately, not loosened to
    ``*args, **kwargs``. `_encode_voice` exists specifically because
    v3turbo's `voice=` resolver silently falls back to the preset when
    handed anything that isn't `ref_audio=<path>` / `voice=<preset>` /
    `voice=<dict>` — a tuple matches none of those. A permissive fake here
    would let a regression to "pass the raw tuple straight through" pass
    these tests while still being broken against the real model.
    """

    def __init__(self):
        self.encoded = []

    def encode_reference(self, path, denoise=False):
        self.encoded.append(path)
        # Each call must return something DISTINCT so the "encoded once and
        # cached" assertions actually prove a cache hit rather than just
        # comparing two equal-by-coincidence values.
        call_n = len(self.encoded)
        return (f"<speaker_emb:{path}:{call_n}>", f"<ref_codes:{path}:{call_n}>")


@pytest.fixture
def client(tmp_path):
    c = VieNeuClient({"output_dir": str(tmp_path / "out"), "voice_path": ""})
    c._tts = _FakeTTS()          # _load_model() short-circuits on this
    return c


@pytest.mark.unit
def test_missing_reference_falls_back_instead_of_raising(client, caplog):
    with caplog.at_level(logging.WARNING, logger="speechllm.vieneu"):
        voice = client._resolve_voice("voices/nobody_recorded_this_vi.wav")

    assert voice is None                       # preset
    assert client._tts.encoded == []           # never attempted to encode
    assert "voice_reference_missing" in caplog.text
    # The absolute path matters: the symptom of this branch is audio in the
    # wrong voice, which by ear is indistinguishable from "right voice, bad
    # model". Without the resolved path in the log there is nothing to search.
    assert str(_resolve_ref("voices/nobody_recorded_this_vi.wav")) in caplog.text


@pytest.mark.unit
def test_existing_reference_is_encoded_and_cached(client, tmp_path, monkeypatch):
    wav = tmp_path / "anne_vi.wav"
    wav.write_bytes(b"RIFF")

    first = client._resolve_voice(str(wav))
    second = client._resolve_voice(str(wav))

    assert first == second
    assert len(client._tts.encoded) == 1, "second call re-encoded instead of using the cache"


@pytest.mark.unit
def test_relative_paths_anchor_to_speechllm_not_the_cwd(monkeypatch, tmp_path):
    """uvicorn's working directory must not change which file is found."""
    monkeypatch.chdir(tmp_path)
    assert _resolve_ref("voices/anne_vi.wav").is_absolute()
    assert _resolve_ref("voices/anne_vi.wav").parts[-2:] == ("voices", "anne_vi.wav")


@pytest.mark.unit
def test_absolute_paths_are_left_alone():
    p = _resolve_ref("C:/somewhere/else/x.wav" if __import__("os").name == "nt"
                     else "/somewhere/else/x.wav")
    assert p.is_absolute()
    assert p.name == "x.wav"


@pytest.mark.unit
def test_no_voice_path_at_all_is_the_preset(client):
    assert client._resolve_voice(None) is None
    assert client._tts.encoded == []


# ── _enrol_known_voices ──────────────────────────────────────────────────────
#
# The startup warm-up thread (api_server._warm_up_model) calls this after
# _load_model() so that encode_reference()'s ~3-4s per-voice cost is paid
# once at startup instead of inside whichever user's request happens to name
# a voice first — a real-model check measured 4.36s time-to-first-chunk on a
# voice's first-ever request against 0.25s on an identical warm repeat.

class _SelectivelyFailingTTS(_FakeTTS):
    """Like _FakeTTS, but one specific reference is "corrupt": encoding it
    raises instead of returning an embedding. Stands in for a real bad file
    (truncated download, wrong format) without needing one on disk."""

    def __init__(self, bad_name: str):
        super().__init__()
        self.bad_name = bad_name

    def encode_reference(self, path, denoise=True):
        if self.bad_name in str(path):
            raise RuntimeError(f"corrupt reference: {path}")
        return super().encode_reference(path, denoise=denoise)


@pytest.mark.unit
def test_enrols_every_wav_in_the_voices_dir(tmp_path):
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    (voices_dir / "anne_en.wav").write_bytes(b"RIFF-anne-en")
    (voices_dir / "anne_vi.wav").write_bytes(b"RIFF-anne-vi")
    (voices_dir / "notes.txt").write_text("not a voice, must be ignored")

    c = VieNeuClient({"output_dir": str(tmp_path / "out"), "voice_path": ""})
    c._tts = _FakeTTS()

    c._enrol_known_voices(voices_dir)

    en_key = str(_resolve_ref(str(voices_dir / "anne_en.wav")))
    vi_key = str(_resolve_ref(str(voices_dir / "anne_vi.wav")))
    assert en_key in c._voice_cache
    assert vi_key in c._voice_cache
    assert en_key in c._voice_version_cache
    assert vi_key in c._voice_version_cache
    # The .txt file was never handed to the model.
    assert len(c._tts.encoded) == 2

    # A request naming one of these afterward must hit the cache, not
    # re-encode — this is the whole point of enrolling at startup.
    c._resolve_voice(str(voices_dir / "anne_en.wav"))
    assert len(c._tts.encoded) == 2


@pytest.mark.unit
def test_a_corrupt_reference_does_not_abort_the_rest_of_warm_up(tmp_path, caplog):
    voices_dir = tmp_path / "voices"
    voices_dir.mkdir()
    (voices_dir / "good_en.wav").write_bytes(b"RIFF-good")
    (voices_dir / "bad_vi.wav").write_bytes(b"RIFF-bad")

    c = VieNeuClient({"output_dir": str(tmp_path / "out"), "voice_path": ""})
    c._tts = _SelectivelyFailingTTS(bad_name="bad_vi.wav")

    with caplog.at_level(logging.WARNING, logger="speechllm.vieneu"):
        c._enrol_known_voices(voices_dir)  # must not raise

    good_key = str(_resolve_ref(str(voices_dir / "good_en.wav")))
    bad_key = str(_resolve_ref(str(voices_dir / "bad_vi.wav")))
    assert good_key in c._voice_cache
    assert bad_key not in c._voice_cache
    assert "voice_enrolment_failed" in caplog.text
    assert "bad_vi.wav" in caplog.text


@pytest.mark.unit
def test_missing_voices_dir_does_not_raise(tmp_path, caplog):
    c = VieNeuClient({"output_dir": str(tmp_path / "out"), "voice_path": ""})
    c._tts = _FakeTTS()

    with caplog.at_level(logging.WARNING, logger="speechllm.vieneu"):
        c._enrol_known_voices(tmp_path / "no_such_voices_dir")  # must not raise

    assert c._tts.encoded == []
    assert "voices_dir_missing" in caplog.text
