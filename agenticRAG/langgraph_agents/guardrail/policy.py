"""Which checks run, and in which stage.

This is the only file to touch when adding a check: write the module under
`checks/`, then add one entry here. Nothing else reads these lists by value —
`pipeline.run_input` resolves them at call time, so a test (or a later policy
variant) can replace the list without editing any other file.
"""

from __future__ import annotations

from langgraph_agents.guardrail.checks.base import Check
from langgraph_agents.guardrail.checks.health_triage import HealthTriage
from langgraph_agents.guardrail.checks.hidden_text import HiddenText

# Before the graph runs. May annotate or block.
INPUT_CHECKS: list[Check] = [HealthTriage()]

# After the body exists, before it is persisted. May only append.
OUTPUT_TAIL_CHECKS: list[Check] = []

# On what an external tool returned, before the agent sees it (loop mode).
# Run in order; each check gets the text the previous one returned.
TOOL_RESULT_CHECKS: list[Check] = [HiddenText()]
