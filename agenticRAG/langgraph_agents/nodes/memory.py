"""Memory node — M.5 (in-session) + M.6 Tier 1 (user_memory facts).

Runs FIRST before planner (D9: coreference resolve needs memory context).
Loads what the model is shown about this user and this conversation:

    user_facts        user_memory facts (Tier 1), safety-relevant first
    session_summary   summary chunks of this session (frozen), oldest first
    messages          raw history no chunk covers yet (user/assistant only)

Facts and summaries are state fields of their own, which reverses D20 ("context
flows through messages"): the message route is exactly where they were being
dropped.

M.5 rules:
  - PostgreSQL = source of truth, and the only store read here
  - Summary: chunk-based, frozen on write, CAS idempotent
  - Raw history = every message after the last active chunk, so chunks + raw
    always cover the whole session
  - The summarizer compacts once the raw part passes `history_compact_at`,
    leaving `history` raw
  - Edge A: summarize fail → raw part keeps growing → capped at
    `history_compact_at + history`, oldest dropped and logged
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
_SUMMARY_TOKEN_THRESHOLD = 10_000   # D13: single threshold for summarize trigger
_STM_TOKEN_BUDGET = 1500      # legacy default; use history_budget() instead (T11)


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


# ── Tier 1: user_memory facts (always-on, cheap, no vector search) ────────

async def _load_user_facts(user_id: str) -> list[str]:
    """Load Tier 1 facts from user_memory, the ones that bear on safety first.

    The prompt budgets downstream drop facts from the END of this list, so a
    movement restriction or a health condition must not queue behind a
    preference. Newest first within each group.
    """
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT fact_text FROM user_memory
            WHERE user_id = $1 AND valid = true
            ORDER BY CASE category
                         WHEN 'restriction' THEN 0
                         WHEN 'condition' THEN 1
                         ELSE 2
                     END,
                     created_at DESC
            LIMIT 50
            """,
            user_id,
        )
        return [r["fact_text"] for r in rows]
    except Exception as exc:
        logger.warning("user_facts_load_failed", extra={"error": str(exc)})
        return []


# ── Summary chunks (frozen, append-only, from summaries table) ────────────

async def _load_summary_chunks(session_id: str) -> list[str]:
    """Load active summary chunks for session, ordered by covers_from_seq."""
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT summary_text FROM summaries
            WHERE session_id = $1 AND status = 'active'
            ORDER BY covers_from_seq
            """,
            session_id,
        )
        return [r["summary_text"] for r in rows]
    except Exception as exc:
        logger.warning("summary_chunks_load_failed", extra={"error": str(exc)})
        return []


# ── Raw history (every message no summary chunk covers yet) ───────────────

def _token_estimate(text: str) -> int:
    """Rough token count: ~4 chars per token for Vietnamese/English."""
    return len(text) // 4


async def _load_raw_history(session_id: str) -> list[dict]:
    """Messages after the last active summary chunk, oldest first.

    One statement, so it runs alongside the other two loads. A dirty chunk
    (a message in its range was deleted) is not coverage until it is rebuilt.
    """
    try:
        pg = get_pg_client()
        await pg.connect()
        rows = await pg.fetch(
            """
            SELECT role, content FROM messages
            WHERE session_id = $1
              AND seq_id > COALESCE((
                  SELECT MAX(covers_up_to_seq) FROM summaries
                  WHERE session_id = $1 AND status = 'active'
              ), 0)
            ORDER BY seq_id
            """,
            session_id,
        )
        return [{"role": r["role"], "content": r["content"]} for r in rows]
    except Exception as exc:
        logger.warning("raw_history_load_failed", extra={"error": str(exc)})
        return []


def _drop_history_overflow(history: list[dict], session_id: str) -> list[dict]:
    """Drop the oldest messages while the raw history is over the ceiling.

    Loud on purpose: what goes here is in no summary either, so the model has
    a hole in this conversation until the summarizer catches up.
    """
    ceiling = history_ceiling()
    sizes = [_token_estimate(m.get("content") or "") for m in history]
    kept_tokens = sum(sizes)
    dropped = 0
    while kept_tokens > ceiling:
        kept_tokens -= sizes[dropped]
        dropped += 1
    if not dropped:
        return history
    logger.warning("history_overflow_dropped", extra={
        "session_id": session_id,
        "dropped_messages": dropped,
        "kept_messages": len(history) - dropped,
        "dropped_tokens": sum(sizes[:dropped]),
        "kept_tokens": kept_tokens,
        "ceiling_tokens": ceiling,
    })
    return history[dropped:]


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

    Loads user facts + summary chunks + raw history. Facts and summaries go
    out as `user_facts` / `session_summary`; `messages` gets the raw history
    as user/assistant messages and nothing else.
    """
    t0 = time.perf_counter()
    request_id = config["configurable"].get("request_id", "-")
    user_id = config["configurable"]["user_id"]
    session_id = config["configurable"]["session_id"]

    logger.info("node_start", extra={
        "node": "memory", "request_id": request_id, "session_id": session_id,
    })

    # All three loads are independent — run concurrently
    user_facts, summary_chunks, raw_history = await asyncio.gather(
        _load_user_facts(user_id),
        _load_summary_chunks(session_id),
        _load_raw_history(session_id),
    )
    raw_history = _drop_history_overflow(raw_history, session_id)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("node_complete", extra={
        "node": "memory", "request_id": request_id,
        "elapsed_ms": elapsed_ms,
        "user_facts_count": len(user_facts),
        "summary_chunks_count": len(summary_chunks),
        "recent_raw_count": len(raw_history),
    })

    return {
        "messages": _recent_raw_to_messages(raw_history),
        "user_facts": user_facts,
        "session_summary": summary_chunks,
    }
