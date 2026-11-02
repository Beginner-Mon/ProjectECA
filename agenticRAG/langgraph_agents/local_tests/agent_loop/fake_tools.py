"""Deterministic in-process tools with OpenAI tool schemas (probe only).

`bind_tools(state, scenario=None)` returns callables bound to a per-run
`state` dict — no network, no database; "memory" is the dict itself:

- ``find_exercise(condition)``          read; a fixed exercise for a named
  body part (knee / back / shoulder), else ``{"found": False}``
- ``get_free_slots(date)``              read; the fixed slots of a known date,
  else ``[]``
- ``create_reminder(title, date, time)`` write; records the call in
  ``state["reminders"]`` and returns ``{"ok": True, "id": "rN"}``

g0-probes additions (three more tools, same state discipline):

- ``kb_search(query)``                   read, internal; one fixed passage for
  knee / back / shoulder, else ``{"found": False}``
- ``web_search(query)``                  read; sends the query outside; fixed
  results — ``scenario["web_text"]`` decides the text (P10), else a fixed
  default
- ``read_inbox()``                       read; returns ``scenario["inbox"]``
  (default: no messages)

Scenario options, set per probe (in-process, deterministic):

- ```` {"flaky": {"tool": name}} ````        the named tool returns
  ``{"error": "transient: upstream timeout"}`` on its first call only
- ``{"slow": {"tool": name, "seconds": n}}`` the named tool sleeps n seconds
  before returning; the tool time is recorded per call (``elapsed_s`` in
  ``state["calls"]``) apart from the model latency
- ``{"timeout_after_write": True}``        ``create_reminder`` records the
  reminder, then returns ``{"error": "timeout: no response from calendar"}``

Every executed call is appended to ``state["calls"]`` (tool name, args, ok,
result, elapsed_s) so the tasks'/probes' checks and the runner can see what
ran and how long the tools took.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

EXERCISES: dict[str, str] = {
    "knee": "Wall Slide (quadriceps)",
    "back": "Bird Dog (spinal stability)",
    "shoulder": "External Rotation with Band (rotator cuff)",
}

SLOTS: dict[str, list[str]] = {
    "2026-11-02": ["07:00", "12:30", "17:45"],
    "2026-11-03": ["08:00", "10:15", "16:00"],
    "2026-11-04": ["06:30", "11:00", "15:30"],
    "2026-11-05": ["09:00", "14:00"],
}

KB_PASSAGES: dict[str, str] = {
    "knee": ("Knee osteoarthritis: start with closed-chain quadriceps work such as "
             "wall slides, once a day, pain-free range only; avoid deep lunges."),
    "back": ("Lower back pain: prefer neutral-spine stability work such as the bird "
             "dog, 2 sets of 8 per side; stop anything that radiates pain down a leg."),
    "shoulder": ("Rotator-cuff tendinitis: external rotation with a band, light "
                 "resistance, 2-3 sets of 12; no overhead pressing for now."),
}

DEFAULT_WEB_TEXT = ("Gentle knee exercises for beginners: wall slides, quad sets, "
                    "calf stretches, and seated knee bends.")

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

KB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "kb_search",
        "description": ("Search the clinic's own knowledge base (read-only, internal) "
                        "and return one evidence passage. Does not change anything."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "condition or topic, e.g. 'knee'"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": ("Search the public web (read-only) and return a few result "
                        "snippets. The query is sent outside this system; it does not "
                        "change anything."),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}

READ_INBOX_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_inbox",
        "description": ("Read the user's inbox messages (read-only). Does not change "
                        "anything."),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

# Stage 3C: stand-ins for the tools the DeepWiki plugin declares (names as the
# model sees them, D4). Dry runs and plugin-less runs use these; `--live
# --plugins deepwiki` replaces them with the real catalog entry.
def _deepwiki_schema(tool: str, description: str, props: dict[str, Any],
                     required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": f"deepwiki__{tool}",
            "description": description,
            "parameters": {"type": "object", "properties": props,
                           "required": required, "additionalProperties": False},
        },
    }


_REPO = {"type": "string", "description": "GitHub repository as owner/name"}
DEEPWIKI_SCHEMAS: list[dict[str, Any]] = [
    _deepwiki_schema("read_wiki_structure",
                     "List the documentation topics of a public GitHub repository.",
                     {"repoName": _REPO}, ["repoName"]),
    _deepwiki_schema("read_wiki_contents",
                     "Read the documentation of a public GitHub repository.",
                     {"repoName": _REPO}, ["repoName"]),
    _deepwiki_schema("ask_question",
                     "Ask a question about a public GitHub repository and get an "
                     "answer grounded in its documentation.",
                     {"repoName": _REPO, "question": {"type": "string"}},
                     ["repoName", "question"]),
]

SCHEMAS_BY_NAME: dict[str, dict[str, Any]] = {
    schema["function"]["name"]: schema
    for schema in [*TOOL_SCHEMAS, KB_SEARCH_SCHEMA, WEB_SEARCH_SCHEMA, READ_INBOX_SCHEMA,
                   *DEEPWIKI_SCHEMAS]
}


def schemas_for(names: list[str]) -> list[dict[str, Any]]:
    """Only the schemas a probe offers are sent, in the probe's order."""
    out: list[dict[str, Any]] = []
    for name in names:
        schema = SCHEMAS_BY_NAME.get(name)
        if schema is None:
            raise KeyError(f"unknown tool name: {name}")
        out.append(schema)
    return out


def _find_exercise(condition: str) -> dict[str, Any]:
    """Fix round 3: match by body part. A real KB search matches by meaning,
    so a whole-word match of the body part in the lowercased condition beats
    the old "key phrase inside the condition" test that failed 'knee
    osteoarthritis' (the model's natural first search). Wrist and anything
    else stay ``{"found": False}``. The result's ``condition`` field is the
    matched body part."""
    key = (condition or "").lower()
    for name, exercise in EXERCISES.items():
        if re.search(rf"\b{re.escape(name)}\b", key):
            return {"found": True, "condition": name, "exercise": exercise}
    return {"found": False}


def _get_free_slots(date: str) -> dict[str, Any]:
    return {"date": date, "slots": list(SLOTS.get(date, []))}


def bind_tools(state: dict[str, Any],
               scenario: dict[str, Any] | None = None,
               ) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Bind the six tools to `state` (write calls land in state["reminders"]).

    `scenario` (default None = all three scenario kinds off) carries the g0
    probe's per-run scenario options; without it the original three tools
    behave exactly as before.
    """
    scenario = scenario or {}
    state.setdefault("reminders", [])
    state.setdefault("calls", [])
    flaky_name = (scenario.get("flaky") or {}).get("tool")
    slow_spec = scenario.get("slow") or {}
    slow_name = slow_spec.get("tool")
    slow_seconds = float(slow_spec.get("seconds", 0.0))
    timeout_after_write = bool(scenario.get("timeout_after_write"))
    flaky_called: set[str] = set()

    def wrap(name: str, fn: Callable[..., dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def run(args: dict[str, Any]) -> dict[str, Any]:
            t0 = time.perf_counter()
            try:
                out = fn(**args)
            except Exception as exc:  # noqa: BLE001 - deterministic tools, but stay safe
                state["calls"].append({"tool": name, "args": dict(args), "ok": False,
                                       "error": str(exc),
                                       "elapsed_s": round(time.perf_counter() - t0, 6)})
                raise
            state["calls"].append({"tool": name, "args": dict(args), "ok": True,
                                   "result": out,
                                   "elapsed_s": round(time.perf_counter() - t0, 6)})
            return out

        return run

    def create_reminder(title: str, date: str, time: str) -> dict[str, Any]:
        # NOTE: the parameter stays named `time` (the model's tool schema binds
        # **args by name); nothing inside this body needs the time module.
        state["reminders"].append({"title": title, "date": date, "time": time})
        if timeout_after_write:
            return {"error": "timeout: no response from calendar"}
        return {"ok": True, "id": f"r{len(state['reminders'])}"}

    def kb_search(query: str) -> dict[str, Any]:
        key = (query or "").lower()
        for name_, passage in KB_PASSAGES.items():
            if name_ in key:
                return {"found": True, "topic": name_, "passage": passage}
        return {"found": False}

    web_text = scenario.get("web_text") or DEFAULT_WEB_TEXT

    def web_search(query: str) -> dict[str, Any]:
        return {"query": query,
                "results": [{"title": "Web results", "text": web_text}]}

    def read_inbox() -> dict[str, Any]:
        return {"messages": [dict(m) for m in scenario.get("inbox", [])]}

    def deepwiki_structure(repoName: str) -> dict[str, Any]:
        return {"repo": repoName, "topics": ["Overview", "Persistence and checkpointers",
                                             "Graph API"]}

    def deepwiki_contents(repoName: str) -> dict[str, Any]:
        return {"repo": repoName,
                "text": "Checkpointers save the graph state after each step so a run "
                        "can resume or be replayed."}

    def deepwiki_ask(repoName: str, question: str) -> dict[str, Any]:
        return {"repo": repoName, "question": question,
                "answer": "A checkpointer stores a snapshot of the graph state at every "
                          "step, keyed by thread id."}

    def scenario_layer(name: str, fn: Callable[..., dict[str, Any]]):
        # each layer captures the CURRENT inner by value (`inner = run`),
        # never the rebinding variable
        run = fn
        if flaky_name and name == flaky_name:
            inner = run

            def flaky_run(*a, **kw) -> dict[str, Any]:
                if name not in flaky_called:
                    flaky_called.add(name)
                    return {"error": "transient: upstream timeout"}
                return inner(*a, **kw)
            run = flaky_run
        if slow_name and name == slow_name:
            inner2 = run

            def slow_run(*a, **kw) -> dict[str, Any]:
                time.sleep(slow_seconds)
                return inner2(*a, **kw)
            run = slow_run
        return run

    direct = {
        "find_exercise": _find_exercise,
        "get_free_slots": _get_free_slots,
    }
    return {
        "find_exercise": wrap("find_exercise", scenario_layer("find_exercise", direct["find_exercise"])),
        "get_free_slots": wrap("get_free_slots", scenario_layer("get_free_slots", direct["get_free_slots"])),
        "create_reminder": wrap("create_reminder", scenario_layer("create_reminder", create_reminder)),
        "kb_search": wrap("kb_search", scenario_layer("kb_search", kb_search)),
        "web_search": wrap("web_search", scenario_layer("web_search", web_search)),
        "read_inbox": wrap("read_inbox", scenario_layer("read_inbox", read_inbox)),
        "deepwiki__read_wiki_structure": wrap(
            "deepwiki__read_wiki_structure",
            scenario_layer("deepwiki__read_wiki_structure", deepwiki_structure)),
        "deepwiki__read_wiki_contents": wrap(
            "deepwiki__read_wiki_contents",
            scenario_layer("deepwiki__read_wiki_contents", deepwiki_contents)),
        "deepwiki__ask_question": wrap(
            "deepwiki__ask_question",
            scenario_layer("deepwiki__ask_question", deepwiki_ask)),
    }
