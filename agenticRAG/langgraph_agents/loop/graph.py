"""Wiring for the read-only loop graph.

`build_loop_graph()` — plain sync (no MCP discovery).

    START ─► memory ─► route_entry ─► load_context ─► agent
                                  │              ▲      │
                                  └─┬─ vision ────┘      │
                                    └─ persona (error)   │
                                                         ▼
                                                    tools_read ─┘
                                                          │
                                                          ▼ (no more calls)
                                                        persona ─► END

`route_entry` wraps the pipeline's `route_after_memory` and maps its names:
`planner → load_context`, `error_handler → persona`, `vision → vision`
(the rare CRITICAL path; persona handles an empty `agent_result` with its
error fallback — noted in PROGRESS).

No checkpointer; recursion_limit 30.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, END, START

from langgraph_agents.loop.agent import agent_node
from langgraph_agents.loop.context import load_context_node
from langgraph_agents.loop.persona import persona_node
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.tools_node import tools_node
from langgraph_agents.routing import route_after_memory, check_errors
from langgraph_agents.nodes.memory import memory_node
from langgraph_agents.nodes.vision import vision_node
from langgraph_agents.shared.logging import get_logger

_logger = get_logger("langgraph.loop.graph")

_RECURSION_LIMIT = 30


def route_entry(state: LoopState) -> str:
    """After memory: where this turn goes next.

    Wraps `route_after_memory` and maps its pipeline names to loop nodes:
    planner → load_context, error_handler → persona, vision → vision.
    """
    outcome = route_after_memory(state)  # type: ignore[arg-type]
    mapping = {
        "vision": "vision",
        "planner": "load_context",
        "error_handler": "persona",
    }
    return mapping.get(outcome, "load_context")


def route_after_agent(state: LoopState) -> str:
    """After agent: tools_read (read calls pending) or persona (done).

    The agent node records its decision in the explicit `_route` field so the
    conditional edge does not have to re-inspect the transcript.
    """
    return state.get("_route", "persona")


def build_loop_graph(memory=None):
    """Build (sync) and compile the read-only loop graph. No checkpointer.

    `memory` is a probe and test hook: a node function used in place of the
    real `memory_node` (the probe runner seeds the user's facts with it and
    touches no database). Production calls `build_loop_graph()` with none.
    """
    g = StateGraph(LoopState)  # type: ignore[arg-type]

    g.add_node("memory", memory if memory is not None else memory_node)
    g.add_node("vision", vision_node)
    g.add_node("load_context", load_context_node)
    g.add_node("agent", agent_node)
    g.add_node("tools_read", tools_node)
    g.add_node("persona", persona_node)

    g.add_edge(START, "memory")
    g.add_conditional_edges("memory", route_entry, {
        "vision": "vision",
        "load_context": "load_context",
        "persona": "persona",
    })
    g.add_edge("vision", "load_context")
    g.add_edge("load_context", "agent")
    g.add_conditional_edges("agent", route_after_agent, {
        "tools_read": "tools_read",
        "persona": "persona",
    })
    g.add_edge("tools_read", "agent")
    g.add_edge("persona", END)

    return g.compile().with_config(recursion_limit=_RECURSION_LIMIT)
