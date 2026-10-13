"""Retrieval tools — M.2 + M.6 (kb_search + memory_search + resume_last_session).

M.4 schema targets:
  - kb_search → kb_embeddings table (public KB, FK documents)
  - memory_search → summaries table (2-step tenant scope, D19)

Decisions encoded:
  D11:  Memory tách Knowledge — 2 index/tool riêng
  D19:  NO user_id on summaries — tenant via 2-step (conversations→summaries)
  D23:  Empty {found:[]} ≠ error {error}
  D28:  Top-k caps: kb=5, memory=3
  M.6:  memory_search: topic→vector, temporal→SQL filter (tách 2 trục)
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from langgraph_agents.db import turn_index
from langgraph_agents.shared import get_embedding_service, get_pg_client
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.tools")

# ── YouTube transcript cap (D28: budget ~3k tokens ≈ 12000 chars) ─────────
_YT_CHAR_CAP = 12_000

# Nguồn thuộc thư viện tra cứu (plan T5, mở rộng B5). Trùng SOURCE_TYPE
# trong scripts/ingest_kb_pgvector.py:81 — đổi một trong hai mà quên bên còn
# lại thì kb_search trả rỗng (không lỗi, D23) và mọi lượt lâm sàng thành refuse.
# nhs_uk (49 đoạn) có nhãn riêng ở sources.KB_SOURCE_TYPE_LABELS, không gộp
# vào tên thư viện ECA. source_type khác vẫn bị loại ngay ở WHERE này.
LIBRARY_SOURCE_TYPES = ("exercise_db", "nhs_uk")


def _retrieval_threshold(key: str) -> float | None:
    """Một ngưỡng similarity trong `langgraph.retrieval` (plan T5/T8e).

    None = không lọc (hành vi cũ). Giá trị do số đo quyết, không đoán.
    """
    try:
        import yaml
        from pathlib import Path

        config_path = Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
        if not config_path.exists():
            return None
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        value = (cfg.get("langgraph", {}) or {}).get("retrieval", {}) or {}
        threshold = value.get(key)
        return float(threshold) if threshold is not None else None
    except Exception:
        return None


def _kb_min_similarity() -> float | None:
    """Ngưỡng similarity cho kb_search, từ config (plan T5).

    None = không lọc (hành vi cũ). Giá trị do số đo T1 quyết — phân bố (c) và
    (d) chồng lấn nên hiện chưa đặt trong config; chỉ lọc khi Tri chốt số.
    """
    return _retrieval_threshold("kb_min_similarity")


def _self_min_similarity() -> float | None:
    """Ngưỡng riêng cho recall_self (plan T8e). None = không lọc."""
    return _retrieval_threshold("self_min_similarity")


def _memory_min_similarity() -> float | None:
    """Ngưỡng cho memory_search. None = không lọc.

    Không có ngưỡng thì có summary là trả về, kể cả cho "bạn là ai". Số 0.84
    trong config đo 12/10 trên bộ tổng hợp (10 câu nhắc lại / 12 câu khác):
    giữ 10/10, chặn câu ngoài lề (danh tính, thời tiết, nấu ăn); câu sức khoẻ
    khác chủ đề vẫn lọt. Chỉnh lại theo `memory_search_done.top_similarity`.
    """
    return _retrieval_threshold("memory_min_similarity")


_TURN_SNIPPET_CHARS = 700


def _history_min_similarity() -> float:
    """Ngưỡng cho memory_search khi đọc turn_index (0.87, probe 13/10).

    Khác ngưỡng trên: 0.84 đo trên summary, còn đây đo trên từng lượt
    (docs/tracking/history-search-probe-V0.md). Thiếu key thì vẫn có sàn.
    """
    value = _retrieval_threshold("history_min_similarity")
    return 0.87 if value is None else value


async def _embed_query_and_words(embed_svc, query: str, config) -> tuple:
    """(embedding of the retriever's query, embedding of the user's own words).

    Scored against the user's own words as well as the retriever's query. The
    retriever writes its queries in English; stored text is in the conversation's
    language. English query vs Vietnamese summary ranked the right summary first
    in 1 of 7 cases; the user's own words, 7 of 7 (scripts measured 12/10,
    docs/worklogs/12-10-2026.md). One embedding when the two texts are equal.
    """
    user_words = (config["configurable"].get("query") or "").strip()
    texts = [query] if user_words in ("", query) else [query, user_words]
    vecs = await asyncio.gather(*(embed_svc.aembed_query(t) for t in texts))
    return vecs[0], vecs[-1]


def _ambiguous(results: list) -> bool:
    """Top-2 results from different sessions with similarity gap < 0.05 (A1)."""
    if len(results) < 2:
        return False
    r0, r1 = results[0], results[1]
    return r0["session_id"] != r1["session_id"] and (r0["similarity"] - r1["similarity"]) < 0.05


def _to_uuid(value: str) -> str:
    """Coerce user-supplied string into a deterministic UUID."""
    import uuid
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, str(value)))


# ═══════════════════════════════════════════════════════════════════════════
# kb_search — public knowledge base (kb_embeddings table)
# ═══════════════════════════════════════════════════════════════════════════

@tool(description=(
    "Search ECA's exercise and health knowledge base. Use for questions "
    "about exercises, stretches, anatomy, physiotherapy techniques and "
    "health facts. The knowledge base is written in English: write the query "
    "in English, using the name of the exercise, muscle or joint."
))
async def kb_search(query: str, top_k: int = 5) -> list[dict]:
    """Search the internal PT/wellness knowledge base.

    Use for: exercises, stretches, anatomy, physiotherapy techniques,
    health/wellness facts. Returns chunks with content + similarity score.

    Args:
        query: Search query in Vietnamese or English.
        top_k: Max results (default 5, capped at 5 per D28).

    Returns:
        List of {content, similarity, source_type, document_title} dicts.
        Empty list if no results found (NOT an error — D23).
    """
    top_k = min(top_k, 5)  # D28 cap

    try:
        embed_svc = get_embedding_service()
        pg = get_pg_client()
        t0 = time.perf_counter()
        await pg.connect()
        t_connect = time.perf_counter()

        # Generate embedding with query: prefix for search
        query_vec = await embed_svc.aembed_query(query)
        t_embed = time.perf_counter()

        rows = await pg.fetch(
            """
            SELECT ke.content, ke.chunk_index,
                   1 - (ke.embedding <=> $1) AS similarity,
                   d.source_type, d.title
            FROM kb_embeddings ke
            JOIN documents d ON ke.document_id = d.id
            WHERE d.source_type = ANY($3)
            ORDER BY ke.embedding <=> $1
            LIMIT $2
            """,
            query_vec,
            top_k,
            list(LIBRARY_SOURCE_TYPES),
        )
        t_db = time.perf_counter()

        min_sim = _kb_min_similarity()

        results = [
            {
                "content": r["content"],
                "similarity": round(float(r["similarity"]), 4),
                "source_type": r["source_type"],
                "document_title": r["title"] or "",
                "chunk_index": r["chunk_index"],
            }
            for r in rows
            if min_sim is None or float(r["similarity"]) >= min_sim
        ]

        # Per-stage times: a slow search is either the model (embed_ms) or the
        # database (connect_ms, db_ms), and the logs should say which.
        logger.info("kb_search_done", extra={
            "query": query[:80], "results": len(results),
            "top_similarity": results[0]["similarity"] if results else None,
            "connect_ms": round((t_connect - t0) * 1000),
            "embed_ms": round((t_embed - t_connect) * 1000),
            "db_ms": round((t_db - t_embed) * 1000),
        })
        return results

    except Exception as exc:
        # D23: error → raise so retriever can retry
        logger.error("kb_search_error", extra={"query": query[:80], "error": str(exc)})
        raise


# ═══════════════════════════════════════════════════════════════════════════
# memory_search — user's past session summaries (summaries table, 2-step)
# ═══════════════════════════════════════════════════════════════════════════

@tool(description=(
    "Search this user's earlier conversations with you. Use when the user "
    "refers back to something said before, names a past time (last week, "
    "yesterday), or asks you to repeat something. `since_days` limits the "
    "search to recent days."
))
async def memory_search(
    query: str,
    since_days: Optional[int] = None,
    top_k: int = 3,
    config: RunnableConfig = None,
) -> dict:
    """Search user's past conversation summaries for relevant context.

    Use for: "lần trước", "tuần trước", "tôi đã hỏi", "nhắc lại",
    "tiếp tục bài tập". Topic search uses vector similarity;
    temporal search uses SQL date filter (M.6: tách 2 trục).

    Args:
        query: What to search for in past conversations.
        since_days: Optional — only search within N days (e.g. 7 for "tuần trước").
        top_k: Max results (default 3, capped at 3 per D28).

    Returns:
        {found: true, results: [{summary_text, session_id, similarity, created_at}]}
        OR {found: false} if no results (NOT error — D23).
    """
    top_k = min(top_k, 3)  # D28 cap

    # Scope injected from runtime config — LLM does NOT see these (M.6/D19)
    user_id = _to_uuid(config["configurable"]["user_id"])
    current_session_id = config["configurable"]["session_id"]

    try:
        if turn_index.enabled():
            return await _search_turn_index(
                query, since_days, top_k, user_id, current_session_id, config)
        return await _search_summaries(
            query, since_days, top_k, user_id, current_session_id, config)
    except Exception as exc:
        logger.error("memory_search_error", extra={"query": query[:80], "error": str(exc)})
        raise


async def _search_summaries(query, since_days, top_k, user_id, current_session_id, config) -> dict:
    """The original path: the user's conversation summaries (summaries table)."""
    embed_svc = get_embedding_service()
    pg = get_pg_client()
    await pg.connect()

    # Step 1: Get user's session_ids (tenant scope — M.6)
    session_rows = await pg.fetch(
        "SELECT session_id FROM conversations WHERE user_id = $1",
        user_id,
    )
    user_session_ids = [r["session_id"] for r in session_rows]

    if not user_session_ids:
        return {"found": False}

    # Step 2: Vector search scoped to user's summaries (D19), scored against the
    # user's own words as well as the retriever's query (see the helper).
    query_vec, words_vec = await _embed_query_and_words(embed_svc, query, config)

    # When the conversation happened, not when it was summarised: a session
    # is now summarised once the user starts the next one, possibly days
    # later. Read from the last message the summary covers.
    sql = """
        SELECT s.summary_text, s.session_id,
               COALESCE(m.created_at, s.created_at) AS created_at,
               GREATEST(1 - (s.embedding <=> $1), 1 - (s.embedding <=> $2)) AS similarity
        FROM summaries s
        LEFT JOIN messages m
          ON m.session_id = s.session_id AND m.seq_id = s.covers_up_to_seq
        WHERE s.session_id = ANY($3)
          AND s.session_id != $4
          AND s.status = 'active'
    """
    params = [query_vec, words_vec, user_session_ids, current_session_id]
    # Temporal filter (M.6: tách topic/temporal — 2 trục)
    if since_days is not None and since_days > 0:
        params.append(str(since_days))
        sql += f" AND COALESCE(m.created_at, s.created_at) >= now() - (${len(params)} || ' days')::interval"
    params.append(top_k)
    sql += f" ORDER BY similarity DESC LIMIT ${len(params)}"
    rows = await pg.fetch(sql, *params)

    results = [
        {
            "summary_text": r["summary_text"],
            "session_id": str(r["session_id"]),
            "similarity": round(float(r["similarity"]), 4),
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]
    top_similarity = results[0]["similarity"] if results else None
    min_sim = _memory_min_similarity()
    if min_sim is not None:
        results = [r for r in results if r["similarity"] >= min_sim]

    response = {"found": True, "results": results}
    if _ambiguous(results):
        response["ambiguous"] = True
        response["candidates"] = [
            {"summary_text": r["summary_text"][:80],
             "session_id": r["session_id"]}
            for r in results[:3]
        ]

    logger.info("memory_search_done", extra={
        "query": query[:80],
        "user_sessions": len(user_session_ids),
        "results": len(results),
        "dropped_below_min": len(rows) - len(results),
        "top_similarity": top_similarity,
        "since_days": since_days,
        "source": "summaries",
    })
    return response if results else {"found": False}


# One statement: the user's own conversations (join on conversations.user_id),
# never the current one, scored as the greater of the two similarities.
_TURN_SEARCH_SQL = """
    SELECT t.content, t.session_id, c.title, t.turn_at,
           GREATEST(1 - (t.embedding <=> $1), 1 - (t.embedding <=> $2)) AS similarity
    FROM turn_index t
    JOIN conversations c ON c.session_id = t.session_id
    WHERE c.user_id = $3
      AND t.session_id != $4
"""


async def _search_turn_index(query, since_days, top_k, user_id, current_session_id, config) -> dict:
    """Memory v2: the original turns (turn_index), floor `history_min_similarity`."""
    embed_svc = get_embedding_service()
    pg = get_pg_client()
    await pg.connect()

    query_vec, words_vec = await _embed_query_and_words(embed_svc, query, config)

    sql = _TURN_SEARCH_SQL
    params = [query_vec, words_vec, user_id, current_session_id]
    if since_days is not None and since_days > 0:
        params.append(str(since_days))
        sql += f" AND t.turn_at >= now() - (${len(params)} || ' days')::interval"
    params.append(top_k)
    sql += f" ORDER BY similarity DESC LIMIT ${len(params)}"
    rows = await pg.fetch(sql, *params)

    results = [
        {
            "snippet": r["content"][:_TURN_SNIPPET_CHARS],
            "session_id": str(r["session_id"]),
            "session_title": r["title"],
            "date": r["turn_at"].date().isoformat(),
            "similarity": round(float(r["similarity"]), 4),
        }
        for r in rows
    ]
    top_similarity = results[0]["similarity"] if results else None
    floor = _history_min_similarity()
    results = [r for r in results if r["similarity"] >= floor]

    response = {"found": True, "results": results}
    if _ambiguous(results):
        response["ambiguous"] = True
        response["candidates"] = [
            {"snippet": r["snippet"][:80], "session_id": r["session_id"]}
            for r in results[:3]
        ]

    logger.info("memory_search_done", extra={
        "query": query[:80],
        "results": len(results),
        "dropped_below_min": len(rows) - len(results),
        "top_similarity": top_similarity,
        "since_days": since_days,
        "source": "turn_index",
    })
    return response if results else {"found": False}


# ═══════════════════════════════════════════════════════════════════════════
# resume_last_session — tool riêng cho "tiếp tục session vừa rồi" (M.6)
# ═══════════════════════════════════════════════════════════════════════════

@tool(description=(
    "Load the user's most recent earlier session. Use when the user wants "
    "to continue where they left off, rather than recall one fact."
))
async def resume_last_session(
    since_days: Optional[int] = None,
    config: RunnableConfig = None,
) -> dict:
    """Resume the most recent past session for this user.

    Use for: "tiếp tục", "làm tiếp bài hôm trước", "quay lại bài tập".
    Returns summaries + recent messages from the most recent session
    (excluding current session). No vector search needed — recency-based.

    Args:
        since_days: Optional time window (e.g. 1 for "hôm qua", 7 for "tuần trước").

    Returns:
        {found: true, session_id, summary_chunks, recent_messages}
        OR {found: false}
    """
    # Scope injected from runtime config — LLM does NOT see these
    user_id = _to_uuid(config["configurable"]["user_id"])
    current_session_id = config["configurable"]["session_id"]

    try:
        pg = get_pg_client()
        await pg.connect()

        # Step 1: Find top 2 most recent sessions for ambiguity check (A1)
        base_sql = """
            SELECT session_id, title, updated_at
            FROM conversations
            WHERE user_id = $1
              AND session_id != $2
        """
        params = [user_id, current_session_id]
        if since_days is not None and since_days > 0:
            base_sql += " AND updated_at >= now() - ($3 || ' days')::interval"
            params.append(str(since_days))

        top2 = await pg.fetch(
            base_sql + " ORDER BY updated_at DESC LIMIT 2", *params,
        )

        if not top2:
            return {"found": False}

        target_session = str(top2[0]["session_id"])

        # Ambiguity check (A1): ≥2 sessions + top-2 gap < 24h
        is_ambiguous = False
        candidates = []
        if len(top2) >= 2:
            gap_seconds = (
                top2[0]["updated_at"] - top2[1]["updated_at"]
            ).total_seconds()
            if gap_seconds < 86400:  # 24 hours
                is_ambiguous = True
                for t in top2[:3]:
                    # Get first user message as preview
                    preview_row = await pg.fetchrow(
                        "SELECT content FROM messages WHERE session_id = $1 AND role = 'user' ORDER BY seq_id LIMIT 1",
                        t["session_id"],
                    )
                    preview = (preview_row["content"][:80] + "...") if preview_row else "(empty)"
                    candidates.append({
                        "session_id": str(t["session_id"]),
                        "preview": preview,
                        "updated_at": t["updated_at"].isoformat(),
                    })

        # Step 2: Load summaries (frozen chunks)
        summary_rows = await pg.fetch(
            """
            SELECT summary_text, covers_from_seq, covers_up_to_seq
            FROM summaries
            WHERE session_id = $1 AND status = 'active'
            ORDER BY covers_from_seq
            """,
            target_session,
        )
        summary_chunks = [r["summary_text"] for r in summary_rows]

        # Step 3: Load recent messages after last summary
        last_seq = summary_rows[-1]["covers_up_to_seq"] if summary_rows else 0
        msg_rows = await pg.fetch(
            """
            SELECT role, content, seq_id
            FROM messages
            WHERE session_id = $1 AND seq_id > $2
            ORDER BY seq_id
            LIMIT 10
            """,
            target_session,
            last_seq,
        )
        recent_messages = [
            {"role": r["role"], "content": r["content"]} for r in msg_rows
        ]

        logger.info("resume_last_session_done", extra={
            "target_session": target_session,
            "summary_chunks": len(summary_chunks),
            "recent_messages": len(recent_messages),
        })

        result = {
            "found": True,
            "session_id": target_session,
            "summary_chunks": summary_chunks,
            "recent_messages": recent_messages,
        }
        if is_ambiguous:
            result["ambiguous"] = True
            result["candidates"] = candidates

        return result

    except Exception as exc:
        logger.error("resume_last_session_error", extra={"error": str(exc)})
        raise


# ═══════════════════════════════════════════════════════════════════════════
# youtube_transcript — fetch spoken transcript of a YouTube video (no DB write)
# ═══════════════════════════════════════════════════════════════════════════

@tool(description=(
    "Fetch the spoken transcript of a YouTube video. Use when the user's "
    "message contains a YouTube link; pass the URL exactly as written. "
    "Speech only: it does not see the video."
))
async def youtube_transcript(url: str) -> dict:
    """Fetch the spoken transcript of a YouTube video the user pasted.

    Use ONLY when the user's message contains a YouTube link (youtube.com/watch?v=
    or youtu.be/). Returns the transcript text (truncated) for answering questions
    about that specific video. Does NOT understand visuals — speech only.

    Args:
        url: The YouTube URL from the user's message (copy verbatim).

    Returns:
        {found: true, video_id, transcript, truncated: bool}  on success
        {found: false, reason: "no_transcript"}                if video has no captions
    """
    from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi

    from langgraph_agents.tools.youtube_ingest import _extract_video_id

    # Invalid URL format → return {found: false} without raising (D23: empty, not error)
    try:
        video_id = _extract_video_id(url)
    except ValueError:
        logger.info("youtube_transcript_invalid_url", extra={"url": url[:120]})
        return {"found": False, "reason": "invalid_url"}

    # Fetch transcript in thread (sync lib, don't block event loop)
    try:
        entries = await asyncio.to_thread(
            YouTubeTranscriptApi.get_transcript,
            video_id,
            languages=["vi", "en"],
        )
    except CouldNotRetrieveTranscript:
        # No captions / disabled / unavailable → D23 empty, do NOT raise
        logger.info("youtube_transcript_no_captions", extra={"video_id": video_id})
        return {"found": False, "reason": "no_transcript"}
    # Any other exception (network, HTTP, parse) → raise so retriever/error_handler can handle (D23 service error)

    full_text = " ".join(e["text"] for e in entries)

    # Truncate to cap (D28 budget)
    truncated = len(full_text) > _YT_CHAR_CAP
    if truncated:
        full_text = full_text[:_YT_CHAR_CAP]

    logger.info("youtube_transcript_done", extra={
        "video_id": video_id, "chars": len(full_text), "truncated": truncated,
    })
    return {"found": True, "video_id": video_id, "transcript": full_text, "truncated": truncated}


# ═══════════════════════════════════════════════════════════════════════════
# recall_self — what the character knows about itself (plan T8e)
# ═══════════════════════════════════════════════════════════════════════════

@tool(description=(
    "Look up what you know about yourself: appearance, clothes, tastes, "
    "history. Use when the user asks about you."
))
async def recall_self(query: str, config: RunnableConfig = None) -> dict:
    """Search what you know about yourself: appearance, clothes,
    tastes, history. Use when the user asks about you.

    Args:
        query: What the user asked about you (appearance, height, ...).

    Returns:
        {found: true, results: [{title, kind, content, similarity}]}
        OR {found: false} if no sheet rows match (NOT error — D23).

    The character is never a parameter: the slug comes from the turn's
    persona_id, gated twice — this WHERE clause and the RLS policy on
    app.character (migration 013).
    """
    persona_id = (config or {}).get("configurable", {}).get("persona_id", "anne")

    try:
        embed_svc = get_embedding_service()
        pg = get_pg_client()
        await pg.connect()

        query_vec = await embed_svc.aembed_query(query)

        rows = await pg.fetch(
            """
            SELECT ck.title, ck.kind, ck.content,
                   1 - (ck.embedding <=> $1) AS similarity
            FROM character_knowledge ck
            WHERE ck.character_slug = $2
            ORDER BY ck.embedding <=> $1
            LIMIT 3
            """,
            query_vec,
            persona_id,
        )

        min_sim = _self_min_similarity()

        results = [
            {
                "title": r["title"] or "",
                "kind": r["kind"],
                "content": r["content"],
                "similarity": round(float(r["similarity"]), 4),
            }
            for r in rows
            if min_sim is None or float(r["similarity"]) >= min_sim
        ]

        if not results:
            return {"found": False}

        logger.info("recall_self_done", extra={
            "persona_id": persona_id, "results": len(results),
        })
        return {"found": True, "results": results}

    except Exception as exc:
        logger.error("recall_self_error", extra={"error": str(exc)})
        raise


# ── Exports ───────────────────────────────────────────────────────────────
# Legacy name pgvector_search → new name kb_search (backwards compat)
pgvector_search = kb_search
