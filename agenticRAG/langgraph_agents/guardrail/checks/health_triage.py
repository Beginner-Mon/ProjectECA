"""Health triage — the only input check in v1.

It reads the current user message and nothing else, and returns the verdict as
an annotation. It never blocks: a message that needs a warning still gets an
answer, with the warning in front of it.
"""

from __future__ import annotations

from langgraph_agents.guardrail.checks.base import CheckContext
from langgraph_agents.guardrail.triage import triage
from langgraph_agents.guardrail.types import Decision


class HealthTriage:
    name = "health_triage"
    stage = "input"

    async def run(self, ctx: CheckContext) -> Decision:
        verdict = await triage(ctx.message, request_id=ctx.request_id)
        return Decision("annotate", {"verdict": verdict})
