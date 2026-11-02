"""Loop-mode routes: `POST /chat/cancel {session_id, request_id}`, and the
plugins panel: `GET /me/plugins`, `POST /me/plugins/{id}` (stage 3, card 3C).

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


# ── Plugins panel (stage 3C) ─────────────────────────────────────────────────


class PluginToggle(BaseModel):
    enabled: bool


def _panel_status(manifest, settings, enabled: bool) -> str:
    """The status the panel shows for one plugin, with no network call.

    Same order as `plugins.runtime.prepare_turn`: the global switch, the
    per-plugin switch, the user's own choice, an endpoint this deployment does
    not have, then the definitions pin. `unavailable` and `web_search_off` are
    per-turn facts and are not shown here.
    """
    from langgraph_agents.loop.plugins import runtime

    if settings.plugins_disabled or manifest.id in (settings.plugins_off or []):
        return runtime.OFF
    if not enabled:
        return runtime.DISABLED
    if not manifest.resolved_url():
        return runtime.UNCONFIGURED
    if not manifest.is_pinned() or runtime.cached_status(manifest) == runtime.DEFINITION_CHANGED:
        return runtime.DEFINITION_CHANGED
    return runtime.ACTIVE


def _visible_catalog(settings) -> list:
    from langgraph_agents.loop.plugins.manifest import load_catalog

    return [m for m in load_catalog() if not m.requires_gate or settings.google_enabled]


def _plugin_row(manifest, settings, enabled: bool) -> dict:
    return {"id": manifest.id, "name": manifest.name, "publisher": manifest.publisher,
            "description": manifest.description, "enabled": enabled,
            "status": _panel_status(manifest, settings, enabled)}


@router.get("/me/plugins")
async def list_plugins(uid: str = Depends(current_user_id)):
    """`{loop, plugins}`. Pipeline users get `loop: false` and no plugins."""
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        return {"loop": False, "plugins": []}
    from langgraph_agents.loop.plugins.store import get_plugin_store

    catalog = _visible_catalog(settings)
    try:
        rows = await get_plugin_store().get_all(str(uid)) if catalog else {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"plugins unavailable: {type(exc).__name__}")
    return {"loop": True,
            "plugins": [_plugin_row(m, settings, rows.get(m.id, m.enabled_by_default))
                        for m in catalog]}


@router.post("/me/plugins/{plugin_id}")
async def set_plugin(plugin_id: str, body: PluginToggle,
                     uid: str = Depends(current_user_id)):
    """Switch one plugin on or off for this user. Unknown or hidden id: 404."""
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        raise HTTPException(404, "unknown plugin")
    manifest = next((m for m in _visible_catalog(settings) if m.id == plugin_id), None)
    if manifest is None:
        raise HTTPException(404, "unknown plugin")
    from langgraph_agents.loop.plugins.store import get_plugin_store

    try:
        await get_plugin_store().set_enabled(str(uid), manifest.id, body.enabled)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"plugins unavailable: {type(exc).__name__}")
    return _plugin_row(manifest, settings, body.enabled)
