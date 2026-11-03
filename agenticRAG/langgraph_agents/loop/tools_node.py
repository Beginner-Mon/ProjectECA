"""The `tools_read` node: run the allowed calls in parallel.

Every `tool_call_id` gets exactly one tool message, refusals and errors
included. The budget line goes at the end of the round's last tool message.
Gate per call, read retries, normalise with the remaining-char budget,
breaker records, repeat-empty marking, and the actions / motion_jobs /
turn-registry updates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph_agents.loop.errors import (
    APPROVAL_UNAVAILABLE, AUTH_EXPIRED, BUDGET, CANCELLED, INVALID_ARGS,
    REFUSED_BY_POLICY, ToolResult, classify, normalize, oauth_reason,
)
from langgraph_agents.loop.gate import GateDecision, check, needs_verdict, _call_args, _call_id, _call_name, _is_egress_or_clinical
from langgraph_agents.loop.limits import (
    ensure_turn, _msg_content, _msg_role, _msg_tool_calls,
)
from langgraph_agents.loop.prompts import budget_line
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.tools.registry import build_turn_tools, ToolSpec
from langgraph_agents.loop.trace import log_plugin_call, log_turn
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.tools_node")


def _spec_by_name(specs: list[ToolSpec]) -> dict[str, ToolSpec]:
    return {s.name: s for s in specs}


async def _run_one(
    call: dict[str, Any],
    spec: ToolSpec | None,
    turn: Any,
    config: dict,
    remaining: int,
    forced_refusal: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Run one call through the gate + the tool. Returns an action record
    and a tool message.

    A write call that passes the gate is NOT run (stage 4): the result carries
    `pending` (the call and its arguments) and no tool message; the `propose`
    node stores it and the turn pauses. `forced_refusal` skips the gate: the
    second write call of a round is refused `one_action_per_round` before the
    calls start in parallel (the gate's own cap check could race).
    """
    tool_call_id = _call_id(call)
    tool_name = _call_name(call) or "?"
    args = _call_args(call)
    plugin_id = getattr(spec, "plugin_id", None) if spec is not None else None
    started = time.perf_counter()

    round_n = max(1, int(turn.round_calls or 0))
    action: dict[str, Any] = {
        "tool": tool_name,
        "tool_call_id": tool_call_id,
        "action_id": tool_call_id,
        "status": "ok",
        "risk": spec.risk if spec else "?",
        "kind": "",
        "round": round_n,
        "result_chars": 0,
        "source_label": (getattr(spec, "plugin_id", None) or getattr(spec, "name", None)
                         or tool_name) if spec is not None else tool_name,
    }

    # ── gate ──────────────────────────────────────────────────────
    if forced_refusal is not None:
        decision = GateDecision.refuse(*forced_refusal)
    else:
        decision = await check(call, spec, turn)
    if decision.kind == "needs_approval":
        turn.record_write()
        action["status"] = "pending"
        return {"action": action, "tool_message": None,
                "pending": {"tool_call_id": tool_call_id, "tool": spec.name,
                            "arguments": args}}
    if decision.refusal:
        action["status"] = "refused"
        action["kind"] = decision.kind
        action["reason"] = decision.message
        content = json.dumps({"refused": decision.kind, "reason": decision.message},
                             ensure_ascii=False)
        result = ToolResult(ok=False, content=content, kind="failed",
                            empty=False, source_label=tool_name, provenance={})
        if plugin_id:
            _audit(config, plugin_id, tool_name, args, f"refused:{decision.kind}",
                   started, 0)
        return {"action": action, "tool_message": _tool_msg(tool_call_id, tool_name, content)}

    # ── call (read retries) ───────────────────────────────────────
    retries = int(getattr(turn.caps, "retries_read", 1) or 1)
    raw = None
    exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            raw = await spec.call(args, config)  # type: ignore[arg-type]
            exc = None
            break
        except Exception as e:  # noqa: BLE001
            exc = e
            # Stage 5: "reconnect" does not get better by asking again.
            if attempt >= retries or oauth_reason(e) in (
                    "not_connected", "auth_expired", "token_unreadable"):
                break
            await asyncio.sleep(0.1 * (attempt + 1))

    if plugin_id:
        # D7: only a transport or protocol fault counts against the plugin's
        # breaker; a tool-level error answer (`{"error": ...}`) does not.
        from langgraph_agents.loop.plugins.runtime import get_breaker
        reason = oauth_reason(exc) if exc is not None else None
        if exc is not None and reason is None:
            get_breaker(plugin_id).record_failure()
        elif exc is None:
            get_breaker(plugin_id).record_success()
        # A connector auth fault is the user's connection, not the plugin's health.

    if exc is not None:
        action["status"] = "failed"
        action["kind"] = classify(exc)["kind"] if plugin_id else "error"
        action["error"] = type(exc).__name__
        content = json.dumps({"error": type(exc).__name__}, ensure_ascii=False)
        if plugin_id and action["kind"] == AUTH_EXPIRED:
            # F8: the plugin is left out for the rest of the turn; the UI shows
            # "reconnect". The text is fixed: it names no token and no provider error.
            plugins = getattr(turn, "plugins", None)
            if plugins is not None:
                plugins.invalidate(
                    plugin_id,
                    "not_connected" if oauth_reason(exc) == "not_connected"
                    else "auth_expired")
                plugins.reconnect.append(plugin_id)
            content = json.dumps({
                "error": "auth_expired",
                "message": f"{plugin_id} needs the user to connect it again. "
                           "It is not available in this turn."}, ensure_ascii=False)
        result = ToolResult(ok=False, content=content, kind="failed",
                            empty=False, source_label=tool_name, provenance={})
    else:
        result = await normalize(spec, raw, remaining)
        action["result_chars"] = len(result.content)
        if result.empty:
            turn.repeat_empty_signatures.add(
                f"{tool_name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}")
        if not result.ok:
            action["status"] = "failed"
            action["kind"] = "error"

    content = result.content
    if plugin_id:
        outcome = "ok" if action["status"] == "ok" else (
            f"failed:{action['kind']}" if action["kind"] else "failed")
        _audit(config, plugin_id, tool_name, args, outcome, started,
               int(action.get("result_chars") or 0))
    return {"action": action, "tool_message": _tool_msg(tool_call_id, tool_name, content)}


def _audit(config: dict, plugin_id: str, tool: str, args: dict, outcome: str,
           started: float, result_chars: int) -> None:
    """D9: one audit line per plugin call. A hash of the arguments, never text."""
    digest = hashlib.sha256(
        json.dumps(args, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    log_plugin_call(
        (config.get("configurable") or {}).get("request_id", "-"),
        plugin_id=plugin_id, tool=tool, outcome=outcome,
        duration_ms=round((time.perf_counter() - started) * 1000),
        result_chars=result_chars, args_sha256=digest)


def _tool_msg(tool_call_id: str, tool_name: str, content: str) -> dict:
    # Transcript tool messages carry NO extra wire keys (no `name` key).
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id,
    }


async def tools_node(state: LoopState, config: RunnableConfig) -> dict:
    turn = ensure_turn(config)
    request_id = (config.get("configurable") or {}).get("request_id", "-")
    turn.verdict_getter = (config.get("configurable") or {}).get("verdict_getter")

    # Pending tool calls from the last assistant message.
    transcript = list(state.get("transcript") or [])
    last = transcript[-1] if transcript else None
    tool_calls = _msg_tool_calls(last) or []
    if not tool_calls:
        return {}

    specs = build_turn_tools(config.get("configurable") or {})

    # A1/R4: fetch the guardrail verdict once for this round, but only when a
    # call needs it (egress_read or clinical). Internal-only rounds do not
    # wait, and the agent's model call never waits — time to first token is
    # preserved. Stored on turn.verdict so the gate sees it.
    # Falls back to guardrail.handoff.current_verdict when no verdict_getter
    # is injected (R4: the verdict fails safe, never silently routine).
    called = {_call_name(call) for call in tool_calls}
    wait_for_verdict = any(needs_verdict(s) for s in specs if s.name in called)
    if wait_for_verdict:
        getter = turn.verdict_getter or _default_verdict_getter
        try:
            turn.verdict = await getter(config)
        except Exception:  # noqa: BLE001
            # A broken guardrail is a routine turn (policy), never a failed one.
            from langgraph_agents.guardrail.types import fallback_verdict
            turn.verdict = fallback_verdict()

    # Remaining turn-level chars for normalisation.
    remaining = max(0, int(turn.caps.max_result_chars_per_turn - turn.used_result_chars))

    # Run all allowed calls in parallel.
    spec_by_name = _spec_by_name(specs)
    jobs = []
    write_seen = False
    for call in tool_calls:
        spec = spec_by_name.get(_call_name(call))
        forced = None
        if spec is not None and spec.risk == "write":
            if write_seen:
                forced = (BUDGET, "one_action_per_round: a write call already ran this round")
            write_seen = True
        jobs.append(_run_one(call, spec, turn, config, remaining, forced))
    results = await asyncio.gather(*jobs)

    # Taint rule 1: a source with data_class="personal" that was actually read
    # (the call ran and returned) marks the turn, so later egress or clinical
    # calls go to the gate's rule 1. Calls in the same round are already past
    # the gate; only later rounds see it.
    # `plugin_id` names the destination that holds the data: a later call to the
    # same connector is not "another destination" (PLAN §9, rule 1).
    personal = {s.name: s.plugin_id for s in specs if s.data_class == "personal"}
    for res in results:
        act = res["action"]
        if act["tool"] in personal and act["status"] == "ok":
            turn.personal_sources.append(
                {"tool": act["tool"], "data_class": "personal",
                 "plugin_id": personal[act["tool"]]})

    # Build tool messages; budget line on the LAST one.
    tool_messages: list[dict] = []
    actions: list[dict] = []
    motion_jobs: list[dict] = []
    pending_write: dict | None = None
    answered = [r for r in results if r.get("tool_message") is not None]
    for res in results:
        action = res["action"]
        turn.call_registry[action["tool_call_id"]] = action["tool"]
        if res.get("pending") is not None:
            pending_write = res["pending"]
    for i, res in enumerate(answered):
        action = res["action"]
        actions.append(action)
        msg = res["tool_message"]

        # Motion job capture: request_motion returns a payload with job_id.
        if action["tool"] == "request_motion" and action["status"] == "ok":
            try:
                payload = json.loads(msg["content"])
                if isinstance(payload, dict) and payload.get("job_id"):
                    motion_jobs.append(payload)
                    turn.motion_jobs.append(payload)
            except (json.JSONDecodeError, ValueError):
                pass

        # Budget line on the round's last tool message.
        if i == len(answered) - 1:
            left = max(0.0, turn.caps.loop_time_s - turn.budget.elapsed())
            msg["content"] = f"{msg['content']}{budget_line(
                round_n=turn.round_calls or 1,
                max_rounds=turn.caps.max_rounds,
                seconds_left=left)}"
        tool_messages.append(msg)

    # result_chars accounting
    for res in answered:
        turn.result_chars(len(res["tool_message"]["content"]))

    # Caps spent → force tool_choice="none" next agent round.
    caps = turn.caps
    if turn.round_calls >= caps.max_calls_per_round or turn.turn_calls >= caps.max_calls_per_turn:
        turn.force_no_tools = True

    new_transcript = [*transcript, *tool_messages]
    return {
        "transcript": new_transcript,
        "actions": actions,
        "motion_jobs": motion_jobs,
        "turn_status": state.get("turn_status", "completed"),
        "pending_write": pending_write,
    }


def _default_verdict_getter(config: dict):
    """Fallback verdict getter: guardrail.handoff.current_verdict.

    Used when no verdict_getter is injected in the config (R4: the verdict
    fails safe, never silently routine).
    """
    from langgraph_agents.guardrail.handoff import current_verdict
    return current_verdict(config)
