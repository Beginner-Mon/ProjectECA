"""Ingest a character's sheet/backstory into character_knowledge (plan T8c).

Reads `agenticRAG/langgraph_agents/personas/<slug>/sheet.md` (and
`backstory.md` when present), splits on `##` sections (overlong paragraphs
split further), embeds with the shared service (`passage:` prefix, same as the
KB ingest), and replaces that (slug, kind)'s rows.

Writes as the OWNER (VVA_PG_DSN_OWNER) — the app role has SELECT only on
character_knowledge. Never run without Tri's go-ahead in-session: Neon is
shared with prod.

Usage:
    python scripts/ingest_character_pgvector.py anne --dry-run
    python scripts/ingest_character_pgvector.py anne
"""

from __future__ import annotations

# Offline embedding load MUST be set before the embedding stack is imported —
# huggingface_hub caches these flags at import time (see fixes/retrieval-perf P1).
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import asyncio
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "agenticRAG"))

PERSONAS_DIR = (
    REPO_ROOT / "agenticRAG" / "langgraph_agents" / "personas"
)

KINDS = ("sheet", "backstory")
# A section longer than this is split further on paragraph breaks (plan T8c).
# Sized for the ~10K token window: About-you carries 900 chars max (T8f).
MAX_CHUNK_CHARS = 800
EMBED_BATCH = 32


# ── Chunking (pure — unit-tested without model or DB) ────────────────────

def _split_long_paragraph(para: str, limit: int) -> list[str]:
    """Chia một đoạn đơn quá dài theo câu, giữ thứ tự."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
    if len(sentences) <= 1:
        return [para]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for sent in sentences:
        if current and current_len + len(sent) > limit:
            parts.append(" ".join(current))
            current, current_len = [], 0
        current.append(sent)
        current_len += len(sent)
    if current:
        parts.append(" ".join(current))
    return parts


def split_paragraphs(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split an overlong section on blank lines, keeping order."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paras:
        if len(para) > limit:
            if current:
                chunks.append("\n\n".join(current))
                current, current_len = [], 0
            chunks.extend(_split_long_paragraph(para, limit))
            continue
        if current and current_len + len(para) > limit:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or ([text.strip()] if text.strip() else [])


def parse_persona_file(path: Path, kind: str) -> list[dict]:
    """Split sheet.md/backstory.md on ## headers into {title, content} chunks."""
    sections: list[dict] = []
    header: str | None = None
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if header and text:
            for part in split_paragraphs(text):
                sections.append({"title": header, "content": part})

    for line in path.read_text(encoding="utf-8").split("\n"):
        match = re.match(r"^##\s+(.+)$", line)
        if match:
            flush()
            header = match.group(1).strip()
            body = []
        else:
            body.append(line)
    flush()
    return [{"kind": kind, **s} for s in sections]


def load_character_chunks(slug: str) -> list[dict]:
    """All chunks for one character: sheet.md, then backstory.md when present."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", slug):
        raise SystemExit(f"bad slug: {slug!r}")
    chunks: list[dict] = []
    for kind in KINDS:
        path = PERSONAS_DIR / slug / f"{kind}.md"
        if path.is_file():
            chunks.extend(parse_persona_file(path, kind))
    return chunks


# ── Ingest ───────────────────────────────────────────────────────────────

async def ingest(slug: str, chunks: list[dict], *, dry_run: bool) -> None:
    from langgraph_agents.db.postgres import PostgresClient

    kinds = sorted({c["kind"] for c in chunks})
    print(f"{slug}: {len(chunks)} chunks ({', '.join(kinds)})")
    for c in chunks:
        print(f"  [{c['kind']}] {c['title']}: {len(c['content'])} chars")
    if dry_run:
        print("dry-run: no embedding, no writes.")
        return

    dsn = os.environ.get("VVA_PG_DSN_OWNER")
    if not dsn:
        raise SystemExit(
            "VVA_PG_DSN_OWNER is required (app role has SELECT only). Refusing."
        )

    print("Loading embedding model (offline)...")
    from langgraph_agents.shared import get_embedding_service

    embed = get_embedding_service()
    contents = [c["content"] for c in chunks]
    vectors: list = []
    for start in range(0, len(contents), EMBED_BATCH):
        vectors.extend(embed.embed_passages(contents[start:start + EMBED_BATCH]))
        print(f"  {len(vectors)}/{len(contents)} embedded", end="\r", flush=True)
    print(f"\nEmbedded {len(vectors)} chunks. Writing to the database...")

    pg = PostgresClient(dsn)
    await pg.connect()
    # One transaction: delete this (slug, kind) rows, insert fresh. A crash
    # during embedding costs nothing — nothing was deleted yet.
    async with pg._pool.acquire() as conn:  # noqa: SLF001 — maintenance script
        async with conn.transaction():
            for kind in kinds:
                await conn.execute(
                    "DELETE FROM character_knowledge "
                    "WHERE character_slug = $1 AND kind = $2",
                    slug, kind,
                )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                await conn.execute(
                    """
                    INSERT INTO character_knowledge
                        (character_slug, kind, title, content, chunk_index, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    slug, chunk["kind"], chunk["title"], chunk["content"],
                    index, vector,
                )
    print(f"Done: {len(chunks)} character_knowledge rows for {slug!r}.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest character sheet into pgvector.")
    parser.add_argument("slug", help="character slug (personas/<slug>/sheet.md)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chunks = load_character_chunks(args.slug)
    if not chunks:
        print(f"ERROR: no sheet.md or backstory.md for {args.slug!r}.")
        return 1

    asyncio.run(ingest(args.slug, chunks, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
