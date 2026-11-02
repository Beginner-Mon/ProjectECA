"""The `propose` node (stage 4): a write call that passed the gate becomes a
stored action and the turn pauses for the user's approval.

Steps, in order:

  1. build the card (display, recipients) and the provenance (the sources read
     earlier in this turn) from the pending call and the manifest;
  2. seal the arguments and insert the action as `proposed` (idempotent on
     `action_id = uuid5(thread_id, tool_call_id)`);
  3. wait for the turn's guardrail verdict (it travels with the paused turn);
  4. seal the transcript and save it in `loop_turns` (`pause`, timed: `pause_ms`
     goes into the trace, E12);
  5. write the code-made line "Waiting for your approval: <summary>" as the
     turn's answer. `api/main.py` sends the guardrail head before it, and the
     `approval_required` event after the node's update.

The node ends the graph run (`approval` set). If the action cannot be stored
(no sealer, a repeated call id) the call is answered with a refusal and the
loop goes on (`approval` stays None).
"""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from langgraph_agents.guardrail.handoff import current_verdict
from langgraph_agents.loop.actions import (
    INVALIDATED, PROPOSED, ActionRecord, get_action_store, new_action, seal_payload,
)
from langgraph_agents.loop.approval import (
    build_display, build_recipients, card_payload, summary,
)
from langgraph_agents.loop.crypto import SealerUnavailable
from langgraph_agents.loop.errors import APPROVAL_UNAVAILABLE, REFUSED_BY_POLICY
from langgraph_agents.loop.limits import ensure_turn
from langgraph_agents.loop.outcome import pending_line
from langgraph_agents.loop.pause import seal_turn, verdict_to_json
from langgraph_agents.loop.plugins.manifest import get_manifest
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.tools.registry import build_turn_tools
from langgraph_agents.loop.trace import log_turn
from langgraph_agents.loop.turns import get_turn_store
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.propose")


def provenance_of(actions: list[dict]) -> list[dict]:
    """`[{tool, source_label}]`: the sources read before the proposal. Only
    calls that ran and returned (status ok), once each, in call order."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for a in actions or []:
        if a.get("status") != "ok":
            continue
        item = (str(a.get("tool", "")), str(a.get("source_label") or a.get("tool", "")))
        if item in seen:
            continue
        seen.add(item)
        out.append({"tool": item[0], "source_label": item[1]})
    return out


def _refusal(state: dict, pending: dict, kind: str, reason: str) -> dict:
    """Answer the pending call with a refusal; the loop goes on."""
    tcid = pending["tool_call_id"]
    content = json.dumps({"refused": kind, "reason": reason}, ensure_ascii=False)
    action = {"tool": pending["tool"], "tool_call_id": tcid, "action_id": tcid,
              "status": "refused", "risk": "write", "kind": kind, "reason": reason,
              "round": 0, "result_chars": 0}
    return {
        "transcript": [*(state.get("transcript") or []),
                       {"role": "tool", "content": content, "tool_call_id": tcid}],
        "actions": [action],
        "pending_write": None,
        "approval": None,
        "_route": "agent",
    }


async def propose_node(state: LoopState, config: RunnableConfig) -> dict:
    turn = ensure_turn(config)
    try:
        return await _propose(state, config, turn)
    finally:
        # The run ends here when a card was shown; the persona node (which
        # normally closes the plugin sessions) does not run.
        plugins = getattr(turn, "plugins", None)
        if plugins is not None and turn.pending_card is not None:
            await plugins.close()


async def _propose(state: LoopState, config: RunnableConfig, turn: Any) -> dict:
    cfg = config.get("configurable") or {}
    pending = state.get("pending_write") or {}
    user_id = str(cfg.get("user_id", ""))
    session_id = str(cfg.get("session_id", ""))
    thread_id = str(cfg.get("thread_id", ""))
    request_id = cfg.get("request_id", "-")
    locale = cfg.get("locale", "en")
    args = pending.get("arguments") or {}

    specs = {s.name: s for s in build_turn_tools(cfg)}
    spec = specs.get(pending.get("tool"))
    if spec is None or not spec.plugin_id:
        return _refusal(state, pending, REFUSED_BY_POLICY, "unknown write tool")
    manifest = get_manifest(spec.plugin_id, cfg.get("plugin_catalog"))
    decl = manifest.tools.get(spec.remote_name or "") if manifest else None

    display = build_display(args, decl)
    record: ActionRecord = new_action(
        user_id=user_id, session_id=session_id, thread_id=thread_id,
        tool_call_id=pending["tool_call_id"], plugin_id=spec.plugin_id,
        tool=spec.name, arguments=args,
        plugin_version=manifest.version if manifest else "",
        tool_def_sha256=spec.def_sha256, payload_display=display,
        recipients=build_recipients(args, decl),
        provenance=provenance_of(state.get("actions") or []), risk="write")
    try:
        record.payload_enc = await seal_payload(
            args, user_id=user_id, plugin_id=spec.plugin_id,
            action_id=record.action_id)
    except SealerUnavailable:
        return _refusal(state, pending, APPROVAL_UNAVAILABLE,
                        "approvals are not available: no sealing key is configured")

    store = get_action_store()
    if not await store.propose(record):
        return _refusal(state, pending, REFUSED_BY_POLICY,
                        "this call id was already used in this turn")

    try:
        verdict = await current_verdict(config)
    except Exception:  # noqa: BLE001 - a broken guardrail is a routine turn
        from langgraph_agents.guardrail.types import fallback_verdict
        verdict = fallback_verdict()

    turn.pending_card = {"thread_id": thread_id, "action_id": record.action_id}
    carry_state = {"user_instructions": state.get("user_instructions"),
                   "user_facts": state.get("user_facts"),
                   "actions": list(state.get("actions") or []),
                   "motion_jobs": state.get("motion_jobs"),
                   "loop_trace": state.get("loop_trace")}
    try:
        blob = await seal_turn(transcript=state.get("transcript") or [],
                               state=carry_state, query=cfg.get("query", ""),
                               user_id=user_id, thread_id=thread_id)
        state_json = {
            "action_id": record.action_id,
            "tool_call_id": pending["tool_call_id"],
            "rounds": int(state.get("rounds", 0)),
            "declined_hashes": sorted(turn.declined_hashes),
            "verdict": verdict_to_json(verdict),
            "persona_id": cfg.get("persona_id", "anne"),
            "previous_persona_id": cfg.get("previous_persona_id"),
            "locale": locale,
            "output_mode": cfg.get("output_mode", "text"),
            "speaks_aloud": bool(cfg.get("speaks_aloud", False)),
            "web_search": bool(cfg.get("web_search", False)),
            "token_limit": cfg.get("token_limit"),
        }
        t0 = time.perf_counter()
        paused = await get_turn_store().pause(
            user_id, session_id, thread_id, request_id=request_id,
            transcript_enc=blob, state_json=state_json,
            expires_at=record.expires_at)
        pause_ms = round((time.perf_counter() - t0) * 1000)
        if not paused:
            raise RuntimeError("turn could not be paused")
    except Exception:
        # No saved turn means no card: do not leave a proposal nobody can answer.
        turn.pending_card = None
        await store.transition(user_id, record.action_id, PROPOSED, INVALIDATED)
        raise

    log_turn(request_id, {"node": "propose", "pause_ms": pause_ms,
                          "action_id": record.action_id})

    line = pending_line(summary(display), locale)
    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None
    if writer is not None:
        writer({"content": line})

    actions = list(state.get("actions") or [])
    actions.append({"tool": spec.name, "tool_call_id": pending["tool_call_id"],
                    "action_id": record.action_id, "status": "proposed",
                    "risk": "write", "kind": "", "round": 0, "result_chars": 0})
    loop_trace = list(state.get("loop_trace") or [])
    loop_trace.append({"rounds": int(state.get("rounds", 0)), "actions": len(actions),
                       "turn_status": "awaiting_approval", "pause_ms": pause_ms,
                       "action_id": record.action_id, "request_id": request_id})
    return {
        "final_answer": line,
        "turn_status": "awaiting_approval",
        "pending_action_id": record.action_id,
        "approval": card_payload(record),
        "actions": actions,
        "loop_trace": loop_trace,
        "motion_jobs": state.get("motion_jobs") or [],
        "pending_write": None,
        "total_tokens": 0,
    }
