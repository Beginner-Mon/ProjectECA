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
# Floor on what counts as the first sentence. Below this the "sentence" is a
# list marker, a decimal, or a heading — speaking it alone is worse than
# speaking nothing. ~40 chars is about 2 seconds of audio.
_MIN_FIRST_SENTENCE = 40
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
    """Cut right after the earliest terminator that actually ends a sentence.

    "Earliest terminator" alone is wrong, and wrong in a way that is silent:
    a reply opening with a numbered list ("1. Khoi dong khop vai...") has its
    first "." at index 1, so the spoken text became "1." — 0.1s of audio
    saying "one dot". A decimal ("tap 2.5 phut") did the same at index 13.
    Two guards, both cheap:

    * a "." preceded by a digit is a list marker or a decimal, never the end
      of a sentence in this domain (replies are exercise instructions);
    * a candidate shorter than _MIN_FIRST_SENTENCE is not a sentence worth
      speaking — keep looking. This also covers a heading line ending in a
      newline before the real first sentence.

    Order of preference, and the reason for it:

    1. the earliest terminator that clears both guards — a whole sentence,
       long enough to be worth hearing;
    2. failing that, the earliest terminator that clears the digit guard
       even if it is short ("Xin chào…" with no other sentence after it).
       A short whole sentence beats a mid-sentence cut: Owner's rule is
       never to stop the voice mid-sentence;
    3. "" — no terminator at all, so the caller cuts at a word boundary.
       This is the only path that can end mid-sentence, and only because
       there is no sentence to end.
    """
    fallback = ""
    for i, char in enumerate(text):
        if char not in _SENTENCE_ENDS:
            continue
        if char == "." and i > 0 and text[i - 1].isdigit():
            continue
        candidate = text[:i] if char == "\n" else text[: i + 1]
        if len(candidate.strip()) < _MIN_FIRST_SENTENCE:
            fallback = fallback or candidate
            continue
        return candidate
    return fallback


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
