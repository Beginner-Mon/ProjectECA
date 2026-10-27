"""Guardrail layer — safety decisions made outside the agent graph.

The graph only *reads* a verdict; it never decides one. Data types live in
`types.py`, the checks and their policy in `checks/` + `policy.py`, and the
three things built from a verdict (the prompt directive, the head/tail lines,
and the handoff to the graph) in `directive.py`, `stream.py` and `handoff.py`.
"""

from langgraph_agents.guardrail.types import (
    CheckContext,
    Decision,
    InputResult,
    Verdict,
)

__all__ = ["CheckContext", "Decision", "InputResult", "Verdict"]
