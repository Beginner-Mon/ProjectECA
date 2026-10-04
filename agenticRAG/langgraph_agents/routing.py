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


def _last_ai_index(messages: list) -> int | None:
    from langchain_core.messages import AIMessage
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], AIMessage):
            return i
    return None


def failed_tool_names(state: AgentState) -> list[str]:
    """Tên các tool trả lỗi ở vòng retriever vừa xong."""
    from langchain_core.messages import ToolMessage
    from langgraph_agents.evidence import classify_tool_result

    messages = state.get("messages", [])
    idx = _last_ai_index(messages)
    if idx is None:
        return []
    return [m.name or "?" for m in messages[idx + 1:]
            if isinstance(m, ToolMessage)
            and classify_tool_result(str(m.content)) == "error"]


def retrieval_fault(state: AgentState) -> str | None:
    """Lỗi của vòng retriever vừa xong: "no_tool_called" | "tool_error" | None.

    AIMessage cuối trong state là của retriever: synthesizer không ghi vào messages.
    Kết quả rỗng không phải lỗi (D24).
    """
    messages = state.get("messages", [])
    idx = _last_ai_index(messages)
    if idx is None:
        return None
    if not getattr(messages[idx], "tool_calls", None):
        return "no_tool_called"
    if failed_tool_names(state):
        return "tool_error"
    return None


def _after_retrieval(state: AgentState) -> str:
    return "kimodo" if wants_motion(state) else "synthesizer"


def route_after_retriever(state: AgentState) -> str:
    """Retriever → tools | retriever_agent (không gọi tool, tối đa 1 lần quay lại)
    | kimodo | synthesizer | error_handler."""
    if check_errors(state) == "error_handler":
        return "error_handler"
    messages = state.get("messages", [])
    last_msg = messages[-1] if messages else None
    if last_msg is not None and getattr(last_msg, "tool_calls", None):
        return "tools"
    if (retrieval_fault(state) == "no_tool_called"
            and state.get("retriever_rounds", 0) < MAX_RETRIEVER_ROUNDS):
        return "retriever_agent"
    return _after_retrieval(state)


def route_after_tools(state: AgentState) -> str:
    """Tools → retriever_agent (tool lỗi, tối đa 1 lần quay lại) | kimodo | synthesizer
    | error_handler."""
    if check_errors(state) == "error_handler":
        return "error_handler"
    if (retrieval_fault(state) == "tool_error"
            and state.get("retriever_rounds", 0) < MAX_RETRIEVER_ROUNDS):
        return "retriever_agent"
    return _after_retrieval(state)


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
    """Grader → synthesizer (viết thêm phần thiếu, một lần) | END."""
    result = state.get("grader_result", "pass")
    if result == "retry":
        return "synthesizer"
    return "end"
