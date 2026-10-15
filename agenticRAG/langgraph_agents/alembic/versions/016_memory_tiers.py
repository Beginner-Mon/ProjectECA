"""user_memory tiers + rolling STM + drop turn_index (memory-v2 tiers).

Revision ID: 016_memory_tiers
Revises: 015_user_memory_ltm
Create Date: 2026-10-15

WHAT
----
1. `user_memory` gains one tier-1/tier-2 split per row: `kind` (instruction or
   fact), `tier` (1 = standing instruction, 2 = project fact), `status`
   (candidate or active), `project` label, per-conversation sightings
   (`seen_count`, `seen_projects`, `last_seen_session_id`), `said_at` (the
   date shown in the prompt) and a tier-2 `embedding` (NULL = still to embed).
   Existing rows backfill in place: `restriction` becomes a tier-1
   instruction, `condition` becomes tier 1, everything else stays tier 2; no
   row is deleted here.

2. `conversations` gains the rolling STM: one `stm_summary` covering every
   message with `seq_id <= stm_up_to_seq`, backfilled from the active
   `summaries` chunks. The `summaries` table itself stays for rollback.

3. `turn_index` goes away (`DROP TABLE ... CASCADE`). It is derived data,
   rebuildable from `messages`, and the tier-2 reader replaces it.

Downgrade drops what upgrade added, in reverse, and rebuilds `turn_index`
from 014's statements. No RLS or grant work needed: the 007 policies are per
table and the app role's grants already cover new columns (same argument as
008 and 015).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "016_memory_tiers"
down_revision: Union[str, None] = "015_user_memory_ltm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared
    # statements (004 never ran for exactly this reason).
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'fact'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_kind_check "
        "CHECK (kind IN ('instruction', 'fact'))"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS tier SMALLINT NOT NULL DEFAULT 2"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_tier_check "
        "CHECK (tier IN (1, 2))"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_status_check "
        "CHECK (status IN ('candidate', 'active'))"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS project TEXT"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_project_check "
        "CHECK (project IS NULL OR char_length(project) <= 60)"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS seen_count INT NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS seen_projects TEXT[] NOT NULL DEFAULT '{}'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS last_seen_session_id UUID "
        "REFERENCES conversations(session_id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS said_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS embedding vector(384)"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.kind IS "
        "'Instruction for the agent or fact about the user.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.tier IS "
        "'1 means a standing instruction shown in every conversation, "
        "2 means a fact or project-scoped instruction retrieved by similarity.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.status IS "
        "'Active rows are used, candidate rows are still being confirmed.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.project IS "
        "'Project or topic label, at most 60 characters. NULL means no project.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.seen_count IS "
        "'How many different conversations this row was seen in.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.seen_projects IS "
        "'One project label per conversation counted, empty string when none.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.last_seen_session_id IS "
        "'The conversation this row was last seen in. Deleting that "
        "conversation clears this column, while only deleting the "
        "source_session_id conversation removes the row.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.said_at IS "
        "'When the user said the current content. Shown as the date in the prompt.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.embedding IS "
        "'Tier-2 embedding of the project label and fact. NULL means still to embed.'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_memory_tier "
        "ON user_memory (user_id, tier, status) "
        "WHERE valid = true"
    )
    # routes_crud.py may not mention embeddings (test_crud_app.py), so the
    # application can never clear one itself when it edits a row — the
    # trigger does it instead.
    op.execute(
        "CREATE OR REPLACE FUNCTION user_memory_clear_embedding() "
        "RETURNS trigger AS $$ "
        "BEGIN "
        "IF NEW.fact_text IS DISTINCT FROM OLD.fact_text "
        "OR NEW.project IS DISTINCT FROM OLD.project THEN "
        "NEW.embedding := NULL; "
        "END IF; "
        "RETURN NEW; "
        "END; $$ LANGUAGE plpgsql"
    )
    op.execute(
        "CREATE TRIGGER user_memory_clear_embedding "
        "BEFORE UPDATE ON user_memory "
        "FOR EACH ROW EXECUTE FUNCTION user_memory_clear_embedding()"
    )
    op.execute(
        "ALTER TABLE conversations "
        "ADD COLUMN IF NOT EXISTS stm_summary TEXT"
    )
    op.execute(
        "COMMENT ON COLUMN conversations.stm_summary IS "
        "'Rolling summary of this conversation up to stm_up_to_seq.'"
    )
    op.execute(
        "ALTER TABLE conversations "
        "ADD COLUMN IF NOT EXISTS stm_up_to_seq BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "COMMENT ON COLUMN conversations.stm_up_to_seq IS "
        "'The messages.seq_id the rolling summary covers. 0 means none yet.'"
    )
    op.execute(
        "UPDATE user_memory SET kind = 'instruction', tier = 1 "
        "WHERE category = 'restriction'"
    )
    op.execute(
        "UPDATE user_memory SET tier = 1 "
        "WHERE category = 'condition'"
    )
    op.execute(
        "UPDATE user_memory SET said_at = COALESCE(created_at, now()), "
        "last_seen_session_id = source_session_id"
    )
    op.execute(
        "UPDATE conversations SET "
        "stm_summary = sub.text, stm_up_to_seq = sub.up_to "
        "FROM (SELECT session_id, "
        "STRING_AGG(summary_text, chr(10) || chr(10) "
        "ORDER BY covers_from_seq) AS text, "
        "MAX(covers_up_to_seq) AS up_to "
        "FROM summaries WHERE status = 'active' GROUP BY session_id) sub "
        "WHERE conversations.session_id = sub.session_id"
    )
    # Last: the destructive statement runs only after everything else landed.
    op.execute("DROP TABLE IF EXISTS turn_index CASCADE")


def downgrade() -> None:
    # Reverse order: turn_index comes back before anything is dropped.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS turn_index (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id           UUID NOT NULL
                REFERENCES conversations(session_id) ON DELETE CASCADE,
            user_message_id      UUID NOT NULL
                REFERENCES messages(id) ON DELETE CASCADE,
            assistant_message_id UUID NOT NULL
                REFERENCES messages(id) ON DELETE CASCADE,
            content              TEXT NOT NULL,
            embedding            vector(384) NOT NULL,
            turn_at              TIMESTAMPTZ NOT NULL,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT turn_index_assistant_message_key
                UNIQUE (assistant_message_id)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE turn_index IS "
        "'One embedded row per turn (question + answer) for searching past "
        "conversations. turn_at is when the turn happened, not when it was "
        "indexed. Rows are written once, and RLS reaches the owner through "
        "conversations on app.user_id.'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_turn_index_embedding "
        "ON turn_index USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_turn_index_session "
        "ON turn_index (session_id)"
    )
    op.execute('ALTER TABLE "turn_index" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY turn_index_owner ON "turn_index" FOR ALL '
        "USING (EXISTS (SELECT 1 FROM conversations c "
        'WHERE c.session_id = "turn_index".session_id '
        "AND c.user_id = current_setting('app.user_id')::uuid)) "
        "WITH CHECK (EXISTS (SELECT 1 FROM conversations c "
        'WHERE c.session_id = "turn_index".session_id '
        "AND c.user_id = current_setting('app.user_id')::uuid))"
    )
    op.execute('GRANT SELECT, INSERT, DELETE ON "turn_index" TO "eca_user"')
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS stm_up_to_seq")
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS stm_summary")
    op.execute("DROP TRIGGER IF EXISTS user_memory_clear_embedding ON user_memory")
    op.execute("DROP FUNCTION IF EXISTS user_memory_clear_embedding()")
    op.execute("DROP INDEX IF EXISTS idx_user_memory_tier")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS said_at")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS last_seen_session_id")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS seen_projects")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS seen_count")
    op.execute(
        "ALTER TABLE user_memory DROP CONSTRAINT IF EXISTS user_memory_project_check"
    )
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS project")
    op.execute(
        "ALTER TABLE user_memory DROP CONSTRAINT IF EXISTS user_memory_status_check"
    )
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS status")
    op.execute(
        "ALTER TABLE user_memory DROP CONSTRAINT IF EXISTS user_memory_tier_check"
    )
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS tier")
    op.execute(
        "ALTER TABLE user_memory DROP CONSTRAINT IF EXISTS user_memory_kind_check"
    )
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS kind")
