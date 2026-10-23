"""Fact extraction (memory v2 tiers): what the user said, kept across conversations.

After a turn, `extract_user_facts` reads the messages of a conversation that
have not been read yet, shows them to the `memory` role together with the
standing instructions and the newest tier-2 rows, and applies the operations
it answers with (ADD, UPDATE, REPEAT, DELETE, FORGET_ALL) to `user_memory`
(migration 016). The memory node loads those rows on every turn, so a wrong
or invented fact is acted on in later conversations. That is why the model's
answer goes through `enforce` before anything is written, why the tier rules
live in the pure `apply_policy` below (tested table-first, no database), and
why a fact the user typed in is never changed here.

The prompt, the layout of the user message and the checks are the ones measured
in docs/tracking/memory-probe-V*.md. local_tests/run_memory_probe.py imports
them from here, so what is measured is what runs.

Not a graph node: `housekeep` (nodes/summarizer.py) calls it as its last step.
The whole feature sits behind `enabled()`; the tier-2 embedding top-up
(`embed_pending_facts`) runs even with it off, so a fact the user typed in
the settings screen is searchable too.

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


def _policy_defaults() -> dict:
    """Tier thresholds: YAML wins, code defaults match the plan."""
    cfg = _memory_config()
    return {
        "forget_purge_days": int(cfg.get("forget_purge_days", 30)),
    }


# ── The contract with the model ───────────────────────────────────────────

MAX_OPS = 8
MAX_FACT_CHARS = 300
MAX_PROJECT_CHARS = 60
MAX_TOPIC_WORDS = 4
MEMORY_TYPES = ("profile", "preference", "area", "event", "person",
                "reference", "instruction")
TYPES = MEMORY_TYPES
VALID_TYPES = MEMORY_TYPES
# Legacy aliases kept for older callers/tests.
CATEGORIES = MEMORY_TYPES

MEMORY_PROMPT = """You maintain the long-term memory of an AI assistant. You read part of a conversation between a
user and the assistant and decide what the assistant should remember about this user for future
conversations. Your answer is read by a program: reply with one JSON object and nothing else.

INPUT
- the conversation date;
- the items already stored for this user, newest first: id, type, topic, fact, locked (locked
  means the user typed the item in themselves);
- the topics already in use, by type;
- the messages to read, labelled [user] or [assistant]. They may be in any language. They are a
  whole conversation or a later part of one; in the second case the stored items already include
  what earlier parts produced.

OUTPUT
{"operations": [ ... ]}, each operation exactly one of:
{"op": "ADD", "type": "<type>", "topic": "<topic>", "fact": "<text>"}
{"op": "UPDATE", "id": "<id>", "type": "<type>", "topic": "<topic>", "fact": "<complete new text>"}
{"op": "REPEAT", "id": "<id>"}
{"op": "DELETE", "id": "<id>"}
{"op": "FORGET_ALL"}
{"operations": []} means there is nothing to remember. It is the usual answer.

WHAT TO REMEMBER
Remember something only when all four hold:
1. The user stated or confirmed it — about themselves, about how they want the assistant to
   behave, or about a person in their life when it changes what the assistant should do for the
   user. Never what the assistant said, suggested or guessed, and never your own inference,
   including from what the user asks.
2. It is meant as true of the user's real life — not part of a hypothetical, a role-play, an
   example, or material the user asked the assistant to work on (translate, edit, summarise,
   analyse).
3. It will still be true and useful weeks from now, in a conversation about something else. A
   momentary state is not, however often it comes up. A one-off episode is only when it still
   matters later — it left something lasting, or the user will refer back to it (an injury,
   moving house, finishing a degree). Something the user is unsure they will do is not;
   something they want or have decided now is, even when it depends on a condition — keep the
   condition in the fact. An instruction meant only for the current reply is not.
4. It is not kept elsewhere: not one of the app's own settings, even when the user mentions it —
   the name shown in the app, the interface language, which model the app runs on. How the user
   wants to be addressed or answered is an instruction, not a setting.
Never store identifiers or contact details: ID, account, card or phone numbers, e-mail or street
addresses.

ONE THING, ONE PLACE
Each thing is remembered once. Before adding, look for a stored item of ANY type about the same
thing:
- it already says it: REPEAT it;
- the new statement adds to it, narrows it, makes an exception to it or changes it: UPDATE it.
  Write the complete new text, keep every part that is still true, and keep the old state
  briefly when it matters ("Now prefers X (until October 2026: Y)");
- it is no longer true: UPDATE it to what is true now, keeping the old state briefly when it
  matters. DELETE it only when nothing true is left to keep, or when the user asks to forget it;
- the user asks to forget everything: FORGET_ALL, alone. It removes what was learned from
  conversations; locked items stay.
A locked item is never UPDATEd or REPEATed. When the user says it is no longer true, asks to
forget it, or replaces it, DELETE it and ADD what is true now, if anything. When they add to it,
ADD only the new part.
Split a statement into separate items when its parts could change or be needed separately (an
event and the lasting condition it caused); keep together what only makes sense together.
Use only ids that appear in the stored items.

EACH ITEM
- type, one of:
  profile: stable facts about the user — identity, circumstances, abilities, health, regular
  activities;
  preference: likes, dislikes, and how they want things done or answered;
  area: something they are working on that changes over time;
  event: a dated past occurrence that still matters later;
  person: someone in the user's life, only when identifiable (a name or a unique relation);
  reference: a tool, device, service, document or source the user relies on;
  instruction: how the assistant should behave.
- topic: a short English title of at most 4 words for what the item is about, within its type.
  Reuse a topic already in use for that type when it fits. For a person, the topic is who they
  are. An instruction that applies to one area only takes that area's topic.
- fact: one short, neutral English statement, understandable on its own, whatever language the
  conversation is in. Keep names as written. Only what was said, nothing added. Turn relative
  times into absolute ones with the conversation date ("last month" -> "September 2026"); drop a
  time that cannot be made absolute.
- At most 8 operations, one item per operation, at most 300 characters per fact.

EXAMPLES (structure only; conversations may be in any language)
Date 2026-10-23. Nothing stored. [user] "I'm exhausted this week. I've been vegetarian for ten years."
{"operations": [{"op": "ADD", "type": "profile", "topic": "diet", "fact": "Has been vegetarian since 2016."}]}
Date 2026-10-23. Nothing stored. [user] "La semana pasada me caí de la bici y todavía me duele la muñeca derecha."
{"operations": [{"op": "ADD", "type": "event", "topic": "health", "fact": "Fell off a bicycle in October 2026."}, {"op": "ADD", "type": "profile", "topic": "health", "fact": "Has ongoing right wrist pain since a bicycle fall in October 2026."}]}
Date 2026-10-23. Nothing stored. [user] "My sister Mai is a pharmacist, I usually ask her about medicines."
{"operations": [{"op": "ADD", "type": "person", "topic": "sister Mai", "fact": "Is a pharmacist; the user often asks her about medicines."}]}
Date 2026-10-23. Stored f5 {"type": "area", "topic": "marathon training", "fact": "Training for a marathon in March 2027."}. [user] "For my marathon training, always suggest low-impact cross-training on rest days."
{"operations": [{"op": "ADD", "type": "instruction", "topic": "marathon training", "fact": "Suggest low-impact cross-training on rest days."}]}
Date 2026-10-23. Stored f2 {"type": "profile", "topic": "health", "fact": "Has lower back pain."}. [assistant] "It could be a slipped disc." [user] "Maybe. Which stretches can I do?"
{"operations": []}
Date 2026-10-23. Stored f3 {"type": "profile", "topic": "work", "fact": "Works as a nurse."}. [user] "I moved to night shifts last month."
{"operations": [{"op": "UPDATE", "id": "f3", "type": "profile", "topic": "work", "fact": "Works as a nurse, on night shifts since September 2026."}]}
Date 2026-10-23. Stored locked f1 {"type": "preference", "topic": "units", "fact": "Prefers metric units."}. [user] "Use imperial units from now on."
{"operations": [{"op": "DELETE", "id": "f1"}, {"op": "ADD", "type": "preference", "topic": "units", "fact": "Prefers imperial units."}]}
Date 2026-10-23. [user] "Imagine I won the lottery — what should I buy first?"
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


def _check_text_op(op: Any) -> str | None:
    """The fact text for an ADD/UPDATE, or the drop reason."""
    fact = op.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return "empty_fact"
    fact = fact.strip()
    if len(fact) > MAX_FACT_CHARS:
        return "too_long"
    if _looks_like_identifier(fact):
        return "identifier"
    return None


def _norm_topic(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.strip().split())


def _check_topic(op: Any) -> str | None:
    """The topic for an ADD/UPDATE, or the drop reason."""
    topic = _norm_topic(op.get("topic"))
    if topic is None or not topic:
        return "bad_topic"
    if len(topic) > MAX_PROJECT_CHARS:
        return "bad_topic"
    if len(topic.split()) > MAX_TOPIC_WORDS:
        return "bad_topic"
    return None


def enforce(raw_ops: list[Any], stored: list[dict]
            ) -> tuple[list[dict], list[dict]]:
    """The limits the prompt states, held in code. Returns (kept, dropped).

    `stored` are the items the model was shown: {"id", "type", "topic",
    "fact", "locked"}. A "safety" key on a model operation is ignored.
    dropped = [{"reason", "op"}]. The dropped
    operations still carry the model's text, so count them and never log them.
    """
    by_id = {f["id"]: f for f in stored}
    # Known topics by type, for reuse normalisation.
    known_by_type: dict[str, dict[str, str]] = {}
    for f in stored:
        t = f.get("type")
        topic = _norm_topic(f.get("topic"))
        if t is not None and topic:
            known_by_type.setdefault(t, {}).setdefault(topic.casefold(), topic)

    def _reuse_type_topic(typ: str, topic: str) -> str:
        known = known_by_type.get(typ, {})
        return known.get(topic.casefold(), topic)

    kept: list[dict] = []
    dropped: list[dict] = []

    def drop(reason: str, op: Any) -> None:
        dropped.append({"reason": reason, "op": op})

    for i, op in enumerate(raw_ops):
        if i >= MAX_OPS:
            drop("over_limit", op)
            continue
        if not isinstance(op, dict) or op.get("op") not in (
                "ADD", "UPDATE", "REPEAT", "DELETE", "FORGET_ALL"):
            drop("bad_op", op)
            continue
        kind = op["op"]
        if kind == "FORGET_ALL":
            kept.append({"op": kind})
            continue
        clean: dict[str, Any] = {"op": kind}
        if kind == "ADD":
            if op.get("type") not in MEMORY_TYPES:
                drop("bad_type", op)
                continue
            reason = _check_text_op(op)
            if reason is not None:
                drop(reason, op)
                continue
            reason = _check_topic(op)
            if reason is not None:
                drop(reason, op)
                continue
            topic = _reuse_type_topic(op["type"], _norm_topic(op["topic"]))
            clean["type"] = op["type"]
            clean["topic"] = topic
            clean["fact"] = op["fact"].strip()
        elif kind == "UPDATE":
            if op.get("type") not in MEMORY_TYPES:
                drop("bad_type", op)
                continue
            reason = _check_text_op(op)
            if reason is not None:
                drop(reason, op)
                continue
            reason = _check_topic(op)
            if reason is not None:
                drop(reason, op)
                continue
            fid = op.get("id")
            if not isinstance(fid, str) or fid not in by_id:
                drop("unknown_id", op)
                continue
            if by_id[fid].get("locked"):
                drop("locked_id", op)
                continue
            topic = _reuse_type_topic(op["type"], _norm_topic(op["topic"]))
            clean["id"] = fid
            clean["type"] = op["type"]
            clean["topic"] = topic
            clean["fact"] = op["fact"].strip()
        elif kind == "REPEAT":
            fid = op.get("id")
            if not isinstance(fid, str) or fid not in by_id:
                drop("unknown_id", op)
                continue
            if by_id[fid].get("locked"):
                drop("locked_id", op)
                continue
            clean["id"] = fid
        else:  # DELETE
            fid = op.get("id")
            if not isinstance(fid, str) or fid not in by_id:
                drop("unknown_id", op)
                continue
            # D8: DELETE on a locked id is kept.
            clean["id"] = fid
        kept.append(clean)
    return kept, dropped


# ── Tier policy (pure: no database, no clock) ─────────────────────────────

def apply_policy(row: dict | None, op: dict, ctx: dict) -> dict:
    """Decide what one operation writes. Returns {"action", "fields"}.

    `row` is the stored row (or None for ADD): {"id", "fact", "type",
    "topic", "kind", "tier", "status", "project", "seen_count",
    "seen_projects", "last_seen_session_id", "source"}. `ctx` carries the
    counting session and the known area topics:
    {"session_id", "area_topics": set[str]}.
    `fields` holds the values to write; the caller stamps said_at/updated_at.

    Actions: add | refresh (REPEAT seen again) | rewrite (content changed) |
    hide (valid=false) | forget_all | noop.
    """
    session_id = ctx["session_id"]
    area_topics = {t.casefold() for t in (ctx.get("area_topics") or [])}
    kind = op["op"]

    def _tier_for(typ: str, topic: str) -> tuple[int, str]:
        # Tier 1 holds instructions only: an instruction that is not about one
        # of the user's areas. Everything else is tier 2.
        if typ == "instruction" and _norm_topic(topic).casefold() not in area_topics:
            return 1, "active"
        return 2, "active"

    if kind == "ADD":
        typ = op["type"]
        topic = op["topic"]
        tier, status = _tier_for(typ, topic)
        label = ""
        return {"action": "add", "fields": {
            "memory_type": typ, "fact": op["fact"], "kind": "instruction"
            if typ == "instruction" else "fact",
            "tier": tier, "status": status,
            "project": topic,
            "seen_count": 1, "seen_projects": [label],
            "last_seen_session_id": session_id,
            "category": None,
        }}
    if kind == "REPEAT":
        if row is None or row.get("last_seen_session_id") == session_id:
            # One conversation counts once.
            return {"action": "noop", "fields": {}}
        seen = (row.get("seen_count") or 0) + 1
        projects = list(row.get("seen_projects") or []) + [""]
        fields: dict[str, Any] = {
            "seen_count": seen, "seen_projects": projects,
            "last_seen_session_id": session_id,
        }
        return {"action": "refresh", "fields": fields}
    if kind == "UPDATE":
        typ = op["type"]
        topic = op["topic"]
        tier, status = _tier_for(typ, topic)
        fields = {
            "fact": op["fact"], "memory_type": typ,
            "kind": "instruction" if typ == "instruction" else "fact",
            "project": topic,
            "tier": tier, "status": status,
            "last_seen_session_id": session_id,
            "category": None,
        }
        return {"action": "rewrite", "fields": fields}
    if kind == "DELETE":
        return {"action": "hide", "fields": {}}
    if kind == "FORGET_ALL":
        return {"action": "forget_all", "fields": {}}
    return {"action": "noop", "fields": {}}


# ── The call ──────────────────────────────────────────────────────────────

def render_user_message(date: str, tier1: list[dict], tier2: list[dict],
                        projects: list[str], msgs: list[dict], scope: str) -> str:
    """The user message: the date, all stored items, known topics, the slice.

    `tier1` and `tier2` are combined into one list, newest first, capped at
    200 rows. Each row carries {"id", "type", "topic", "fact",
    "locked"} (and optionally "said_at"). `scope` tells the model what the
    messages are, a whole conversation or part of one.
    """
    items = [*tier1, *tier2]

    def _sort_key(f: dict):
        v = f.get("said_at")
        return ("" if v is None else str(v))

    if any(f.get("said_at") is not None for f in items):
        items = sorted(items, key=_sort_key, reverse=True)

    capped = len(items) > 200
    shown = items[:200]
    if capped:
        logger.info("render_user_message_capped", extra={"count": len(items)})

    def _line(f: dict) -> str:
        return json.dumps({"id": f["id"], "type": f.get("type"),
                           "topic": f.get("topic"),
                           "fact": f["fact"],
                           "locked": bool(f.get("locked", False))},
                          ensure_ascii=False)

    stored_block = "\n".join(_line(f) for f in shown) if shown else "(none)"

    topics_by_type: dict[str, set[str]] = {}
    for f in items:
        t = f.get("type")
        topic = _norm_topic(f.get("topic"))
        if t is not None and topic:
            topics_by_type.setdefault(t, set()).add(topic)

    topic_lines = []
    for typ in MEMORY_TYPES:
        topics = sorted(topics_by_type.get(typ, ()))
        if topics:
            topic_lines.append(f"{typ}: {', '.join(topics)}")
    known_block = "\n".join(topic_lines) if topic_lines else "(none)"

    lines = "\n".join(f"[{m['role']}] {m['content']}" for m in msgs)
    return (f"Conversation date: {date}\n\n"
            f"Stored items (newest first):\n{stored_block}\n\n"
            f"Known topics by type:\n{known_block}\n\n"
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
_MAX_TIER2_SHOWN = 200

# What the scope line of the user message says. The probe measured "a whole
# conversation" and single answered turns; a slice that starts later in a
# conversation is neither, so it is described in the prompt's own terms.
_SCOPE_FROM_START = "a whole conversation"
_SCOPE_CONTINUED = ("part of a conversation, the messages after those already read; "
                    "the stored facts already include what earlier parts produced")

# Selection and reading in one statement: a round-trip from this service costs
# about a second, and a run that only finds an answer to step over has to stay
# at one read and one write. Scoped to the user by the WHERE (RLS would scope it
# too; this keeps the intent visible in the statement).
_UNREAD_SQL = """
    SELECT c.session_id, c.ltm_up_to_seq, m.seq_id, m.role, m.content, m.created_at
    FROM (
        SELECT c.session_id, c.ltm_up_to_seq, c.updated_at
        FROM conversations c
        WHERE c.user_id = $1
          AND EXISTS (SELECT 1 FROM messages m
                      WHERE m.session_id = c.session_id AND m.seq_id > c.ltm_up_to_seq)
        ORDER BY c.updated_at DESC
        LIMIT $2
    ) c
    CROSS JOIN LATERAL (
        SELECT m.seq_id, m.role, m.content, m.created_at
        FROM messages m
        WHERE m.session_id = c.session_id AND m.seq_id > c.ltm_up_to_seq
        ORDER BY m.seq_id
        LIMIT $3
    ) m
    ORDER BY c.updated_at DESC, c.session_id, m.seq_id
"""

# What the model may change is what the memory node acts on (nodes/memory.py):
# the same tier-1 rows plus the newest tier-2 slice.
_TIER1_SQL = """
    SELECT id, fact_text, category, memory_type, kind, tier, status, project,
           seen_count, seen_projects, last_seen_session_id, source, said_at
    FROM user_memory
    WHERE user_id = $1 AND valid = true AND tier = 1
    ORDER BY said_at DESC
"""

_TIER2_SQL = """
    SELECT id, fact_text, category, memory_type, kind, tier, status, project,
           seen_count, seen_projects, last_seen_session_id, source, said_at
    FROM user_memory
    WHERE user_id = $1 AND valid = true AND tier = 2
    ORDER BY said_at DESC
    LIMIT $2
"""

_PROJECTS_SQL = """
    SELECT DISTINCT project FROM user_memory
    WHERE user_id = $1 AND valid = true AND project IS NOT NULL
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


def _conversation_date(messages: list) -> str:
    """The day the model resolves "three months ago" against: the newest
    user message of the slice, falling back to now."""
    at = None
    for message in reversed(messages):
        if message["role"] == "user" and message["created_at"]:
            at = message["created_at"]
            break
    at = at or datetime.now(timezone.utc)
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
    INSERT INTO user_memory (user_id, fact_text, category, memory_type, kind, tier, status,
                             project, seen_count, seen_projects, source,
                             source_session_id, last_seen_session_id,
                             said_at, updated_at)
    SELECT $1::uuid, $2::text, NULL::text, $3::text, $4::text, $5::smallint, $6::text,
           $7::text, $8::int, $9::text[], 'extracted', $10::uuid, $10::uuid,
           now(), now()
    WHERE NOT EXISTS (
        SELECT 1 FROM user_memory
        WHERE user_id = $1::uuid AND valid = true AND fact_text = $2::text
    )
"""

# `source = 'extracted'` repeats in SQL what `enforce` already refused: a fact
# the user typed is never changed here, whatever reaches this statement.
_REPEAT_SQL = """
    UPDATE user_memory
    SET seen_count = $3, seen_projects = $4, last_seen_session_id = $5::uuid,
        said_at = now(), updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""

_UPDATE_SQL = """
    UPDATE user_memory
    SET fact_text = $3, memory_type = $4::text, project = $5::text,
        kind = $6::text,
        tier = $7::smallint, status = $8::text,
        last_seen_session_id = $9::uuid, said_at = now(), updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""

# Retired, not removed: the row stays and the memory node stops loading it.
_HIDE_SQL = """
    UPDATE user_memory
    SET valid = false, updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND valid = true
"""

_FORGET_ALL_SQL = """
    UPDATE user_memory
    SET valid = false, updated_at = now()
    WHERE user_id = $1::uuid AND source = 'extracted' AND valid = true
"""

# Old candidates and hidden rows, wherever they sit — not just the newest 60
# the model is shown. Runs once per extraction for the user, even when the
# slice held no user message.
_PURGE_SQL = """
    DELETE FROM user_memory WHERE user_id = $1 AND valid = false
      AND updated_at < now() - make_interval(days => $2)
"""


def _affected(status: str) -> int:
    """Rows a statement touched, from its command tag ("UPDATE 1", "INSERT 0 1")."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


def _planned_writes(kept: list[dict], rows: dict, row_ids: dict,
                    user_id: str, session_id, ctx: dict
                    ) -> tuple[list[tuple], int]:
    """The statement for each kept operation, and how many ADDs repeat a fact.

    Each entry is (count it under, reason when it touches no row, sql, args).
    `rows` maps the short prompt id to the full stored row (as `apply_policy`
    reads it); `row_ids` maps it to the database UUID.
    """
    known = {f["fact"] for f in rows.values()}
    writes: list[tuple] = []
    duplicates = 0
    for op in kept:
        if op["op"] == "ADD":
            if op["fact"] in known:
                duplicates += 1
                continue
            known.add(op["fact"])
            decided = apply_policy(None, op, ctx)
            f = decided["fields"]
            writes.append(("added", "duplicate", _ADD_SQL,
                           (user_id, f["fact"], f["memory_type"], f["kind"],
                            f["tier"], f["status"], f["project"],
                            f["seen_count"], f["seen_projects"], session_id)))
        elif op["op"] == "REPEAT":
            decided = apply_policy(rows[op["id"]], op, ctx)
            if decided["action"] == "noop":
                continue
            f = decided["fields"]
            writes.append(("repeated", "gone", _REPEAT_SQL,
                           (row_ids[op["id"]], user_id, f["seen_count"],
                            f["seen_projects"], f["last_seen_session_id"])))
        elif op["op"] == "UPDATE":
            decided = apply_policy(rows[op["id"]], op, ctx)
            f = decided["fields"]
            row = rows[op["id"]]
            writes.append(("updated", "gone", _UPDATE_SQL,
                           (row_ids[op["id"]], user_id, f["fact"], f["memory_type"],
                            f["project"], f["kind"],
                            f.get("tier", row["tier"]),
                            f.get("status", row["status"]),
                            f["last_seen_session_id"])))
        elif op["op"] == "DELETE":
            writes.append(("deleted", "gone", _HIDE_SQL,
                           (row_ids[op["id"]], user_id)))
        else:  # FORGET_ALL
            writes.append(("deleted", "gone", _FORGET_ALL_SQL, (user_id,)))
    return writes, duplicates


async def _operations(session_id, user_message: str,
                      usage: dict | None = None) -> list | None:
    """The model's operations for one slice, or None without a usable answer.

    A timeout, a provider error and an answer that is not the envelope all mean
    the same thing here: nothing is written and the next run reads the slice
    again. The warning names the type of the error only. A provider error can
    repeat the request and a parsing error quotes the answer.
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


async def _read_conversation(pg, user_id: str, session_id, messages: list,
                             policy: dict) -> dict:
    """One conversation's slice: gate, call, checks, writes. Returns its counts."""
    started = time.perf_counter()
    from_seq = messages[0]["ltm_up_to_seq"]
    up_to_seq = messages[-1]["seq_id"]
    applied = {"added": 0, "updated": 0, "deleted": 0, "repeated": 0}
    dropped: Counter = Counter()

    # Only the user's own words can become a fact. A slice without any (the
    # answer of the turn before, typically) is stepped over without a call.
    llm_called = any(m["role"] == "user" for m in messages)
    usage: dict = {"input_tokens": None, "output_tokens": None}
    if not llm_called:
        await pg.execute(_MARK_SQL, session_id, user_id, from_seq, up_to_seq)
    else:
        tier1 = await pg.fetch(_TIER1_SQL, user_id)
        tier2 = await pg.fetch(_TIER2_SQL, user_id, _MAX_TIER2_SHOWN)
        projects = [r["project"] for r in await pg.fetch(_PROJECTS_SQL, user_id)]
        # Short ids in the prompt, as measured; a UUID is easy to copy wrongly.
        full: dict[str, dict] = {}
        stored: list[dict] = []
        for n, r in enumerate([*tier1, *tier2], start=1):
            short = f"f{n}"
            full[short] = {
                "id": short, "fact": r["fact_text"], "category": r.get("category"),
                "memory_type": r.get("memory_type", "profile"),
                "kind": r.get("kind", "fact"), "tier": r.get("tier", 2),
                "status": r.get("status", "active"), "project": r.get("project"),
                "topic": r.get("project"),
                "seen_count": r.get("seen_count", 1),
                "seen_projects": list(r.get("seen_projects") or []),
                "last_seen_session_id": r.get("last_seen_session_id"),
                "source": r.get("source", "extracted"),
                "said_at": r.get("said_at"),
            }
            stored.append({"id": short, "fact": r["fact_text"],
                           "type": r.get("memory_type", "profile"),
                           "topic": r.get("project"),
                           "category": r.get("category"),
                           "locked": r["source"] != "extracted"})

        def _view(short: str) -> dict:
            f = full[short]
            return {"id": short, "fact": f["fact"],
                    "type": f["memory_type"], "topic": f["topic"],
                    "kind": f["kind"], "project": f["project"],
                    "locked": f["source"] != "extracted",
                    "said_at": f.get("said_at")}

        area_topics = [f.get("topic") for f in stored
                       if f.get("type") == "area" and f.get("topic")]
        ctx = {"session_id": session_id, "area_topics": area_topics, **policy}
        raw_ops = await _operations(session_id, render_user_message(
            _conversation_date(messages),
            [_view(s) for s in full if full[s]["tier"] == 1],
            [_view(s) for s in full if full[s]["tier"] == 2],
            projects, messages,
            _SCOPE_CONTINUED if from_seq else _SCOPE_FROM_START), usage)
        if raw_ops is None:
            return {"llm_called": True, "failed": True, **applied}

        kept, refused = enforce(raw_ops, stored)
        dropped.update(d["reason"] for d in refused)
        if any(op["op"] == "FORGET_ALL" for op in kept):
            skipped = [op for op in kept if op["op"] != "FORGET_ALL"]
            if skipped:
                dropped["forget_all_skipped"] += len(skipped)
            kept = [op for op in kept if op["op"] == "FORGET_ALL"]
        row_ids = {f"f{n}": r["id"]
                   for n, r in enumerate([*tier1, *tier2], start=1)}
        writes, duplicates = _planned_writes(kept, full, row_ids, user_id,
                                             session_id, ctx)
        if duplicates:
            dropped["duplicate"] += duplicates

        async with pg.transaction() as conn:
            if _affected(await conn.execute(
                    _MARK_SQL, session_id, user_id, from_seq, up_to_seq)):
                for count_as, missed, sql, args in writes:
                    touched = _affected(await conn.execute(sql, *args))
                    if touched:
                        # FORGET_ALL hides many rows with one statement.
                        applied[count_as] += touched if sql is _FORGET_ALL_SQL else 1
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


async def _purge_user(pg, user_id: str, policy: dict) -> int:
    """Delete long-hidden rows, wherever they sit."""
    tag = await pg.execute(_PURGE_SQL, user_id, policy["forget_purge_days"])
    return _affected(tag if isinstance(tag, str) else str(tag))


async def extract_user_facts(user_id: str, current_session_id: str) -> dict:
    """Read this user's unread messages and bring `user_memory` up to date.

    At most `_MAX_CONVERSATIONS` conversations per run, one slice each, most
    recently updated first. Old candidates and long-hidden rows are purged
    once per run, even when nothing was read.

    Returns counts for the run. May raise for the database; housekeep catches.
    A failed model call does not raise: that conversation is left as it was,
    marker included, and the next one is still read. Runs inside the request,
    so the pg client has the user bound.
    """
    policy = _policy_defaults()
    pg = get_pg_client()
    rows = await pg.fetch(
        _UNREAD_SQL, user_id, _MAX_CONVERSATIONS, _SLICE_MAX_ROWS)

    summary = {"conversations": 0, "llm_calls": 0, "failed": 0,
               "added": 0, "updated": 0, "deleted": 0, "repeated": 0,
               "purged": 0}
    for session_id, unread in itertools.groupby(rows, key=lambda r: r["session_id"]):
        outcome = await _read_conversation(
            pg, user_id, session_id, _take_slice(list(unread)), policy)
        summary["conversations"] += 1
        summary["llm_calls"] += int(outcome["llm_called"])
        summary["failed"] += int(outcome["failed"])
        for key in ("added", "updated", "deleted", "repeated"):
            summary[key] += outcome[key]
    try:
        summary["purged"] = await _purge_user(pg, user_id, policy)
    except Exception:
        pass
    return summary


async def embed_pending_facts(user_id: str, limit: int = 20) -> int:
    """Embed tier-2 rows still missing one, oldest first. Returns how many.

    Runs even with extraction off, so a fact the user typed is searchable.
    Never raises: the caller logs the type, never the text.
    """
    from langgraph_agents.shared import get_embedding_service

    pg = get_pg_client()
    try:
        await pg.connect()
        rows = await pg.fetch(
            """SELECT id, fact_text, project, memory_type FROM user_memory
               WHERE user_id = $1 AND valid = true AND tier = 2
                 AND embedding IS NULL
               ORDER BY said_at LIMIT $2""",
            user_id, limit,
        )
        if not rows:
            return 0
        texts = []
        for r in rows:
            if r.get("memory_type") in ("area", "person") and r.get("project"):
                texts.append(f"{r['project']}: {r['fact_text']}")
            else:
                texts.append(r["fact_text"])
        vectors = await get_embedding_service().aembed_passages(texts)
        done = 0
        for row, vector in zip(rows, vectors):
            tag = await pg.execute(
                "UPDATE user_memory SET embedding = $2, updated_at = now() "
                "WHERE id = $1::uuid AND user_id = $3::uuid "
                "AND embedding IS NULL",
                row["id"], vector, user_id,
            )
            done += 1 if _affected(tag if isinstance(tag, str) else str(tag)) else 0
        return done
    except Exception as exc:
        logger.warning("fact_embedding_failed", extra={
            "error": type(exc).__name__,
        })
        return 0
