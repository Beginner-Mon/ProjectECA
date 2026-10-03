"""Routing — M.2 HAI CỔNG ĐỘC LẬP (D2, D15).

Decisions encoded:
  D2:   Routing ⟸ needs_retrieval; grader ⟸ required_outputs (2 independent gates)
  D2b:  Manager says WHAT, dev chooses HOW — no tool flags in planner
  D15:  NEVER merge gates → safety bug (no-retrieval + safety tag → skip grader)
  D22:  Clarify = multi-turn, no loop in graph
  D24:  Retry ONLY for grader quality fail, NOT for empty results
"""

from langgraph_agents.state import AgentState, ErrorSeverity


def wants_motion(state: AgentState) -> bool:
    """Kimodo runs when the planner says the user asked to see a movement."""
    return "motion_descriptor" in (state.get("required_outputs") or [])


def check_errors(state: AgentState) -> str:
    """After each node: route to error_handler if CRITICAL error exists."""
    for err in state.get("errors", []):
        if err.get("severity") == ErrorSeverity.CRITICAL:
            return "error_handler"
    return "continue"


# ── After memory ──────────────────────────────────────────────────────────

def route_after_memory(state: AgentState) -> str:
    """Memory → planner (always, unless CRITICAL error)."""
    if check_errors(state) == "error_handler":
        return "error_handler"
    return "planner"


# ── After planner — TWO INDEPENDENT PATHS ─────────────────────────────────
# Path A: retriever gate (⟸ needs_retrieval)
# Path B: Kimodo gate (⟸ motion_descriptor tag, hard edge)
# Both can run in parallel (LangGraph fan-out)

def route_after_planner(state: AgentState) -> str:
    """Planner → retriever_agent | kimodo | synthesizer | error_handler.

    Cổng RETRIEVER ⟸ needs_retrieval (D2).
    Kimodo hard edge ⟸ motion_descriptor tag (D3, D26, S2).
    Does NOT read required_outputs — those are for the grader gate (D15).
    (Exception: the Kimodo edge reads the motion tag — routing, not grading.)

    Priority (single path — one conditional edge per node):
      1. CRITICAL error → error_handler
      2. needs_clarification → synthesizer (skip all)
      3. needs_retrieval → retriever_agent (may chain to kimodo after)
      4. motion tag (only) → kimodo
      5. neither → synthesizer (chat/greeting/safety-only)
    """
    if check_errors(state) == "error_handler":
        return "error_handler"

    if state.get("needs_clarification"):
        return "synthesizer"

    if state.get("needs_retrieval"):
        return "retriever_agent"

    if wants_motion(state):
        return "kimodo"

    return "synthesizer"


# ── After retriever ───────────────────────────────────────────────────────

MAX_RETRIEVER_ROUNDS = 2  # Hard cap: retriever_agent may run at most this many times


def route_after_retriever(state: AgentState) -> str:
    """Retriever → tools (more calls) | kimodo (motion tag) | synthesizer | error_handler.

    Hard cap (P2): uses state.retriever_rounds (incremented by retriever_agent_node each
    execution). If rounds >= MAX_RETRIEVER_ROUNDS, force → synthesizer regardless of
    pending tool_calls. This is a hard per-turn ceiling — it covers both normal loops and
    grader-triggered retries (simplest choice: counter is never reset mid-turn).
    After retrieval done: chain to kimodo on the motion tag (D26: motion after retrieval).
    """
    if check_errors(state) == "error_handler":
        return "error_handler"

    # Hard cap: if we've already hit the max rounds, skip to synthesizer
    retriever_rounds = state.get("retriever_rounds", 0)
    if retriever_rounds >= MAX_RETRIEVER_ROUNDS:
        if wants_motion(state):
            return "kimodo"
        return "synthesizer"

    from langchain_core.messages import AIMessage
    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    last_has_tool_calls = bool(
        last_msg and getattr(last_msg, "tool_calls", None)
    )

    if last_has_tool_calls:
        return "tools"

    # Retrieval done — chain to kimodo on the motion tag (D26)
    if wants_motion(state):
        return "kimodo"
    return "synthesizer"


# ── After synthesizer — GRADER GATE ───────────────────────────────────────
# Cổng GRADER ⟸ required_outputs != [] (D2, D15)
# Independent from retriever gate — MUST run even when retriever was skipped.
# This closes the safety bug: "đau ngực" (no retrieval, safety tag) still gets
# grader enforcement.

def route_after_synthesizer(state: AgentState) -> str:
    """Synthesizer → grader | END.

    Cổng GRADER ⟸ required_outputs != [] (D2, D15).
    Empty tags → fast-path END (D8: chat/general/clarify no contract).
    """
    if check_errors(state) == "error_handler":
        return "error_handler"

    required_outputs = state.get("required_outputs", [])
    if required_outputs:
        return "grader"
    return "end"


# ── After grader ──────────────────────────────────────────────────────────

def route_after_grader(state: AgentState) -> str:
    """Grader → retriever_agent (retry once) | END.

    Retry only for quality fails (D6: safety fails get template cứng, no retry).
    Retry count max 1 (D24).
    """
    result = state.get("grader_result", "pass")
    if result == "retry":
        return "retriever_agent"
    return "end"
