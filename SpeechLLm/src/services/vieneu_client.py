import base64
import hashlib
import io
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

logger = logging.getLogger("speechllm.vieneu")

# SpeechLLm/ — src/services/vieneu_client.py -> services -> src -> SpeechLLm
_ROOT = Path(__file__).resolve().parents[2]


def _resolve_ref(voice_path: str) -> Path:
    """Resolve a caller-supplied reference path against SpeechLLm's own root.

    The caller is the LangGraph service on another process (and, deployed, on
    another host); it sends a name like `voices/anne_vi.wav` and cannot see this
    filesystem. Anchoring to `_ROOT` rather than the CWD means the answer does
    not change depending on where uvicorn was started from.
    """
    p = Path(voice_path)
    return p if p.is_absolute() else (_ROOT / p)


class VoiceResolutionError(FileNotFoundError):
    """A requested reference voice could not be resolved into cloned audio.

    Covers all three failure shapes under one exception so a caller needs
    only one `except` clause: no `voice_path` supplied at all, the resolved
    path does not exist, or it exists but cannot be encoded (corrupt/unreadable
    file). The message always names the resolved, ABSOLUTE path (or says none
    was given) — "wrong voice" and "no voice configured" sound identical by
    ear, which is exactly what let a missing reference answer in VieNeu's own
    preset voice with nothing in the product saying anything was wrong. There
    is no fallback left to reach for; this is raised instead.
    """


class VieNeuClient:
    """
    Local TTS using VieNeu-TTS v3-Turbo (ONNX Runtime, CPU-only).
    Vietnamese-English bilingual, zero-shot voice cloning.
    """

    def __init__(self, config: dict):
        # v3turbo, not turbo: `turbo` is the v2 GGUF backend and needs
        # `pip install vieneu[gpu]` (llama-cpp-python), so this default decided
        # whether a minimal install worked at all.
        self.mode = config.get("mode", "v3turbo")
        self.model_path = config.get("model_path", None)
        self.device = config.get("device", "cpu")
        # Off by default because it is the setting the current reference voice was
        # accepted under. It also matters more later: stage 1 will supply a
        # deliberately processed recording, and denoising would strip exactly the
        # character that was tuned into it.
        self.denoise = bool(config.get("denoise", False))
        # Codec for synthesize_stream()'s chunks. "opus" (OGG/Opus, via
        # soundfile) is the default; "wav" (PCM_16) is the fallback for a
        # client that cannot decode Opus. See configs/models.yaml for the
        # measured size numbers behind opus_compression_level's default.
        self.codec = config.get("codec", "opus")
        self.opus_compression_level = float(config.get("opus_compression_level", 0.5))
        self._tts = None
        self._voice_cache: dict = {}
        self._voice_version_cache: dict = {}

        src = self.model_path or "HF hub (auto-download)"
        print(f"[VieNeu] Initialized (mode={self.mode}, device={self.device}, "
              f"model={src}).")

    def _load_model(self):
        if self._tts is not None:
            return self._tts

        from vieneu import Vieneu

        print(f"[VieNeu] Loading model (mode={self.mode})...")
        load_start = time.time()

        kwargs = {"mode": self.mode, "device": self.device}
        # NOTE: `backbone_repo` is a HUGGINGFACE REPO ID, not a filesystem path —
        # the repo and the file name are separate constructor arguments. Leave
        # model_path empty to get the library's own default and let the weights
        # come from the hub. (Deployed, there is no route to the hub: bake the
        # weights into the image or pull them from S3.)
        if self.model_path:
            kwargs["backbone_repo"] = self.model_path

        self._tts = Vieneu(**kwargs)

        print(f"[VieNeu] Model loaded in {time.time() - load_start:.1f}s")
        return self._tts

    def _encode_voice(self, tts, ref: Path) -> dict:
        """Enrol a reference wav in the shape v3turbo's ``voice=`` actually accepts.

        ``encode_reference`` returns a TUPLE ``(speaker_emb, ref_codes)``, but
        v3turbo's ``_resolve_ref`` understands only ``ref_audio=<path>``,
        ``voice=<preset name>`` or ``voice=<dict>``. A tuple matches none of those
        and falls through to the DEFAULT PRESET — silently, with no error and no
        warning. Measured against the reference recording: the tuple scored 0.19
        speaker-similarity, the preset 0.21, and this dict 0.67. Passing the tuple
        was not cloning at all; it merely looked like it.

        A dict rather than switching to ``ref_audio=<path>`` because enrolment
        costs ~2.9s and the result is cached per file; ``ref_audio=`` would redo it
        on every single request.
        """
        emb, codes = tts.encode_reference(str(ref), denoise=self.denoise)
        return {"speaker_emb": emb, "codes": codes}

    def _resolve_voice(self, voice_path: Optional[str] = None):
        """Encode the requested reference voice. Raises if it cannot be.

        No more silent substitution: a missing `voice_path`, a reference that
        does not exist, or one that exists but fails to encode (corrupt/
        unreadable) all raise `VoiceResolutionError` now instead of quietly
        answering in VieNeu's built-in preset voice. This is also the only
        place in the system that can answer "does that file exist?" — the
        caller builds the name on another process, and in the deployed layout
        another host.
        """
        if not voice_path:
            raise VoiceResolutionError(
                "No voice_path supplied — a reference voice is required."
            )

        ref = _resolve_ref(voice_path)
        key = str(ref)
        if key in self._voice_cache:
            return self._voice_cache[key]

        if not ref.is_file():
            # The absolute path matters: the symptom of this branch is "no
            # audio at all", which by ear is indistinguishable from "right
            # voice, mediocre model" until someone actually goes looking —
            # without the resolved path in the message there is nothing to
            # search for.
            raise VoiceResolutionError(
                f"Reference voice not found: requested={voice_path} resolved={ref}"
            )

        tts = self._load_model()
        print(f"[VieNeu] Encoding voice: {ref}")
        v_start = time.time()
        try:
            encoded = self._encode_voice(tts, ref)
        except Exception as e:
            raise VoiceResolutionError(
                f"Reference voice could not be encoded: requested={voice_path} "
                f"resolved={ref} ({e})"
            ) from e
        self._voice_cache[key] = encoded
        print(f"[VieNeu] Voice encoded in {time.time() - v_start:.1f}s")
        return self._voice_cache[key]

    def _enrol_known_voices(self, voices_dir: Optional[Path] = None) -> None:
        """Pre-encode every reference clip under voices/*.wav at startup.

        Enrolment (`encode_reference`, inside `_resolve_voice`) costs ~3-4s
        for a full-length reference and was previously paid inside the
        FIRST request that named a given `voice_path` — a real-model check
        measured 4.36s time-to-first-chunk cold against 0.25s on an
        identical warm repeat, almost entirely this cost. Called from
        api_server's startup warm-up, after `_load_model()` and before
        `/health` reports ready, so that cost is paid once here instead of
        by whichever user's request happens to name a voice first.

        Goes through `_resolve_voice` and `_voice_version` exactly as a real
        request would — same `_resolve_ref` anchoring, same cache dicts — so
        the key this writes is the key a later request reads under. Default
        `voices_dir` is `_ROOT / "voices"`; overridable so tests can point
        this at a tmp directory instead of the repo's real voice catalog.

        A missing or corrupt file must not take down the whole warm-up:
        voices are added to the catalog before anyone records audio for
        them (see `_resolve_voice`'s own docstring) — a bad file here is
        that same case, not a startup failure. Logged as a WARNING with the
        exception rather than silently skipped, because "voice never got
        enrolled" is otherwise indistinguishable from "voice enrolled fine,
        first live request was just unlucky".
        """
        voices_dir = Path(voices_dir) if voices_dir is not None else (_ROOT / "voices")
        if not voices_dir.is_dir():
            logger.warning("voices_dir_missing path=%s -> no voices pre-enrolled", voices_dir)
            return

        for wav_path in sorted(voices_dir.glob("*.wav")):
            enrol_start = time.time()
            try:
                self._resolve_voice(str(wav_path))
                self._voice_version(str(wav_path))
            except Exception:
                logger.warning("voice_enrolment_failed path=%s", wav_path, exc_info=True)
                continue
            print(f"[VieNeu] Pre-enrolled {wav_path.name} in "
                  f"{time.time() - enrol_start:.2f}s")

    def _content_hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]

    def _voice_version(self, voice_path: str) -> str:
        """Hash of the reference audio actually used, for the stream's `start` line.

        The frontend's IndexedDB cache is what actually reads this, to know
        when a cached clip is stale — the reference file changed content (a
        voice got re-recorded) even though its path/name did not. The
        LangGraph service in between does not interpret this value at all;
        it only forwards it from `start` to the browser as `speech_start`.

        Always the resolved reference file's own hash: `_resolve_voice` has
        already guaranteed, by the time this runs, that `voice_path` names a
        real, readable file — there is no more "preset" fallback version for
        this to report instead.

        Hashed once per resolved file and cached — reading + hashing a ~1MB
        wav on every request would undo the point of caching voice encoding.
        """
        ref = _resolve_ref(voice_path)
        key = str(ref)
        if key not in self._voice_version_cache:
            self._voice_version_cache[key] = self._content_hash(ref)
        return self._voice_version_cache[key]

    def _encode_chunk(self, audio: np.ndarray, sample_rate: int) -> bytes:
        """Encode one chunk of float32 PCM to a complete, self-standing file.

        Each chunk is encoded independently (not appended to a running
        encoder) so that the browser side can `decodeAudioData` any one chunk
        on its own without needing the others. That independence is exactly
        what cost Opus its default: restarting a LOSSY encoder per chunk
        measurably clicked at some join points (2/38, 0/13, 1/13 across three
        real-model runs) even though the equivalent uncompressed-PCM stream
        had none. FLAC is lossless — re-encoding the same PCM samples from a
        fresh encoder state still decodes back to those exact samples, so
        chunk independence costs nothing at the join. See configs/models.yaml
        for the size tradeoff behind the codec choice.
        """
        buf = io.BytesIO()
        if self.codec == "opus":
            sf.write(
                buf, audio, sample_rate,
                format="OGG", subtype="OPUS",
                compression_level=self.opus_compression_level,
            )
        elif self.codec == "flac":
            sf.write(buf, audio, sample_rate, format="FLAC", subtype="PCM_16")
        else:
            sf.write(buf, audio, sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

    # Coalescing target for synthesize_stream(): infer_stream() yields ~76
    # small pieces for a 1500-char answer (about half of them short silence
    # arrays between phrases), each a network round trip if sent as-is. 2.0s
    # of audio per chunk after the first brings that down to ~20-25 without
    # noticeably delaying anything after the first chunk, which is the one
    # that latency actually depends on.
    _STREAM_BUFFER_SECONDS = 2.0

    def synthesize_stream(self, text: str, language: str = "vi",
                           voice_path: Optional[str] = None):
        """Yield NDJSON-ready dicts per the /synthesize/stream contract.

        A SYNC generator, deliberately — infer_stream() is plain CPU-bound
        Python/ONNX work, not I/O, so there is nothing for `async def` to
        await here. api_server wraps this generator in a StreamingResponse;
        Starlette runs a sync generator's body in a threadpool, which is what
        keeps this from blocking the event loop while it works.

        Everything from model load through the final flush is inside one
        try/except. That is broader than the contract strictly requires (it
        only calls out emitting `error` for a failure "after `start` has been
        sent"), but by the time ANY line of this generator has run, Starlette
        has already sent HTTP 200 and the response headers — the ASGI
        StreamingResponse sends `http.response.start` before pulling the
        first item from the body iterator, not after. There is no point past
        which raising a plain exception would still produce a clean HTTP
        error status; the only way to fail without leaving the client hanging
        on a truncated stream is to emit an `error` line ourselves, no matter
        where in this function the failure happens.
        """
        try:
            if not text.strip():
                raise ValueError("Text for TTS cannot be empty.")

            tts = self._load_model()
            voice = self._resolve_voice(voice_path)
            version = self._voice_version(voice_path)
            sample_rate = tts.sample_rate

            yield {
                "type": "start",
                "codec": self.codec,
                "sample_rate": sample_rate,
                "voice_version": version,
            }

            seq = 0
            total_duration = 0.0
            buffer_parts: list = []
            buffer_duration = 0.0
            first_piece = True

            infer_start = time.time()

            for piece in tts.infer_stream(text=text, voice=voice):
                piece = np.asarray(piece, dtype=np.float32)
                piece_duration = len(piece) / sample_rate

                if first_piece:
                    # Emitted unbuffered, immediately: this is the whole
                    # reason infer_stream() exists over infer() — first audio
                    # at ~0.44s instead of waiting ~38s for the whole answer.
                    first_piece = False
                    chunk_audio = piece
                    chunk_duration = piece_duration
                else:
                    buffer_parts.append(piece)
                    buffer_duration += piece_duration
                    if buffer_duration < self._STREAM_BUFFER_SECONDS:
                        continue
                    chunk_audio = np.concatenate(buffer_parts)
                    chunk_duration = buffer_duration
                    buffer_parts = []
                    buffer_duration = 0.0

                audio_bytes = self._encode_chunk(chunk_audio, sample_rate)
                yield {
                    "type": "chunk",
                    "seq": seq,
                    "codec": self.codec,
                    "audio": base64.b64encode(audio_bytes).decode("ascii"),
                    "duration": round(chunk_duration, 3),
                }
                seq += 1
                total_duration += chunk_duration

            # Flush whatever did not reach the 2.0s threshold — otherwise the
            # tail end of every answer (a partial buffer smaller than the
            # target) would be silently dropped rather than sent short.
            if buffer_parts:
                chunk_audio = np.concatenate(buffer_parts)
                audio_bytes = self._encode_chunk(chunk_audio, sample_rate)
                yield {
                    "type": "chunk",
                    "seq": seq,
                    "codec": self.codec,
                    "audio": base64.b64encode(audio_bytes).decode("ascii"),
                    "duration": round(buffer_duration, 3),
                }
                seq += 1
                total_duration += buffer_duration

            infer_time = time.time() - infer_start
            print(f"[VieNeu] Streamed {len(text)} chars in {infer_time:.2f}s "
                  f"({seq} chunks, {total_duration:.2f}s audio, codec={self.codec}, "
                  f"lang={language})")

            yield {
                "type": "end",
                "chunks": seq,
                "duration": round(total_duration, 3),
            }

        except Exception as e:
            # Not re-raised: once `start` — or even the very first byte of
            # this response — has gone out, there is no HTTP status left to
            # change. An `error` line is the only way the client learns
            # synthesis failed instead of just seeing the connection end.
            logger.exception("synthesize_stream failed text_len=%d voice_path=%s",
                              len(text), voice_path)
            yield {"type": "error", "message": str(e)}
