"""Split a reply into the sentences that are spoken, one TTS call each.

Companion to `voice.py`: that file holds the rule for "in WHICH voice",
this one holds the rule for "in what PIECES". The single choke point is
`api/main.py::_stream_speech`, shared by /chat and POST /tts — both call
sites get the same plan with no per-route logic.

Why sentences (Owner decision 28-09-2026, replacing the 600-char cap):
generation runs SLOWER than playback (0.64–0.74× realtime), so one call for
the whole reply forces the browser to sit in silence until enough audio is
buffered to play it gaplessly — ~0.56 × the reply's length, capped at 15 s.
One call per sentence lets the first sentence sound after its own short
buffer, and the unavoidable waiting lands BETWEEN sentences, where a pause
after a full stop sounds like speech rather than a stutter. The whole reply
is spoken; the old rule spoke only the first sentence of anything over
600 chars.

Where the numbers come from (challenge these if the setup changes):

- ``CHARS_PER_AUDIO_SECOND = 17.9`` — measured: the D6 probe text
  (2,255 chars) produced ~126s of audio (2255 / 126 ≈ 17.9).
- ``MIN_SEGMENT_CHARS = 40`` — about 2 s of audio. Shorter sentences
  ("Chào bạn.") are joined to the next, so the voice does not start with
  half a second of audio and then a pause.
- ``MAX_SEGMENT_CHARS = 400`` — one pathological sentence cannot approach
  the Lambda 300 s timeout on its own: 400 chars ≈ 22 s of audio ≈ ~35 s of
  synthesis. Longer ones are cut at a word boundary.

Optional cap: ``TTS_MAX_SPOKEN_CHARS`` env, read at CALL time. Unset (the
default) speaks everything. When set, whole segments are kept while they fit
(the first always is). Unparseable or non-positive values mean "no cap" — a
bad env var must never take TTS down.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "CHARS_PER_AUDIO_SECOND",
    "MAX_SEGMENT_CHARS",
    "MIN_SEGMENT_CHARS",
    "SpokenPlan",
    "SpokenSegment",
    "plan_spoken_text",
    "split_sentences",
]

CHARS_PER_AUDIO_SECOND = 17.9
MIN_SEGMENT_CHARS = 40
MAX_SEGMENT_CHARS = 400

_ENV_LIMIT_NAME = "TTS_MAX_SPOKEN_CHARS"

_SENTENCE_ENDS = ".!?…"
# Closing marks that belong to the sentence they follow: `Tốt lắm!"` ends
# after the quote, not before it.
_CLOSERS = "\"'”’)]»"


@dataclass(frozen=True)
class SpokenSegment:
    text: str
    estimated_audio_s: float  # len(text) / CHARS_PER_AUDIO_SECOND


@dataclass(frozen=True)
class SpokenPlan:
    segments: tuple[SpokenSegment, ...]
    truncated: bool  # True only when TTS_MAX_SPOKEN_CHARS dropped segments

    @property
    def spoken_chars(self) -> int:
        return sum(len(s.text) for s in self.segments)

    @property
    def estimated_audio_s(self) -> float:
        return sum(s.estimated_audio_s for s in self.segments)


def _limit() -> int | None:
    raw = os.environ.get(_ENV_LIMIT_NAME, "")
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _is_sentence_end(text: str, i: int) -> bool:
    """Is text[i] (a terminator) really the end of a sentence?

    A "." next to digits usually is not, and missing that is silent: the
    voice says "one dot" and pauses. Replies are exercise instructions, so
    both of these are routine:

    * a decimal — "2.5 phút": digit on both sides;
    * a list marker — "1. Khởi động…": only digits between the start of the
      line and the ".".

    "…tập 10 lần." still ends a sentence: a digit before, but not a decimal
    and not at the start of a line.
    """
    if text[i] != ".":
        return True
    before = text[i - 1] if i > 0 else ""
    after = text[i + 1] if i + 1 < len(text) else ""
    if before.isdigit() and after.isdigit():
        return False
    line_start = text.rfind("\n", 0, i) + 1
    head = text[line_start:i].strip()
    if head and head.isdigit():
        return False
    return True


def _raw_sentences(text: str) -> list[str]:
    """Cut after every real terminator (and after each line)."""
    out: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == "\n":
            out.append(text[start:i])
            start = i + 1
        elif char in _SENTENCE_ENDS and _is_sentence_end(text, i):
            # "?!", "..." and a closing quote stay with the sentence.
            j = i + 1
            while j < n and (text[j] in _SENTENCE_ENDS or text[j] in _CLOSERS):
                j += 1
            out.append(text[start:j])
            start = j
            i = j
            continue
        i += 1
    out.append(text[start:])
    return [s.strip() for s in out if s.strip()]


def _cut_long(sentence: str) -> list[str]:
    """Split a sentence longer than MAX_SEGMENT_CHARS at word boundaries."""
    pieces: list[str] = []
    rest = sentence
    while len(rest) > MAX_SEGMENT_CHARS:
        window = rest[:MAX_SEGMENT_CHARS]
        space = max(window.rfind(" "), window.rfind("\t"))
        cut = space if space > 0 else MAX_SEGMENT_CHARS
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return pieces


def split_sentences(text: str) -> list[str]:
    """The reply as the pieces it is spoken in, in order, nothing dropped.

    Short sentences are joined forward until a piece reaches
    MIN_SEGMENT_CHARS; a short LAST piece stays on its own (it is the end of
    the reply, nothing follows it to pause before). Over-long sentences are
    cut at word boundaries.
    """
    segments: list[str] = []
    pending = ""
    for sentence in _raw_sentences(text):
        pending = f"{pending} {sentence}" if pending else sentence
        if len(pending) >= MIN_SEGMENT_CHARS:
            segments.extend(_cut_long(pending))
            pending = ""
    if pending:
        segments.extend(_cut_long(pending))
    return segments


def plan_spoken_text(text: str) -> SpokenPlan:
    """Decide what gets spoken, and in which pieces. Everything, by default."""
    pieces = split_sentences(text)
    limit = _limit()
    truncated = False
    if limit is not None:
        kept: list[str] = []
        total = 0
        for piece in pieces:
            if kept and total + len(piece) > limit:
                truncated = True
                break
            kept.append(piece)
            total += len(piece)
        pieces = kept
    return SpokenPlan(
        segments=tuple(
            SpokenSegment(text=p, estimated_audio_s=len(p) / CHARS_PER_AUDIO_SECOND)
            for p in pieces
        ),
        truncated=truncated,
    )
