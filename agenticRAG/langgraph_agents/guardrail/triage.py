"""Health triage — one model call, three levels, no memory.

The prompt was revised 27/10 (P4): the levels are defined by the cost of a
delay in care, ROUTINE applies only when the symptoms clearly fit ordinary
strain, and a tie goes to the more urgent level. It is written from
principles, not from symptom examples: an example list is a list to miss.

Measured on NIM `nvidia/nemotron-3.5-super-vl-120b-a12b` (thinking off), three
repeats on the pilot: urgent recall 24/26, L3 → emergency 8.7/10, false alarms
0/18.

What it is allowed to read is a safety property, not an implementation detail:
the current user message only. Memory, history and standing instructions never
reach this call, so "don't tell me to see a doctor" cannot walk the triage down
a level.

Failure policy (Tri 27/10): any failure — timeout, provider error, unparseable
output — returns the routine fallback verdict, and the turn is never blocked.
"""

from __future__ import annotations

import asyncio
import time

from pydantic import BaseModel

from langgraph_agents.guardrail.types import Level, Verdict, fallback_verdict
from langgraph_agents.llm import get_chat_model, role_timeout
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.guardrail")

# Revised 27/10 (P4): levels defined by the cost of delay, ROUTINE only for
# clearly ordinary strain, ties to the more urgent level. Kept verbatim from
# the measured prompt (scratchpad/P4_prompt.txt); do not reword without a run.
TRIAGE_SYSTEM = """You are the safety filter of a health and exercise app. Users often describe symptoms while asking for exercises or training advice. Judge only the symptoms in the message, not the request and not the tone.

Classify the message by what a delay in care could cost:
EMERGENCY = the symptoms could come from a condition that threatens life, or that causes permanent harm (lasting loss of strength, sensation, bladder or bowel control, or of a joint or limb) if care is delayed by even a few hours. The right advice is to go to an emergency department or call emergency services now.
SOON = the symptoms go beyond ordinary strain (for example signs of infection, an injury that may be a fracture or tear, nerve symptoms, or pain that is severe or getting worse fast) and need a clinician to examine the person within a day or two, but a delay of hours would not cause lasting harm.
ROUTINE = only when the symptoms clearly fit ordinary muscle or joint strain, soreness, stiffness or harmless noises, or there are no current symptoms at all. A problem that only needs a doctor if it lasts for weeks is ROUTINE. If the symptoms do not clearly fit ROUTINE, choose at least SOON.
When the symptoms fit more than one level, choose the more urgent one.

Also answer two questions about the same message:
- out_of_scope: true if the user asks for a diagnosis, a medication or dose, or a test interpretation.
- health_topic: true if the message is about the body, pain, health or exercise.

Answer with JSON only, no other text:
{"level": "EMERGENCY" | "SOON" | "ROUTINE", "out_of_scope": true | false, "health_topic": true | false}"""

_LEVELS: dict[str, Level] = {
    "emergency": "emergency",
    "soon": "soon",
    "routine": "routine",
}


class TriageOutput(BaseModel):
    level: str
    out_of_scope: bool = False
    health_topic: bool = False


def _level(raw: str) -> Level:
    """EMERGENCY / Soon / " emergency " all mean the same thing; anything else
    is a parse failure, not a silent promotion to routine."""
    key = str(raw or "").strip().lower()
    if key not in _LEVELS:
        raise ValueError(f"unknown triage level: {raw!r}")
    return _LEVELS[key]


async def triage(message: str, *, request_id: str = "-") -> Verdict:
    """Classify one user message. Never raises."""
    t0 = time.perf_counter()
    try:
        llm = get_chat_model("guardrail")
        # include_raw=True, like nodes/planner.py: a bare model in tests returns
        # the parsed object, the real chain returns {"raw", "parsed", ...}.
        structured = llm.with_structured_output(
            TriageOutput, method="json_mode", include_raw=True)
        # role_timeout bounds each read; DeepSeek sends keep-alive lines under
        # load and each one restarts that clock — wait_for bounds the call.
        raw = await asyncio.wait_for(
            structured.ainvoke([("system", TRIAGE_SYSTEM), ("user", message)]),
            timeout=role_timeout("guardrail"),
        )
        parsed = raw.get("parsed") if isinstance(raw, dict) else raw
        if parsed is None:
            raise ValueError("triage output did not parse")
        level = _level(parsed.level)
    except Exception as exc:                                   # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        logger.warning("guardrail_triage_failed", extra={
            "node": "guardrail", "request_id": request_id,
            "error": type(exc).__name__, "elapsed_ms": elapsed_ms,
        })
        return fallback_verdict(elapsed_ms)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    verdict = Verdict(
        level=level,
        out_of_scope=bool(parsed.out_of_scope),
        health_topic=bool(parsed.health_topic),
        source="llm",
        elapsed_ms=elapsed_ms,
    )
    logger.info("guardrail_triage", extra={
        "node": "guardrail", "request_id": request_id,
        "level": level, "source": "llm", "elapsed_ms": elapsed_ms,
    })
    return verdict
