"""Head, tail and composition — the three places a verdict becomes text.

The split is the whole point of the layer:

  head  emitted BEFORE the first body token, so the warning is the first thing
        the user sees. Level-specific: emergency gets the emergency line (in
        the character's words when the persona has one, otherwise the code
        default), soon gets the red-flag line.
  body  the agent's answer, untouched.
  tail  appended after the body, in the same order and with the same separator
        the grader's `_finish` used: referral, then disclaimer.

`safety_keys` is the single mapping from a verdict to the fixed lines. The rows
that also read planner tags are transitional and go away once the planner stops
emitting safety tags (Phase 4).
"""

from __future__ import annotations

from langgraph_agents.guardrail.types import Verdict
from langgraph_agents.tag_contract import get_safety_text

# Every key the guardrail can emit. `emergency_screen` is head-only (it has no
# tail position); the other three keep their old meanings.
SAFETY_KEYS = (
    "emergency_screen",
    "red_flag_screen",
    "referral_advice",
    "scope_disclaimer",
)

# Order matters only for the list returned by `safety_keys`.
_ORDER = ("emergency_screen", "red_flag_screen", "referral_advice", "scope_disclaimer")

# The tags a turn can ask for that describe WHAT the answer must contain. Any
# of them means the answer is health guidance, which carries the disclaimer.
_QUALITY_TAGS = frozenset({
    "exercise_protocol", "exercise_steps", "contraindication",
    "evidence_citation", "motion_descriptor",
})

# Planner safety tags kept as an input until Phase 4 removes them from the
# planner. After that this set and the transitional block below are dead.
_PLANNER_SAFETY_TAGS = frozenset({
    "red_flag_screen", "referral_advice", "scope_disclaimer",
})

_TAIL_ORDER = ("referral_advice", "scope_disclaimer")


def safety_keys(verdict: Verdict, planner_tags: list[str] | None = None) -> list[str]:
    """The fixed safety lines this turn carries."""
    tags = set(planner_tags or ())
    keys: set[str] = set()

    if verdict.level == "emergency":
        keys.update({"emergency_screen", "referral_advice"})
    elif verdict.level == "soon":
        keys.update({"red_flag_screen", "referral_advice"})

    if verdict.out_of_scope:
        keys.add("referral_advice")

    if verdict.health_topic or (tags & _QUALITY_TAGS) or verdict.level != "routine":
        keys.add("scope_disclaimer")

    # Transitional (until Phase 4): a safety tag the planner still emits keeps
    # its line, so the two sources never fight over the same turn.
    keys |= tags & _PLANNER_SAFETY_TAGS

    return [key for key in _ORDER if key in keys]


def head(verdict: Verdict, persona_id: str = "anne", locale: str = "en") -> str:
    """The warning shown before the answer, with its trailing blank line."""
    if verdict.level == "emergency":
        text = (get_safety_text("emergency_screen", persona_id, locale)
                or get_safety_text("red_flag_screen", persona_id, locale))
    elif verdict.level == "soon":
        text = get_safety_text("red_flag_screen", persona_id, locale)
    else:
        return ""
    return f"{text}\n\n" if text else ""


def tail(keys: list[str], persona_id: str = "anne", locale: str = "en") -> str:
    """The closing lines, each preceded by a blank line. "" when there are none."""
    parts = []
    for key in _TAIL_ORDER:
        if key not in keys:
            continue
        text = get_safety_text(key, persona_id, locale)
        if text:
            parts.append(f"\n\n{text}")
    return "".join(parts)


def compose(head_text: str, body: str, tail_text: str) -> str:
    """The text the user read, the session stores and TTS speaks.

    head and tail already carry their own separators, so this is concatenation
    and nothing else — one function so the three call sites cannot disagree.
    """
    return f"{head_text}{body}{tail_text}"
