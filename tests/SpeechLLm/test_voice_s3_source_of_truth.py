# -*- coding: utf-8 -*-
"""_should_use_s3 must be an explicit switch, not a heuristic (finding 8,
13-09-2026 review of the SpeechLLm Lambda deploy work).

The old version decided by string SHAPE ("has '/' and ends in .wav") and then
preferred a LOCAL file when one happened to exist, justified only by
"voices/ is not baked into the prod image, so local won't exist there
anyway" — a derived assumption about another file's contents, not an
enforced invariant. If that assumption ever stopped holding (a .dockerignore
edit, a base layer copying voices/ back in, a future build step), production
would silently start serving a stale local voice while `_voice_version()`
kept reporting the hash embedded in the S3 key — a mismatch with no symptom.

The fix: when VOICE_BUCKET is set, S3 is the source of truth, full stop.
These tests prove a local file of the same name is no longer preferred once
a bucket is configured, and that the existing fail-loudly guarantee (a key
that cannot be fetched raises VoiceResolutionError, never falls back to
another voice) still holds.
"""
from __future__ import annotations

import pytest

from src.services import vieneu_client as vc
from src.services.vieneu_client import VieNeuClient, VoiceResolutionError


class _FakeTTS:
    def __init__(self):
        self.encoded = []

    def encode_reference(self, path, denoise=False):
        self.encoded.append(path)
        return (f"<emb:{path}>", f"<codes:{path}>")


class _FakeS3Client:
    """Stands in for boto3's S3 client. download_file() writes deterministic
    bytes so the test can tell an S3-sourced file apart from a local one."""

    def __init__(self, objects: dict[str, bytes] | None = None, fail_keys: set[str] | None = None):
        self.objects = objects or {}
        self.fail_keys = fail_keys or set()
        self.download_calls: list[tuple[str, str]] = []

    def download_file(self, bucket, key, dest_path):
        self.download_calls.append((bucket, key))
        if key in self.fail_keys or key not in self.objects:
            raise RuntimeError(f"NoSuchKey: {key}")
        with open(dest_path, "wb") as f:
            f.write(self.objects[key])


@pytest.fixture
def client(tmp_path):
    c = VieNeuClient({"output_dir": str(tmp_path / "out")})
    c._tts = _FakeTTS()
    return c


@pytest.fixture
def isolated_s3_cache(tmp_path, monkeypatch):
    """Downloads land under a per-test tmp dir, never the real system temp."""
    cache_dir = tmp_path / "s3_cache"
    monkeypatch.setattr(vc, "_S3_CACHE_DIR", cache_dir)
    return cache_dir


@pytest.fixture
def isolated_voice_root(tmp_path, monkeypatch):
    """Anchors _resolve_ref()'s relative-path resolution at a per-test tmp
    dir instead of the real SpeechLLm/ checkout.

    Without this, a test that writes a "local file of the same name" (the
    whole point of the regression this file guards against) would write
    through `_resolve_ref("voices/anne_vi.wav")` straight into the REAL
    `SpeechLLm/voices/anne_vi.wav` — a tracked 5 MB reference recording —
    and clobber it with test bytes. That happened once while writing this
    file; this fixture is why it cannot happen again.
    """
    monkeypatch.setattr(vc, "_ROOT", tmp_path)
    return tmp_path


# ── _should_use_s3: explicit switch ──────────────────────────────────────────

@pytest.mark.unit
def test_bucket_unset_never_uses_s3_even_for_key_shaped_paths(monkeypatch):
    for name in vc._VOICE_BUCKET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert vc._should_use_s3("characters/anne/voice/a1b2c3d4.wav") is False
    assert vc._should_use_s3("voices/anne_vi.wav") is False


@pytest.mark.unit
def test_bucket_set_always_uses_s3_regardless_of_shape(monkeypatch):
    monkeypatch.setenv("VOICE_BUCKET", "vva-voices")
    # Not '/'-shaped, doesn't end in .wav — the old heuristic would have said
    # False here (silently taking the local branch); the explicit switch
    # says True because a bucket is configured, full stop.
    assert vc._should_use_s3("anne") is True
    assert vc._should_use_s3("voices/anne_vi.wav") is True


@pytest.mark.unit
def test_explicit_s3_prefix_always_wins_bucket_or_not(monkeypatch):
    for name in vc._VOICE_BUCKET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert vc._should_use_s3("s3://some-bucket/characters/anne/voice/x.wav") is True


@pytest.mark.unit
def test_empty_voice_path_never_uses_s3(monkeypatch):
    monkeypatch.setenv("VOICE_BUCKET", "vva-voices")
    assert vc._should_use_s3("") is False


# ── The actual regression: a local file of the same name must NOT win ───────

@pytest.mark.unit
def test_local_file_of_the_same_name_is_not_preferred_when_bucket_is_configured(
    client, tmp_path, monkeypatch, isolated_s3_cache, isolated_voice_root,
):
    """The exact scenario finding 8 describes: voices/ reappears on disk (or
    was never removed) while VOICE_BUCKET is set. Before the fix, a local
    file existing was enough to silently win over S3 — this asserts the
    opposite: S3 is fetched, the local file is never touched."""
    monkeypatch.setenv("VOICE_BUCKET", "vva-voices")

    # A local file DOES exist at the resolved path for this voice_path — the
    # old heuristic's "if local.is_file(): return False" branch.
    local_ref = vc._resolve_ref("voices/anne_vi.wav")
    local_ref.parent.mkdir(parents=True, exist_ok=True)
    local_ref.write_bytes(b"STALE-LOCAL-COPY")

    fake_s3 = _FakeS3Client(objects={"voices/anne_vi.wav": b"S3-CONTENT"})
    monkeypatch.setattr(vc, "_get_s3_client", lambda: fake_s3)

    assert vc._should_use_s3("voices/anne_vi.wav") is True

    client._resolve_voice("voices/anne_vi.wav")

    # S3 was actually called for this key...
    assert ("vva-voices", "voices/anne_vi.wav") in fake_s3.download_calls
    # ...and the encoded reference came from the S3-downloaded file, not the
    # stale local one sitting at the same resolved path.
    assert len(client._tts.encoded) == 1
    encoded_path = client._tts.encoded[0]
    assert str(local_ref) != str(encoded_path)


@pytest.mark.unit
def test_voice_version_also_reads_the_s3_key_hash_not_local_content(
    client, tmp_path, monkeypatch, isolated_s3_cache, isolated_voice_root,
):
    """The mismatch finding 8 warns about: _voice_version() must agree with
    which source _resolve_voice() actually used. Both must route through
    _should_use_s3() the same way for the same voice_path."""
    monkeypatch.setenv("VOICE_BUCKET", "vva-voices")
    local_ref = vc._resolve_ref("characters/anne/voice/9f2c1a4b.wav")
    local_ref.parent.mkdir(parents=True, exist_ok=True)
    local_ref.write_bytes(b"STALE-LOCAL-COPY-DIFFERENT-CONTENT")

    fake_s3 = _FakeS3Client(objects={"characters/anne/voice/9f2c1a4b.wav": b"S3-CONTENT"})
    monkeypatch.setattr(vc, "_get_s3_client", lambda: fake_s3)

    version = client._voice_version("characters/anne/voice/9f2c1a4b.wav")

    # The hash embedded in the S3 key, not sha256 of the stale local bytes.
    assert version == "9f2c1a4b"


# ── Fail loudly is preserved ──────────────────────────────────────────────

@pytest.mark.unit
def test_s3_key_that_cannot_be_fetched_still_raises_not_falls_back(
    client, monkeypatch, isolated_s3_cache,
):
    monkeypatch.setenv("VOICE_BUCKET", "vva-voices")
    fake_s3 = _FakeS3Client(objects={}, fail_keys={"characters/anne/voice/missing.wav"})
    monkeypatch.setattr(vc, "_get_s3_client", lambda: fake_s3)

    with pytest.raises(VoiceResolutionError):
        client._resolve_voice("characters/anne/voice/missing.wav")

    assert client._tts.encoded == []


@pytest.mark.unit
def test_explicit_s3_prefix_with_no_bucket_configured_raises_not_silently_local(
    client, monkeypatch,
):
    for name in vc._VOICE_BUCKET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(VoiceResolutionError, match="VOICE_BUCKET not configured"):
        client._resolve_voice("s3://vva-voices/characters/anne/voice/x.wav")
