"""Three deterministic in-process tools with OpenAI tool schemas (probe only).

`bind_tools(state)` returns the three callables bound to a per-run `state`
dict — no network, no database; "memory" is the dict itself:

- ``find_exercise(condition)``          read; a fixed exercise for a few known
  conditions, else ``{"found": False}``
- ``get_free_slots(date)``              read; the fixed slots of a known date,
  else ``[]``
- ``create_reminder(title, date, time)`` write; records the call in
  ``state["reminders"]`` and returns ``{"ok": True, "id": "rN"}``

Every executed call is appended to ``state["calls"]`` (tool name, args, ok,
result) so the tasks' code checks can see what ran.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

EXERCISES: dict[str, str] = {
    "knee pain": "Wall Slide (quadriceps)",
    "lower back pain": "Bird Dog (spinal stability)",
    "shoulder tendinitis": "External Rotation with Band (rotator cuff)",
}

SLOTS: dict[str, list[str]] = {
    "2026-11-02": ["07:00", "12:30", "17:45"],
    "2026-11-03": ["08:00", "10:15", "16:00"],
    "2026-11-04": ["06:30", "11:00", "15:30"],
    "2026-11-05": ["09:00", "14:00"],
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "find_exercise",
            "description": "Look up one physical-therapy exercise for a named condition.",
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string", "description": "e.g. 'knee pain'"},
                },
                "required": ["condition"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_free_slots",
            "description": "List the free time slots for one date (YYYY-MM-DD).",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["date"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Schedule one reminder that the user should do an exercise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": "string", "description": "HH:MM"},
                },
                "required": ["title", "date", "time"],
                "additionalProperties": False,
            },
        },
    },
]


def _find_exercise(condition: str) -> dict[str, Any]:
    key = (condition or "").lower()
    for name, exercise in EXERCISES.items():
        if name in key:
            return {"found": True, "condition": name, "exercise": exercise}
    return {"found": False}


def _get_free_slots(date: str) -> dict[str, Any]:
    return {"date": date, "slots": list(SLOTS.get(date, []))}


def bind_tools(state: dict[str, Any]) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Bind the three tools to `state` (write calls land in state["reminders"])."""
    state.setdefault("reminders", [])
    state.setdefault("calls", [])

    def wrap(name: str, fn: Callable[..., dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def run(args: dict[str, Any]) -> dict[str, Any]:
            try:
                out = fn(**args)
            except Exception as exc:  # noqa: BLE001 - deterministic tools, but stay safe
                state["calls"].append({"tool": name, "args": dict(args), "ok": False, "error": str(exc)})
                raise
            state["calls"].append({"tool": name, "args": dict(args), "ok": True, "result": out})
            return out

        return run

    def create_reminder(title: str, date: str, time: str) -> dict[str, Any]:
        state["reminders"].append({"title": title, "date": date, "time": time})
        return {"ok": True, "id": f"r{len(state['reminders'])}"}

    return {
        "find_exercise": wrap("find_exercise", _find_exercise),
        "get_free_slots": wrap("get_free_slots", _get_free_slots),
        "create_reminder": wrap("create_reminder", create_reminder),
    }
