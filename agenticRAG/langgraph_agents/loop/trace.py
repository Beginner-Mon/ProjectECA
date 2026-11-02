"""Logs and metrics for the read-only loop.

One `agent_loop_turn` log line per turn with the fields of plan §10, and
`request_id` everywhere. Never log payload text or tokens.
"""

from __future__ import annotations

from typing import Any

from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop")


def log_turn(request_id: str, fields: dict[str, Any]) -> None:
    """Emit the per-turn `agent_loop_turn` log line.

    Callers pass only non-text fields (counts, statuses, timings). Payload
    text and token counts are deliberately excluded.
    """
    logger.info("agent_loop_turn", extra={"request_id": request_id, **fields})


def log_plugin_call(request_id: str, *, plugin_id: str, tool: str, outcome: str,
                    duration_ms: int, result_chars: int, args_sha256: str) -> None:
    """One `agent_loop_plugin_call` audit line per plugin call (D9).

    Only identifiers, counts and a hash of the arguments. Never the argument
    text, the result text or a token.
    """
    logger.info("agent_loop_plugin_call", extra={
        "request_id": request_id, "plugin_id": plugin_id, "tool": tool,
        "outcome": outcome, "duration_ms": duration_ms,
        "result_chars": result_chars, "args_sha256": args_sha256,
    })
