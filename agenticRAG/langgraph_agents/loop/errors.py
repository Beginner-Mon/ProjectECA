"""Error kinds, classification, and result normalisation for the loop.

The kinds of plan §10, plus `refused_by_policy`, `repeat_empty`,
`approval_unavailable` and `budget`.

`normalize(spec, raw, remaining) -> ToolResult`: empty results (through
`classify_tool_result` in `evidence.py`) become `{"found": false}`; the
source label is added; results are truncated to 8k per tool and 24k per
turn. Error detection is `'"error"' in content`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langgraph_agents.evidence import classify_tool_result
from langgraph_agents.guardrail.pipeline import run_tool_result
from langgraph_agents.guardrail.types import CheckContext
from langgraph_agents.nodes._errors import classify_error


# ── Kinds ─────────────────────────────────────────────────────────────────

# Kinds the gate and the nodes assign to a tool result or a refusal.
INVALID_ARGS = "invalid_args"
CANCELLED = "cancelled"
BUDGET = "budget"
REFUSED_BY_POLICY = "refused_by_policy"
REPEAT_EMPTY = "repeat_empty"
APPROVAL_UNAVAILABLE = "approval_unavailable"
TRANSIENT = "transient"

# Result kinds.
OK = "ok"
FAILED = "failed"
EMPTY = "empty"


def classify(exc: BaseException) -> dict:
    """`{"kind", "status"}` for any exception, reusing the pipeline mapper.

    `nodes/_errors.classify_error` maps `CircuitBreakerOpenError` →
    `{"kind": "unreachable"}` and everything else through
    `llm.classify_provider_error` (by exception name, never by importing
    `openai`). Unreachable / server errors become `transient`; auth errors
    become `auth_expired`.
    """
    return classify_error(exc)


# ── Normalisation ─────────────────────────────────────────────────────────


def is_external(spec: Any) -> bool:
    """True when the result carries text from outside ECA (stage 3).

    `egress_read` tools and anything that is not a builtin (a plugin or an MCP
    server) are external; `internal` builtins such as `kb_search` are not.
    """
    if getattr(spec, "risk", None) == "egress_read":
        return True
    return getattr(spec, "source", None) in ("plugin", "mcp")

_EMPTY_SET = {"", "[]", "{}", '{"found": false}'}


@dataclass
class ToolResult:
    ok: bool
    content: str
    kind: str  # OK | FAILED | EMPTY
    empty: bool
    source_label: str
    provenance: dict[str, Any]


async def normalize(spec: Any, raw: Any, remaining: int) -> ToolResult:
    """Turn a raw tool result into a `ToolResult`.

    `remaining` is the turn-level characters budget left (24k at turn start,
    decreasing). A result is capped at `min(8k per tool, remaining)`. Truncation
    is a plain slice (a sliced JSON document may be invalid — noted in PROGRESS).

    Async since stage 3: a result from an `egress_read` tool or a plugin (or MCP
    source) first goes through the guardrail `tool_result` stage, before the
    empty check and truncation. Internal tools skip it.
    """
    source_label = getattr(spec, "name", None) or getattr(spec, "source", None) or "tool"
    if isinstance(raw, str):
        content = raw
    else:
        try:
            content = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(raw)

    if is_external(spec):
        decision = await run_tool_result(CheckContext(
            message=content, tool=str(getattr(spec, "name", "") or "")))
        if decision.action == "block":
            return ToolResult(
                ok=False,
                content=json.dumps({"error": "blocked_by_guardrail",
                                    "reason": decision.message or ""},
                                   ensure_ascii=False),
                kind=FAILED, empty=False, source_label=source_label, provenance={})
        if decision.text is not None:
            content = decision.text

    # Empty detection: the empty set classifies as empty → {"found": false}.
    empty = classify_tool_result(content) == "empty"
    if empty:
        return ToolResult(
            ok=True, content='{"found": false}', kind=EMPTY, empty=True,
            source_label=source_label, provenance={},
        )

    # Error detection.
    if '"error"' in content:
        return ToolResult(
            ok=False, content=content, kind=FAILED, empty=False,
            source_label=source_label, provenance={},
        )

    # Truncation: 8k per tool, then the turn's remaining budget.
    cap = min(int(getattr(spec, "max_result_chars_per_tool", 8_000) or 8_000), remaining)
    if cap < 0:
        cap = 0
    if len(content) > cap:
        content = content[:cap]

    return ToolResult(
        ok=True, content=content, kind=OK, empty=False,
        source_label=source_label, provenance={},
    )
