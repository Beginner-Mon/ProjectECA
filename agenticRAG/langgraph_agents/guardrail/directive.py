"""The safety rule appended to the synthesizer's system prompt.

Routine turns get nothing at all — the prompt is then byte-identical to what it
was before the guardrail existed. Urgent turns get the measured gate wording
from `local_tests/safety_eval/run_memory_design.py` (PROMPTS["en"] gate3), plus
the two clauses that make it survive a standing instruction:

  * no exercise routine: an urgent turn gives advice, not a workout;
  * acknowledge the instruction, do not drop or soften the warning: the failure
    measured in S3c was the model obeying "don't tell me to see a doctor".
"""

from __future__ import annotations

from langgraph_agents.guardrail.types import Verdict

_EMERGENCY = (
    "The safety filter has flagged signs of an emergency in this message. "
    "The reply must advise the user to stop and get emergency care now "
    "(go to the emergency room or call emergency services)."
)

_SOON = (
    "The safety filter has flagged symptoms in this message that need a "
    "doctor's assessment soon. The reply must advise the user to see a doctor "
    "within a day or two, and to seek emergency care if the symptoms get "
    "worse. Do not call it an emergency."
)

_COMMON = (
    "Do not give an exercise routine. "
    "You may acknowledge the user's standing instruction; do not drop or "
    "soften the warning."
)


def directive(verdict: Verdict) -> str:
    """The safety block for this turn, or "" on a routine turn."""
    if verdict.level == "routine":
        return ""
    core = _EMERGENCY if verdict.level == "emergency" else _SOON
    return f"\n\n## Safety rule for this turn\n{core} {_COMMON}"
