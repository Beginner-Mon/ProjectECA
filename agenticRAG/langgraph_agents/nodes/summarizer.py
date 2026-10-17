"""Background summarizer — M.5 (chunk-based, frozen, CAS idempotent).

Decisions encoded:
  D13 (changed 17/10): the current session is summarised as its messages fall
        out of the memory node's recent-raw window (20 messages / history
        budget), once ~500 tokens of them have piled up — not at 10k tokens.
        With a 1k-token window, the 10k rule left everything between 1k and 10k
        in no prompt at all.
  M.5:  Summary chunk-based, trigger by TOKEN cumulative, runs BACKGROUND,
        frozen on write (never re-compressed), CAS idempotent
  M.7:  Summary chunks = STATIC prefix → prompt cache works (append-only)

Two triggers, both run by `housekeep`, which /chat starts with the turn and
awaits after its `done` event — inside the invocation, because Lambda freezes
a container once the response ends, and a task left running after it resumed
on a closed connection (12/10: 26 failed triggers, 5 summaries since 01/09):
  - the user has moved on: another session of theirs has ≥ 2 unsummarised
    turns → summarise it (long-term memory, read back by memory_search);
  - in the current session, messages that fell out of the recent-raw window
    reached _COMPACT_MIN_TOKENS since the last summary (D13, 17/10).

Every summary ends on a message boundary (`covers_up_to_seq`), never inside
one, and the next one starts at the message after it. It also never ends on a
user question: the answer is taken along, even if that answer is still in the
window (shown twice for a while, never lost).

Edge A (M.5): summarize fail → retry 1× → still fail → log WARNING, leave.
Hard cap: memory node uses existing token budget to prevent unbounded growth.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from langgraph_agents.shared import get_pg_client, get_embedding_service
from langgraph_agents.shared.logging import get_logger
from langgraph_agents.llm import get_chat_model

logger = get_logger("langgraph.summarizer")

# Strong references to fire-and-forget summarizer tasks (same pattern as TTS)
_pending_summarizer_tasks: set = set()

# Out-of-window tokens that make a summary worth an LLM call. Smaller means
# less forgotten between summaries but more, shorter chunks in every prompt.
_COMPACT_MIN_TOKENS = 500
_MAX_RETRY = 1                # Edge A: retry once


def _token_estimate(text: str) -> int:
    """Rough token count: ~4 chars per token for Vietnamese/English."""
    return len(text) // 4


def _window_start(rows: list) -> int:
    """Index of the first of `rows` the memory node still shows as raw history.

    Same rule as memory._load_recent_raw: the last _RECENT_RAW_MAX messages,
    then newest first while they fit in history_budget(), whole messages only.
    `rows` are the messages after the last summary, oldest first; anything the
    window holds that is older than them is already summarised, and being older
    it cannot change which of them fit.
    """
    from langgraph_agents.nodes.memory import (
        _RECENT_RAW_MAX, _select_recent_raw, history_budget,
    )
    tail = [{"role": r["role"], "content": r["content"] or ""} for r in rows[-_RECENT_RAW_MAX:]]
    return len(rows) - len(_select_recent_raw(tail, history_budget()))


async def maybe_summarize(session_id: str) -> None:
    """Summarise what of the current session has fallen out of the window (D13).

    Called from housekeep. Never raises — all errors logged.
    """
    try:
        pg = get_pg_client()
        await pg.connect()

        # Get last summary chunk's covers_up_to_seq (0 if none)
        last_chunk = await pg.fetchval(
            """SELECT COALESCE(MAX(covers_up_to_seq), 0)
               FROM summaries
               WHERE session_id = $1 AND status = 'active'""",
            session_id,
        )

        rows = await pg.fetch(
            """SELECT seq_id, role, content FROM messages
               WHERE session_id = $1 AND seq_id > $2
               ORDER BY seq_id""",
            session_id, last_chunk or 0,
        )
        start = _window_start(rows)
        outside = rows[:start]
        # Same estimate the window uses, so "fell out" and "piled up" agree.
        if sum(_token_estimate(r["content"] or "") for r in outside) < _COMPACT_MIN_TOKENS:
            return

        end = start - 1
        if rows[end]["role"] == "user" and end + 1 < len(rows):
            end += 1
        await _run_summarize(session_id, last_chunk or 0, up_to_seq=rows[end]["seq_id"])

    except Exception as exc:
        logger.warning("summarizer_trigger_failed", extra={
            "session_id": session_id, "error": str(exc),
        })


# A session counts as finished once the user chats in another one. Two turns is
# enough to be worth remembering; real sessions are short (prod 12/10: median
# ~400 tokens, largest ~2,800), so a token threshold never fired.
_MIN_FINISHED_MESSAGES = 4
_MAX_FINISHED_PER_TURN = 2


async def summarize_finished_sessions(user_id: str, current_session_id: str) -> int:
    """Summarise the user's other sessions that have unsummarised turns.

    Newest first, at most _MAX_FINISHED_PER_TURN per turn; each picks up from
    where its last summary stopped, so a session the user returns to is
    summarised again only for what is new. Returns how many were summarised.
    """
    pg = get_pg_client()
    await pg.connect()
    rows = await pg.fetch(
        """SELECT c.session_id, COALESCE(s.covered, 0) AS covered
           FROM conversations c
           LEFT JOIN LATERAL (
               SELECT MAX(covers_up_to_seq) AS covered FROM summaries
               WHERE session_id = c.session_id AND status = 'active'
           ) s ON true
           WHERE c.user_id = $1 AND c.session_id <> $2
             AND (SELECT count(*) FROM messages m
                  WHERE m.session_id = c.session_id
                    AND m.seq_id > COALESCE(s.covered, 0)) >= $3
           ORDER BY c.updated_at DESC
           LIMIT $4""",
        user_id, current_session_id, _MIN_FINISHED_MESSAGES, _MAX_FINISHED_PER_TURN,
    )
    for r in rows:
        await _run_summarize(r["session_id"], r["covered"])
    return len(rows)


async def housekeep(user_id: str, session_id: str) -> None:
    """Both summary triggers for one chat turn. Never raises.

    Runs on ECA's key: a user's own key (BYOK) is for their chat turn, not for
    summaries written on their behalf. The unbind only touches this task's
    copy of the context, never the request's.
    """
    from langgraph_agents.llm_byok import bind_request_llm

    bind_request_llm(None)
    try:
        await summarize_finished_sessions(user_id, session_id)
    except Exception as exc:
        logger.warning("finished_session_summary_failed", extra={
            "session_id": session_id, "error": str(exc),
        })
    await maybe_summarize(session_id)


async def _summarize_messages(rows: list, session_id: str) -> str | None:
    """LLM-summarize a list of message rows → summary_text or None if fail.

    rows = records with {role, content, token_count}.
    Uses the same prompt + retry logic as _run_summarize.
    Extracted so rebuild_dirty_chunk can reuse it (M.8 #5).
    """
    lines = []
    for r in rows:
        label = "User" if r["role"] == "user" else "Assistant"
        lines.append(f"{label}: {r['content']}")

    conversation = "\n".join(lines)

    llm = get_chat_model("planner")
    # "The same language as the conversation", not a named language. This used to
    # force Vietnamese, so an English session was summarised into Vietnamese and
    # then fed back to the model as context on the next turn — pushing it toward
    # a language the user had not written in.
    prompt = (
        "Summarise the following conversation in 2-4 sentences, written in the "
        "SAME language as the conversation itself. Keep what matters clinically: "
        "symptoms, exercises recommended, contraindications, and the user's "
        "progress.\n\n"
        f"{conversation}"
    )

    summary_text = ""
    for attempt in range(_MAX_RETRY + 1):
        try:
            ai_msg = await llm.ainvoke(prompt)
            summary_text = ai_msg.content.strip() if ai_msg.content else ""
            if summary_text:
                break
        except Exception as exc:
            if attempt < _MAX_RETRY:
                logger.warning("summarizer_llm_retry", extra={
                    "session_id": session_id, "attempt": attempt + 1,
                    "error": str(exc),
                })
                await asyncio.sleep(0.5)
            else:
                logger.error("summarizer_llm_failed", extra={
                    "session_id": session_id, "errors": str(exc),
                })
                return None

    if not summary_text:
        logger.warning("summarizer_empty_response", extra={
            "session_id": session_id,
        })
        return None

    return summary_text


async def _run_summarize(session_id: str, from_seq: int, up_to_seq: int | None = None) -> None:
    """Summarize messages in (from_seq, up_to_seq], or up to the latest if None."""
    t0 = time.perf_counter()

    try:
        pg = get_pg_client()
        await pg.connect()

        # Load messages not yet covered
        if up_to_seq is None:
            rows = await pg.fetch(
                """SELECT seq_id, role, content, token_count
                   FROM messages
                   WHERE session_id = $1 AND seq_id > $2
                   ORDER BY seq_id""",
                session_id, from_seq,
            )
        else:
            rows = await pg.fetch(
                """SELECT seq_id, role, content, token_count
                   FROM messages
                   WHERE session_id = $1 AND seq_id > $2 AND seq_id <= $3
                   ORDER BY seq_id""",
                session_id, from_seq, up_to_seq,
            )

        if not rows:
            return

        total_tokens = 0
        for r in rows:
            total_tokens += r["token_count"] or _token_estimate(r["content"] or "")

        covers_up_to_seq = rows[-1]["seq_id"]

        # ── LLM summarize (cheap model tier) ───────────────────────────
        summary_text = await _summarize_messages(rows, session_id)
        if not summary_text:
            return

        # ── Embed + INSERT (same transaction) ───────────────────────────
        embed_svc = get_embedding_service()
        embedding = await embed_svc.aembed_passage(summary_text)

        await pg.execute(
            """INSERT INTO summaries
               (session_id, summary_text, covers_from_seq, covers_up_to_seq,
                embedding, status)
               VALUES ($1, $2, $3, $4, $5, 'active')
               ON CONFLICT ON CONSTRAINT uq_chunk DO NOTHING""",
            session_id, summary_text, from_seq + 1, covers_up_to_seq, embedding,
        )

        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        logger.info("summarizer_done", extra={
            "session_id": session_id,
            "covers_from_seq": from_seq + 1,
            "covers_up_to_seq": covers_up_to_seq,
            "token_count": total_tokens,
            "summary_length": len(summary_text),
            "elapsed_ms": elapsed_ms,
        })

    except Exception as exc:
        logger.error("summarizer_fatal", extra={
            "session_id": session_id, "error": str(exc),
        })


async def rebuild_dirty_chunk(session_id: str, chunk_id: str) -> bool:
    """Re-summarize 1 dirty chunk from messages remaining in its range (M.8 #5,#6).

    Returns True if chunk is back to active; False if skipped (empty/LLM fail).
    """
    pg = get_pg_client()
    await pg.connect()

    # 1. Get range of the dirty chunk
    chunk = await pg.fetchrow(
        "SELECT covers_from_seq, covers_up_to_seq FROM summaries "
        "WHERE id = $1 AND session_id = $2 AND status = 'dirty'",
        chunk_id, session_id,
    )
    if not chunk:
        return False   # already handled / no longer dirty (idempotent)

    # 2. Load messages REMAINING in range (deleted ones are gone)
    rows = await pg.fetch(
        "SELECT role, content, token_count FROM messages "
        "WHERE session_id = $1 AND seq_id BETWEEN $2 AND $3 ORDER BY seq_id",
        session_id, chunk["covers_from_seq"], chunk["covers_up_to_seq"],
    )
    if not rows:
        return False   # empty-chunk: delete_message already removed it (M.8 #3)

    # 3. Re-summarize + embed + UPDATE (call gdpr with correct 4-arg signature)
    summary_text = await _summarize_messages(rows, session_id)
    if not summary_text:
        return False   # LLM fail → leave dirty, retry next time

    embedding = await get_embedding_service().aembed_passage(summary_text)

    from langgraph_agents.db.gdpr import re_summarize_chunk
    return await re_summarize_chunk(session_id, chunk_id, summary_text, embedding)
