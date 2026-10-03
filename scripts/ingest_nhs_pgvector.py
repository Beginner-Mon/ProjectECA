"""Ingest a small set of NHS website pages into the knowledge base (trial).

Why NHS.uk: its content is released under the Open Government Licence v3.0 —
commercial use and adaptation allowed, attribution required. NHS inform
(Scotland) looks similar but is non-commercial and forbids derivative works, so
it is deliberately NOT a source. See docs/worklogs/29-09-2026.md.

Two steps, so the text can be reviewed before anything touches the database:

    # 1. fetch the pages, write data/knowledge_base/nhs_uk.jsonl (needs beautifulsoup4)
    python scripts/ingest_nhs_pgvector.py build

    # 2. embed, then write. Needs VVA_PG_DSN_OWNER — the app role (eca_user)
    #    can only SELECT from documents/kb_embeddings (migration 007).
    python scripts/ingest_nhs_pgvector.py ingest --dry-run
    python scripts/ingest_nhs_pgvector.py ingest

One document per page section (h2), one chunk per document — the same shape as
exercise_db. Every chunk starts with "Source: NHS website" so the attribution
travels with the text into the synthesizer's context.

Rollback:  DELETE FROM documents WHERE source_type = 'nhs_uk';  (embeddings cascade)
"""

from __future__ import annotations

import os

# Same as ingest_kb_pgvector.py: offline flags before the embedding stack loads.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "agenticRAG"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ingest_kb_pgvector import _backend_is_running  # noqa: E402

SOURCE_TYPE = "nhs_uk"
DATA_FILE = REPO_ROOT / "data" / "knowledge_base" / "nhs_uk.jsonl"
ENV_FILE = REPO_ROOT / "agenticRAG" / ".env"
ATTRIBUTION = "Contains public sector information licensed under the Open Government Licence v3.0."

# (path, kind). kind tells a reader of the metadata what the page is for:
# red_flags = when to see a GP / 111 / 999; exercise = step-by-step routine.
PAGES = [
    ("conditions/back-pain", "red_flags"),
    ("conditions/knee-pain", "red_flags"),
    ("conditions/neck-pain", "red_flags"),
    ("conditions/shoulder-pain", "red_flags"),
    ("live-well/exercise/strength-exercises", "exercise"),
    ("live-well/exercise/balance-exercises", "exercise"),
    ("live-well/exercise/flexibility-exercises", "exercise"),
    ("live-well/exercise/sitting-exercises", "exercise"),
]


# ── build: fetch + split into sections ────────────────────────────────────

def _fetch(path: str) -> str:
    req = urllib.request.Request(
        f"https://www.nhs.uk/{path}/",
        headers={"User-Agent": "Mozilla/5.0 (ECA knowledge-base ingest)"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_page(html: str, path: str, kind: str) -> list[dict]:
    """One record per h2 section of <main>, text only.

    Stops at the "More in …" navigation and the review footer. Skips video
    blocks (nothing to read) and a <p> nested in an <li> (NHS do/don't lists
    repeat the item text inside a paragraph).
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise SystemExit("build needs beautifulsoup4:  pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup
    page = main.find("h1").get_text(" ", strip=True)
    reviewed = ""

    sections: list[dict] = [{"heading": "Overview", "lines": []}]
    in_video = False
    for el in main.find_all(["h2", "h3", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if text.startswith("Page last reviewed"):
            reviewed = text
            break
        if el.name == "h2" and text.startswith("More in "):
            break
        if el.name == "p" and el.find_parent("li"):
            continue
        if el.find_parent("figure"):  # image captions ("Shot by NHS Choices")
            continue
        if el.name in ("h2", "h3"):
            in_video = text.startswith("Video:")
            if el.name == "h2":
                sections.append({"heading": text, "lines": []})
                continue
        if in_video:
            continue
        prefix = "- " if el.name == "li" else ("## " if el.name == "h3" else "")
        sections[-1]["lines"].append(prefix + text)

    records = []
    for sec in sections:
        if not sec["lines"]:
            continue
        title = f"NHS — {page}: {sec['heading']}"
        content = "\n".join([f"Source: NHS website (nhs.uk) — {page}", f"Section: {sec['heading']}", *sec["lines"]])
        records.append({
            "external_id": f"https://www.nhs.uk/{path}/#{_slug(sec['heading'])}",
            "title": title,
            "content": content,
            "metadata": {
                "url": f"https://www.nhs.uk/{path}/",
                "page": page,
                "section": sec["heading"],
                "kind": kind,
                "reviewed": reviewed,
                "licence": "OGL-3.0",
                "attribution": ATTRIBUTION,
            },
        })
    return records


def build() -> int:
    records = []
    for path, kind in PAGES:
        page_records = parse_page(_fetch(path), path, kind)
        print(f"  {len(page_records):2d} sections  {path}")
        records.extend(page_records)
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    longest = max(len(r["content"]) for r in records)
    print(f"Wrote {len(records)} records to {DATA_FILE.relative_to(REPO_ROOT)} (longest {longest} chars)")
    return 0


# ── ingest: embed first, then one transaction ─────────────────────────────

def _owner_dsn() -> str | None:
    if os.environ.get("VVA_PG_DSN_OWNER"):
        return os.environ["VVA_PG_DSN_OWNER"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*VVA_PG_DSN_OWNER\s*=\s*(.+?)\s*$", line)
            if m:
                return m.group(1).strip("'\"")
    return None


async def ingest(records: list[dict], *, dry_run: bool) -> int:
    """Embed everything before touching the database (see ingest_kb_pgvector.ingest)."""
    from langgraph_agents.shared import get_embedding_service

    print("Loading embedding model (offline)...")
    vectors = get_embedding_service().embed_passages([r["content"] for r in records])
    print(f"Embedded {len(vectors)} records ({len(vectors[0])} dims).")
    if dry_run:
        print("Dry run: nothing written.")
        return 0

    dsn = _owner_dsn()
    if not dsn:
        print("ERROR: VVA_PG_DSN_OWNER is not set (env or agenticRAG/.env).\n"
              "       The app role can only read the knowledge base.")
        return 1

    import asyncpg
    from pgvector.asyncpg import register_vector

    conn = await asyncpg.connect(dsn, timeout=30)
    try:
        await register_vector(conn)
        async with conn.transaction():
            deleted = await conn.fetchval(
                "WITH d AS (DELETE FROM documents WHERE source_type = $1 RETURNING 1) SELECT count(*) FROM d",
                SOURCE_TYPE,
            )
            for rec, vec in zip(records, vectors):
                doc_id = await conn.fetchval(
                    "INSERT INTO documents (source_type, external_id, title, metadata) "
                    "VALUES ($1, $2, $3, $4::jsonb) RETURNING id",
                    SOURCE_TYPE, rec["external_id"], rec["title"], json.dumps(rec["metadata"], ensure_ascii=False),
                )
                await conn.execute(
                    "INSERT INTO kb_embeddings (document_id, chunk_index, content, embedding) VALUES ($1, 0, $2, $3)",
                    doc_id, rec["content"], vec,
                )
    finally:
        await conn.close()
    print(f"Done: replaced {deleted} '{SOURCE_TYPE}' documents with {len(records)}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="NHS.uk pages -> knowledge base (trial).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="fetch pages and write the JSONL")
    ing = sub.add_parser("ingest", help="embed the JSONL and write it to the database")
    ing.add_argument("--dry-run", action="store_true", help="embed only, write nothing")
    ing.add_argument("--force", action="store_true", help="run even while the backend is up")
    args = parser.parse_args()

    if args.cmd == "build":
        return build()

    if _backend_is_running() and not args.force:
        print("ABORT: the backend is running on :8000 — two torch processes segfault "
              "(see ingest_kb_pgvector.py). Stop it, or pass --force.")
        return 2
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE.relative_to(REPO_ROOT)} not found — run `build` first.")
        return 1
    records = [json.loads(line) for line in DATA_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return asyncio.run(ingest(records, dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
