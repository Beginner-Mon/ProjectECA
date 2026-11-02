"""Wiring for the read-only loop graph.

`build_loop_graph()` — plain sync (no MCP discovery).

    START ─┬─ resume=approve ─► execute ─► agent
           ├─ resume=decline ─► declined ─► agent
           └─ new turn ─► memory ─► route_entry ─► load_context ─► agent
                                  │              ▲      │
                                  └─┬─ vision ────┘      │
                                    └─ persona (error)   │
                                                         ▼
                                                    tools_read ─┘
                                                          │  └─ write call ─► propose ─► END
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
from langgraph_agents.loop.execute import declined_node, execute_node
from langgraph_agents.loop.persona import persona_node
from langgraph_agents.loop.propose import propose_node
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


def route_start(state: LoopState) -> str:
    """Where a run begins: a resumed turn goes straight to `execute` (approve)
    or `declined`; a new turn goes through `memory`. A resume never runs
    `propose`, `memory` or any read tool again."""
    resume = state.get("resume")
    if resume:
        return "execute" if resume.get("decision") == "approve" else "declined"
    return "memory"


def route_after_tools(state: LoopState) -> str:
    """After `tools_read`: a write call that passed the gate goes to `propose`."""
    return "propose" if state.get("pending_write") else "agent"


def route_after_propose(state: LoopState) -> str:
    """A card was shown (`approval` set) ends the run; a refused proposal goes
    back to the agent with the refusal as the call's answer."""
    return END if state.get("approval") else "agent"


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
    g.add_node("propose", propose_node)
    g.add_node("execute", execute_node)
    g.add_node("declined", declined_node)

    g.add_conditional_edges(START, route_start, {
        "memory": "memory",
        "execute": "execute",
        "declined": "declined",
    })
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
    g.add_conditional_edges("tools_read", route_after_tools, {
        "propose": "propose",
        "agent": "agent",
    })
    g.add_conditional_edges("propose", route_after_propose, {
        END: END,
        "agent": "agent",
    })
    g.add_edge("execute", "agent")
    g.add_edge("declined", "agent")
    g.add_edge("persona", END)

    return g.compile().with_config(recursion_limit=_RECURSION_LIMIT)
