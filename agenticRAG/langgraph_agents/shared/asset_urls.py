"""Shared CloudFront signed URL helper — D5c, trimmed by T1.

Moved from api/motion_status.py. `sign_url` signs ANY S3 key (motions/* and,
since D5b, characters/*/audio/*) via the SAME key group (asset_stack reuses
motion_key_group, KHONG tao khoa moi). CORS lives on the CloudFront
response-headers policy. Voice keys (voice_vi_key / voice_en_key) are NOT
signed: they live in the private voice bucket with NO CloudFront behavior.

T1 removed `sign_static_audio`: signing clips inside a PUBLIC GET /characters
response was meaningless — anyone could fetch the signed URL without a token.
Clips are now served via GET /characters/{slug}/audio (Cognito, no-store),
which calls `sign_url` per clip. `motion_status.py` still uses `sign_url`.

The RSA private key is resolved from SSM at call time (MOTION_SIGNING_KEY_PARAM
holds the parameter NAME, not the key), cached per process via lru_cache —
same as llm.py's _secret_from_ssm. Never baked into Lambda env or CFN template.

SIGNED_URL_TTL is 5 minutes (the motion leg's choice). /audio callers refetch
on 403; see contract B.
"""

from __future__ import annotations

import datetime
import os
from functools import lru_cache

import boto3
from botocore.signers import CloudFrontSigner
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# 5 minutes — same as motion_status before the move (contract B: /audio
# returns `expires_at` per clip so callers know when to refetch).
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
