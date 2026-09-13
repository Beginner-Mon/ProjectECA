import base64
import hashlib
import io
import logging
import os
import re
import tempfile
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


# ── S3 voice bucket (D5d) ─────────────────────────────────────────────────
# Reference voices are S3 KEYS in the private voice bucket (no CloudFront).
# Local dev keeps plain files under voices/*.wav when VOICE_BUCKET is unset.
# Deployed, SpeechLLm has IAM read-only on the bucket prefix and downloads to
# /tmp before encoding. The bucket name is injected by speechllm_stack.py.

_VOICE_BUCKET_ENV_VARS = ("VOICE_BUCKET", "VOICE_S3_BUCKET", "VVA_VOICE_BUCKET", "SPEECHLLM_VOICE_BUCKET")
_S3_CACHE_DIR = Path(tempfile.gettempdir()) / "speechllm_voices"
_S3_HASH_RE = re.compile(r"([0-9a-fA-F]{8})")


def _voice_bucket() -> Optional[str]:
    for name in _VOICE_BUCKET_ENV_VARS:
        v = os.getenv(name, "").strip()
        if v:
            return v
    return None


def _is_s3_enabled() -> bool:
    return _voice_bucket() is not None


def _strip_s3_prefix(key: str) -> str:
    if key.startswith("s3://"):
        # s3://bucket/key -> key
        parts = key[5:].split("/", 1)
        return parts[1] if len(parts) == 2 else parts[0]
    return key.lstrip("/")


def _should_use_s3(voice_path: str) -> bool:
    """True if voice_path should be fetched from S3 rather than local FS.

    Explicit, not heuristic (finding 8, 13-09-2026). When VOICE_BUCKET is
    configured, S3 is the source of truth for every voice_path — full stop.
    A local file of the same name is never preferred, even if one happens to
    exist. Local files are the source ONLY when no bucket is configured at
    all (a developer machine with no S3 access).

    This used to be a heuristic: "looks like an S3 key (has '/', ends in
    .wav) AND the local file doesn't already exist" — prefer local, on the
    theory that ".dockerignore excludes voices/ from the image, so in prod
    local won't exist and this falls through to S3 anyway." That is a
    derived assumption about the CONTENTS of another file, not an enforced
    invariant, and it failed silently in both directions:
      - If voices/ ever reappeared in the image (a .dockerignore edit, a
        base layer copying it, a future build step), the Lambda would serve
        a STALE LOCAL voice while _voice_version() kept reporting the hash
        embedded in the S3 key — the browser caches audio under a version
        that does not match what it actually heard, with no symptom.
      - A key that did not end in ".wav" silently took the local branch and
        then failed as "missing", pointing at the wrong cause.
    Making the mode explicit deletes the "local silently wins" path:
    production behaviour no longer depends on what happens to be on disk.
    """
    if not voice_path:
        return False
    # Explicit s3:// always uses S3, bucket configured or not — _download_from_s3
    # raises VoiceResolutionError loudly if the bucket is missing.
    if voice_path.startswith("s3://"):
        return True
    return _is_s3_enabled()


def _get_s3_client():
    import boto3
    return boto3.client("s3")


def _s3_download_path(s3_key: str) -> Path:
    """Local cache path for a given S3 key (under /tmp)."""
    _S3_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Use hash of full key to avoid collisions on same filename from different prefixes
    key_hash = hashlib.sha256(s3_key.encode()).hexdigest()[:8]
    # Preserve original filename for debuggability
    fname = Path(s3_key).name
    return _S3_CACHE_DIR / f"{key_hash}_{fname}"


def _download_from_s3(s3_key: str) -> Path:
    """Download S3 key to local cache and return the local Path. Raises VoiceResolutionError on failure."""
    bucket = _voice_bucket()
    if not bucket:
        raise VoiceResolutionError(f"VOICE_BUCKET not configured but S3 key requested: {s3_key}")
    key = _strip_s3_prefix(s3_key)
    local_path = _s3_download_path(key)
    if local_path.is_file() and local_path.stat().st_size > 0:
        return local_path
    try:
        _get_s3_client().download_file(bucket, key, str(local_path))
    except Exception as e:
        # Clean up partial file
        try:
            if local_path.exists():
                local_path.unlink()
        except Exception:
            pass
        raise VoiceResolutionError(
            f"Reference voice not found in S3: bucket={bucket} key={key} ({e})"
        ) from e
    if not local_path.is_file():
        raise VoiceResolutionError(
            f"Reference voice not found in S3 after download: bucket={bucket} key={key}"
        )
    return local_path


def _extract_hash_from_s3_key(s3_key: str) -> str:
    """Extract 8-char hash embedded in S3 key (e.g. characters/anne/audio/9f2c1a4b.ogg -> 9f2c1a4b).

    Falls back to sha256 of the key string if no 8-hex substring is found — still
    stable per key, just not the content hash the uploader embedded. The uploader
    (scripts/upload_characters_to_s3.py) always embeds sha256[:8] of the file
    content in the filename, so the regex path is the normal one.
    """
    m = _S3_HASH_RE.search(Path(s3_key).stem)
    if m:
        return m.group(1).lower()
    # Fallback: hash of the key string itself (stable, not content hash, but better than random)
    return hashlib.sha256(s3_key.encode()).hexdigest()[:8]


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
        bucket = _voice_bucket()
        if bucket:
            src += f", voice_bucket={bucket}"
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

        D5d: voice_path may be an S3 key (characters/anne/voice/a1b2c3d4.wav)
        when VOICE_BUCKET is set. In that case the file is downloaded to /tmp
        and cached before encoding. The cache key is the S3 key's hash, not the
        filename, so re-recording the same character with new content (new hash)
        naturally busts the cache.
        """
        if not voice_path:
            raise VoiceResolutionError(
                "No voice_path supplied — a reference voice is required."
            )

        # S3 path: download to temp and cache by S3 key
        if _should_use_s3(voice_path):
            s3_key = _strip_s3_prefix(voice_path)
            cache_key = f"s3://{_voice_bucket()}/{s3_key}"
            if cache_key in self._voice_cache:
                return self._voice_cache[cache_key]
            # Download (or reuse cached download)
            local_ref = _download_from_s3(s3_key)
            tts = self._load_model()
            print(f"[VieNeu] Encoding voice from S3: {s3_key} -> {local_ref}")
            v_start = time.time()
            try:
                encoded = self._encode_voice(tts, local_ref)
            except Exception as e:
                raise VoiceResolutionError(
                    f"Reference voice could not be encoded: requested={voice_path} "
                    f"s3_key={s3_key} local={local_ref} ({e})"
                ) from e
            self._voice_cache[cache_key] = encoded
            print(f"[VieNeu] Voice encoded in {time.time() - v_start:.1f}s")
            return self._voice_cache[cache_key]

        # Local path (dev or fallback)
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
        """Pre-encode every reference clip at startup.

        Enrolment (`encode_reference`, inside `_resolve_voice`) costs ~3-4s
        for a full-length reference and was previously paid inside the
        FIRST request that named a given `voice_path` — a real-model check
        measured 4.36s time-to-first-chunk cold against 0.25s on an
        identical warm repeat, almost entirely this cost. Called from
        api_server's startup warm-up, after `_load_model()` and before
        `/health` reports ready, so that cost is paid once here instead of
        by whichever user's request happens to name a voice first.

        D5d: when VOICE_BUCKET is set, lists the S3 prefix for *.wav and
        pre-enrols each S3 key via _resolve_voice's S3 path (download + cache).
        This is why SpeechLLm reads S3 directly rather than via presigned URLs:
        pre-enrol is what brings 4.36s -> 0.25s, and presigned URLs would lose it.
        The health check stays red until this finishes, so a cold start that
        still needs to enrol is not reported as ready.

        A missing or corrupt file must not take down the whole warm-up:
        voices are added to the catalog before anyone records audio for
        them — a bad file here is that same case, not a startup failure.
        Logged as a WARNING with the exception rather than silently skipped.
        """
        # S3 path: enumerate bucket
        if _is_s3_enabled():
            bucket = _voice_bucket()
            try:
                s3 = _get_s3_client()
                paginator = s3.get_paginator("list_objects_v2")
                # List all .wav keys — the bucket is private and only holds voices,
                # so full scan is cheap. If it grows, add Prefix="voices/" or "characters/"
                found = 0
                for page in paginator.paginate(Bucket=bucket):
                    for obj in page.get("Contents", []):
                        key = obj["Key"]
                        if not key.lower().endswith(".wav"):
                            continue
                        found += 1
                        enrol_start = time.time()
                        try:
                            self._resolve_voice(key)
                            self._voice_version(key)
                        except Exception:
                            logger.warning("voice_enrolment_failed s3_key=%s", key, exc_info=True)
                            continue
                        print(f"[VieNeu] Pre-enrolled s3://{bucket}/{key} in "
                              f"{time.time() - enrol_start:.2f}s")
                if found == 0:
                    logger.warning("voices_s3_empty bucket=%s -> no voices pre-enrolled", bucket)
                return
            except Exception:
                logger.warning("voices_s3_list_failed bucket=%s", bucket, exc_info=True)
                # Fall through to local scan as fallback — warm-up must not die

        # Local path (dev or S3 fallback)
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

        D5d: for S3 keys the version is the hash EMBEDDED in the key itself
        (e.g. characters/anne/audio/9f2c1a4b.ogg -> 9f2c1a4b), not the file's
        content hash — the key already is content-addressed by the uploader.
        For local files it is the file's sha256[:16] as before. Cached per
        resolved key.
        """
        # S3 key: extract hash from key, not file content
        if _should_use_s3(voice_path):
            s3_key = _strip_s3_prefix(voice_path)
            cache_key = f"s3://{_voice_bucket()}/{s3_key}"
            if cache_key not in self._voice_version_cache:
                self._voice_version_cache[cache_key] = _extract_hash_from_s3_key(s3_key)
            return self._voice_version_cache[cache_key]

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
