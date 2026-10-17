"""POST /me/uploads — a short-lived presigned POST for one private upload.

SCRUM-45 part 1 (docs/plans/scrum-45-image-input.md). The browser uploads
straight to S3: API Gateway caps a payload at 10 MB, and the CRUD Lambda has no
reason to hold the bytes. What this route controls is the policy it signs:

  * the key — `<prefix>/<user_id>/<random>.<ext>`, so an object can always be
    traced to its owner and `key_belongs_to` can refuse anyone else's;
  * the content type — one of a short list per kind;
  * the size — `content-length-range`, which only a POST policy can enforce
    (a presigned PUT signs no size, so a client could send anything).

The prefix comes first (`images/…`, later `docs/…`) because S3 lifecycle rules
filter by prefix only: images expire after N days (VvaUploadsStack), user
documents are kept until the user deletes them.

boto3 is imported inside the function: the Lambda runtime ships it, local
development of the other routes never needs it (see
infra/lambda/crud_api/requirements.txt).
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from langgraph_agents.api.auth import current_user_id
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.api.uploads")

router = APIRouter(tags=["uploads"])

EXPIRES_IN_S = 300


@dataclass(frozen=True)
class UploadKind:
    prefix: str
    max_bytes: int
    extensions: dict[str, str]   # content type → file extension


KINDS: dict[str, UploadKind] = {
    # Photos are shrunk in the browser before upload (≤ 1600 px, JPEG 0.85), so
    # 5 MB leaves room for a PNG screenshot without inviting raw camera files.
    "image": UploadKind(
        prefix="images",
        max_bytes=5 * 1024 * 1024,
        extensions={"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"},
    ),
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class UploadRequest(BaseModel):
    kind: Literal["image"]
    content_type: str
    size_bytes: int = Field(gt=0)


class UploadTicket(BaseModel):
    key: str
    url: str
    fields: dict[str, str]
    expires_in: int
    max_bytes: int


def key_belongs_to(user_id: str, key: str, kind: str) -> bool:
    """True only for a key this route could have issued to `user_id`.

    Checked before anything reads an object a client names (the agent's vision
    step, the history view), so one user cannot point at another's upload.
    """
    spec = KINDS.get(kind)
    if spec is None:
        return False
    exts = "|".join(re.escape(e) for e in spec.extensions.values())
    pattern = rf"{re.escape(spec.prefix)}/{re.escape(user_id)}/[0-9a-f]{{32}}(?:{exts})"
    return re.fullmatch(pattern, key) is not None


@router.post("/me/uploads", response_model=UploadTicket)
async def create_upload(body: UploadRequest, uid: str = Depends(current_user_id)) -> UploadTicket:
    bucket = os.getenv("UPLOADS_BUCKET")
    if not bucket:
        # A deploy that forgot the variable; say so instead of signing for nothing.
        raise HTTPException(503, "Uploads are not configured")
    spec = KINDS[body.kind]
    ext = spec.extensions.get(body.content_type)
    if ext is None:
        raise HTTPException(415, f"Unsupported type for {body.kind}: {body.content_type}")
    if body.size_bytes > spec.max_bytes:
        raise HTTPException(413, f"File is larger than {spec.max_bytes} bytes")
    if not re.fullmatch(_UUID, uid):
        # The key embeds the user id; anything else would break key_belongs_to.
        raise HTTPException(400, "Unexpected user id")

    import boto3
    from botocore.config import Config

    key = f"{spec.prefix}/{uid}/{uuid.uuid4().hex}{ext}"
    s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    post = s3.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={"Content-Type": body.content_type},
        Conditions=[
            {"Content-Type": body.content_type},
            ["content-length-range", 1, spec.max_bytes],
        ],
        ExpiresIn=EXPIRES_IN_S,
    )
    logger.info("upload_ticket_issued", extra={
        "kind": body.kind, "content_type": body.content_type, "size_bytes": body.size_bytes,
    })
    return UploadTicket(
        key=key, url=post["url"], fields=post["fields"],
        expires_in=EXPIRES_IN_S, max_bytes=spec.max_bytes,
    )
