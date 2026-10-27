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
