"""Retrieval tools — M.2 (kb_search).

M.4 schema targets:
  - kb_search → kb_embeddings table (public KB, FK documents)

Decisions encoded:
  D11:  Memory tách Knowledge — 2 index/tool riêng
  D23:  Empty {found:[]} ≠ error {error}
  D28:  Top-k caps: kb=5
"""

from __future__ import annotations

import asyncio
import time

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

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
