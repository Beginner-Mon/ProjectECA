"""Turn index (memory v2, B1b): one embedded row per turn, written after the reply.

A turn is the user's message plus the assistant's answer to it. `index_new_turns`
finds this user's answers that have no row in `turn_index` yet (migration 014)
and writes one row each; `memory_search` reads the table back. Which text is
indexed, how many characters are embedded and where the similarity floor sits
come from the probe in docs/tracking/history-search-probe-V0.md.

Two database round-trips whatever the batch size, because a round-trip from this
service is ~1 s: one read, one INSERT ... SELECT FROM unnest. The embedding
service is imported inside the function — this module is imported by code the
CRUD Lambda also loads (tests/langgraph_agents/test_crud_app.py), and the
embedding model must stay out of it.

The whole feature sits behind `enabled()`: with it off the application does
what it did before this module existed.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.turn_index")

# Stored text is what memory_search hands back; the embedding sees less of it.
# 1,500 characters is what the probe measured; beyond that the answer's tail
# dilutes the question that is being matched.
_CONTENT_MAX_CHARS = 4000
_EMBED_MAX_CHARS = 1500


def _yaml_enabled() -> bool:
    """`langgraph.memory.history_index_enabled`, False when missing or unreadable."""
    try:
        import yaml

        path = Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
        if not path.exists():
            return False
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        memory = (cfg.get("langgraph", {}) or {}).get("memory", {}) or {}
        return bool(memory.get("history_index_enabled", False))
    except Exception:
        return False


def enabled() -> bool:
    """Is the turn index on? VVA_HISTORY_INDEX ("1"/"0") wins, then the YAML, else off."""
    env = os.getenv("VVA_HISTORY_INDEX")
    if env in ("0", "1"):
        return env == "1"
    return _yaml_enabled()


# Each answer paired with the nearest earlier user message of its conversation:
# a question that never got an answer is simply not a turn, and an answer whose
# turn was cut short is an ordinary answer row. Scoped to the user by the join
# (RLS would scope it too; this keeps the intent visible in the statement).
_PENDING_SQL = """
    SELECT a.id AS assistant_id, a.session_id, a.content AS answer,
           COALESCE(a.created_at, now()) AS answered_at,
           u.id AS question_id, u.content AS question
    FROM messages a
    JOIN conversations c ON c.session_id = a.session_id AND c.user_id = $1
    JOIN LATERAL (
        SELECT m.id, m.content FROM messages m
        WHERE m.session_id = a.session_id AND m.role = 'user' AND m.seq_id < a.seq_id
        ORDER BY m.seq_id DESC LIMIT 1
    ) u ON true
    WHERE a.role = 'assistant'
      AND NOT EXISTS (SELECT 1 FROM turn_index t WHERE t.assistant_message_id = a.id)
    ORDER BY a.seq_id DESC
    LIMIT $2
"""

# Embeddings travel as text and are cast on the way in: an array of `vector`
# would need its own codec on the connection.
_INSERT_SQL = """
    INSERT INTO turn_index
        (session_id, user_message_id, assistant_message_id, content, embedding, turn_at)
    SELECT x.session_id, x.user_message_id, x.assistant_message_id,
           x.content, x.embedding::vector, x.turn_at
    FROM unnest($1::uuid[], $2::uuid[], $3::uuid[], $4::text[], $5::text[], $6::timestamptz[])
        AS x(session_id, user_message_id, assistant_message_id, content, embedding, turn_at)
    ON CONFLICT ON CONSTRAINT turn_index_assistant_message_key DO NOTHING
    RETURNING id
"""


def _vector_literal(vec) -> str:
    return "[" + ",".join(f"{float(v):.7g}" for v in vec) + "]"


async def index_new_turns(user_id: str, limit: int = 8) -> int:
    """Index up to `limit` of this user's newest unindexed turns. Returns rows written.

    May raise; housekeep catches. Runs inside the request, so the pg client has
    the user bound and the INSERT passes the table's WITH CHECK.
    """
    from langgraph_agents.shared import get_embedding_service, get_pg_client

    started = time.perf_counter()
    pg = get_pg_client()
    pending = await pg.fetch(_PENDING_SQL, user_id, limit)

    written = 0
    if pending:
        contents = [f"{r['question']}\n{r['answer']}"[:_CONTENT_MAX_CHARS] for r in pending]
        vectors = await get_embedding_service().aembed_passages(
            [c[:_EMBED_MAX_CHARS] for c in contents])
        rows = await pg.fetch(
            _INSERT_SQL,
            [r["session_id"] for r in pending],
            [r["question_id"] for r in pending],
            [r["assistant_id"] for r in pending],
            contents,
            [_vector_literal(v) for v in vectors],
            [r["answered_at"] for r in pending],
        )
        written = len(rows)

    logger.info("turn_index_done", extra={
        "count": written,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
    })
    return written
