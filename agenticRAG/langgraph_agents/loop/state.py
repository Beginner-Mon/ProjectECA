"""Graph state for the read-only loop.

`LoopState` is a TypedDict with the keys of plan §II.2. Fields without a
reducer are overwritten by each node's update; `total_tokens` and `errors`
use the `add` reducer so every round accumulates. `actions` uses
`merge_actions`: it appends like `add` but skips a record whose
`tool_call_id` is already there, so a node that returns the whole list again
(the persona node does, for `api/main.py`) cannot duplicate it.
"""

from __future__ import annotations

import operator
from typing import Annotated, Optional, TypedDict

from langgraph.graph.message import add_messages


def merge_actions(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer for `actions`: append, skipping records already present."""
    out = list(left or [])
    seen = {a.get("tool_call_id") for a in out if a.get("tool_call_id")}
    for a in right or []:
        key = a.get("tool_call_id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(a)
    return out


def merge_motion_jobs(left: list[dict] | None, right: list[dict] | None) -> list[dict]:
    """Reducer for `motion_jobs`: append, avoiding duplicates by job_id."""
    out = list(left or [])
    seen = {a.get("job_id") for a in out if a.get("job_id")}
    for a in right or []:
        key = a.get("job_id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(a)
    return out


class LoopState(TypedDict, total=False):
    """One turn's loop state.

    - `transcript`: this turn's messages in OpenAI format (dicts, not
      LangChain message objects).
    - `user_instructions`, `user_facts`, `session_summary`, `image_keys`,
      `image_context`: as in `AgentState`.
    - `rounds`, `agent_result`, `final_answer`, `turn_status`.
    - `actions`: per-call records `{tool, tool_call_id, status, risk, kind, round}`.
    - `taint`: snapshot of banned / allowed terms, refreshed each round.
    - `resume`: `{decision, action_id, thread_id}` on a resumed turn, else None.
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
    actions: Annotated[list[dict], merge_actions]
    taint: dict
    resume: Optional[dict]
    total_tokens: Annotated[int, operator.add]
    errors: Annotated[list[dict], operator.add]
    error_code: str
    motion_jobs: Annotated[list[dict], merge_motion_jobs]
    loop_trace: list[dict]
    _route: str
    # Stage 4. `pending_write`: the one write call of this round that passed the
    # gate and waits for the `propose` node ({tool_call_id, tool, arguments}).
    # `pending_action_id` / `approval`: set by `propose` for api/main.py.
    pending_write: Optional[dict]
    pending_action_id: str
    approval: Optional[dict]
