"""Background summarizer — M.5 (chunk-based, frozen, CAS idempotent).

Decisions encoded:
  M.5:  Summary chunk-based, trigger by message size, runs BACKGROUND,
        frozen on write (never re-compressed), CAS idempotent
  M.7:  Summary chunks = STATIC prefix → prompt cache works (append-only)

Two triggers, both run by `housekeep`, which /chat starts with the turn and
awaits after its `done` event — inside the invocation, because Lambda freezes
a container once the response ends, and a task left running after it resumed
on a closed connection (12/10: 26 failed triggers, 5 summaries since 01/09):
  - the user has moved on: another session of theirs has ≥ 2 unsummarised
    turns → summarise it (long-term memory, read back by memory_search);
  - the current session's unsummarised part passed `history_compact_at` →
    summarise its oldest part, leaving the newest `history` tokens raw.

Edge A (M.5): summarize fail → retry 1× → still fail → log WARNING, leave.
Hard cap: memory node drops the oldest raw history past its ceiling.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from langgraph_agents.shared import get_pg_client, get_embedding_service
from langgraph_agents.shared.context import context_budget
from langgraph_agents.shared.logging import get_logger
from langgraph_agents.llm import get_chat_model

logger = get_logger("langgraph.summarizer")

# Strong references to fire-and-forget summarizer tasks (same pattern as TTS)
_pending_summarizer_tasks: set = set()

_MAX_RETRY = 1                # Edge A: retry once


def _token_estimate(text: str) -> int:
    """Rough token count: ~4 chars per token for Vietnamese/English."""
    return len(text) // 4


def _compaction_cut(rows: list, history_budget: int | None = None,
                    compact_at: int | None = None) -> int | None:
    """seq_id to summarise up to, or None while the raw part still fits.

    rows = the session's unsummarised messages, oldest first. Size is the
    message's own length: `token_count` holds the whole turn's LLM usage.

    Walking back from the newest message, whole turns stay raw while they fit
    the `history` budget, and the latest turn stays raw whatever its size.
    Everything older becomes one chunk, so the raw part always starts with a
    user message.

    The two budgets default to the current ones; housekeep passes the numbers
    read inside the request, where a user's model window may scale them.
    """
    if history_budget is None:
        history_budget = context_budget("history")
    if compact_at is None:
        compact_at = context_budget("history_compact_at")
    sizes = [_token_estimate(r["content"] or "") for r in rows]
    if sum(sizes) <= compact_at:
        return None

    budget = history_budget
    raw_from = len(rows)          # index of the first message left raw
    raw_tokens = 0
    while raw_from > 0:
        turn_from = raw_from - 1
        while turn_from > 0 and rows[turn_from]["role"] != "user":
            turn_from -= 1
        turn_tokens = sum(sizes[turn_from:raw_from])
        if raw_from < len(rows) and raw_tokens + turn_tokens > budget:
            break
        raw_from = turn_from
        raw_tokens += turn_tokens

    if raw_from == 0:
        return None               # one turn, however long: nothing older to fold
    return rows[raw_from - 1]["seq_id"]


async def maybe_summarize(session_id: str, history_budget: int | None = None,
                          compact_at: int | None = None) -> None:
    """Compact the current session once its raw part outgrows the budget.

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

        up_to_seq = _compaction_cut(rows, history_budget, compact_at)
        if up_to_seq is None:
            return

        await _run_summarize(session_id, last_chunk or 0, up_to_seq)

    except Exception as exc:
        logger.warning("summarizer_trigger_failed", extra={
            "session_id": session_id, "error": str(exc),
        })


# A session counts as finished once the user chats in another one. Two turns is
# enough to be worth remembering; real sessions are short (prod 12/10: median
# ~400 tokens, largest ~2,800), so a token threshold never fired. Counted in
# answers: a turn that died left a question and nothing to summarise it with.
_MIN_FINISHED_TURNS = 2
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
                    AND m.role = 'assistant'
                    AND m.seq_id > COALESCE(s.covered, 0)) >= $3
           ORDER BY c.updated_at DESC
           LIMIT $4""",
        user_id, current_session_id, _MIN_FINISHED_TURNS, _MAX_FINISHED_PER_TURN,
    )
    for r in rows:
        await _run_summarize(r["session_id"], r["covered"])
    return len(rows)


async def housekeep(user_id: str, session_id: str,
                    history_budget: int | None = None,
                    compact_at: int | None = None) -> None:
    """Both summary triggers for one chat turn. Never raises.

    Runs on ECA's key: a user's own key (BYOK) is for their chat turn, not for
    summaries written on their behalf. The unbind only touches this task's
    copy of the context, never the request's. The two budgets come from the
    request (see _compaction_cut); None reads them here.
    """
    from langgraph_agents.llm_byok import bind_request_llm

    bind_request_llm(None)
    try:
        await summarize_finished_sessions(user_id, session_id)
    except Exception as exc:
        logger.warning("finished_session_summary_failed", extra={
            "session_id": session_id, "error": str(exc),
        })
    await maybe_summarize(session_id, history_budget, compact_at)


async def _summarize_messages(rows: list, session_id: str) -> str | None:
    """LLM-summarize a list of message rows → summary_text or None if fail.

    rows = records with {role, content}.
    Uses the same prompt + retry logic as _run_summarize.
    Extracted so rebuild_dirty_chunk can reuse it (M.8 #5).
    """
    lines = []
    for r in rows:
        label = "User" if r["role"] == "user" else "Assistant"
        lines.append(f"{label}: {r['content']}")

    conversation = "\n".join(lines)

    llm = get_chat_model("memory")
    # The summary stands in for these messages on later turns of the same
    # conversation, so it keeps what the assistant needs to carry on — not a
    # description of the chat.
    #
    # "The same language as the conversation", not a named language. This used to
    # force Vietnamese, so an English session was summarised into Vietnamese and
    # then fed back to the model as context on the next turn — pushing it toward
    # a language the user had not written in.
    prompt = (
        "Summarise the conversation below so that the assistant can continue it "
        "without the original messages. Keep: what the user reported (symptoms, "
        "conditions, limits); which exercises or advice were already given "
        "(their names, and sets and reps if stated); what the user agreed to or "
        "declined; cautions already given; and anything still open. Use at most "
        "about 120 words, written in the SAME language as the conversation "
        "itself.\n\n"
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


async def _run_summarize(
    session_id: str, from_seq: int, up_to_seq: int | None = None,
) -> None:
    """Summarize messages in (from_seq, up_to_seq] into one chunk.

    up_to_seq=None takes everything after from_seq (a finished session), up to
    its last answer.
    """
    t0 = time.perf_counter()

    try:
        pg = get_pg_client()
        await pg.connect()

        # Load messages not yet covered
        rows = await pg.fetch(
            """SELECT seq_id, role, content
               FROM messages
               WHERE session_id = $1 AND seq_id > $2
                 AND ($3::bigint IS NULL OR seq_id <= $3)
               ORDER BY seq_id""",
            session_id, from_seq, up_to_seq,
        )

        # A session the user has left can still have a turn running: its
        # question is here, its answer is not yet. A chunk that ended on the
        # question would leave that answer as raw history with nothing asked.
        if up_to_seq is None:
            while rows and rows[-1]["role"] == "user":
                rows = rows[:-1]

        if not rows:
            return

        total_tokens = sum(_token_estimate(r["content"] or "") for r in rows)

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
