"""CloudFront signed URLs for the characters Lambda — pure-Python signing.

The catalog Lambda (infra/lambda/characters/handler.py) hands out signed URLs
for pre-rendered audio clips (characters/{slug}/audio/{hash}.ogg, behind the
`characters/*/audio/*` behavior that reuses the motion key group). It runs
from a Lambda layer that is deliberately pure-Python only (it uses pg8000
rather than asyncpg for the same reason), and the layer is bundled with a
plain `pip install` on the build host with no `--platform` flag — so this
module signs with `rsa` (pure Python), NOT `cryptography` (which would
install a Windows wheel into a Linux layer: synth green, deploy green,
ImportError only when Lambda imports it).

Same shape as the agent's `langgraph_agents/shared/asset_urls.py`, which is
the reference implementation: CloudFrontSigner + PKCS#1 v1.5 + SHA-1, private
key from SSM (`MOTION_SIGNING_KEY_PARAM` holds the parameter NAME, not the
key), cached for the life of the process. With the same key, key pair id and
expiry, both produce byte-identical URLs (pinned by test).

Missing environment (MOTION_KEY_PAIR_ID / MOTION_SIGNING_KEY_PARAM /
ASSET_BASE_URL) raises — never an unsigned URL, never a raw S3 key. The
handler maps that to 500 with a logged reason per contract B.
"""

from __future__ import annotations

import datetime
import os

import boto3
from botocore.signers import CloudFrontSigner

import rsa

# Process-lifetime cache for the PEM, same reasoning as the agent's
# `_signing_key_pem` (lru_cache): every /audio call would otherwise be an SSM
# round trip for a value that cannot rotate mid-invocation.
_signing_key_pem_cache: str | None = None


def _signing_key_pem() -> str:
    """Read the CloudFront private signing key from SSM. Cached per process.

    boto3's SSM client is built here, not at import time, so importing this
    module never requires AWS credentials.
    """
    global _signing_key_pem_cache
    if _signing_key_pem_cache is None:
        _signing_key_pem_cache = boto3.client("ssm").get_parameter(
            Name=os.environ["MOTION_SIGNING_KEY_PARAM"], WithDecryption=True,
        )["Parameter"]["Value"]
    return _signing_key_pem_cache


def _load_private_key(pem: str) -> rsa.PrivateKey:
    """Parse the PEM the same key the agent signs with.

    `rsa.PrivateKey.load_pkcs1` reads the traditional PKCS#1 block
    (BEGIN RSA PRIVATE KEY). A PKCS#8 block (BEGIN PRIVATE KEY) is rejected
    with a clear error rather than mis-signed output.
    """
    return rsa.PrivateKey.load_pkcs1(pem.encode(), format="PEM")


def _rsa_signer(message: bytes) -> bytes:
    return rsa.sign(message, _load_private_key(_signing_key_pem()), "SHA-1")


def sign_cloudfront_url(s3_key: str, ttl_seconds: int) -> tuple[str, datetime.datetime]:
    """Sign one S3 key into a CloudFront URL. Returns (url, expires_at_utc).

    `expires_at_utc` is timezone-aware UTC — the handler reports it as
    `expires_at` per contract B so callers know when to refetch.

    Raises KeyError when MOTION_KEY_PAIR_ID / MOTION_SIGNING_KEY_PARAM /
    ASSET_BASE_URL is missing, and whatever SSM/PEM errors the read raises —
    the caller turns those into 500, never an unsigned URL.
    """
    key_pair_id = os.environ["MOTION_KEY_PAIR_ID"]
    base_url = os.environ["ASSET_BASE_URL"]
    expires_at = (
        datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=ttl_seconds)
    )
    signer = CloudFrontSigner(key_pair_id, _rsa_signer)
    url = signer.generate_presigned_url(
        f"{base_url}/{s3_key}", date_less_than=expires_at,
    )
    return url, expires_at


def clear_signing_cache() -> None:
    """Drop the cached PEM. Tests only — production never rotates mid-process."""
    global _signing_key_pem_cache
    _signing_key_pem_cache = None
