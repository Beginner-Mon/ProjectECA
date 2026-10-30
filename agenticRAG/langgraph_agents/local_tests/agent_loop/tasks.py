"""Six English probe tasks and their deterministic checks (probe only).

Each task carries:
    prompt           the user text sent to the model
    checks
      expected_calls ordered subsequence the executed call log must contain;
                     every entry: {"tool", "args_contains" (substring, per arg,
                     case-insensitive)}. Extra calls are allowed except where
                     `forbid_calls` says otherwise.
      forbid_calls    tool names that must never be executed; "*" = any tool
      forbid_facts    substrings that must NOT appear in the final answer
                      (fix round 2: structural, wording-independent)
      facts           substrings the FINAL answer must contain (case-insensitive)
      facts_any       at least one of these substrings must be in the answer

F5 (fix round 1): comparisons run through `normalize_time_tokens` first, so
`07:00` = `7:00` = `7:00 AM` = `7 am` and `14:00` = `2 pm`; answer-fact and
call-argument matching both use it.

`check_task` returns None on pass, or the name of the failed check:
    expected_calls | forbid_calls | facts | facts_any | no_answer
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_MER = r"(?:a\.?m\.?|p\.?m\.?)"
# 7:00 / 7:00 AM / 14:00 — the meridiem is optional and consumed when present
_COLON_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(\s*" + _MER + r")?", re.IGNORECASE)
# 7 am / 2 pm (no colon) — applied second, after the colon form, so "7:00 am"
# is matched whole by the first pattern
_BARE_MER_TIME = re.compile(r"\b(\d{1,2})\s*(" + _MER + r")\b", re.IGNORECASE)


def _hour24(hour: int, mer: str) -> int:
    mer = mer.strip().replace(".", "").lower()
    if mer.startswith("p") and hour < 12:
        hour += 12
    elif mer.startswith("a") and hour == 12:
        hour = 0
    return hour


def _colon_sub(match: re.Match[str]) -> str:
    hour = int(match.group(1))
    minute = match.group(2)
    if match.group(3):
        hour = _hour24(hour, match.group(3))
    return f"{hour:02d}:{minute}"


def _bare_sub(match: re.Match[str]) -> str:
    return f"{_hour24(int(match.group(1)), match.group(2)):02d}:00"


def normalize_time_tokens(text: str) -> str:
    """Canonical form for time tokens: `7:00 AM` -> `07:00`, `2 pm` -> `14:00`.

    Non-time text passes through untouched (dates have no colon+two digits).
    """
    text = _COLON_TIME.sub(_colon_sub, text)
    return _BARE_MER_TIME.sub(_bare_sub, text)


_DASH_MAP = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014"})
_SPACE_MAP = str.maketrans({c: " " for c in "\u00a0\u202f\u2009"})


def normalize_check_text(text: str) -> str:
    """Fix round 3 item 1: Unicode NFKC, then map the dash family
    ‐ ‑ ‒ – — to '-' and the no-break/thin space family to a plain
    space, BEFORE the time normalization.

    Real matrix-v1 output: `Bird\u202fDog` (narrow no-break space), dates
    like `2026\u201111\u201103` (non-breaking hyphen) — both defeated plain
    substring matching although the answer was correct.
    """
    text = unicodedata.normalize("NFKC", text).translate(_DASH_MAP)
    return text.translate(_SPACE_MAP)


def _norm(value: str | None) -> str:
    # fix round 3: the answer text is normalized in the order lower ->
    # NFKC + dash/space map -> time tokens, so `Bird Dog` matches
    # "bird dog" and `07:00` = `7 am` as before.
    return normalize_time_tokens(normalize_check_text((value or "").lower()))


def _has(fact: str, text: str | None) -> bool:
    return _norm(fact) in _norm(text)


def _args_match(expected_args: dict[str, str], actual: dict[str, Any]) -> bool:
    return all(_has(sub, str(actual.get(key, ""))) for key, sub in expected_args.items())


def check_task(task: dict[str, Any], call_log: list[dict[str, Any]], answer: str | None) -> str | None:
    checks = task.get("checks", {})
    calls = [(c.get("tool", ""), c.get("args") or {}) for c in call_log]

    for tool in checks.get("forbid_calls", []):
        actual_tools = [name for name, _ in calls]
        if tool == "*" and actual_tools:
            return "forbid_calls"
        if tool != "*" and any(name == tool for name in actual_tools):
            return "forbid_calls"

    j = 0
    for exp in checks.get("expected_calls", []):
        matched = False
        while j < len(calls):
            name, args = calls[j]
            j += 1
            if name == exp["tool"] and _args_match(exp.get("args_contains", {}), args):
                matched = True
                break
        if not matched:
            return "expected_calls"

    answer = answer or ""
    for fact in checks.get("forbid_facts", []):
        if _has(fact, answer):
            return "forbid_facts"
    for fact in checks.get("facts", []):
        if not _has(fact, answer):
            return "facts"
    for fact in checks.get("facts_any", []):
        if _has(fact, answer):
            return None
    if checks.get("facts_any"):
        return "facts_any"
    return None


def task_by_id(task_id: str) -> dict[str, Any]:
    for task in TASKS:
        if task["id"] == task_id:
            return task
    raise KeyError(f"unknown task id: {task_id}")


TASKS: list[dict[str, Any]] = [
    {
        "id": "knee",
        "needs_rounds": True,
        "prompt": ("My knee hurts after running. Find an exercise for my knee and set a "
                   "reminder so I do it at the first free slot on 2026-11-02."),
        "checks": {
            "expected_calls": [
                {"tool": "find_exercise", "args_contains": {"condition": "knee"}},
                {"tool": "get_free_slots", "args_contains": {"date": "2026-11-02"}},
                {"tool": "create_reminder",
                 "args_contains": {"date": "2026-11-02", "time": "07:00", "title": "wall slide"}},
            ],
            "facts": ["wall slide", "07:00"],
        },
    },
    {
        "id": "back",
        "needs_rounds": True,
        "prompt": ("Find a strengthening exercise for lower back pain and remind me to do "
                   "it at the first free slot on 2026-11-03."),
        "checks": {
            "expected_calls": [
                {"tool": "find_exercise", "args_contains": {"condition": "back"}},
                {"tool": "get_free_slots", "args_contains": {"date": "2026-11-03"}},
                {"tool": "create_reminder",
                 "args_contains": {"date": "2026-11-03", "time": "08:00", "title": "bird dog"}},
            ],
            "facts": ["bird dog", "08:00"],
        },
    },
    {
        "id": "shoulder",
        "needs_rounds": True,
        "prompt": ("Does your catalog have an exercise for shoulder tendinitis? If you find "
                   "one, set a reminder for me at the first free slot on 2026-11-04."),
        "checks": {
            "expected_calls": [
                {"tool": "find_exercise", "args_contains": {"condition": "shoulder"}},
                {"tool": "get_free_slots", "args_contains": {"date": "2026-11-04"}},
                {"tool": "create_reminder",
                 "args_contains": {"date": "2026-11-04", "time": "06:30", "title": "external rotation"}},
            ],
            "facts": ["external rotation", "06:30"],
        },
    },
    {
        "id": "wrist",
        "needs_rounds": True,
        "prompt": ("My wrist sprain is getting worse. Find an exercise for it and set a "
                   "reminder for me at the first free slot on 2026-11-02."),
        "checks": {
            "expected_calls": [
                {"tool": "find_exercise", "args_contains": {"condition": "wrist"}},
            ],
            "forbid_calls": ["create_reminder"],
            # fix round 2: structural — the fake tool has no wrist exercise, so a
            # correct turn looks up the condition, never writes, and the answer
            # names none of the catalog's exercises (wording-independent).
            "forbid_facts": ["wall slide", "bird dog", "external rotation"],
        },
    },
    {
        "id": "slots",
        "needs_rounds": True,
        "prompt": "What are my free slots on 2026-11-05? List them.",
        "checks": {
            "expected_calls": [
                {"tool": "get_free_slots", "args_contains": {"date": "2026-11-05"}},
            ],
            "facts": ["09:00", "14:00"],
        },
    },
    {
        "id": "noweek",
        "needs_rounds": False,
        "prompt": ("Without looking anything up: in two or three sentences, how often per "
                   "week should a beginner repeat the same strength exercise, and why?"),
        "checks": {
            "expected_calls": [],
            "forbid_calls": ["*"],
            "facts_any": ["week"],
        },
    },
]
