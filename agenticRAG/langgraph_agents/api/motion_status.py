"""GET /motion/{job_id} — read status, hand out a CloudFront signed URL when done.

The file never passes through Lambda: worker -> S3, browser -> CloudFront. Lambda's
response limit is 6 MB and routing the bytes through it would pay for the transfer
twice.

Must be a CloudFront signed URL, NOT an S3 presigned URL: infra/infra/asset_stack.py's
module docstring says CORS lives on the CloudFront response-headers policy and
deliberately not on the bucket — the bucket blocks all public access and a browser
fetching an S3 presigned URL directly would be CORS-blocked (and get a 403 from the
Origin Access Control besides).

The RSA private key never sits in an environment variable or a CDK context value.
asset_stack.py's docstring is explicit about the matching public key: "kept outside
CDK entirely — it never appears here." The private half is the same class of secret,
one degree more sensitive (it's what actually authorizes access, not just verifies
it) — so it is resolved from an SSM SecureString at call time
(`MOTION_SIGNING_KEY_PARAM` holds the *parameter name*, not the key), the same
`_secret_from_ssm` shape `llm.py` already uses for the LLM API keys. It is never
baked into the Lambda's environment or the CloudFormation template.

This module used to be the only place in `langgraph_agents` that imported
`botocore.signers`/`cryptography` for CloudFront signing. Since D5c the
signing lives in `langgraph_agents.shared.asset_urls` so both motion and
static_audio (characters/*/audio/*) reuse the same key group — this module
now re-exports that helper. vva_motion stays boto3+stdlib only because it is
COPY'd into the GPU worker image.
"""
from __future__ import annotations

import os

import boto3

from vva_motion.jobs import read_status

# D5c: signing moved to shared so /characters can reuse it for static_audio
from langgraph_agents.shared.asset_urls import (  # noqa: F401 — re-exported for backwards compat
    SIGNED_URL_TTL,
    _rsa_signer,
    _signing_key_pem,
    sign_url,
)

_TABLE = None


def _table():
    """Lazily-initialised DynamoDB table handle — same pattern as
    nodes/kimodo.py's `_table()`. Built on first use, not at import time, so
    importing this module never requires AWS credentials or MOTION_TABLE."""
    global _TABLE
    if _TABLE is None:
        _TABLE = boto3.resource("dynamodb").Table(os.environ["MOTION_TABLE"])
    return _TABLE


def motion_status(job_id: str) -> dict:
    """Read a job row and decide what the caller is allowed to know.

    A URL is returned ONLY when status == "done" — returning one earlier invites
    the browser to fetch a key that does not exist yet, and CloudFront caches
    404s (error_caching_min_ttl=0 on motions/* is belt and braces, not a licence
    to fetch early; see asset_stack.py).
    """
    row = read_status(_table(), job_id)
    if row is None:
        return {"status": "not_found"}
    status = row["status"]
    if status == "done":
        # `done` without an s3_key should be impossible — complete_job writes
        # both in one UpdateExpression. But this is a public authed route and
        # the alternative to a guard is an unhandled KeyError, i.e. a 500 for a
        # condition the caller can do nothing about. A row can also be hand-
        # edited, or written by a future code path that forgets the key.
        # Report it as a failure the browser can stop polling on.
        s3_key = row.get("s3_key")
        if not s3_key:
            return {"status": "failed", "reason": "render finished without an output key"}
        return {"status": "done", "url": sign_url(s3_key)}
    out = {"status": status}
    if row.get("reason"):
        out["reason"] = row["reason"]
    return out
