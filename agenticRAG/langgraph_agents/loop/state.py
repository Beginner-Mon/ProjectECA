"""Graph state for the read-only loop.

`LoopState` is a TypedDict with the keys of plan §II.2. Fields without a
reducer are overwritten by each node's update; `total_tokens` and `errors`
use the `add` reducer so every round accumulates.
"""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


class LoopState(TypedDict, total=False):
    """One turn's loop state.

    - `transcript`: this turn's messages in OpenAI format (dicts, not
      LangChain message objects).
    - `user_instructions`, `user_facts`, `session_summary`, `image_keys`,
      `image_context`: as in `AgentState`.
    - `rounds`, `agent_result`, `final_answer`, `turn_status`.
    - `actions`: per-call records `{tool, tool_call_id, status, risk, kind, round}`.
    - `taint`: snapshot of banned / allowed terms, refreshed each round.
    - `resume`: stage-4 placeholder, None in stage 1.
    - `total_tokens`, `errors`, `error_code`: accumulators.
    - `motion_jobs`: motion jobs enqueued this turn (added in stage 1 — small
      deviation from the plan's state list, needed to deliver the `motion`
      event like the pipeline's `kimodo` branch).
    - `loop_trace`: per-turn trace records for `meta["loop"]` and for the
      synthetic load_context pair of later turns.
    """

    transcript: Annotated[list[dict], add_messages]
    user_instructions: list[str]
    user_facts: list[str]
    session_summary: list[str]
    image_keys: list[str]
    image_context: Optional[list[dict]]
    rounds: int
    agent_result: str
    final_answer: str
    turn_status: str
    actions: list[dict]
    taint: dict
    resume: Optional[dict]
    total_tokens: Annotated[int, operator.add]
    errors: Annotated[list[dict], operator.add]
    error_code: str
    motion_jobs: list[dict]
    loop_trace: list[dict]
    _route: str
