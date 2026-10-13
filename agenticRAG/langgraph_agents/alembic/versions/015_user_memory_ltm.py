"""user_memory: where a fact came from, and how far a conversation was read.

Revision ID: 015_user_memory_ltm
Revises: 014_turn_index
Create Date: 2026-10-13

WHAT
----
Until now a row in `user_memory` was always typed in by the user through the
API. Long-term memory adds a second writer: after a conversation, a background
step extracts durable facts the user said about themselves and stores them in
the same table. That needs three things in the schema.

1. `user_memory.source` ('user' or 'extracted'). The extractor must never
   overwrite or remove a fact the user typed, so the two have to be told apart.
   Every existing row was typed by a user, which is what the default says.

2. `user_memory.source_session_id`. An extracted fact remembers the
   conversation it came from. NULL for facts the user typed. The foreign key
   cascades on purpose: the product owner decided that deleting a conversation
   deletes the facts extracted from it.

3. `conversations.ltm_up_to_seq`. The `messages.seq_id` up to which the
   extractor has read this conversation, so a conversation is not processed
   twice. 0 means not read yet.

`user_memory.updated_at` is added alongside, because an extracted fact can be
refreshed by a later conversation and `created_at` then says nothing about it.

No RLS or grant work needed. The policies on `user_memory` and `conversations`
(007) are per table, and eca_user's table-level GRANT already includes new
columns, the same argument as in 008.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "015_user_memory_ltm"
down_revision: Union[str, None] = "014_turn_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements
    # (004 never ran for exactly this reason). So the column and its CHECK are
    # two statements.
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'user'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_source_check "
        "CHECK (source IN ('user', 'extracted'))"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.source IS "
        "'Who wrote this fact. user means the user typed it, extracted means "
        "the memory extractor took it from a conversation. The extractor never "
        "changes or removes a user fact.'"
    )

    # ON DELETE CASCADE is deliberate and was decided by the product owner:
    # deleting a conversation deletes the facts extracted from it. It looks odd
    # for a memory table to depend on a conversation, but nothing derived from a
    # conversation may outlive it (same rule as turn_index in 014).
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS source_session_id UUID "
        "REFERENCES conversations(session_id) ON DELETE CASCADE"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.source_session_id IS "
        "'The conversation an extracted fact came from, or NULL for a fact the "
        "user typed. Deleting the conversation deletes the fact.'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_memory_source_session "
        "ON user_memory (source_session_id) "
        "WHERE source_session_id IS NOT NULL"
    )

    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.updated_at IS "
        "'When this fact was last written or refreshed. For a new row it equals "
        "created_at.'"
    )

    op.execute(
        "ALTER TABLE conversations "
        "ADD COLUMN IF NOT EXISTS ltm_up_to_seq BIGINT NOT NULL DEFAULT 0"
    )
    op.execute(
        "COMMENT ON COLUMN conversations.ltm_up_to_seq IS "
        "'The messages.seq_id up to which the memory extractor has read this "
        "conversation. 0 means it has not been read yet.'"
    )


def downgrade() -> None:
    # Reverse order. Dropping a column drops its comment and its index, but the
    # index and constraint are named here anyway so the intent is explicit.
    op.execute("ALTER TABLE conversations DROP COLUMN IF EXISTS ltm_up_to_seq")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS updated_at")
    op.execute("DROP INDEX IF EXISTS idx_user_memory_source_session")
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS source_session_id")
    op.execute(
        "ALTER TABLE user_memory DROP CONSTRAINT IF EXISTS user_memory_source_check"
    )
    op.execute("ALTER TABLE user_memory DROP COLUMN IF EXISTS source")
