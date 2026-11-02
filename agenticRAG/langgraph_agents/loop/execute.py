"""The `execute` and `declined` nodes (stage 4): a resumed turn starts here.

`POST /chat/resume` has already claimed the paused turn (CAS `paused ->
resumed`) and moved the action out of `proposed` (`approved` or `declined`).
Nothing below runs without that decision.

`execute` follows plan §3's six rules, in this order:

  1. the cancel flag: Stop before the write means the action does not run
     (`approved -> invalidated`);
  2. re-check: the user may still use loop mode, the plugin is still on and its
     tool definition hash is unchanged, the action has not expired. Any miss
     moves `approved -> invalidated` (or `expired`) and nothing runs;
  3. compare-and-set `approved -> executing`. The record is written BEFORE the
     call, independent of any saved turn. Losing the CAS means somebody else
     moved it: nothing runs;
  4. run the STORED payload (opened from `payload_enc`, hash-checked), never
     arguments the model produced again. If the manifest names an
     `idempotency_arg`, send `action_id` in it. Write tools get 0 retries;
  5. record the result: `succeeded`, or `failed` when the server answered with
     an error. A timeout or a dropped connection after the call started is
     `unknown`, never retried;
  6. a row left in `executing` with no result (the process died mid-call) is
     swept to `unknown` and never run again. That sweep happens at the start of
     every loop turn and resume (`sweep_stale`, E5), and rule 3's CAS cannot be
     won from `executing`.

Every path answers the write call's `tool_call_id` with exactly one tool
message, so the transcript stays valid for the agent's next round.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph_agents.loop.actions import (
    APPROVED, EXECUTING, EXPIRED, FAILED, INVALIDATED, SUCCEEDED, UNKNOWN,
    get_action_store, open_payload, payload_sha256,
)
from langgraph_agents.loop.context import _prepare_plugins
from langgraph_agents.loop.crypto import SealError
from langgraph_agents.loop.errors import normalize
from langgraph_agents.loop.limits import (
    ensure_turn, _msg_role, _msg_tool_calls,
)
from langgraph_agents.loop.mcp_client import DEFAULT_CALL_TIMEOUT_S
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.taint import build_taint
from langgraph_agents.loop.tools.registry import build_turn_tools
from langgraph_agents.loop.tools_node import _audit
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.execute")

# The whole call, including the server's own work. Tests patch this constant;
# production never changes it.
WRITE_TIMEOUT_S = DEFAULT_CALL_TIMEOUT_S + 2.0
# A row still `executing` after this long is swept to `unknown` (E5).
STALE_EXECUTING_S = WRITE_TIMEOUT_S + 30.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def sweep_stale(user_id: str, session_id: str) -> list[str]:
    """Rule 6 / E5: rows of this session stuck in `executing` become `unknown`."""
    return await get_action_store().sweep_stale_executing(
        user_id, session_id, STALE_EXECUTING_S)


async def restore_turn_context(state: dict, config: dict) -> None:
    """Rebuild what a fresh `TurnContext` lacks on a resumed turn: the declined
    payload hashes, the call registry (for the persona's evidence), the taint
    terms, and this turn's plugin tools."""
    turn = ensure_turn(config)
    cfg = config.get("configurable") or {}
    resume = state.get("resume") or {}
    turn.declined_hashes |= set(resume.get("declined_hashes") or [])
    for m in state.get("transcript") or []:
        if _msg_role(m) != "assistant":
            continue
        for tc in _msg_tool_calls(m) or []:
            if isinstance(tc, dict):
                name = tc.get("name") or (tc.get("function") or {}).get("name")
                if tc.get("id") and name:
                    turn.call_registry[tc["id"]] = name
    taint = build_taint(state.get("user_instructions") or [],
                        state.get("user_facts") or [], cfg.get("query", ""))
    turn.banned_terms = list(taint.banned_terms)
    turn.allowed_terms = list(taint.allowed_terms)
    await _prepare_plugins(turn, cfg, cfg.get("request_id", "-"))


def _tool_msg(tool_call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id,
            "content": json.dumps(payload, ensure_ascii=False)}


def _result(state: dict, tool_call_id: str, tool: str, action_id: str, status: str,
            message: dict, **extra: Any) -> dict:
    action = {"tool": tool, "tool_call_id": tool_call_id, "action_id": action_id,
              "status": status, "risk": "write", "kind": extra.pop("kind", ""),
              "round": 0, "result_chars": extra.pop("result_chars", 0), **extra}
    return {
        "transcript": [*(state.get("transcript") or []), message],
        "actions": [action],
        "resume": None,
    }


async def execute_node(state: LoopState, config: RunnableConfig) -> dict:
    turn = ensure_turn(config)
    cfg = config.get("configurable") or {}
    resume = state.get("resume") or {}
    user_id = str(cfg.get("user_id", ""))
    action_id = str(resume.get("action_id", ""))
    tcid = str(resume.get("tool_call_id", ""))
    store = get_action_store()

    await restore_turn_context(state, config)

    record = await store.get(user_id, action_id)
    tool = record.tool if record else "?"

    def stop(status: str, kind: str, reason: str) -> dict:
        msg = _tool_msg(tcid, {"refused": kind, "reason": reason})
        return _result(state, tcid, tool, action_id, status, msg,
                       kind=kind, reason=reason)

    # ── rule 1: the cancel flag ───────────────────────────────────
    checker = cfg.get("cancel_checker")
    cancelled = False
    if checker is not None:
        try:
            cancelled = bool(await checker(config))
        except Exception:  # noqa: BLE001
            cancelled = False
    if cancelled:
        turn.cancelled = True
        await store.transition(user_id, action_id, APPROVED, INVALIDATED)
        return stop(INVALIDATED, "cancelled", "the user pressed Stop before it ran")

    # ── rule 2: re-check ──────────────────────────────────────────
    if record is None or record.status != APPROVED:
        # A replay or a second resume: not ours to move, nothing runs.
        return stop(record.status if record else INVALIDATED, "not_approved",
                    "there is no approved action to run")
    spec = next((s for s in build_turn_tools(cfg) if s.name == record.tool), None)
    if cfg.get("loop_allowed", True) is False:
        why: tuple[str, str] | None = (INVALIDATED, "loop mode is off for this user")
    elif spec is None:
        why = (INVALIDATED, "the plugin is off or no longer offers this tool")
    elif spec.def_sha256 != record.tool_def_sha256:
        why = (INVALIDATED, "the tool definition changed after approval")
    elif _utcnow() >= record.expires_at:
        why = (EXPIRED, "the approval expired")
    else:
        why = None
    if why is not None:
        await store.transition(user_id, action_id, APPROVED, why[0])
        return stop(why[0], "refused_by_policy", why[1])

    # ── rule 3: compare-and-set approved -> executing ─────────────
    if not await store.transition(user_id, action_id, APPROVED, EXECUTING):
        return stop(INVALIDATED, "not_approved", "another request already moved this action")

    # ── rule 4: run the stored payload ────────────────────────────
    started = time.perf_counter()
    try:
        args = await open_payload(record.payload_enc or b"", user_id=user_id,
                                  plugin_id=record.plugin_id, action_id=action_id)
        if payload_sha256(args) != record.payload_sha256:
            raise SealError("stored payload does not match its hash")
    except Exception as exc:  # noqa: BLE001 - nothing ran
        await store.transition(user_id, action_id, EXECUTING, FAILED,
                               result_ref="payload_unreadable")
        msg = _tool_msg(tcid, {"error": "payload_unreadable"})
        return _result(state, tcid, tool, action_id, FAILED, msg,
                       kind="payload_unreadable", error=type(exc).__name__)
    if spec.idempotency_arg:
        args = {**args, spec.idempotency_arg: action_id}

    raw: Any = None
    exc: BaseException | None = None
    try:
        raw = await asyncio.wait_for(spec.call(args, config), WRITE_TIMEOUT_S)  # type: ignore[arg-type]
    except asyncio.CancelledError:
        # The stream was cut while the call was in flight: the write may have
        # run. Record that, then let the cancellation through.
        await asyncio.shield(store.transition(user_id, action_id, EXECUTING, UNKNOWN))
        raise
    except Exception as e:  # noqa: BLE001 - a timeout is asyncio.TimeoutError
        exc = e

    plugin_id = record.plugin_id
    from langgraph_agents.loop.plugins.runtime import get_breaker
    if exc is not None:
        get_breaker(plugin_id).record_failure()
    else:
        get_breaker(plugin_id).record_success()

    # ── rule 5: record the result ─────────────────────────────────
    if exc is not None:
        # The call started and no answer came back: the write may have run.
        await store.transition(user_id, action_id, EXECUTING, UNKNOWN)
        outcome, status = f"unknown:{type(exc).__name__}", UNKNOWN
        message = _tool_msg(tcid, {"status": "unknown",
                                   "note": "no answer came back; it may or may not have run"})
        extra = {"kind": "timeout", "error": type(exc).__name__}
    else:
        remaining = max(0, int(turn.caps.max_result_chars_per_turn - turn.used_result_chars))
        result = await normalize(spec, raw, remaining)
        ref = hashlib.sha256(result.content.encode("utf-8")).hexdigest()[:16]
        if result.ok:
            status, outcome = SUCCEEDED, "ok"
            await store.transition(user_id, action_id, EXECUTING, SUCCEEDED, result_ref=ref)
            extra = {"result_chars": len(result.content)}
        else:
            status, outcome = FAILED, "failed"
            await store.transition(user_id, action_id, EXECUTING, FAILED, result_ref=ref)
            extra = {"kind": "error", "error": "tool_error"}
        message = {"role": "tool", "tool_call_id": tcid, "content": result.content}
    _audit(config, plugin_id, record.tool, args, outcome, started,
           len(message["content"]))
    return _result(state, tcid, tool, action_id, status, message, **extra)


async def declined_node(state: LoopState, config: RunnableConfig) -> dict:
    """The user declined: the call is answered, the payload is remembered so the
    gate refuses the same one again this turn, and the agent carries on."""
    turn = ensure_turn(config)
    cfg = config.get("configurable") or {}
    resume = state.get("resume") or {}
    user_id = str(cfg.get("user_id", ""))
    action_id = str(resume.get("action_id", ""))
    tcid = str(resume.get("tool_call_id", ""))

    await restore_turn_context(state, config)
    record = await get_action_store().get(user_id, action_id)
    if record is not None:
        turn.declined_hashes.add(record.payload_sha256)
    msg = _tool_msg(tcid, {"declined": True,
                           "note": "the user declined this action; it was not run"})
    out = _result(state, tcid, record.tool if record else "?", action_id,
                  "declined", msg)
    return out
