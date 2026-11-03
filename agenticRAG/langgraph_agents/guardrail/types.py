"""Data types for the guardrail layer.

Data only: nothing here calls a model, reads the database or knows about the
graph. A `Verdict` is what the rest of the turn reads; a `Decision` is what one
check returns; a `CheckContext` is what one check is given.
"""

from __future__ import annotations

from dataclasses import asdict as _asdict
from dataclasses import dataclass, field
from typing import Literal

# Three levels, deliberately: EMERGENCY needs care now, SOON needs a doctor
# within a day or two, ROUTINE needs nothing beyond the answer itself. A
# two-level gate could not tell "call 115" from "see a doctor this week", and
# the warning line differs for the two.
Level = Literal["emergency", "soon", "routine"]
Action = Literal["allow", "annotate", "block"]


@dataclass(frozen=True)
class Verdict:
    """What the guardrail decided about one user message.

    `source` says whether a model produced this or a failure did: only
    `"fallback"` verdicts should ever be routine for an urgent message, and the
    monitoring filter counts them by that field. `elapsed_ms` is the triage
    call's own time, so a p90 alarm can point at one place.
    """

    level: Level
    out_of_scope: bool
    health_topic: bool
    source: Literal["llm", "fallback"]
    elapsed_ms: int

    def asdict(self) -> dict:
        return _asdict(self)


@dataclass(frozen=True)
class Decision:
    """One check's answer.

    `allow`    — nothing to say.
    `annotate` — add `annotations` (the triage verdict travels this way).
    `block`    — stop the turn and send `message` instead of an answer. Only an
                 input-stage check can block; an output-tail check can append.

    `text` is used by the `tool_result` stage only: when set, it is the tool
    result with the check's changes applied, and replaces what the check was
    given. Input and output_tail checks leave it `None`.
    """

    action: Action
    annotations: dict = field(default_factory=dict)
    message: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class CheckContext:
    """Everything a check is allowed to know about the turn.

    `message` is the user's own words and nothing else: triage never sees
    memory, history or standing instructions (Tri 27/10). A check that later
    needs more has to be given it here, deliberately.
    """

    message: str
    locale: str = "en"
    persona_id: str = "anne"
    user_id: str | None = None
    request_id: str = "-"
    # `tool_result` stage only: `message` then holds the tool's result text
    # (not the user's words), and `tool` names where it came from.
    tool: str = ""
    # Maximum characters for the tool result (including any wrapper).
    # Used by `tool_result` checks that wrap the text to ensure the wrapper
    # survives truncation.
    max_chars: int | None = None


@dataclass(frozen=True)
class InputResult:
    """The whole input stage: one verdict, plus a block if a check asked for one."""

    verdict: Verdict
    blocked: Decision | None = None


def fallback_verdict(elapsed_ms: int = 0) -> Verdict:
    """The verdict of a check that never produced one: routine, nothing added.

    Triage-failure policy (Tri 27/10): the turn is never blocked on a failed
    guardrail, so failure has to mean "nothing to add". `source="fallback"` is
    what separates this from a model that genuinely read the message.
    """
    return Verdict(
        level="routine",
        out_of_scope=False,
        health_topic=False,
        source="fallback",
        elapsed_ms=elapsed_ms,
    )
