"""Which checks run, and in which stage.

This is the only file to touch when adding a check: write the module under
`checks/`, then add one entry here. Nothing else reads these lists by value —
`pipeline.run_input` resolves them at call time, so a test (or a later policy
variant) can replace the list without editing any other file.
"""

from __future__ import annotations

from langgraph_agents.guardrail.checks.base import Check
from langgraph_agents.guardrail.checks.health_triage import HealthTriage

# Before the graph runs. May annotate or block.
INPUT_CHECKS: list[Check] = [HealthTriage()]

# After the body exists, before it is persisted. May only append.
OUTPUT_TAIL_CHECKS: list[Check] = []
