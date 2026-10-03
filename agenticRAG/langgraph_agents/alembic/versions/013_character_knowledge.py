"""Character knowledge: per-character sheet/backstory chunks for recall_self.

Revision ID: 013_character_knowledge
Revises: 012_message_feedback
Create Date: 2026-10-01

WHAT
----
`personas/<slug>/sheet.md` (and `backstory.md`) chunked + embedded, one row per
chunk. Read through the `recall_self` tool (plan T8e) with a double gate:

  1. SQL `WHERE character_slug = $n` from the turn's persona_id, AND
  2. RLS policy `character_knowledge_owner` on `app.character`.

The policy takes `current_setting('app.character')` with EXACTLY ONE argument —
no `missing_ok`. A turn that forgot `bind_request_character()` must error (or
match nothing loudly at the tool layer), never silently read another
character's sheet. Same rule as 007's `app.user_id` (see test_rls_policies.py).

The application role gets SELECT only. Chunk (re)ingest runs as the owner via
scripts/ingest_character_pgvector.py — never from the request path.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "013_character_knowledge"
down_revision: Union[str, None] = "012_message_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"

_CHARACTER_PREDICATE = (
    "character_slug = current_setting('app.character')"
)


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements
    # (004 never ran for exactly this reason).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS character_knowledge (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            character_slug TEXT NOT NULL,
            kind           TEXT NOT NULL CHECK (kind IN ('sheet', 'backstory')),
            title          TEXT,
            content        TEXT NOT NULL,
            chunk_index    INT NOT NULL,
            embedding      vector(384) NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE character_knowledge IS "
        "'Per-character sheet/backstory chunks for the recall_self tool. "
        "Written by the owner-side ingest script only; the app reads its own "
        "character through RLS on app.character.'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_character_knowledge_embedding "
        "ON character_knowledge USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_character_knowledge_slug "
        "ON character_knowledge (character_slug)"
    )
    op.execute('ALTER TABLE "character_knowledge" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY character_knowledge_owner ON "character_knowledge" FOR SELECT '
        f"USING ({_CHARACTER_PREDICATE})"
    )
    op.execute(f'GRANT SELECT ON "character_knowledge" TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS character_knowledge_owner ON "character_knowledge"')
    op.execute('ALTER TABLE IF EXISTS "character_knowledge" DISABLE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON "character_knowledge" FROM "{APP_ROLE}"')
    op.execute("DROP INDEX IF EXISTS idx_character_knowledge_embedding")
    op.execute("DROP INDEX IF EXISTS idx_character_knowledge_slug")
    op.execute("DROP TABLE IF EXISTS character_knowledge")
