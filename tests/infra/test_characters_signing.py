# -*- coding: utf-8 -*-
"""T3 — CloudFront signing in the shared Lambda layer (pure-Python `rsa`).

The characters Lambda signs /audio URLs from a layer that bundles with a
plain host-side `pip install` and no --platform flag, so `cryptography`
would install a Windows wheel into a Linux layer (synth green, deploy
green, ImportError at import). This pins three things:

1. sign with an RSA pair generated in the test, verify with the public key;
2. with the SAME key, key pair id and expiry, signing.py's URL is
   byte-identical to the agent's asset_urls.sign_url (the reference impl);
3. after `cdk synth VvaCharacterStack`, the layer asset in cdk.out/ has
   rsa/ and no .pyd anywhere (the bundler's native-extension scan agrees).
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

import aws_cdk as cdk
import pytest
import rsa

_LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "infra" / "lambda"
sys.path.insert(0, str(_LAMBDA_ROOT / "layer"))

from shared import signing  # noqa: E402

_ENV = cdk.Environment(account="244203483654", region="us-east-1")


@pytest.fixture
def keypair():
    """A throwaway 512-bit RSA pair. Small because it only signs one short
    policy per test; 512-bit keeps the suite fast and proves nothing about
    production key strength (production keys live in SSM, not here)."""
    return rsa.newkeys(512)


@pytest.fixture
def signed_env(monkeypatch, keypair):
    pub, priv = keypair
    pem = priv.save_pkcs1(format="PEM").decode()
    monkeypatch.setenv("MOTION_KEY_PAIR_ID", "KTEST1234567890")
    monkeypatch.setenv("MOTION_SIGNING_KEY_PARAM", "/vva/motion/signing-key-pem")
    monkeypatch.setenv("ASSET_BASE_URL", "https://d111111abcdef8.cloudfront.net")
    monkeypatch.setattr(signing, "_signing_key_pem", lambda: pem)
    signing.clear_signing_cache()
    return pub, priv


@pytest.mark.unit
def test_rsa_signer_verifies_against_the_public_key(signed_env):
    pub, _priv = signed_env
    message = b"cloudfront-policy-bytes"

    signature = signing._rsa_signer(message)

    # Raises rsa.VerificationError when the signature is wrong — returning
    # the hash name on success.
    assert rsa.verify(message, signature, pub) == "SHA-1"


@pytest.mark.unit
def test_layer_url_is_byte_identical_to_the_agent_reference_impl(
    monkeypatch, signed_env,
):
    """Same key, same key pair id, same expiry → same URL, character for
    character. The agent's asset_urls.sign_url is the reference; this layer
    copy must not drift from it (different query order or encoding would
    still verify, but drift is how two implementations silently diverge)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agenticRAG"))
    from langgraph_agents.shared import asset_urls

    pub, priv = signed_env
    pem = priv.save_pkcs1(format="PEM").decode()

    # Freeze "now" in BOTH modules: sign_url bakes now()+TTL internally and
    # takes no expiry argument, so the only way to hold the instant fixed on
    # both sides is to freeze the clock each side reads.
    fixed_now = datetime.datetime(2026, 9, 17, 10, 0, 0, tzinfo=datetime.timezone.utc)

    class _FrozenDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(asset_urls.datetime, "datetime", _FrozenDateTime)
    monkeypatch.setattr(signing.datetime, "datetime", _FrozenDateTime)
    monkeypatch.setattr(asset_urls, "_signing_key_pem", lambda: pem)
    monkeypatch.setenv("MOTION_KEY_PAIR_ID", "KTEST1234567890")
    monkeypatch.setenv(
        "ASSET_BASE_URL", "https://d111111abcdef8.cloudfront.net",
    )

    s3_key = "characters/anne/audio/9f2c1a4b.ogg"
    # asset_urls TTL is 5 minutes = 300s; pass the same to the layer copy.
    layer_url, expires_at = signing.sign_cloudfront_url(s3_key, 300)
    agent_url = asset_urls.sign_url(s3_key)

    assert layer_url == agent_url
    assert expires_at == fixed_now + datetime.timedelta(seconds=300)


@pytest.mark.unit
def test_missing_env_raises_never_unsigned(monkeypatch, keypair):
    """No MOTION_KEY_PAIR_ID (or its siblings) → KeyError, never a URL
    without a signature and never a raw S3 key. The handler maps this to
    500 per contract B."""
    _pub, priv = keypair
    pem = priv.save_pkcs1(format="PEM").decode()
    monkeypatch.setattr(signing, "_signing_key_pem", lambda: pem)
    signing.clear_signing_cache()
    monkeypatch.delenv("MOTION_KEY_PAIR_ID", raising=False)
    monkeypatch.delenv("MOTION_SIGNING_KEY_PARAM", raising=False)
    monkeypatch.delenv("ASSET_BASE_URL", raising=False)

    with pytest.raises(KeyError):
        signing.sign_cloudfront_url("characters/anne/audio/9f2c1a4b.ogg", 300)


@pytest.mark.unit
def test_layer_asset_has_rsa_and_no_native_extensions(tmp_path):
    """The real synth check: bundle the layer and scan the output.

    cdk.App(outdir=tmp_path) keeps cdk.out out of the repo. Local bundling
    pip-installs pg8000 + rsa on the host (both py3-none-any) and the
    bundler's own native scan must agree: rsa/ present, zero .pyd/.so.
    """
    from infra.character_stack import CharacterStack

    app = cdk.App(
        outdir=str(tmp_path / "cdk.out"),
        context={
            "motion_key_pair_id": "K2TESTONLY",
            "asset_base_url": "https://d111111abcdef8.cloudfront.net",
        },
    )
    CharacterStack(app, "VvaCharacterStack", env=_ENV)
    assembly = app.synth(force=True, validate_on_synthesis=False)
    assert assembly.get_stack_by_name("VvaCharacterStack") is not None

    asset_dirs = [p for p in Path(assembly.directory).glob("asset.*") if p.is_dir()]
    assert asset_dirs, "no bundled assets in cdk.out — layer bundling did not run"
    python_dirs = [p / "python" for p in asset_dirs if (p / "python").is_dir()]
    assert python_dirs, "no python/ dir in any asset — shared/ was not copied"

    assert any((p / "rsa" / "__init__.py").is_file() for p in python_dirs), (
        "rsa/ missing from the bundled layer — requirements.txt change lost?"
    )
    native = [
        p for python_dir in python_dirs for p in python_dir.rglob("*")
        if p.suffix.lower() in (".so", ".pyd", ".dll", ".dylib")
    ]
    assert native == [], (
        f"native extensions in a supposedly pure-Python layer: "
        f"{sorted({p.name for p in native})[:5]}"
    )
