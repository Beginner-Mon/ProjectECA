"""Read-only agent loop (stage 1, GĐ1).

A LangGraph sub-graph that runs the agent's internal tool loop inside one
turn: memory → load_context → agent ⇄ tools_read → persona → END.

`loop/` imports shared code. Shared code never imports `loop/`; the only
exceptions are `api/main.py` and `api/routes_loop.py` (PLAN.md II.1).
"""

from .graph import build_loop_graph

__all__ = ["build_loop_graph"]
