"""The input stage: run every input check, fold their answers into one result.

Two properties matter here and both are deliberate:

  * one failing check cannot fail the turn. Exceptions are logged as
    `guardrail_check_failed` and treated as `allow`.
  * a block does not cancel the other checks. They are already running in
    parallel; the first block wins the routing decision, and every annotation
    still lands in the verdict.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from langgraph_agents.guardrail import policy
from langgraph_agents.guardrail.types import (
    CheckContext,
    Decision,
    InputResult,
    Verdict,
    fallback_verdict,
)
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.guardrail")


def _merge(checks, results, request_id: str) -> InputResult:
    verdict: Verdict | None = None
    blocked: Decision | None = None
    for check, result in zip(checks, results):
        if isinstance(result, BaseException):
            logger.warning("guardrail_check_failed", extra={
                "node": "guardrail", "request_id": request_id,
                "check": check.name, "error": type(result).__name__,
            })
            continue
        if result is None:
            continue
        if result.action == "block" and blocked is None:
            blocked = result
        candidate = result.annotations.get("verdict")
        if isinstance(candidate, Verdict):
            verdict = candidate
    return InputResult(verdict or fallback_verdict(), blocked)


async def run_input(ctx: CheckContext) -> InputResult:
    """Run the input checks for one turn. Never raises."""
    checks = list(policy.INPUT_CHECKS)
    if not checks:
        return InputResult(fallback_verdict())
    results = await asyncio.gather(
        *(check.run(ctx) for check in checks), return_exceptions=True)
    return _merge(checks, results, ctx.request_id)


async def run_tool_result(ctx: CheckContext) -> Decision:
    """Run the `tool_result` checks over one tool result. Never raises.

    `ctx.message` is the result text. Checks run in order and each sees the text
    the previous one returned. The answer is one `Decision`: `block` as soon as
    a check blocks; otherwise `annotate` with the final `text` if anything
    changed, else `allow`.

    A check that raises FAILS CLOSED here (unlike the input stage, which logs
    and skips): the text is untrusted and from outside ECA, so the result is
    `block` with `{"check_failed": <name>}` and the caller drops the result.
    Skipping would hand the agent the unstripped text.
    """
    text = ctx.message
    annotations: dict = {}
    changed = False
    for check in list(policy.TOOL_RESULT_CHECKS):
        try:
            result = await check.run(replace(ctx, message=text))
        except Exception as exc:  # noqa: BLE001 - fail closed on untrusted text
            logger.warning("guardrail_check_failed", extra={
                "node": "guardrail", "request_id": ctx.request_id,
                "check": check.name, "error": type(exc).__name__,
                "stage": "tool_result",
            })
            return Decision("block", {"check_failed": check.name},
                            message="a safety check could not run on this result")
        if result is None:
            continue
        if result.action == "block":
            return Decision("block", {**annotations, **result.annotations},
                            message=result.message)
        annotations.update(result.annotations)
        if result.text is not None and result.text != text:
            text, changed = result.text, True
    if changed:
        return Decision("annotate", annotations, text=text)
    if annotations:
        return Decision("annotate", annotations)
    return Decision("allow")
