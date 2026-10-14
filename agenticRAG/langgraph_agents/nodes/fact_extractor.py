"""Fact extraction (memory v2, C1b): what the user said about themselves, kept across conversations.

After a turn, `extract_user_facts` reads the messages of a conversation that
have not been read yet, shows them to the `memory` role together with the facts
already stored, and applies the operations it answers with (ADD, UPDATE,
DELETE) to `user_memory` (migration 015). The memory node loads those rows on
every turn, so a wrong or invented fact is acted on in later conversations.
That is why the model's answer goes through `enforce` before anything is
written, and why a fact the user typed in is never changed here.

The prompt, the layout of the user message and the checks are the ones measured
in docs/tracking/memory-probe-V0.md. local_tests/run_memory_probe.py imports
them from here, so what is measured is what runs.

Not a graph node: `housekeep` (nodes/summarizer.py) calls it as its last step.
The whole feature sits behind `enabled()`.

Nothing here logs a fact, a message or the model's answer. They are health
data and the logs are kept for 90 days, so a log line carries ids, counts and
the type of an error, never its message.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from langgraph_agents.llm import get_chat_model
from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.fact_extractor")


# ── The switches ──────────────────────────────────────────────────────────

def _memory_config() -> dict:
    """`langgraph.memory` from the YAML, {} when missing or unreadable."""
    try:
        import yaml

        path = Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return (cfg.get("langgraph", {}) or {}).get("memory", {}) or {}
    except Exception:
        return {}


def enabled() -> bool:
    """Is fact extraction on? VVA_LTM ("1"/"0") wins, then the YAML, else off."""
    env = os.getenv("VVA_LTM")
    if env in ("0", "1"):
        return env == "1"
    return bool(_memory_config().get("ltm_enabled", False))


def include_current_session() -> bool:
    """Read the conversation the user is in, every turn? YAML only, on by default.

    On, a fact said now is stored before the user's next message. Off, a
    conversation is read once the user chats in another one.
    """
    return bool(_memory_config().get("ltm_include_current_session", True))


# ── The contract with the model ───────────────────────────────────────────

MAX_OPS = 5
MAX_FACT_CHARS = 300
CATEGORIES = ("condition", "restriction", "goal", "preference", "profile")

MEMORY_PROMPT = """\
You maintain the long-term memory of a wellness and physical-therapy chat assistant. \
You read part of a conversation and decide which durable facts about the USER must be \
written down, changed or removed. Your answer is read by a program, not by a person, \
so reply with a single JSON object and nothing else.

You are given:
- the date of the conversation;
- the facts already stored for this user. Each has an "id", the fact text, a \
"category" and a "locked" flag. Locked means the user typed that fact in themselves;
- the messages to read, each labelled [user] or [assistant]. The input says whether \
they are a whole conversation or a single turn (one user message and the assistant's \
reply to it). In the single-turn case the stored facts already include what earlier \
turns produced.

OUTPUT FORMAT
{"operations": [ ... ]}
Each operation is exactly one of:
{"op": "ADD", "fact": "<text>", "category": "<category>"}
{"op": "UPDATE", "id": "<id of a stored fact>", "fact": "<new full text>", "category": "<category>"}
{"op": "DELETE", "id": "<id of a stored fact>"}
An empty list, {"operations": []}, means "nothing new". It is the normal answer for \
most conversations. Never write an operation just to have one.

WHAT MAY BE WRITTEN DOWN
- Only what the USER said about themselves. Never something the assistant suggested, \
assumed or guessed, even if the user did not object. Never your own inference. Never \
something about another person: a relative's or friend's condition is not a fact about \
the user.
- Durable facts only: still true and useful a month from now, in a conversation about \
something else. Not passing states (tired today, a bad night), not the details of a \
one-off request (the routine they asked for today, an event tomorrow), not small talk.
- Health facts are wanted: injuries, surgeries, diagnoses the user reports, ongoing \
pain, movements a clinician told them to avoid. Also goals; lasting personal likes, \
dislikes and habits, the things a friend would remember about them (a favourite food \
or drink, a sport or hobby, how they like to be spoken to, how answers should be \
written); and basic profile facts (job, routine, equipment). A lasting like or habit \
counts even when it is mentioned in passing; what happened today does not.
- Never store identifiers: ID or card numbers, phone numbers, e-mail addresses, \
street addresses. If the user gives one, leave it out entirely.
- Resolve relative times with the conversation date. "Three months ago", "last month", \
"since last winter", "recently" must become an absolute month and year (for example \
"July 2026"), because a relative time is wrong a month later. If it cannot be made \
absolute, leave the time out rather than keep a relative one.

CATEGORIES (closed list, use exactly these words)
- condition: injuries, surgeries, diagnoses, ongoing pain or symptoms
- restriction: movements, loads or activities the user must or wants to avoid
- goal: something the user is working towards
- preference: lasting likes, dislikes, and how they want answers written
- profile: other stable facts about the user (job, routine, equipment, schedule)

HOW TO COMPARE WITH STORED FACTS
- The same fact is already stored: do nothing.
- The user's new statement changes or extends a stored fact: UPDATE it, using the \
stored id. Write the complete new text. When the old state matters, keep it briefly, \
for example "now pain-free when walking (in July 2026: pain after 10 minutes)".
- The user says a stored fact is no longer true: DELETE it, using the stored id.
- A locked fact must never be updated or deleted. If the user contradicts a locked \
fact, ADD the user's new statement as a new fact instead.
- Only use ids that appear in the stored facts. Never invent an id.
- Do not repeat in an ADD a fact that is already stored.

STYLE OF EACH FACT
- At most 5 operations in total, and each fact at most 300 characters.
- One fact per operation. Several facts mean several operations.
- Write each fact in the language the user wrote it in, as a short neutral statement \
about the user, understandable without the conversation (no "she said", no "as above").
- Only what was said. Do not add details, causes, severity or advice.

EXAMPLES (structure only; write facts in the user's own language)
User says they had a meniscus operation in May 2025 and nothing is stored:
{"operations": [{"op": "ADD", "fact": "Had meniscus surgery in May 2025", "category": "condition"}]}
User says "my back is fine now" and stored fact f2 says "Has lower back pain":
{"operations": [{"op": "DELETE", "id": "f2"}]}
User only chats, asks for a one-off routine, or the assistant guesses a diagnosis \
the user never confirmed:
{"operations": []}
"""


class Operations(BaseModel):
    """The raw JSON envelope. Each operation is checked by `enforce`."""

    operations: list[dict]


# ── Checks on the model's answer ──────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DATE_LIKE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
# Seven or more digits, with at most one separator (space, dot, dash, bracket)
# between two of them.
_LONG_DIGITS_RE = re.compile(r"\d(?:[\s.\-()]?\d){6,}")


def _looks_like_identifier(text: str) -> bool:
    if _EMAIL_RE.search(text):
        return True
    return bool(_LONG_DIGITS_RE.search(_DATE_LIKE_RE.sub(" ", text)))


def enforce(raw_ops: list[Any], stored: list[dict]
            ) -> tuple[list[dict], list[dict]]:
    """The limits the prompt states, held in code. Returns (kept, dropped).

    `stored` are the facts the model was shown: {"id", "fact", "category",
    "locked"}. dropped = [{"reason", "op"}]. The dropped operations still
    carry the model's text, so count them and never log them.
    """
    by_id = {f["id"]: f for f in stored}
    kept: list[dict] = []
    dropped: list[dict] = []

    def drop(reason: str, op: Any) -> None:
        dropped.append({"reason": reason, "op": op})

    for i, op in enumerate(raw_ops):
        if i >= MAX_OPS:
            drop("over_limit", op)
            continue
        if not isinstance(op, dict) or op.get("op") not in ("ADD", "UPDATE", "DELETE"):
            drop("bad_op", op)
            continue
        kind = op["op"]
        clean: dict[str, Any] = {"op": kind}
        if kind in ("ADD", "UPDATE"):
            fact = op.get("fact")
            if not isinstance(fact, str) or not fact.strip():
                drop("empty_fact", op)
                continue
            fact = fact.strip()
            if len(fact) > MAX_FACT_CHARS:
                drop("too_long", op)
                continue
            if op.get("category") not in CATEGORIES:
                drop("bad_category", op)
                continue
            if _looks_like_identifier(fact):
                drop("identifier", op)
                continue
            clean["fact"] = fact
            clean["category"] = op["category"]
        if kind in ("UPDATE", "DELETE"):
            fid = op.get("id")
            if not isinstance(fid, str) or fid not in by_id:
                drop("unknown_id", op)
                continue
            if by_id[fid].get("locked"):
                drop("locked_id", op)
                continue
            clean["id"] = fid
        kept.append(clean)
    return kept, dropped


# ── The call ──────────────────────────────────────────────────────────────

def render_user_message(date: str, stored: list[dict], msgs: list[dict],
                        scope: str) -> str:
    """The user message: the date, the stored facts, the labelled messages.

    `scope` tells the model what the messages are, a whole conversation or
    part of one.
    """
    facts = json.dumps(
        [{"id": f["id"], "fact": f["fact"], "category": f["category"],
          "locked": bool(f.get("locked", False))} for f in stored],
        ensure_ascii=False, indent=1) if stored else "(none)"
    lines = "\n".join(f"[{m['role']}] {m['content']}" for m in msgs)
    return (f"Conversation date: {date}\n\n"
            f"Stored facts:\n{facts}\n\n"
            f"Messages to read ({scope}):\n{lines}\n\n"
            "Reply with the JSON object only.")


def extraction_llm():
    """The `memory` role, answering with the operations envelope as JSON."""
    return get_chat_model("memory").with_structured_output(
        Operations, method="json_mode", include_raw=True)


async def request_operations(structured_llm, user_message: str) -> dict:
    """One call: {"raw", "parsed", "parsing_error"}. May raise.

    An answer that is not the envelope does not raise: `parsed` is None then.
    """
    return await structured_llm.ainvoke(
        [("system", MEMORY_PROMPT), ("user", user_message)])


# ── Reading ───────────────────────────────────────────────────────────────

_MAX_CONVERSATIONS = 2
# A longer backlog is worked through over several runs: the marker only
# advances to the last message read.
_SLICE_MAX_CHARS = 8000
# Rows one conversation brings back before the slice is cut by length. Real
# turns fill 8,000 characters in about a dozen messages; this bounds a backlog
# of very short ones.
_SLICE_MAX_ROWS = 40
_MAX_STORED_FACTS = 50

# What the scope line of the user message says. The probe measured "a whole
# conversation" and single answered turns; a slice that starts later in a
# conversation is neither, so it is described in the prompt's own terms.
_SCOPE_FROM_START = "a whole conversation"
_SCOPE_CONTINUED = ("part of a conversation, the messages after those already read; "
                    "the stored facts already include what earlier parts produced")

# Selection and reading in one statement: a round-trip from this service costs
# about a second, and a run that only finds an answer to step over has to stay
# at one read and one write. Scoped to the user by the WHERE (RLS would scope it
# too; this keeps the intent visible in the statement). The current
# conversation, when it may be read at all, comes first.
_UNREAD_SQL = """
    SELECT c.session_id, c.ltm_up_to_seq, m.seq_id, m.role, m.content, m.created_at
    FROM (
        SELECT c.session_id, c.ltm_up_to_seq, c.updated_at
        FROM conversations c
        WHERE c.user_id = $1
          AND ($3::boolean OR c.session_id <> $2)
          AND EXISTS (SELECT 1 FROM messages m
                      WHERE m.session_id = c.session_id AND m.seq_id > c.ltm_up_to_seq)
        ORDER BY (c.session_id = $2) DESC, c.updated_at DESC
        LIMIT $4
    ) c
    CROSS JOIN LATERAL (
        SELECT m.seq_id, m.role, m.content, m.created_at
        FROM messages m
        WHERE m.session_id = c.session_id AND m.seq_id > c.ltm_up_to_seq
        ORDER BY m.seq_id
        LIMIT $5
    ) m
    ORDER BY (c.session_id = $2) DESC, c.updated_at DESC, c.session_id, m.seq_id
"""

# The same rows, in the same order, that the memory node hands the answering
# model (nodes/memory.py::_load_user_facts): what the model acts on is what may
# be changed here.
_FACTS_SQL = """
    SELECT id, fact_text, category, source
    FROM user_memory
    WHERE user_id = $1 AND valid = true
    ORDER BY CASE category
                 WHEN 'restriction' THEN 0
                 WHEN 'condition' THEN 1
                 ELSE 2
             END,
             created_at DESC
    LIMIT $2
"""


def _take_slice(messages: list) -> list:
    """The oldest unread messages that fit `_SLICE_MAX_CHARS`, each one whole.

    At least one, however long: the marker has to get past it.
    """
    taken: list = []
    chars = 0
    for message in messages:
        size = len(message["content"] or "")
        if taken and chars + size > _SLICE_MAX_CHARS:
            break
        taken.append(message)
        chars += size
    return taken


def _conversation_date(message) -> str:
    """The day the model resolves "three months ago" against."""
    at = message["created_at"] or datetime.now(timezone.utc)
    return at.date().isoformat()


# ── Writing ───────────────────────────────────────────────────────────────

# The marker moves only from where this run started reading, and it moves
# first: when another turn's run got there in between, this statement touches
# no row and the operations are not applied a second time.
_MARK_SQL = """
    UPDATE conversations SET ltm_up_to_seq = $4
    WHERE session_id = $1::uuid AND user_id = $2::uuid AND ltm_up_to_seq = $3
"""

# NOT EXISTS covers what the comparison in Python cannot see: a user with more
# valid facts than the model is shown.
_ADD_SQL = """
    INSERT INTO user_memory (user_id, fact_text, category, source, source_session_id)
    SELECT $1::uuid, $2::text, $3::text, 'extracted', $4::uuid
    WHERE NOT EXISTS (
        SELECT 1 FROM user_memory
        WHERE user_id = $1::uuid AND valid = true AND fact_text = $2::text
    )
"""

# `source = 'extracted'` repeats in SQL what `enforce` already refused: a fact
# the user typed is never changed here, whatever reaches this statement.
_UPDATE_SQL = """
    UPDATE user_memory
    SET fact_text = $3, category = $4, updated_at = now(), source_session_id = $5::uuid
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""

# Retired, not removed: the row stays and the memory node stops loading it.
_RETIRE_SQL = """
    UPDATE user_memory
    SET valid = false, updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""


def _affected(status: str) -> int:
    """Rows a statement touched, from its command tag ("UPDATE 1", "INSERT 0 1")."""
    return int(status.rsplit(" ", 1)[-1])


def _writes(kept: list[dict], stored: list[dict], row_ids: dict,
            user_id: str, session_id) -> tuple[list[tuple], int]:
    """The statement for each kept operation, and how many ADDs repeat a fact.

    Each entry is (count it under, reason when it touches no row, sql, args).
    """
    known = {f["fact"] for f in stored}
    writes: list[tuple] = []
    duplicates = 0
    for op in kept:
        if op["op"] == "ADD":
            if op["fact"] in known:
                duplicates += 1
                continue
            known.add(op["fact"])
            writes.append(("added", "duplicate", _ADD_SQL,
                           (user_id, op["fact"], op["category"], session_id)))
        elif op["op"] == "UPDATE":
            writes.append(("updated", "gone", _UPDATE_SQL,
                           (row_ids[op["id"]], user_id, op["fact"], op["category"],
                            session_id)))
        else:
            writes.append(("deleted", "gone", _RETIRE_SQL, (row_ids[op["id"]], user_id)))
    return writes, duplicates


async def _operations(session_id, user_message: str,
                      usage: dict | None = None) -> list | None:
    """The model's operations for one slice, or None without a usable answer.

    A timeout, a provider error and an answer that is not the envelope all mean
    the same thing here: nothing is written and the next run reads the slice
    again. The warning names the type of the error only. A provider error can
    repeat the request and a parsing error quotes the answer.

    `usage`, when given, is filled with the call's `input_tokens` /
    `output_tokens` (None when the answer reports none) for the
    `fact_extraction_done` telemetry. Counts only, never message text.
    """
    started = time.perf_counter()
    reason, error = "call_failed", None
    try:
        result = await request_operations(extraction_llm(), user_message)
    except Exception as exc:
        error = exc
    else:
        if usage is not None:
            raw = result.get("raw") if isinstance(result, dict) else None
            meta = getattr(raw, "usage_metadata", None) or {}
            usage["input_tokens"] = meta.get("input_tokens")
            usage["output_tokens"] = meta.get("output_tokens")
        parsed = result.get("parsed")
        if parsed is not None:
            return list(parsed.operations)
        reason, error = "unparsable", result.get("parsing_error")
    logger.warning("fact_extraction_llm_failed", extra={
        "session_id": str(session_id),
        "reason": reason,
        "error": type(error).__name__,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    })
    return None


async def _read_conversation(pg, user_id: str, session_id, messages: list) -> dict:
    """One conversation's slice: gate, call, checks, writes. Returns its counts."""
    started = time.perf_counter()
    from_seq = messages[0]["ltm_up_to_seq"]
    up_to_seq = messages[-1]["seq_id"]
    applied = {"added": 0, "updated": 0, "deleted": 0}
    dropped: Counter = Counter()

    # Only the user's own words can become a fact. A slice without any (the
    # answer of the turn before, typically) is stepped over without a call.
    llm_called = any(m["role"] == "user" for m in messages)
    usage: dict = {"input_tokens": None, "output_tokens": None}
    if not llm_called:
        await pg.execute(_MARK_SQL, session_id, user_id, from_seq, up_to_seq)
    else:
        rows = await pg.fetch(_FACTS_SQL, user_id, _MAX_STORED_FACTS)
        # Short ids in the prompt, as measured; a UUID is easy to copy wrongly.
        row_ids = {f"f{n}": r["id"] for n, r in enumerate(rows, start=1)}
        stored = [{"id": short, "fact": r["fact_text"], "category": r["category"],
                   "locked": r["source"] != "extracted"}
                  for short, r in zip(row_ids, rows)]
        raw_ops = await _operations(session_id, render_user_message(
            _conversation_date(messages[-1]), stored, messages,
            _SCOPE_CONTINUED if from_seq else _SCOPE_FROM_START), usage)
        if raw_ops is None:
            return {"llm_called": True, "failed": True, **applied}

        kept, refused = enforce(raw_ops, stored)
        dropped.update(d["reason"] for d in refused)
        writes, duplicates = _writes(kept, stored, row_ids, user_id, session_id)
        if duplicates:
            dropped["duplicate"] += duplicates

        async with pg.transaction() as conn:
            if _affected(await conn.execute(
                    _MARK_SQL, session_id, user_id, from_seq, up_to_seq)):
                for count_as, missed, sql, args in writes:
                    if _affected(await conn.execute(sql, *args)):
                        applied[count_as] += 1
                    else:
                        dropped[missed] += 1
            elif writes:
                dropped["superseded"] += len(writes)

    logger.info("fact_extraction_done", extra={
        "session_id": str(session_id),
        "message_count": len(messages),
        "char_count": sum(len(m["content"] or "") for m in messages),
        "llm_called": llm_called,
        **applied,
        "dropped": dict(dropped),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    })
    return {"llm_called": llm_called, "failed": False, **applied}


async def extract_user_facts(user_id: str, current_session_id: str) -> dict:
    """Read this user's unread messages and bring `user_memory` up to date.

    At most `_MAX_CONVERSATIONS` conversations per run, one slice each: the
    current one first when `include_current_session()` allows it, then the
    others, most recently updated first.

    Returns counts for the run. May raise for the database; housekeep catches.
    A failed model call does not raise: that conversation is left as it was,
    marker included, and the next one is still read. Runs inside the request,
    so the pg client has the user bound.
    """
    pg = get_pg_client()
    rows = await pg.fetch(
        _UNREAD_SQL, user_id, current_session_id, include_current_session(),
        _MAX_CONVERSATIONS, _SLICE_MAX_ROWS)

    summary = {"conversations": 0, "llm_calls": 0, "failed": 0,
               "added": 0, "updated": 0, "deleted": 0}
    for session_id, unread in itertools.groupby(rows, key=lambda r: r["session_id"]):
        outcome = await _read_conversation(
            pg, user_id, session_id, _take_slice(list(unread)))
        summary["conversations"] += 1
        summary["llm_calls"] += int(outcome["llm_called"])
        summary["failed"] += int(outcome["failed"])
        for key in ("added", "updated", "deleted"):
            summary[key] += outcome[key]
    return summary
