"""The one interface every guardrail check implements.

Three stages, and the difference is what a check is allowed to do to the turn:

  input        runs before the graph. It may `annotate` (add to the verdict) or
               `block` (replace the answer with `Decision.message`). The first
               block wins; later checks still run, so a block never depends on
               registration order for its annotations.

  output_tail  runs after the graph has written the body. It can only append
               (`annotate`) — the body is already on the user's screen by then,
               so a check at this stage cannot occupy the turn.

  tool_result  runs on what an external tool returned (loop mode), before it
               is truncated and shown to the agent. `ctx.message` is the result
               text. A check may return `Decision.text` (the result with
               changes, e.g. hidden text stripped), annotate, or block.

Adding a check is one module plus one line in `policy.py`; nothing else reads
the check list.
"""

from __future__ import annotations

from typing import Literal, Protocol

from langgraph_agents.guardrail.types import CheckContext, Decision


class Check(Protocol):
    name: str
    stage: Literal["input", "output_tail", "tool_result"]

    async def run(self, ctx: CheckContext) -> Decision: ...
