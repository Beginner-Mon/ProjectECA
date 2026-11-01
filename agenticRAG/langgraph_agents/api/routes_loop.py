"""Loop-mode routes: `POST /chat/cancel {session_id, request_id}`.

The frontend never receives `request_id` on the wire (no token event carries
it), so cancel is by session — accepted with a known race (a brand-new turn
in the same session can slip in before the next round's cancel check; noted
in PROGRESS). The `request_id` body field is kept for the store and for
future precise cancel.

B5: in pipeline mode this endpoint answers 202 without touching any store —
pipeline users never took a loop lease, and on prod the lease table does not
exist until ship.
"""

from __future__ import annotations

import inspect
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from langgraph_agents.api.auth import current_user_id, request_groups
from langgraph_agents.loop.config import load_settings
from langgraph_agents.loop.mode import select_mode

router = APIRouter()


class CancelRequest(BaseModel):
    session_id: str = Field(min_length=1)
    request_id: Optional[str] = None


async def _lease_store():
    # B4: import the loop store inside the handler so importing this module
    # never loads the loop package (and can never stop the pipeline).
    from langgraph_agents.loop.leases import get_lease_store
    return get_lease_store()


async def request_cancel(uid: str, session_id: str, request_id: Optional[str] = None) -> None:
    """Mark the session's loop turn as cancelled.

    The lease store's `request_cancel(user_id, session_id)` sets
    `cancel_requested` on the live lease row for this user only (A3). The
    agent node checks `is_cancelled` each round and ends the turn with
    `turn_status="cancelled"`. Fails closed: a store error raises.
    """
    store = await _lease_store()
    result = store.request_cancel(uid, session_id)
    if inspect.isawaitable(result):
        await result


@router.post("/chat/cancel", status_code=202)
async def chat_cancel(
    body: CancelRequest,
    request: Request,
    uid: str = Depends(current_user_id),
):
    """Request cancellation of the in-progress loop turn for this session.

    Returns 202 once the cancel flag is set. The turn ends on its next round;
    the client should stop reading the stream (abort) locally and not wait for
    the backend.

    B5: pipeline mode answers 202 immediately without touching any store.
    """
    groups = request_groups()
    settings = load_settings()
    if select_mode(groups, settings) == "pipeline":
        return {"status": "cancel_requested", "session_id": body.session_id}
    try:
        await request_cancel(uid, body.session_id, body.request_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"cancel failed: {exc}")
    return {"status": "cancel_requested", "session_id": body.session_id}
