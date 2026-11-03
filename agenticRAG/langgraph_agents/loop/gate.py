"""The loop gate: every tool call passes here before it runs.

`async check(call, spec, turn) -> GateDecision`: `allow`, `refuse(kind,
message)` or `needs_approval(message)`.

Checks in this exact order (plan §II.2):

  (1) unknown tool or bad arguments → invalid_args
      (own small validator: required keys, JSON types, enums, and no argument
      the schema does not declare — not jsonschema; a write proposal over
      `approval.PAYLOAD_CAP` characters is refused here too)
  (1b) stage 3: a plugin tool whose plugin was switched off or changed
      mid-turn (`turn.plugins.invalidate`) → refused_by_policy
  (2) cancel flag → cancelled
  (3) caps → budget, plus one_action_per_round for the write cap
  (4) verdict: egress, clinical and write tools wait for the guardrail
      verdict (fetched by the tools node and stored on the turn); a
      non-routine verdict allows internal tools only, refuse
      `refused_by_policy`
  (5) taint: rule 1 (personal source → another destination) refuses with
      `approval_unavailable` in stage 1; rule 2 (egress_read argument names
      a memory/instruction term) refuses, naming the terms
  (6) repeat-empty → repeat_empty
  (7) rate limit → budget kind (resolved via `rate.get_rate_counter()`);
      a plugin call counts against bucket `plugin:<id>` (D7)
  (8) breaker open → refuse kind `transient`; a plugin has its own breaker,
      per process (D7)
  (9) write → needs_approval (stage 4). Refused with `approval_unavailable`
      unless the plugin is one `dev_fake.writable_plugins()` names and a sealer
      is configured (E4, E9); refused `refused_by_policy` for a payload the
      user declined earlier in this turn

Every refused call is still answered: the tools node emits exactly one
ToolMessage per tool_call_id, refusals and errors included.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph_agents.loop.errors import (
    APPROVAL_UNAVAILABLE, BUDGET, CANCELLED, INVALID_ARGS, REFUSED_BY_POLICY,
    TRANSIENT,
)
from langgraph_agents.loop.approval import payload_too_large
from langgraph_agents.loop.rate import get_rate_counter
from langgraph_agents.loop.tools.registry import ToolSpec


def _call_id(call: dict[str, Any] | Any) -> str:
    if isinstance(call, dict):
        return call.get("id", "")
    return getattr(call, "id", "")


def _call_name(call: dict[str, Any] | Any) -> str | None:
    if isinstance(call, dict):
        return call.get("name") or (call.get("function") or {}).get("name")
    return getattr(call, "name", None)


def _call_args(call: dict[str, Any] | Any) -> dict[str, Any]:
    if isinstance(call, dict):
        args = call.get("arguments")
        if args is None:
            args = call.get("args")
        if args is None:
            args = (call.get("function") or {}).get("arguments")
    else:
        args = getattr(call, "args", {}) or {}
        if not isinstance(args, dict):
            args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, ValueError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    return args


class GateDecision:
    __slots__ = ("kind", "message", "refusal")

    def __init__(self, kind: str, message: str = "", refusal: bool = False):
        self.kind = kind
        self.message = message
        self.refusal = refusal

    @classmethod
    def allow(cls) -> "GateDecision":
        return cls("allow")

    @classmethod
    def refuse(cls, kind: str, message: str) -> "GateDecision":
        return cls(kind, message, refusal=True)

    @classmethod
    def needs_approval(cls, message: str = "") -> "GateDecision":
        return cls("needs_approval", message)


def _validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """Own small validator: required keys, JSON types, enums, and no
    argument that the schema does not declare (every tool, GĐ2 review H1: what
    runs must be what the user can be shown).

    Returns an error message or None when the args are valid.
    """
    parameters = spec.parameters or {}
    required = parameters.get("required", []) or []
    properties = parameters.get("properties", {}) or {}

    for key in required:
        if key not in args:
            return f"missing required argument: {key}"

    for key, value in args.items():
        prop = properties.get(key)
        if prop is None:
            return f"unknown argument: {key}"
        expected = prop.get("type")
        if expected == "string" and not isinstance(value, str):
            return f"argument {key!r} must be a string"
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return f"argument {key!r} must be an integer"
        if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return f"argument {key!r} must be a number"
        if expected == "boolean" and not isinstance(value, bool):
            return f"argument {key!r} must be a boolean"
        if expected == "object" and not isinstance(value, dict):
            return f"argument {key!r} must be an object"
        if expected == "array" and not isinstance(value, list):
            return f"argument {key!r} must be an array"
        enum = prop.get("enum")
        if enum is not None and value not in enum:
            return f"argument {key!r} must be one of {enum}"
    return None


def _is_write(spec: ToolSpec) -> bool:
    return spec.risk == "write"


def _is_egress_or_clinical(spec: ToolSpec) -> bool:
    return spec.risk == "egress_read" or spec.clinical


def needs_verdict(spec: ToolSpec) -> bool:
    """Calls that wait for the guardrail verdict: everything but internal tools."""
    return _is_egress_or_clinical(spec) or _is_write(spec)


async def check(call: dict[str, Any], spec: ToolSpec, turn: Any) -> GateDecision:
    """Run the gate for one tool call. Returns a GateDecision."""

    # ── (1) unknown tool / bad arguments ──────────────────────────
    name = _call_name(call)
    if spec is None:
        return GateDecision.refuse(
            INVALID_ARGS,
            f"unknown tool: {name or '?'}",
        )
    args = _call_args(call)
    bad = _validate_args(spec, args)
    if bad:
        return GateDecision.refuse(INVALID_ARGS, bad)
    if _is_write(spec) and payload_too_large(args):
        return GateDecision.refuse(
            INVALID_ARGS, "payload too large for approval")

    # ── (1b) plugin lifecycle (stage 3) ───────────────────────────
    plugin_id = getattr(spec, "plugin_id", None)
    plugins = getattr(turn, "plugins", None)
    if plugin_id and plugins is not None and not plugins.is_active(plugin_id):
        return GateDecision.refuse(
            REFUSED_BY_POLICY, f"plugin {plugin_id} is off for this turn")

    # ── (2) cancel flag ───────────────────────────────────────────
    if getattr(turn, "cancelled", False):
        return GateDecision.refuse(CANCELLED, "turn cancelled")

    # ── (3) caps ──────────────────────────────────────────────────
    caps = turn.caps
    if turn.round_calls >= caps.max_calls_per_round:
        return GateDecision.refuse(
            BUDGET, f"round call cap ({caps.max_calls_per_round}) spent")
    if turn.turn_calls >= caps.max_calls_per_turn:
        return GateDecision.refuse(
            BUDGET, f"turn call cap ({caps.max_calls_per_turn}) spent")
    if _is_write(spec) and turn.round_writes >= caps.max_writes_per_round:
        return GateDecision.refuse(
            BUDGET, "one_action_per_round: a write call already ran this round")

    # ── (4) verdict: egress / clinical wait; non-routine → internal only ─
    verdict = getattr(turn, "verdict", None)
    level = getattr(verdict, "level", "routine") if verdict else "routine"
    if needs_verdict(spec) and level != "routine":
        return GateDecision.refuse(
            REFUSED_BY_POLICY,
            f"verdict is {level}: internal tools only this turn")

    # ── (5) taint ─────────────────────────────────────────────────
    # Rules 1 and 2 apply to egress_read and clinical calls. A write call is
    # not tested here: it goes to the approval card (step 9), and the card shows
    # the user every argument it will send. Rule 1 refuses with
    # approval_unavailable: no approval path covers an egress read yet.
    from langgraph_agents.loop.taint import TaintSnapshot, find_banned
    if any(
        s.get("data_class", "none") == "personal"
        for s in (turn.personal_sources or [])
    ) and _is_egress_or_clinical(spec):
        return GateDecision.refuse(
            APPROVAL_UNAVAILABLE,
            "rule 1: personal source → another destination needs approval")
    joined = json.dumps(args, ensure_ascii=False)
    banned = []
    if _is_egress_or_clinical(spec):
        snapshot = TaintSnapshot(
            banned_terms=list(turn.banned_terms or []),
            allowed_terms=list(turn.allowed_terms or []),
        )
        banned = find_banned(joined, snapshot)
    if banned:
        terms = ", ".join(f"'{t}'" for t in banned)
        return GateDecision.refuse(
            REFUSED_BY_POLICY,
            f"Your call includes the user's saved details: {terms}. "
            "Remove them and call again, or stop.")

    # ── (6) repeat-empty ──────────────────────────────────────────
    signature = f"{spec.name}|{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
    if signature in turn.repeat_empty_signatures:
        return GateDecision.refuse(
            "repeat_empty", "same tool with the same arguments already returned empty")

    # ── (7) rate limit → budget kind ──────────────────────────────
    # A6: rate-limit only egress_read and plugin tools. Internal tools
    # (kb_search, recall_self, load_skill later) skip the counter.
    if spec.risk == "egress_read" or spec.source == "plugin":
        bucket = f"plugin:{plugin_id}" if plugin_id else spec.name
        rate = get_rate_counter()
        allowed = await rate.hit(turn.user_id, bucket, caps.rate_limit, caps.rate_window_s)
        if not allowed:
            return GateDecision.refuse(BUDGET, "rate limit exceeded")

    # ── (8) breaker open ──────────────────────────────────────────
    # B2: check only the breaker of this call's source, not every breaker.
    if plugin_id:
        from langgraph_agents.loop.plugins.runtime import get_breaker
        breaker = get_breaker(plugin_id)
    else:
        breaker = (turn.breakers or {}).get(spec.source)
    if breaker is not None:
        try:
            if not breaker.allow():
                return GateDecision.refuse(
                    TRANSIENT, f"breaker open: {plugin_id or spec.source}")
        except Exception:
            pass

    # ── (9) write → needs_approval ────────────────────────────────
    if _is_write(spec):
        from langgraph_agents.loop.actions import payload_sha256
        from langgraph_agents.loop.crypto import get_sealer
        from langgraph_agents.loop.dev_fake import writable_plugins
        if not plugin_id or plugin_id not in writable_plugins():
            return GateDecision.refuse(
                APPROVAL_UNAVAILABLE,
                "write tools are not available yet")
        if payload_sha256(args) in getattr(turn, "declined_hashes", ()):
            return GateDecision.refuse(
                REFUSED_BY_POLICY,
                "the user declined this exact action earlier in this turn")
        if get_sealer() is None:
            return GateDecision.refuse(
                APPROVAL_UNAVAILABLE,
                "approvals are not available: no sealing key is configured")
        return GateDecision.needs_approval(
            "this action needs the user's approval")

    return GateDecision.allow()
