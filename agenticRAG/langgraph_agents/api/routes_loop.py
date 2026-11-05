"""Loop-mode routes: `POST /chat/cancel {session_id, request_id}`, and the
plugins panel: `GET /me/plugins`, `POST /me/plugins/{id}` (stage 3, card 3C),
the approval path (stage 4): `POST /chat/resume`, `GET /me/actions/pending`,
and the OAuth connectors (stage 5): `POST /connectors/{plugin_id}/start`,
`POST /connectors/callback`, `GET /me/connectors`, `DELETE /me/connectors/{id}`.

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
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from langgraph_agents.api.auth import current_user_id, request_groups
from langgraph_agents.loop.config import load_settings
from langgraph_agents.loop.connectors.oauth import (
    AuthExpired,
    NotConnected,
    OAuthError,
)
from langgraph_agents.loop.mode import select_mode
from langgraph_agents.shared.logging import get_logger

router = APIRouter()
logger = get_logger("langgraph.api.loop")


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
        logger.warning("cancel_failed", extra={"error": type(exc).__name__})
        raise HTTPException(500, {"code": "cancel_failed"})
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
    row = {"id": manifest.id, "name": manifest.name, "publisher": manifest.publisher,
           "description": manifest.description, "enabled": enabled,
           "status": _panel_status(manifest, settings, enabled)}
    # The frontend shows Connect / Disconnect / Reconnect for OAuth connectors,
    # which it can only tell from the manifest's auth type. Omit the key for
    # non-OAuth plugins so existing wire-shape assertions are unaffected.
    if manifest.needs_user_token():
        row["authType"] = "mcp_oauth"
    return row


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


# ── Approval: resume a paused turn, and the pending cards (stage 4) ──────────


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    decision: Literal["approve", "decline"]


def _as_uuid(value: str, what: str) -> str:
    """404 for a malformed id: a stranger learns nothing, not even the format."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(404, {"code": "not_found", "what": what})


@router.post("/chat/resume")
async def chat_resume(
    body: ResumeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    uid: str = Depends(current_user_id),
):
    """Continue a paused loop turn with the user's decision.

    Streams like `/chat` (same events, no new `stage` for memory). Answers:
    404 for an unknown thread, a thread that is not the caller's, or an
    action id that does not belong to it (E1: a stranger cannot learn that it
    exists); 409 `turn_not_paused` when the thread was already resumed,
    invalidated or finished (a second approve, a parallel resume or a replay
    runs nothing more); 409 `action_not_pending` when the card is no longer
    waiting; 409 `turn_in_progress` when the session has a live turn.
    """
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        raise HTTPException(404, {"code": "not_found", "what": "thread"})
    thread_id = _as_uuid(body.thread_id, "thread")
    action_id = _as_uuid(body.action_id, "thread")

    from langgraph_agents.api import main as api_main
    from langgraph_agents.api.schemas import ChatRequest
    from langgraph_agents.api.sse import stream_response
    from langgraph_agents.db.postgres import bind_request_character
    from langgraph_agents.llm_byok import bind_request_llm
    from langgraph_agents.loop import actions as actions_mod
    from langgraph_agents.loop.execute import sweep_stale
    from langgraph_agents.loop.leases import get_lease_store
    from langgraph_agents.loop.limits import new_turn
    from langgraph_agents.loop.pause import open_turn, verdict_from_json
    from langgraph_agents.loop.turns import PAUSED, get_turn_store
    from langgraph_agents.shared.logging import get_logger, with_request_id

    logger = get_logger("langgraph.loop.resume")
    graph = api_main._get_loop_graph()
    if graph is None:
        raise HTTPException(503, "Loop graph not loaded yet")
    request_id = str(uuid.uuid4())
    turns = get_turn_store()
    store = actions_mod.get_action_store()

    rec = await turns.get(uid, thread_id)
    if rec is None:
        raise HTTPException(404, {"code": "not_found", "what": "thread"})
    if rec.status != PAUSED:
        # The caller owns this thread (the store is owner-scoped), so saying
        # that it was already answered tells nobody anything new.
        raise HTTPException(409, {"code": "turn_not_paused", "request_id": request_id})
    if str(rec.state_json.get("action_id", "")) != action_id:
        raise HTTPException(404, {"code": "not_found", "what": "thread"})
    sj = rec.state_json
    session_id = rec.session_id

    lease = get_lease_store()
    if not await lease.acquire(uid, session_id, request_id, settings.lease_ttl_s):
        raise HTTPException(409, {"code": "turn_in_progress", "request_id": request_id})

    async def _drop_lease() -> None:
        try:
            await lease.release(uid, request_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("lease_release_failed", extra={
                "request_id": request_id, "error": type(exc).__name__})

    try:
        try:
            await sweep_stale(uid, session_id)       # E5
        except Exception as exc:  # noqa: BLE001
            logger.warning("loop_prelude_failed", extra={"error": type(exc).__name__})

        # The CAS that makes two parallel resumes safe: one caller gets the row.
        claimed = await turns.claim(uid, thread_id)
        if claimed is None:
            raise HTTPException(409, {"code": "turn_not_paused", "request_id": request_id})
        try:
            messages, carry = await open_turn(
                claimed.transcript_enc or b"", user_id=uid, thread_id=thread_id)
        except Exception:  # noqa: BLE001 - unreadable: close it, run nothing
            await store.transition(uid, action_id, actions_mod.PROPOSED,
                                   actions_mod.INVALIDATED)
            await turns.finish(uid, thread_id)
            raise HTTPException(409, {"code": "turn_unreadable", "request_id": request_id})

        approve = body.decision == "approve"
        won = await store.transition(
            uid, action_id, actions_mod.PROPOSED,
            actions_mod.APPROVED if approve else actions_mod.DECLINED)
        if not won:
            await turns.finish(uid, thread_id)
            raise HTTPException(409, {"code": "action_not_pending", "request_id": request_id})
    except BaseException:
        await _drop_lease()
        raise

    persona_id = sj.get("persona_id", "anne")
    locale = sj.get("locale", "en")
    output_mode = sj.get("output_mode", "text")
    verdict = verdict_from_json(sj.get("verdict"))
    req = ChatRequest(
        query=carry.get("query", ""), session_id=session_id, persona_id=persona_id,
        previous_persona_id=sj.get("previous_persona_id"), output_mode=output_mode,
        token_limit=sj.get("token_limit"), web_search=bool(sj.get("web_search", False)),
        locale=locale)
    turn = new_turn()
    turn.user_id = uid
    turn.current_message = req.query
    # R5: restore budget elapsed time and turn_calls from sealed turn
    from langgraph_agents.loop.pause import restore_budget
    restore_budget(turn, carry)
    config = {"configurable": {
        "user_id": uid, "session_id": session_id, "query": req.query,
        "user_message_id": str(uuid.uuid4()), "persona_id": persona_id,
        "previous_persona_id": req.previous_persona_id, "output_mode": output_mode,
        "speaks_aloud": output_mode in ("speech", "both") and api_main.tts_enabled(),
        "request_id": request_id, "token_limit": req.token_limit,
        "web_search": req.web_search, "locale": locale,
        "thread_id": thread_id, "turn_ctx": turn, "_lease": (lease, request_id),
        # Rule 2 of execute: this request passed select_mode just above.
        "loop_allowed": True,
        "cancel_checker": lambda cfg: api_main._cancel_checker(cfg, get_lease_store()),
        "verdict_getter": lambda cfg: api_main._verdict_getter(cfg),
        # Guardrail extension #1: the turn runs under the verdict it paused with.
        "guardrail_verdict": verdict,
    }}
    state = {
        "transcript": messages,
        "resume": {"decision": body.decision, "action_id": action_id,
                   "tool_call_id": sj.get("tool_call_id", ""), "thread_id": thread_id,
                   "declined_hashes": list(sj.get("declined_hashes") or []),
                   # Taint rule 1 survives the pause (sealed in the carry).
                   "personal_sources": list(carry.get("personal_sources") or [])},
        "user_instructions": carry.get("user_instructions") or [],
        "user_facts": carry.get("user_facts") or [],
        "actions": carry.get("actions") or [],
        "motion_jobs": carry.get("motion_jobs") or [],
        "loop_trace": carry.get("loop_trace") or [],
        "rounds": int(sj.get("rounds", 0)),
        "errors": [], "total_tokens": 0, "turn_status": "completed",
    }
    bind_request_character(persona_id)
    # BYOK keys are never stored, so a resumed turn runs on ECA's model.
    bind_request_llm(None)
    resume = {"verdict": verdict, "action_id": action_id, "thread_id": thread_id}

    async def event_generator():
        with with_request_id(request_id):
            try:
                async for sse_event in api_main._stream_chat(
                        req, request_id, config, state, background_tasks, request,
                        uid, None, graph=graph, resume=resume):
                    yield sse_event
            finally:
                await _drop_lease()

    return stream_response(event_generator())


@router.get("/me/actions/pending")
async def pending_actions(session_id: str, uid: str = Depends(current_user_id)):
    """The approval cards still waiting in a session, oldest first, so a page
    reload shows them again. A card past its `expires_at` is reported with
    `status: "expired"`. Same shape as the SSE `approval_required` payload plus
    `status`. Pipeline users get `loop: false` and no cards."""
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        return {"loop": False, "actions": []}
    try:
        uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(422, {"code": "invalid_session_id"})
    from langgraph_agents.loop.actions import get_action_store
    from langgraph_agents.loop.approval import card_payload

    try:
        rows = await get_action_store().pending_for_session(str(uid), session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"actions unavailable: {type(exc).__name__}")
    now = datetime.now(timezone.utc)
    return {"loop": True, "actions": [
        {**card_payload(r), "status": "expired" if now >= r.expires_at else "proposed"}
        for r in rows]}


# ── OAuth connectors (stage 5) ──────────────────────────────────────────────


class ConnectorStartRequest(BaseModel):
    plugin_id: str = Field(min_length=1)


class ConnectorCallbackRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)
    iss: Optional[str] = None


class ConnectorInfo(BaseModel):
    plugin_id: str
    status: str
    scopes: tuple[str, ...]
    expires_at: Optional[datetime]


# Map OAuthError.code to HTTP status and safe message.
_OAUTH_ERROR_MAP = {
    # 400
    "invalid_state": (400, "the authorization request is invalid or has expired"),
    "issuer_mismatch": (400, "the authorization response came from an unexpected issuer"),
    "iss_missing": (400, "the authorization response did not name its issuer"),
    "token_exchange_failed": (400, "the authorization server refused the request"),
    # 404
    "unknown_plugin": (404, "unknown connector"),
    "not_available": (404, "this connector is not available"),
    # 502
    "transient": (502, "the authorization server could not be reached, try again"),
    # 503
    "not_configured": (503, "the connector is not configured on this deployment"),
    "sealer_unavailable": (503, "token storage is not available"),
    # Other codes (not directly mapped) fall through to 500.
    # "discovery_failed", "egress_blocked", "bad_token_response",
    # "auth_expired", "not_connected", "token_unreadable"
}


def _raise_oauth_error(exc: OAuthError) -> None:
    """Raise an HTTPException with the mapped status and safe message."""
    mapping = _OAUTH_ERROR_MAP.get(exc.code)
    if mapping:
        status, msg = mapping
        raise HTTPException(status, {"code": exc.code, "message": msg})
    # Fallback: log and return 500 with code only.
    logger.error("oauth_unmapped_code", extra={"code": exc.code})
    raise HTTPException(500, {"code": "internal"})


def _svc() -> "OAuthService":
    """Get the OAuth service instance (allows monkeypatching in tests)."""
    from langgraph_agents.loop.connectors.oauth import get_oauth_service
    return get_oauth_service()


@router.post("/connectors/{plugin_id}/start")
async def connector_start(
    plugin_id: str,
    body: ConnectorStartRequest,
    uid: str = Depends(current_user_id),
    request: Request = None,
):
    """Start the OAuth flow for a connector. Returns the authorization URL.

    The browser navigates to this URL; the provider redirects back to
    `/connectors/callback` with `code`, `state`, and `iss`.

    Errors: 400 (invalid_state, issuer_mismatch, iss_missing,
    token_exchange_failed), 404 (unknown_plugin, not_available),
    502 (transient), 503 (not_configured, sealer_unavailable).
    """
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        raise HTTPException(404, {"code": "not_found", "what": "connector"})
    # The body plugin_id must match the path parameter.
    if body.plugin_id != plugin_id:
        raise HTTPException(400, {"code": "invalid_state", "message": "plugin_id mismatch"})
    svc = _svc()
    try:
        authorize_url = await svc.start(str(uid), plugin_id)
    except OAuthError as exc:
        _raise_oauth_error(exc)
    except NotConnected:
        _raise_oauth_error(OAuthError("not_connected"))
    except AuthExpired:
        _raise_oauth_error(OAuthError("auth_expired"))
    return {"authorize_url": authorize_url}


@router.post("/connectors/callback")
async def connector_callback(
    body: ConnectorCallbackRequest,
    uid: str = Depends(current_user_id),
    request: Request = None,
):
    """Complete the OAuth flow. The browser posts the callback params here.

    Verifies the state belongs to this user, exchanges the code for tokens,
    and stores the sealed connection. Returns the connection info (no tokens).

    Errors: 400 (invalid_state, issuer_mismatch, iss_missing,
    token_exchange_failed), 404 (unknown_plugin, not_available),
    502 (transient), 503 (not_configured, sealer_unavailable).
    """
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        raise HTTPException(404, {"code": "not_found", "what": "connector"})
    svc = _svc()
    try:
        info = await svc.complete(str(uid), body.code, body.state, body.iss)
    except OAuthError as exc:
        _raise_oauth_error(exc)
    except NotConnected:
        _raise_oauth_error(OAuthError("not_connected"))
    except AuthExpired:
        _raise_oauth_error(OAuthError("auth_expired"))
    return ConnectorInfo(
        plugin_id=info.plugin_id,
        status=info.status,
        scopes=info.scopes,
        expires_at=info.expires_at,
    )


@router.get("/me/connectors")
async def list_connectors(uid: str = Depends(current_user_id)):
    """List the user's OAuth connections.

    Returns `{loop: bool, connections: [ConnectorInfo]}`.
    Pipeline users get `loop: false` and an empty list.
    """
    settings = load_settings()
    if select_mode(request_groups(), settings) != "loop":
        return {"loop": False, "connections": []}
    svc = _svc()
    try:
        conns = await svc.list_connections(str(uid))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"connectors unavailable: {type(exc).__name__}")
    return {"loop": True, "connections": [
        ConnectorInfo(
            plugin_id=c.plugin_id,
            status=c.status,
            scopes=c.scopes,
            expires_at=c.expires_at,
        ) for c in conns]}


@router.delete("/me/connectors/{plugin_id}")
async def delete_connector(
    plugin_id: str,
    uid: str = Depends(current_user_id),
):
    """Revoke and delete an OAuth connection.

    Calls the provider's revocation endpoint (RFC 7009) if advertised,
    then deletes the row. Returns 204 on success, 404 if no connection
    existed.
    """
    svc = _svc()
    try:
        existed = await svc.revoke(str(uid), plugin_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"connectors unavailable: {type(exc).__name__}")
    if not existed:
        raise HTTPException(404, {"code": "not_found", "what": "connector"})
    return {"status": "revoked"}
