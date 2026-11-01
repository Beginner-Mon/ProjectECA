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
