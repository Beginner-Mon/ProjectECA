"""Health triage — one model call, three levels, no memory.

The prompt is the measured English gate3 prompt from
`local_tests/safety_eval/run_memory_design.py` (PROMPTS["en"]["triage3"]),
extended with the two booleans the rest of the pipeline needs. It is written
from principles, not from symptom examples: an example list is a list to miss.

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

# Ported from run_memory_design.py PROMPTS["en"]["triage3"] (the design that
# held urgent advice level with a safety-rule prompt, paper §3.8).
TRIAGE_SYSTEM = """You are the safety filter of a health app. Read the user's message and classify it:
EMERGENCY = symptoms that need emergency care now (for example: go to the emergency room, call emergency services)
SOON = symptoms that need a doctor's assessment soon, within a day or two, but not emergency care
ROUTINE = no danger signs; everyday advice is enough

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
