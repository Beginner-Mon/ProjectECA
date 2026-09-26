"""Reply-driven emotion — the avatar's face follows the tone of each reply.

The synthesizer asks the model to open its reply with one tag,

    [emotion: sad 0.4]

which this module reads off the front of the stream and removes, so the tag
never reaches the text the user reads, the voice (TTS), the saved history, or
the grader. The emotion travels to the browser as its own SSE event instead
(api/main.py → `emotion`), where AvatarController.setEmotion applies it.

It rides on the existing synthesizer call — a handful of output tokens, no
extra LLM request — and it comes FIRST so the face changes as the reply starts.

Everything fails safe: no tag, a malformed tag, or an unknown emotion means no
event, and the reply streams exactly as it would have without this feature.
Disable the prompt instruction with VVA_REPLY_EMOTION=0.
"""

from __future__ import annotations

import os
import re

# What the model may choose. `angry` is deliberately absent: a health assistant
# never frowns at a patient (apply_emotion_policy maps it to neutral anyway).
EMOTIONS = ("neutral", "happy", "sad", "relaxed", "surprised")

DEFAULT_INTENSITY = 0.6

# Per-emotion ceilings so the face stays measured in a clinical context: sad at
# most reads as sympathy, not distress; nothing goes theatrical.
_INTENSITY_CAP = {"neutral": 1.0, "happy": 0.8, "sad": 0.5, "relaxed": 0.8, "surprised": 0.6}
_MIN_INTENSITY = 0.1

# Safety content is delivered with a neutral face, whatever the model picked.
_SAFETY_TAGS = {"red_flag_screen", "referral_advice"}

PROMPT_RULE = (
    "\n\n## Facial expression tag\n"
    "Begin your reply with exactly one tag, before anything else:\n"
    "[emotion: NAME INTENSITY]\n"
    "NAME is one of: neutral, happy, sad, relaxed, surprised. INTENSITY is 0.1 to 1.0.\n"
    "It sets your avatar's facial expression and is removed before the user sees "
    "the reply. Pick what matches the reply's tone: a warm greeting or good news "
    "-> happy; empathy for pain, worry or bad news -> sad 0.3-0.5 (gentle concern, "
    "not crying); calm guidance or exercise instructions -> relaxed; genuine "
    "surprise -> surprised; safety warnings or referrals -> neutral. "
    "Write the tag only once, at the very start."
)

# [emotion: happy 0.7] — tolerant of ':' or '=', ',' or space, any case, and
# surrounding whitespace, because that is how models actually write it.
_TAG_RE = re.compile(
    r"^\s*\[\s*emotion\s*[:=]\s*([a-z]+)\s*(?:[,\s]\s*(\d*\.?\d+))?\s*\]\s*",
    re.IGNORECASE,
)
_TAG_OPENING = "[emotion"
_MAX_TAG_LEN = 48  # longer than any real tag: past this, it is not one


def enabled() -> bool:
    return os.getenv("VVA_REPLY_EMOTION", "1") != "0"


def parse_emotion_tag(text: str) -> tuple[dict | None, str]:
    """Split a leading tag off `text`. Returns (emotion or None, remaining text).

    A well-formed tag is always removed, even when its emotion is unknown — the
    tag must never be shown. Text without a leading tag is returned untouched.
    """
    match = _TAG_RE.match(text)
    if not match:
        return None, text
    rest = text[match.end():]
    name = match.group(1).lower()
    if name not in EMOTIONS and name != "angry":
        return None, rest
    raw = match.group(2)
    try:
        intensity = float(raw) if raw else DEFAULT_INTENSITY
    except ValueError:
        intensity = DEFAULT_INTENSITY
    return {"name": name, "intensity": max(0.0, min(1.0, intensity))}, rest


class EmotionTagStream:
    """Incremental version of parse_emotion_tag for a token stream.

    Holds back only the first few characters, and only while they could still
    be the start of a tag, so text starts flowing the moment it is clearly not
    one (the first non-space character is not "[", or the bracket turns out to
    be "[1]" rather than "[emotion…").
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._decided = False
        # After a tag, the space/newline the model puts before the text may
        # arrive in the NEXT chunk; trim it until real text starts.
        self._trim_leading = False

    def feed(self, chunk: str) -> tuple[dict | None, str]:
        """Returns (emotion found in this chunk or None, text safe to show)."""
        if self._decided:
            if self._trim_leading:
                chunk = chunk.lstrip()
                if chunk:
                    self._trim_leading = False
            return None, chunk
        self._buffer += chunk
        head = self._buffer.lstrip()
        if not head:
            return None, ""  # only whitespace so far
        if "]" in head:
            return self._decide()
        lower = head.lower()
        could_be_tag = (
            (_TAG_OPENING.startswith(lower) or lower.startswith(_TAG_OPENING))
            and len(head) <= _MAX_TAG_LEN
        )
        if not could_be_tag:
            return self._decide()
        return None, ""  # still possibly a tag — hold

    def flush(self) -> tuple[dict | None, str]:
        """End of stream: release whatever is still held back."""
        if self._decided:
            return None, ""
        return self._decide()

    def _decide(self) -> tuple[dict | None, str]:
        self._decided = True
        had_tag = _TAG_RE.match(self._buffer) is not None
        emotion, rest = parse_emotion_tag(self._buffer)
        self._buffer = ""
        self._trim_leading = had_tag and not rest
        return emotion, rest


def apply_emotion_policy(emotion: dict | None, mode: str, required_outputs: list) -> dict | None:
    """Keep the face appropriate for a health assistant.

    - Refusals and safety content (red-flag screening, referrals) → neutral.
    - Angry → neutral: never frown at a patient.
    - Every emotion capped (sad ≤ 0.5 reads as sympathy, not distress).
    """
    if emotion is None:
        return None
    name = emotion["name"]
    if mode == "refuse" or _SAFETY_TAGS.intersection(required_outputs or []) or name == "angry":
        return {"name": "neutral", "intensity": 1.0}
    cap = _INTENSITY_CAP.get(name)
    if cap is None:
        return None
    intensity = max(_MIN_INTENSITY, min(cap, float(emotion.get("intensity", DEFAULT_INTENSITY))))
    return {"name": name, "intensity": round(intensity, 2)}
