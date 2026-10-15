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
        "promote_fact_at": int(cfg.get("promote_fact_at", 2)),
        "promote_instruction_at": int(cfg.get("promote_instruction_at", 3)),
        "candidate_ttl_days": int(cfg.get("candidate_ttl_days", 60)),
        "forget_purge_days": int(cfg.get("forget_purge_days", 30)),
    }


# ── The contract with the model ───────────────────────────────────────────

MAX_OPS = 8
MAX_FACT_CHARS = 300
MAX_PROJECT_CHARS = 60
CATEGORIES = ("condition", "restriction", "goal", "preference", "profile",
              "style", "project")
SAFE_CATEGORIES = ("restriction", "condition")

MEMORY_PROMPT = """\
You maintain the long-term memory of a wellness and physical-therapy chat assistant. \
You read part of a conversation and decide which durable facts about the USER must be \
written down, changed, repeated or removed. Your answer is read by a program, not by a person, \
so reply with a single JSON object and nothing else.

You are given:
- the date of the conversation;
- the standing instructions already stored for this user (they apply in every conversation);
- other stored facts, newest first. Each has an "id", the fact text, a \
"category", a "kind" ("instruction" or "fact"), a "project" label or null, and a \
"locked" flag. Locked means the user typed that fact in themselves;
- the known project labels for this user;
- the messages to read, each labelled [user] or [assistant]. The input says whether \
they are a whole conversation or a single turn (one user message and the assistant's \
reply to it). In the single-turn case the stored facts already include what earlier \
turns produced.

OUTPUT FORMAT
{"operations": [ ... ]}
Each operation is exactly one of:
{"op": "ADD", "kind": "instruction|fact", "fact": "<text>", "category": "<category>", "project": "<known label, a new short label, or null>", "explicit": true|false}
{"op": "UPDATE", "id": "<id of a stored fact>", "fact": "<new full text>", "category": "<category>", "project": "<label or null>"}
{"op": "REPEAT", "id": "<id of a stored fact>", "project": "<label or null>"}
{"op": "DELETE", "id": "<id of a stored fact>"}
{"op": "FORGET_ALL"}
An empty list, {"operations": []}, means "nothing new". It is the normal answer for \
most conversations. Never write an operation just to have one.

WHAT MAY BE WRITTEN DOWN
- Only what the USER said or confirmed about themselves, or what the user wants the \
assistant to do. Never something the assistant suggested, assumed or guessed, even if \
the user did not object. Never your own inference. Never something about another \
person: a relative's or friend's condition is not a fact about the user.
- Durable facts only: still true and useful a month from now, in a conversation about \
something else. Not passing states (tired today, a bad night), not the details of a \
one-off request (the routine they asked for today, an event tomorrow), not small talk, \
and not a decision that only holds for this conversation ("do exercise 1 first today").
- A decision or plan the user accepted and that stays valid afterwards IS written down, \
as a fact with the project label it belongs to.
- Health facts are wanted: injuries, surgeries, diagnoses the user reports, ongoing \
pain, movements a clinician told them to avoid. Also goals; lasting personal likes, \
dislikes and habits, the things a friend would remember about them (a favourite food \
or drink, a sport or hobby); and basic profile facts (job, routine, equipment). A lasting \
like or habit counts even when it is mentioned in passing; what happened today does not.
- "kind" is "instruction" for how the assistant should behave and "fact" for something \
true about the user.
- "explicit" is true when the user states it directly ("I am...", "always...", "never...", \
"call me...") and false when it only comes up in passing or is implied.
- "project" is one of the known labels when the item belongs to it, a new SHORT label \
(a few words) when the user starts something new, else null.
- Never store identifiers: ID or card numbers, phone numbers, e-mail addresses, \
street addresses. If the user gives one, leave it out entirely.
- Resolve relative times with the conversation date. "Three months ago", "last month", \
"since last winter", "recently" must become an absolute month and year (for example \
"July 2026"), because a relative time is wrong a month later. If it cannot be made \
absolute, leave the time out rather than keep a relative one.
- Never copy text the user pasted in. At most one short fact saying what the user is \
doing with that text.

CATEGORIES (closed list, use exactly these words)
- condition: injuries, surgeries, diagnoses, ongoing pain or symptoms
- restriction: movements, loads or activities the user must or wants to avoid
- goal: something the user is working towards
- preference: lasting likes and dislikes
- profile: other stable facts about the user (job, routine, equipment, schedule)
- style: how the assistant should talk (how to address them, answer length, language)
- project: something the user is pursuing, and the choices they settled for it

HOW TO COMPARE WITH STORED FACTS
- The user's new statement says again what is already stored: REPEAT it, using the \
stored id, never ADD it again. Give the project label when it has one.
- The user's new statement changes or extends a stored fact: UPDATE it, using the \
stored id. Write the complete new text. When the old state matters, keep it briefly, \
for example "now pain-free when walking (in July 2026: pain after 10 minutes)".
- The user says a stored fact is no longer true, or says to forget it: DELETE it, \
using the stored id.
- The user says to forget everything: FORGET_ALL, alone.
- A locked fact must never be updated or deleted. If the user contradicts a locked \
fact, ADD the user's new statement as a new fact instead.
- Only use ids that appear in the stored facts. Never invent an id.
- Do not repeat in an ADD a fact that is already stored.

STYLE OF EACH FACT
- At most 8 operations in total, and each fact at most 300 characters.
- One fact per operation. Several facts mean several operations.
- Write each fact in the language the user wrote it in, as a short neutral statement \
about the user, understandable without the conversation (no "she said", no "as above").
- Only what was said. Do not add details, causes, severity or advice.

EXAMPLES (structure only; write facts in the user's own language)
User says they had a meniscus operation in May 2025 and nothing is stored:
{"operations": [{"op": "ADD", "kind": "fact", "fact": "Had meniscus surgery in May 2025", "category": "condition", "project": null, "explicit": true}]}
User says "call me Tri" and stored fact f2 says "Call me Du":
{"operations": [{"op": "UPDATE", "id": "f2", "fact": "Call me Tri", "category": "style", "project": null}]}
User says again they avoid deep squats and stored fact f1 says so:
{"operations": [{"op": "REPEAT", "id": "f1", "project": null}]}
User says "forget everything you noted about me":
{"operations": [{"op": "FORGET_ALL"}]}
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


def _check_text_op(op: Any) -> str | None:
    """The fact text for an ADD/UPDATE, or the drop reason."""
    fact = op.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return "empty_fact"
    fact = fact.strip()
    if len(fact) > MAX_FACT_CHARS:
        return "too_long"
    if op.get("category") not in CATEGORIES:
        return "bad_category"
    if _looks_like_identifier(fact):
        return "identifier"
    return None


def _check_project(op: Any) -> str | None:
    """The project label for an ADD/UPDATE/REPEAT, or the drop reason."""
    project = op.get("project")
    if project is None:
        return None
    if not isinstance(project, str) or len(project.strip()) > MAX_PROJECT_CHARS:
        return "bad_project"
    return None


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
            if op.get("kind") not in ("instruction", "fact"):
                drop("bad_kind", op)
                continue
            if not isinstance(op.get("explicit"), bool):
                drop("bad_explicit", op)
                continue
            reason = _check_text_op(op)
            if reason is not None:
                drop(reason, op)
                continue
            reason = _check_project(op)
            if reason is not None:
                drop(reason, op)
                continue
            clean["kind"] = op["kind"]
            clean["fact"] = op["fact"].strip()
            clean["category"] = op["category"]
            clean["project"] = op["project"].strip() if op["project"] else None
            clean["explicit"] = op["explicit"]
        elif kind == "UPDATE":
            reason = _check_text_op(op)
            if reason is not None:
                drop(reason, op)
                continue
            reason = _check_project(op)
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
            clean["id"] = fid
            clean["fact"] = op["fact"].strip()
            clean["category"] = op["category"]
            clean["project"] = op["project"].strip() if op["project"] else None
        elif kind == "REPEAT":
            reason = _check_project(op)
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
            clean["id"] = fid
            clean["project"] = op["project"].strip() if op["project"] else None
        else:  # DELETE
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


# ── Tier policy (pure: no database, no clock) ─────────────────────────────

def _is_safe(category: str) -> bool:
    """A safety-relevant category reaches tier 1 on its first sighting."""
    return category in SAFE_CATEGORIES


def apply_policy(row: dict | None, op: dict, ctx: dict) -> dict:
    """Decide what one operation writes. Returns {"action", "fields"}.

    `row` is the stored row (or None for ADD): {"id", "fact", "category",
    "kind", "tier", "status", "project", "seen_count", "seen_projects",
    "last_seen_session_id", "source"}. `ctx` carries the counting session and
    the thresholds: {"session_id", "promote_fact_at", "promote_instruction_at"}.
    `fields` holds the values to write; the caller stamps said_at/updated_at.

    Actions: add | refresh (REPEAT seen again) | rewrite (content changed) |
    demote (a changed tier-1 instruction falls back to candidate) |
    hide (valid=false) | forget_all | noop.
    """
    session_id = ctx["session_id"]
    kind = op["op"]
    if kind == "ADD":
        project = op.get("project")
        label = project if project else ""
        if _is_safe(op["category"]):
            tier, status = 1, "active"
        elif op["kind"] == "fact":
            tier, status = 2, ("active" if op["explicit"] else "candidate")
        else:
            # A non-safe instruction never enters tier 1 directly.
            tier, status = 2, ("active" if op["explicit"] else "candidate")
        return {"action": "add", "fields": {
            "kind": op["kind"], "fact": op["fact"], "category": op["category"],
            "tier": tier, "status": status, "project": project,
            "seen_count": 1, "seen_projects": [label],
            "last_seen_session_id": session_id,
        }}
    if kind == "REPEAT":
        if row is None or row.get("last_seen_session_id") == session_id:
            # One conversation counts once.
            return {"action": "noop", "fields": {}}
        project = op.get("project")
        seen = (row.get("seen_count") or 0) + 1
        projects = list(row.get("seen_projects") or []) + [project if project else ""]
        fields: dict[str, Any] = {
            "seen_count": seen, "seen_projects": projects,
            "last_seen_session_id": session_id,
        }
        status, tier = row.get("status"), row.get("tier")
        if status == "candidate" and seen >= ctx["promote_fact_at"]:
            status = "active"
        if (row.get("kind") == "instruction"
                and seen >= ctx["promote_instruction_at"]):
            labels = set(projects)
            if len(labels) == 1 and next(iter(labels)):
                # Said in every conversation about the same project: it
                # belongs to that project, not to everywhere.
                tier, project, status = 2, projects[-1], "active"
            else:
                tier, project, status = 1, None, "active"
        fields.update({"tier": tier, "status": status, "project": project})
        return {"action": "refresh", "fields": fields}
    if kind == "UPDATE":
        project = op.get("project")
        if project is None:
            project = row.get("project")
        fields = {"fact": op["fact"], "category": op["category"],
                  "project": project, "last_seen_session_id": session_id}
        if row.get("tier") == 1 and not _is_safe(op["category"]):
            # A changed tier-1 instruction: fall back to candidate.
            label = project if project else ""
            fields.update({"tier": 2, "status": "candidate",
                           "seen_count": 1, "seen_projects": [label]})
            return {"action": "demote", "fields": fields}
        return {"action": "rewrite", "fields": fields}
    if kind == "DELETE":
        return {"action": "hide", "fields": {}}
    if kind == "FORGET_ALL":
        return {"action": "forget_all", "fields": {}}
    return {"action": "noop", "fields": {}}


# ── The call ──────────────────────────────────────────────────────────────

def render_user_message(date: str, tier1: list[dict], tier2: list[dict],
                        projects: list[str], msgs: list[dict], scope: str) -> str:
    """The user message: the date, both tiers, the project labels, the slice.

    `tier1` is every standing instruction; `tier2` the newest tier-2 rows.
    Each row carries {"id", "fact", "category", "kind", "project", "locked"}.
    `scope` tells the model what the messages are, a whole conversation or
    part of one.
    """
    def _lines(rows: list[dict]) -> str:
        return "\n".join(
            json.dumps({"id": f["id"], "kind": f.get("kind", "fact"),
                        "fact": f["fact"], "category": f["category"],
                        "project": f.get("project"),
                        "locked": bool(f.get("locked", False))},
                       ensure_ascii=False)
            for f in rows)

    standing = _lines(tier1) if tier1 else "(none)"
    others = _lines(tier2) if tier2 else "(none)"
    labels = ", ".join(projects) if projects else "(none)"
    lines = "\n".join(f"[{m['role']}] {m['content']}" for m in msgs)
    return (f"Conversation date: {date}\n\n"
            f"Standing instructions (apply in every conversation):\n{standing}\n\n"
            f"Other stored facts, newest first:\n{others}\n\n"
            f"Known project labels: {labels}\n\n"
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
_MAX_TIER2_SHOWN = 60

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
    SELECT id, fact_text, category, kind, tier, status, project,
           seen_count, seen_projects, last_seen_session_id, source, said_at
    FROM user_memory
    WHERE user_id = $1 AND valid = true AND tier = 1
    ORDER BY CASE category
                 WHEN 'restriction' THEN 0
                 WHEN 'condition' THEN 1
                 ELSE 2
             END,
             said_at DESC
"""

_TIER2_SQL = """
    SELECT id, fact_text, category, kind, tier, status, project,
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
    INSERT INTO user_memory (user_id, fact_text, category, kind, tier, status,
                             project, seen_count, seen_projects, source,
                             source_session_id, last_seen_session_id,
                             said_at, updated_at)
    SELECT $1::uuid, $2::text, $3::text, $4::text, $5::smallint, $6::text,
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
        tier = $6::smallint, status = $7::text, project = $8::text,
        said_at = now(), updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""

_UPDATE_SQL = """
    UPDATE user_memory
    SET fact_text = $3, category = $4, project = $5::text,
        tier = $6::smallint, status = $7::text,
        seen_count = $8, seen_projects = $9::text[],
        last_seen_session_id = $10::uuid, said_at = now(), updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
"""

# Retired, not removed: the row stays and the memory node stops loading it.
_HIDE_SQL = """
    UPDATE user_memory
    SET valid = false, updated_at = now()
    WHERE id = $1::uuid AND user_id = $2::uuid AND source = 'extracted' AND valid = true
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
    DELETE FROM user_memory WHERE user_id = $1 AND (
      (status = 'candidate' AND said_at < now() - make_interval(days => $2))
      OR (valid = false AND updated_at < now() - make_interval(days => $3)))
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
                           (user_id, f["fact"], f["category"], f["kind"],
                            f["tier"], f["status"], f["project"],
                            f["seen_count"], f["seen_projects"], session_id)))
        elif op["op"] == "REPEAT":
            decided = apply_policy(rows[op["id"]], op, ctx)
            if decided["action"] == "noop":
                continue
            f = decided["fields"]
            writes.append(("repeated", "gone", _REPEAT_SQL,
                           (row_ids[op["id"]], user_id, f["seen_count"],
                            f["seen_projects"], f["last_seen_session_id"],
                            f["tier"], f["status"], f["project"])))
        elif op["op"] == "UPDATE":
            decided = apply_policy(rows[op["id"]], op, ctx)
            f = decided["fields"]
            row = rows[op["id"]]
            writes.append(("updated", "gone", _UPDATE_SQL,
                           (row_ids[op["id"]], user_id, f["fact"], f["category"],
                            f["project"], f.get("tier", row["tier"]),
                            f.get("status", row["status"]),
                            f.get("seen_count", row["seen_count"]),
                            f.get("seen_projects", row["seen_projects"]),
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
                "id": short, "fact": r["fact_text"], "category": r["category"],
                "kind": r.get("kind", "fact"), "tier": r.get("tier", 2),
                "status": r.get("status", "active"), "project": r.get("project"),
                "seen_count": r.get("seen_count", 1),
                "seen_projects": list(r.get("seen_projects") or []),
                "last_seen_session_id": r.get("last_seen_session_id"),
                "source": r.get("source", "extracted"),
            }
            stored.append({"id": short, "fact": r["fact_text"],
                           "category": r["category"],
                           "locked": r["source"] != "extracted"})

        def _view(short: str) -> dict:
            f = full[short]
            return {"id": short, "fact": f["fact"], "category": f["category"],
                    "kind": f["kind"], "project": f["project"],
                    "locked": f["source"] != "extracted"}

        ctx = {"session_id": session_id, **policy}
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
    """Delete expired candidates and long-hidden rows, wherever they sit."""
    tag = await pg.execute(_PURGE_SQL, user_id, policy["candidate_ttl_days"],
                           policy["forget_purge_days"])
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
            """SELECT id, fact_text, project FROM user_memory
               WHERE user_id = $1 AND valid = true AND tier = 2
                 AND embedding IS NULL
               ORDER BY said_at LIMIT $2""",
            user_id, limit,
        )
        if not rows:
            return 0
        texts = [(f"{r['project']}: {r['fact_text']}" if r["project"]
                  else r["fact_text"]) for r in rows]
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
