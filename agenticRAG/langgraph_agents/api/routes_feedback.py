"""Per-message 👍/👎 feedback.

POST   /me/feedback/messages/{message_id}  → upsert a vote (+ optional reasons/comment)
DELETE /me/feedback/messages/{message_id}  → clear it (idempotent)

Storage is `message_feedback` (migration 012): one row per assistant message,
current-state only, owned via message -> conversation -> user (no `user_id`
column — see the migration's docstring). Both routes take identity only from
Depends(current_user_id), same as routes_preferences.py and routes_crud.py.

`comment` is health-adjacent free text (a user explaining what was wrong with
clinical advice). It is written to Postgres and NEVER logged — not in a log
message, not in `extra=`. The two log lines below list every field they carry;
if you add a field to either, `comment` must not be one of them.
"""

from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from langgraph_agents.api.auth import current_user_id
from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.api.feedback")

router = APIRouter(tags=["feedback"])

FeedbackReason = Literal[
    "incorrect", "unsafe", "not_relevant", "incomplete", "hard_to_follow",
    "wrong_language", "motion_issue", "voice_issue", "other",
]

_MAX_REASONS = 9

# One statement, and it is both the ownership check and the write. The WHERE
# clause on messages (id + role='assistant') is what makes "no such message",
# "not mine" (row-level security on messages hides it — 007_rls.py) and "it's
# a user message, not an assistant reply" all collapse into the same outcome:
# zero rows selected, so the INSERT ... SELECT inserts nothing and RETURNING
# yields None. There is no second query that could disagree with this one
# about who owns the message.
_UPSERT = """
    INSERT INTO message_feedback (message_id, rating, reasons, comment)
    SELECT m.id, $2, $3::text[], $4 FROM messages m
    WHERE m.id = $1::uuid AND m.role = 'assistant'
    ON CONFLICT (message_id) DO UPDATE
      SET rating = EXCLUDED.rating, reasons = EXCLUDED.reasons,
          comment = EXCLUDED.comment, updated_at = now()
    RETURNING message_id, rating, reasons, comment, updated_at
"""

# RLS on message_feedback (the FOR ALL policy in 012) already limits this to
# the caller's own rows, so there is nothing further to scope here.
_DELETE = "DELETE FROM message_feedback WHERE message_id = $1::uuid"


class MessageFeedbackIn(BaseModel):
    """Body of POST /me/feedback/messages/{message_id}.

    A 👍 (rating=1) never carries reasons or a comment — the frontend never
    sends them together, and the validator below enforces that server-side
    rather than trusting the caller. That is also what makes a 👍 upsert
    clear out an earlier 👎's reasons/comment: it always writes reasons=[]
    and comment=None.
    """

    rating: Literal[1, -1]
    reasons: list[FeedbackReason] = Field(default_factory=list, max_length=_MAX_REASONS)
    comment: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("reasons")
    @classmethod
    def _dedupe_reasons(cls, value: list[str]) -> list[str]:
        """Drop duplicates, keep first-seen order.

        Runs after the max_length check above, so a caller cannot dodge the
        9-item cap by repeating a code — the cap applies to what was sent,
        not to what survives deduping.
        """
        seen: dict[str, None] = {}
        for reason in value:
            seen.setdefault(reason, None)
        return list(seen)

    @field_validator("comment")
    @classmethod
    def _strip_comment(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _thumbs_up_carries_nothing(self) -> "MessageFeedbackIn":
        if self.rating == 1 and (self.reasons or self.comment):
            raise ValueError("a 👍 rating (1) must not carry reasons or a comment")
        return self


class MessageFeedbackOut(BaseModel):
    message_id: str
    rating: int
    reasons: list[str]
    comment: Optional[str] = None
    updated_at: str


@router.post("/me/feedback/messages/{message_id}", response_model=MessageFeedbackOut)
async def upsert_message_feedback(
    message_id: uuid.UUID,
    body: MessageFeedbackIn,
    uid: str = Depends(current_user_id),
):
    pg = get_pg_client()
    row = await pg.fetchrow(
        _UPSERT, str(message_id), body.rating, body.reasons, body.comment,
    )
    if row is None:
        # Covers three cases the caller cannot tell apart, on purpose: the id
        # does not exist, it belongs to someone else, or it names a user
        # message rather than an assistant reply.
        raise HTTPException(404, "message not found")

    logger.info("feedback_message_saved", extra={
        "user_id": uid,
        "message_id": str(message_id),
        "rating": body.rating,
        "reasons": body.reasons,
    })
    return MessageFeedbackOut(
        message_id=str(row["message_id"]),
        rating=row["rating"],
        reasons=list(row["reasons"]),
        comment=row["comment"],
        updated_at=row["updated_at"].isoformat(),
    )


@router.delete("/me/feedback/messages/{message_id}", status_code=204)
async def clear_message_feedback(
    message_id: uuid.UUID,
    uid: str = Depends(current_user_id),
) -> Response:
    """Always 204. Idempotent: RLS hides other users' rows, so there is no way
    to distinguish "a row was cleared" from "there was nothing to clear" —
    and no reason a caller needs to."""
    pg = get_pg_client()
    await pg.execute(_DELETE, str(message_id))

    logger.info("feedback_message_cleared", extra={
        "user_id": uid,
        "message_id": str(message_id),
    })
    return Response(status_code=204)
