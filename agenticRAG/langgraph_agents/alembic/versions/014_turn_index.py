"""Turn index: one embedded row per turn, for searching past conversations.

Revision ID: 014_turn_index
Revises: 013_character_knowledge
Create Date: 2026-10-13

WHAT
----
One row per turn: the user's question plus the assistant's answer, as a single
text (`content`) with its embedding. It replaces searching short LLM-written
summaries when a user refers back to an older conversation — the search now
runs over what was actually said, and `content` is what the model gets back.

Scoped like `messages` and `summaries` (007): no `user_id` column, the policy
reaches the owner through `conversations` on `app.user_id`. One argument to
`current_setting`, no `missing_ok`, for the same reason as 007.

Rows are written once and never edited, so the application role gets SELECT,
INSERT and DELETE but not UPDATE. `assistant_message_id` is unique, so indexing
the same turn twice is a no-op (`ON CONFLICT ON CONSTRAINT ... DO NOTHING`).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "014_turn_index"
down_revision: Union[str, None] = "013_character_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"

# Same predicate 007 gives `messages`, with this table's name.
_OWNER_PREDICATE = (
    "EXISTS (SELECT 1 FROM conversations c "
    'WHERE c.session_id = "turn_index".session_id '
    "AND c.user_id = current_setting('app.user_id')::uuid)"
)


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements
    # (004 never ran for exactly this reason).
    #
    # Both message FKs cascade: deleting a conversation, or one message of a
    # turn, must take the turn's index row with it. Nothing derived from a
    # message may outlive it.
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
        f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
    )
    # No UPDATE: rows are written once. No ALTER DEFAULT PRIVILEGES (see 007).
    op.execute(f'GRANT SELECT, INSERT, DELETE ON "turn_index" TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS turn_index_owner ON "turn_index"')
    op.execute('ALTER TABLE IF EXISTS "turn_index" DISABLE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON "turn_index" FROM "{APP_ROLE}"')
    op.execute("DROP INDEX IF EXISTS idx_turn_index_embedding")
    op.execute("DROP INDEX IF EXISTS idx_turn_index_session")
    op.execute("DROP TABLE IF EXISTS turn_index")
