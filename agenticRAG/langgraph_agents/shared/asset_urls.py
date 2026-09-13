"""Shared CloudFront signed URL helper — D5c.

Moved from api/motion_status.py so both motion and static_audio can share
the same signing machinery. Previously motion_status was the only caller
and its docstring said CORS lives on CloudFront response-headers policy;
that is still true for both motions/* and characters/*/audio/* — both are
signed via the SAME key group (asset_stack reuses motion_key_group, KHONG
tao khoa moi). Voice keys (voice_vi_key / voice_en_key) are NOT signed:
they live in the private voice bucket with NO CloudFront behavior at all.

The RSA private key is resolved from SSM at call time (MOTION_SIGNING_KEY_PARAM
holds the parameter NAME, not the key), cached per process via lru_cache —
same as llm.py's _secret_from_ssm. Never baked into Lambda env or CFN template.
MOTION_KEY_PAIR_ID / MOTION_SIGNING_KEY_PARAM now serve two purposes
(motion + static audio); renaming them is a separate debt.

SIGNED_URL_TTL must outlive a browser session or the frontend will 403 mid-
play. Current 5 minutes is the motion leg's choice; static_audio callers should
refetch /characters on 403 and retry once (D7), or we extend TTL. Kept at 5m
here to avoid silently changing motion behavior; bump intentionally later if
needed.
"""

from __future__ import annotations

import datetime
import os
from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from botocore.signers import CloudFrontSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.shared.asset_urls")

# 5 minutes — same as motion_status before the move; see module docstring for
# why static_audio may want longer and how D7 handles expiry.
SIGNED_URL_TTL = datetime.timedelta(minutes=5)


@lru_cache(maxsize=1)
def _signing_key_pem() -> str:
    """Read the CloudFront private signing key from SSM. Cached per process."""
    return boto3.client("ssm").get_parameter(
        Name=os.environ["MOTION_SIGNING_KEY_PARAM"], WithDecryption=True,
    )["Parameter"]["Value"]


def _rsa_signer(message: bytes) -> bytes:
    key = serialization.load_pem_private_key(
        _signing_key_pem().encode(), password=None)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA1())


def sign_url(s3_key: str) -> str:
    """Sign ANY S3 key into a CloudFront signed URL (generic, not motion-specific)."""
    signer = CloudFrontSigner(os.environ["MOTION_KEY_PAIR_ID"], _rsa_signer)
    return signer.generate_presigned_url(
        f"{os.environ['ASSET_BASE_URL']}/{s3_key}",
        date_less_than=datetime.datetime.now(datetime.timezone.utc) + SIGNED_URL_TTL,
    )


def sign_static_audio(static_audio: dict) -> dict:
    """Sign every S3 key inside a static_audio JSONB map.

    Input:  {"greeting": {"vi": "characters/anne/audio/9f2c.ogg", ...}, ...}
    Output: {"greeting": {"vi": "https://.../characters/anne/audio/9f2c.ogg?Expires=...&Signature=...&Key-Pair-Id=...", ...}, ...}
    Missing or empty maps are returned as-is. Keys that are already URLs (http)
    are left untouched — they are either legacy data or already signed.

    A key that fails to sign is DROPPED from its language map, not replaced
    with anything — never the raw S3 key, never a placeholder. An earlier
    version put the raw key in the field the frontend treats as a URL; the
    browser then requested it as a relative path against its own origin,
    404'd, and that looked exactly like "this character has no greeting" —
    indistinguishable from the working case, with no way for the caller to
    tell the difference. Same silent-fallback pattern commit 3fff88e7
    deliberately removed from the voice path. Dropping the key instead makes
    it genuinely absent: `getStaticAudioUrl()` on the frontend already falls
    back through voice_language → vi → en → null for a kind/lang that was
    simply never recorded, and a signing failure now looks the same as that,
    not like a broken link. The failure itself is never silent — it is
    logged here with the reason before the key is dropped.
    """
    if not static_audio:
        return static_audio
    out: dict = {}
    for kind, langs in static_audio.items():
        if not isinstance(langs, dict):
            out[kind] = langs
            continue
        signed_langs: dict = {}
        for lang, key in langs.items():
            if not isinstance(key, str) or not key:
                signed_langs[lang] = key
                continue
            if key.startswith("http://") or key.startswith("https://"):
                signed_langs[lang] = key
                continue
            try:
                signed_langs[lang] = sign_url(key)
            except (KeyError, ClientError, BotoCoreError, ValueError) as exc:
                # Narrowed from a bare `except Exception`: these are the
                # expected operational failure modes — KeyError for a missing
                # MOTION_SIGNING_KEY_PARAM/MOTION_KEY_PAIR_ID/ASSET_BASE_URL
                # env var, ClientError/BotoCoreError for SSM (throttled,
                # denied, parameter doesn't exist), ValueError for a
                # malformed PEM key. Signing must never fail the whole
                # /characters response over one bad key, so these are logged
                # and the key is dropped rather than raised. Anything else
                # (TypeError, AttributeError, ...) is a bug in this module,
                # not an operational condition, and is deliberately NOT
                # caught here — it surfaces as a 500 instead of a silently
                # missing greeting.
                logger.error(
                    "static_audio signing failed, dropping key: "
                    "kind=%r lang=%r key=%r error=%s: %s",
                    kind, lang, key, type(exc).__name__, exc,
                )
                continue
        out[kind] = signed_langs
    return out
