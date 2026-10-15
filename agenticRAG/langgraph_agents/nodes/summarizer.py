"""Background STM compaction — one rolling summary per conversation.

Decisions encoded:
  STM:   One summary per conversation (`conversations.stm_summary`), merged
         forward — each compaction folds the previous summary plus the newly
         covered messages into a single replacement, written compare-and-set
         on `stm_up_to_seq`. No embedding: the summary is read by session id,
         never searched.

`housekeep`, which /chat starts with the turn and awaits after its `done`
event — inside the invocation, because Lambda freezes the container once the
response ends, and a task left running after it resumed on a closed
connection (12/10: 26 failed triggers, 5 summaries since 01/09).
"""

from __future__ import annotations

import asyncio
import time

from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.context import context_budget
from langgraph_agents.shared.logging import get_logger
from langgraph_agents.llm import get_chat_model

logger = get_logger("langgraph.summarizer")

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


async def _summarize_rolling(previous: str | None, rows: list, session_id: str,
                             usage: dict | None = None) -> str | None:
    """Fold the previous summary plus `rows` into one new summary, or None.

    rows = records with {role, content, seq_id}, oldest first, all covered by
    the new summary. Uses the `memory` role with one retry, like the chunk
    summariser before it.
    """
    lines = []
    for r in rows:
        label = "User" if r["role"] == "user" else "Assistant"
        lines.append(f"{label}: {r['content']}")
    conversation = "\n".join(lines)

    llm = get_chat_model("memory")
    # The summary stands in for these messages on later turns of the same
    # conversation, so it keeps what the assistant needs to carry on — not a
    # description of the chat. The user's instructions for the assistant and
    # the facts they told about themselves are part of that, alongside what
    # the old prompt kept.
    #
    # "The same language as the conversation", not a named language. This used to
    # force Vietnamese, so an English session was summarised into Vietnamese and
    # then fed back to the model as context on the next turn — pushing it toward
    # a language the user had not written in.
    if previous:
        context = f"Previous summary:\n{previous}\n\nNew messages:\n{conversation}"
    else:
        context = conversation
    prompt = (
        "Write one rolling summary of the conversation below so that the "
        "assistant can continue it without the original messages. "
        "Fold the previous summary (when there is one) together with the new "
        "messages: keep what is still true, drop what was superseded. Keep: "
        "what the user reported (symptoms, conditions, limits); which exercises "
        "or advice were already given (their names, and sets and reps if stated); "
        "what the user agreed to or declined; the user's instructions for how "
        "the assistant should behave; facts the user told about themselves; "
        "cautions already given; and anything still open. Use at most about "
        "250 words, written in the SAME language as the conversation itself.\n\n"
        f"{context}"
    )

    summary_text = ""
    for attempt in range(_MAX_RETRY + 1):
        try:
            ai_msg = await llm.ainvoke(prompt)
            summary_text = ai_msg.content.strip() if ai_msg.content else ""
            if summary_text:
                if usage is not None:
                    meta = getattr(ai_msg, "usage_metadata", None) or {}
                    usage["input_tokens"] = meta.get("input_tokens")
                    usage["output_tokens"] = meta.get("output_tokens")
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


def _applied(status: str) -> int:
    """Rows a statement touched, from its command tag ("UPDATE 1")."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


async def compact_stm(session_id: str, history_budget: int | None = None,
                      compact_at: int | None = None) -> None:
    """Compact the current session once its raw part outgrows the budget.

    Merges the previous rolling summary with the newly covered messages into
    one replacement, compare-and-set on `stm_up_to_seq`: two compactions
    racing write the same range and exactly one wins. Called from housekeep.
    Never raises — all errors logged.
    """
    t0 = time.perf_counter()
    try:
        pg = get_pg_client()
        await pg.connect()

        head = await pg.fetchrow(
            "SELECT stm_summary, stm_up_to_seq FROM conversations "
            "WHERE session_id = $1",
            session_id,
        )
        if head is None:
            return
        from_seq = head["stm_up_to_seq"] or 0

        rows = await pg.fetch(
            """SELECT seq_id, role, content FROM messages
               WHERE session_id = $1 AND seq_id > $2
               ORDER BY seq_id""",
            session_id, from_seq,
        )

        up_to_seq = _compaction_cut(rows, history_budget, compact_at)
        if up_to_seq is None:
            return

        covered = [r for r in rows if r["seq_id"] <= up_to_seq]
        usage: dict = {"input_tokens": None, "output_tokens": None}
        new_summary = await _summarize_rolling(
            head["stm_summary"], covered, session_id, usage)
        if not new_summary:
            return

        tag = await pg.execute(
            "UPDATE conversations SET stm_summary = $2, stm_up_to_seq = $3 "
            "WHERE session_id = $1 AND stm_up_to_seq = $4",
            session_id, new_summary, up_to_seq, from_seq,
        )
        if not _applied(tag if isinstance(tag, str) else str(tag)):
            logger.info("stm_compaction_superseded", extra={
                "session_id": session_id, "from_seq": from_seq,
            })
            return

        logger.info("stm_compacted", extra={
            "session_id": session_id,
            "covers_from_seq": from_seq + 1,
            "covers_up_to_seq": up_to_seq,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000),
        })

    except Exception as exc:
        logger.warning("stm_compaction_failed", extra={
            "session_id": session_id, "error": str(exc),
        })


async def housekeep(user_id: str, session_id: str,
                    history_budget: int | None = None,
                    compact_at: int | None = None) -> None:
    """One chat turn's background work: compact this session, extract facts.

    Runs on ECA's key: a user's own key (BYOK) is for their chat turn, not for
    summaries written on their behalf. The unbind only touches this task's
    copy of the context, never the request's. The two budgets come from the
    request (see _compaction_cut); None reads them here.

    The two steps are independent — run concurrently. Each catches its own
    errors; housekeep never raises.
    """
    from langgraph_agents.llm_byok import bind_request_llm
    from langgraph_agents.nodes import fact_extractor

    bind_request_llm(None)

    async def _compact() -> None:
        try:
            await compact_stm(session_id, history_budget, compact_at)
        except Exception as exc:
            logger.warning("compaction_failed", extra={
                "session_id": session_id, "error": str(exc),
            })

    async def _extract() -> None:
        if not fact_extractor.enabled():
            return
        try:
            await fact_extractor.extract_user_facts(user_id, session_id)
        except Exception as exc:
            # The type and not the message: a database error quotes the row it
            # rejected, and that row is a fact about the user's health.
            logger.warning("fact_extraction_failed", extra={
                "session_id": session_id, "error": type(exc).__name__,
            })

    await asyncio.gather(_compact(), _extract())
