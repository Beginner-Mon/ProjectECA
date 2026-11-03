"""Pausing and restoring a loop turn (stage 4).

At pause the turn's messages and what the persona still needs (the memory
text, the user's message, earlier action records) are sealed together in
`loop_turns.transcript_enc`, bound to {user_id, "loop_turn", thread_id}. The
plaintext `state_json` holds only routing facts: the action id, request
options, counters, the payload hashes the user declined, and the guardrail
verdict the turn runs under. No payload text and no memory text.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Optional

from langgraph_agents.loop.client import to_wire_messages
from langgraph_agents.loop.turns import open_transcript, seal_transcript

# State keys sealed with the messages.
CARRY_KEYS = ("user_instructions", "user_facts", "actions", "motion_jobs",
              "loop_trace", "personal_sources")


async def seal_turn(*, transcript: list, state: dict, query: str, user_id: str,
                    thread_id: str) -> bytes:
    carry = {k: state.get(k) for k in CARRY_KEYS}
    carry["query"] = query
    blob = {"messages": to_wire_messages(list(transcript)), "carry": carry}
    return await seal_transcript(blob, user_id=user_id, thread_id=thread_id)  # type: ignore[arg-type]


async def open_turn(blob: bytes, *, user_id: str, thread_id: str
                    ) -> tuple[list[dict], dict]:
    """`(messages, carry)`. A wrong user or thread fails with `SealError`."""
    data: Any = await open_transcript(blob, user_id=user_id, thread_id=thread_id)
    return list(data["messages"]), dict(data["carry"])


def verdict_to_json(verdict: Any) -> Optional[dict]:
    try:
        return asdict(verdict)
    except TypeError:
        return None


def verdict_from_json(data: Optional[dict]) -> Any:
    from langgraph_agents.guardrail.types import Verdict, fallback_verdict
    if not data:
        return fallback_verdict()
    try:
        return Verdict(**data)
    except TypeError:
        return fallback_verdict()
