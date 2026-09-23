"""Cap how much of a reply is spoken aloud (Owner decision 09-2026).

Companion to `voice.py`: that file holds the rule for "in WHICH voice",
this one holds the rule for "up to WHERE". The single choke point is
`api/main.py::_stream_speech`, shared by /chat and POST /tts — both call
sites get the same budget with no per-route logic.

Where the numbers come from (challenge these if the setup changes):

- ``SPOKEN_CHAR_LIMIT = 600`` — Owner decision, not a measurement. Above
  600 chars only the FIRST sentence is spoken.
- ``CHARS_PER_AUDIO_SECOND = 17.9`` — measured: the D6 probe text
  (2,255 chars) produced ~126s of audio (126 s viewer: 2255 / 126 ≈ 17.9).
- ``FIRST_SENTENCE_MAX = 400`` — a sub-cap so one pathological sentence
  cannot approach the Lambda 300s timeout on its own: 400 chars ≈ 22s of
  audio ≈ ~35s of synthesis at the measured 0.64–0.74× realtime rate.
- The synthesis rate itself (0.64–0.74× realtime, ratios 1.35–1.56) is
  deliberately NOT baked in here: it drifts with RAM, arch and model, and a
  stale server-side constant would silently under-buffer. The frontend
  measures the live rate per turn (speechSchedule.computeStartTime); the
  server only supplies ``estimated_audio_s`` (total expected length, which
  the frontend cannot know until the stream ends).

Override: ``TTS_MAX_SPOKEN_CHARS`` env, read at CALL time (not import) so
tests and per-process config can change it. Unparseable or non-positive
values fall back to the default instead of raising — a bad env var must
never take TTS down.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "SPOKEN_CHAR_LIMIT",
    "FIRST_SENTENCE_MAX",
    "CHARS_PER_AUDIO_SECOND",
    "SpokenPlan",
    "plan_spoken_text",
]

SPOKEN_CHAR_LIMIT = 600
FIRST_SENTENCE_MAX = 400
CHARS_PER_AUDIO_SECOND = 17.9

_ENV_LIMIT_NAME = "TTS_MAX_SPOKEN_CHARS"

# First-sentence terminators, checked in text order (earliest wins).
_SENTENCE_ENDS = (".", "!", "?", "…", "\n")


@dataclass(frozen=True)
class SpokenPlan:
    text: str              # the part actually sent to SpeechLLm
    truncated: bool        # True when the reply was longer than the budget
    estimated_audio_s: float  # len(text) / CHARS_PER_AUDIO_SECOND


def _limit() -> int:
    try:
        value = int(os.environ.get(_ENV_LIMIT_NAME, "") or SPOKEN_CHAR_LIMIT)
    except (TypeError, ValueError):
        return SPOKEN_CHAR_LIMIT
    return value if value > 0 else SPOKEN_CHAR_LIMIT


def _first_sentence(text: str) -> str:
    """Cut right after the earliest sentence terminator, if any."""
    positions = [i for t in _SENTENCE_ENDS if (i := text.find(t)) != -1]
    if not positions:
        return ""
    cut = min(positions)
    if text[cut] == "\n":
        return text[:cut]
    return text[: cut + 1]


def plan_spoken_text(text: str) -> SpokenPlan:
    """Decide how much of ``text`` gets spoken.

    At or under budget the string passes through UNTOUCHED — no strip, no
    normalization — so short replies are byte-identical to before this
    module existed. Over budget only the first sentence is kept (sub-capped
    at FIRST_SENTENCE_MAX), and the result is rstripped.
    """
    limit = _limit()
    if len(text) <= limit:
        return SpokenPlan(
            text=text,
            truncated=False,
            estimated_audio_s=len(text) / CHARS_PER_AUDIO_SECOND,
        )
    first = _first_sentence(text)
    if not first or len(first) > FIRST_SENTENCE_MAX:
        window = text[:FIRST_SENTENCE_MAX]
        space = max(window.rfind(" "), window.rfind("\t"))
        first = window[:space] if space != -1 else window
    spoken = first.rstrip()
    return SpokenPlan(
        text=spoken,
        truncated=True,
        estimated_audio_s=len(spoken) / CHARS_PER_AUDIO_SECOND,
    )
