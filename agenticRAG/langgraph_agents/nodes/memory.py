"""Memory node — M.5 (in-session) + M.6 Tier 1 (user_memory facts).

Runs FIRST before planner (D9: coreference resolve needs memory context).
Loads what the model is shown about this user and this conversation:

    user_instructions tier-1 standing instructions (safety-relevant first)
    session_summary   this session's rolling summary (0 or 1 item)
    user_facts        tier-2 facts about this turn's topic (dated, by project)
    messages          raw history the summary does not cover yet (user/assistant only)

Facts and the summary are state fields of their own, which reverses D20 ("context
flows through messages"): the message route is exactly where they were being
dropped.

M.5 rules:
  - PostgreSQL = source of truth, and the only store read here
  - Summary: one rolling summary per session, merged forward, CAS idempotent
  - Raw history = every message after the summary, so summary + raw
    always cover the whole session
  - ... except the running turn's own question: /chat writes it as the turn
    starts, and the synthesizer adds it to the prompt itself. A question left
    by an earlier turn that never got its answer stays in
  - A session the summary covers to its last message still hands on its latest
    turns, the one case where raw history and summaries overlap
  - The summarizer compacts once the raw part passes `history_compact_at`,
    leaving `history` raw
  - Edge A: summarize fail → raw part keeps growing → capped at
    `history_compact_at + history`, oldest turns dropped and logged
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig

from langgraph_agents.state import AgentState, ErrorSeverity
from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.context import context_budget
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.memory")


# ── Config ────────────────────────────────────────────────────────────────

def _load_memory_config() -> dict:
    import yaml
    config_path = Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f).get("langgraph", {}).get("memory", {})
    return {}


_MEM_CFG = _load_memory_config()
# _REDIS_URL and _RECENT_RAW_KEY were here. Both moved to shared/stm.py on
# 21-08, which owns the key format and the connection for all four call sites.
#
# The config value they read (`langgraph.memory.redis_url` in
# config/langgraph.yaml) is no longer consulted: a URL in a committed YAML file
# cannot differ per environment, which is the whole requirement once this runs
# on Lambda. The store reads REDIS_URL from the environment instead.
_STM_TOKEN_BUDGET = 1500      # legacy default; use history_budget() instead (T11)

# Most rows one history read brings back. The ceiling below is reached long
# before this in any real conversation; it bounds a session whose compaction
# has stalled.
_RAW_HISTORY_MAX_ROWS = 200
# Rows read to find the latest turns of a session that is summarised to its
# end. Ten turns is more than the `history` budget holds of ordinary answers.
_LATEST_TURNS_MAX_ROWS = 20


def history_budget() -> int:
    """Token budget for recent raw (plan T11: config `history`, thiếu = cũ)."""
    return context_budget("history")


def history_ceiling() -> int:
    """Most raw history this node hands on, in tokens.

    The summarizer compacts above `history_compact_at` and leaves `history`
    raw, but it runs alongside the turn, so one more turn can land on a session
    that is due. Past that much, compaction is lagging or failing.
    """
    return context_budget("history_compact_at") + history_budget()


# ── Tier 1: standing user instructions (always-on, cheap, no vector search)

async def _load_user_instructions(user_id: str) -> list[str]:
    """Load tier-1 instructions, the safety-relevant ones first.

    The prompt budgets downstream drop lines from the END of this list, so a
    movement restriction or a health condition must not queue behind a
    preference. Newest said first within each group.
    """
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT fact_text FROM user_memory
            WHERE user_id = $1 AND valid = true
              AND tier = 1 AND status = 'active'
            ORDER BY CASE category
                         WHEN 'restriction' THEN 0
                         WHEN 'condition' THEN 1
                         ELSE 2
                     END,
                     said_at DESC
            """,
            user_id,
        )
        return [r["fact_text"] for r in rows]
    except Exception as exc:
        logger.warning("user_instructions_load_failed", extra={"error": str(exc)})
        return []


# ── Tier 2: project facts, retrieved by similarity to this turn's query ──

def _tier2_config() -> dict:
    import yaml
    from pathlib import Path
    try:
        config_path = Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                memory = yaml.safe_load(f).get("langgraph", {}).get("memory", {})
            return {
                "timeout_ms": int(memory.get("tier2_embed_timeout_ms", 300)),
                "top_k": int(memory.get("tier2_top_k", 5)),
                "min_similarity": float(memory.get("tier2_min_similarity", 0.86)),
            }
    except Exception:
        pass
    return {"timeout_ms": 300, "top_k": 5, "min_similarity": 0.86}


def _tier2_line(row) -> str:
    """One tier-2 row as `- [date] (project) fact` (no parens without a label)."""
    said = row["said_at"]
    day = said.date().isoformat() if hasattr(said, "date") else str(said)[:10]
    label = f"({row['project']}) " if row.get("project") else ""
    return f"- [{day}] {label}{row['fact_text']}"


async def _load_tier2_facts(
    user_id: str, session_id: str, query: str | None,
) -> tuple[list[str], float | None, str | None, int | None]:
    """Tier-2 facts about this turn's topic.

    Returns (lines, top_similarity, skipped_reason, embed_ms). A row last
    seen in this conversation is skipped there — the STM summary already
    covers it. Tier 1 has no such filter: an instruction is one row, and a
    safety note must not depend on the summary.
    """
    import asyncio
    import time

    from langgraph_agents.shared import get_embedding_service

    cfg = _tier2_config()
    if not query:
        return [], None, "no_query", None
    t0 = time.perf_counter()
    try:
        vector = await asyncio.wait_for(
            get_embedding_service().aembed_query(query),
            timeout=cfg["timeout_ms"] / 1000,
        )
    except asyncio.TimeoutError:
        logger.info("tier2_skipped", extra={"reason": "embed_timeout"})
        return [], None, "embed_timeout", None
    except Exception as exc:
        logger.info("tier2_skipped", extra={
            "reason": "embed_error", "error": type(exc).__name__})
        return [], None, "embed_error", None
    embed_ms = round((time.perf_counter() - t0) * 1000)
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT id, fact_text, project, said_at,
                   1 - (embedding <=> $1) AS similarity
            FROM user_memory
            WHERE user_id = $2 AND valid = true
              AND tier = 2 AND status = 'active'
              AND embedding IS NOT NULL
              AND last_seen_session_id IS DISTINCT FROM $3::uuid
            ORDER BY embedding <=> $1
            LIMIT $4
            """,
            vector, user_id, session_id, cfg["top_k"],
        )
        top_sim = max((float(r["similarity"]) for r in rows), default=None)
        hits = [r for r in rows if float(r["similarity"]) >= cfg["min_similarity"]]
        if not hits:
            return [], top_sim, None, embed_ms
        labels = sorted({r["project"] for r in hits if r.get("project")})
        lines = [_tier2_line(r) for r in hits]
        if labels:
            mates = await pg.fetch(
                """
                SELECT id, fact_text, project, said_at
                FROM user_memory
                WHERE user_id = $1 AND valid = true
                  AND tier = 2 AND status = 'active'
                  AND project = ANY($2)
                  AND last_seen_session_id IS DISTINCT FROM $3::uuid
                ORDER BY said_at DESC
                """,
                user_id, labels, session_id,
            )
            seen = {r["id"] for r in hits}
            lines += [_tier2_line(r) for r in mates if r["id"] not in seen]
        return lines, top_sim, None, embed_ms
    except Exception as exc:
        logger.warning("tier2_load_failed", extra={"error": str(exc)})
        return [], None, "load_error", embed_ms


# ── Rolling STM summary + raw history (one statement) ───────────────

async def _load_stm(
    session_id: str, exclude_message_id: str | None = None,
) -> tuple[list[str], list[dict]]:
    """This session's rolling summary plus the messages it does not cover yet.

    One statement: the summary rides along every message row. Read newest
    first so that the row limit keeps the recent end.

    `exclude_message_id` is the running turn's own question, which may or may
    not have reached the table yet.
    """
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT c.stm_summary, m.role, m.content FROM conversations c
            LEFT JOIN LATERAL (
                SELECT m.role, m.content FROM messages m
                WHERE m.session_id = c.session_id
                  AND m.seq_id > c.stm_up_to_seq
                  AND ($2::uuid IS NULL OR m.id <> $2)
                ORDER BY m.seq_id DESC
                LIMIT $3
            ) m ON true
            WHERE c.session_id = $1
            """,
            session_id, exclude_message_id, _RAW_HISTORY_MAX_ROWS,
        )
        if not rows:
            return [], []
        summary = rows[0]["stm_summary"]
        history = [{"role": r["role"], "content": r["content"]}
                   for r in reversed(rows) if r["role"] is not None]
        return ([summary] if summary else [], history)
    except Exception as exc:
        logger.warning("stm_load_failed", extra={"error": str(exc)})
        return [], []


def _token_estimate(text: str) -> int:
    """Rough token count: ~4 chars per token for Vietnamese/English."""
    return len(text) // 4


async def _load_latest_turns(
    session_id: str, exclude_message_id: str | None = None,
) -> list[dict]:
    """The session's latest turns that fit the `history` budget, summarised or not.

    For a session whose every message is under a summary: the user left it,
    another turn's housekeeping summarised it to the end, and now they are
    back. The summary alone would leave the model without the last exchange
    in the user's and its own words.
    """
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT role, content FROM messages
            WHERE session_id = $1
              AND ($2::uuid IS NULL OR id <> $2)
            ORDER BY seq_id DESC
            LIMIT $3
            """,
            session_id, exclude_message_id, _LATEST_TURNS_MAX_ROWS,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        return _latest_turns(history, history_budget())
    except Exception as exc:
        logger.warning("latest_turns_load_failed", extra={"error": str(exc)})
        return []


def _latest_turns(history: list[dict], budget: int) -> list[dict]:
    """The newest whole turns of `history` that fit `budget` tokens.

    A turn starts at a user message; a question that never got its answer is a
    turn of its own. The latest turn is kept whatever its size. Before it only
    whole turns are kept, so the result begins with a user message whenever
    the latest turn does.
    """
    sizes = [_token_estimate(m.get("content") or "") for m in history]
    kept_from = len(history)      # index of the first message kept
    kept_tokens = 0
    while kept_from > 0:
        turn_from = kept_from - 1
        while turn_from > 0 and history[turn_from]["role"] != "user":
            turn_from -= 1
        turn_tokens = sum(sizes[turn_from:kept_from])
        if kept_from < len(history) and (
            history[turn_from]["role"] != "user"
            or kept_tokens + turn_tokens > budget
        ):
            break
        kept_from = turn_from
        kept_tokens += turn_tokens
    return history[kept_from:]


def _drop_history_overflow(history: list[dict], session_id: str) -> list[dict]:
    """Drop the oldest turns while the raw history is over the ceiling.

    Whole turns, and never the latest one. Loud on purpose: what goes here is
    in no summary either, so the model has a hole in this conversation until
    the summarizer catches up.
    """
    ceiling = history_ceiling()
    sizes = [_token_estimate(m.get("content") or "") for m in history]
    if sum(sizes) <= ceiling:
        return history
    kept = _latest_turns(history, ceiling)
    dropped = len(history) - len(kept)
    if not dropped:
        return history            # one turn, however long
    logger.warning("history_overflow_dropped", extra={
        "session_id": session_id,
        "dropped_messages": dropped,
        "kept_messages": len(kept),
        "dropped_tokens": sum(sizes[:dropped]),
        "kept_tokens": sum(sizes[dropped:]),
        "ceiling_tokens": ceiling,
    })
    return kept


def _recent_raw_to_messages(recent: list[dict]) -> list:
    """Convert recent raw dicts to LangChain messages."""
    msgs = []
    for item in recent:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    return msgs


# ── Node ──────────────────────────────────────────────────────────────────

async def memory_node(state: AgentState, config: RunnableConfig) -> dict:
    """Memory node — runs FIRST (before planner, D9).

    Loads standing instructions + rolling summary + raw history + the tier-2
    facts about this turn's topic. Instructions and the summary go out as
    `user_instructions` / `session_summary`, tier-2 lines as `user_facts`;
    `messages` gets the raw history as user/assistant messages and nothing else.
    """
    t0 = time.perf_counter()
    request_id = config["configurable"].get("request_id", "-")
    user_id = config["configurable"]["user_id"]
    session_id = config["configurable"]["session_id"]
    # Absent outside /chat (the local probes): nothing is left out then.
    user_message_id = config["configurable"].get("user_message_id")
    query = config["configurable"].get("query")

    logger.info("node_start", extra={
        "node": "memory", "request_id": request_id, "session_id": session_id,
    })

    # All three loads are independent — run concurrently
    user_instructions, (summary_chunks, raw_history), tier2 = await asyncio.gather(
        _load_user_instructions(user_id),
        _load_stm(session_id, user_message_id),
        _load_tier2_facts(user_id, session_id, query),
    )
    user_facts, tier2_top_similarity, tier2_skipped, tier2_embed_ms = tier2
    if raw_history:
        raw_history = _drop_history_overflow(raw_history, session_id)
    elif summary_chunks:
        # Nothing after the last summary: one more read, on this turn only —
        # its own answer will be raw history for the next.
        raw_history = await _load_latest_turns(session_id, user_message_id)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("node_complete", extra={
        "node": "memory", "request_id": request_id,
        "elapsed_ms": elapsed_ms,
        "user_instructions_count": len(user_instructions),
        "user_facts_count": len(user_facts),
        "tier2_top_similarity": tier2_top_similarity,
        "tier2_embed_ms": tier2_embed_ms,
        "tier2_skipped": tier2_skipped,
        "summary_chunks_count": len(summary_chunks),
        "summary_present": bool(summary_chunks),
        "recent_raw_count": len(raw_history),
    })

    return {
        "messages": _recent_raw_to_messages(raw_history),
        "user_instructions": user_instructions,
        "user_facts": user_facts,
        "session_summary": summary_chunks,
    }
