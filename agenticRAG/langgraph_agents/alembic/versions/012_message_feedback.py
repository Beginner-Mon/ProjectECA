"""Message feedback: per-answer 👍/👎, reasons, and an optional comment.

Revision ID: 012_message_feedback
Revises: 011_character_voices
Create Date: 2026-09-29

WHY NO user_id COLUMN
----------------------
The owner is derivable: message -> conversation -> user, exactly how
`messages` itself is owned (007_rls.py OWNED_VIA_SESSION). A copied user_id
column would be a second source of truth that can drift from the real one.
Deleting a user or a conversation cascades down the FK chain
(users -> conversations -> messages -> message_feedback) with zero extra code.

WHY THE POLICY PREDICATE JOINS messages AND conversations
-----------------------------------------------------------
Foreign keys bypass RLS: a policy-less table would let a user attach feedback
to a message_id that belongs to someone else, because PostgreSQL only checks
that the referenced row exists, not who owns it. USING and WITH CHECK share
the same predicate for the same reason 007's OWNED_VIA_SESSION tables do —
without WITH CHECK a user could write a row they can never read back.

WHY reasons HAS NO DB-LEVEL CHECK
-----------------------------------
Valid reason codes are validated by the API's Pydantic Literal. Adding a
reason is then a code change, not a migration — same reasoning as
messages.extras (008_messages_extras.py).

WHY ONE ROW PER MESSAGE, NOT A HISTORY
-----------------------------------------
message_id is the primary key. This stores current-state only (upsert on
message_id); it does not keep a log of vote changes over time.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "012_message_feedback"
down_revision: Union[str, None] = "011_character_voices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"

# Same predicate for USING and WITH CHECK — see module docstring.
_OWNER_PREDICATE = (
    "EXISTS (SELECT 1 FROM messages m JOIN conversations c ON c.session_id = m.session_id "
    "WHERE m.id = \"message_feedback\".message_id AND m.role = 'assistant' "
    "AND c.user_id = current_setting('app.user_id')::uuid)"
)


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements
    # (004 never ran for exactly this reason).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS message_feedback (
            message_id UUID PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            rating     SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
            reasons    TEXT[] NOT NULL DEFAULT '{}',
            comment    TEXT CHECK (comment IS NULL OR char_length(comment) <= 1000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE message_feedback IS "
        "'One row per assistant message (current state, upsert on message_id) — "
        "no history of vote changes. No user_id: owner is derived via message -> "
        "conversation -> user, same as messages itself (007_rls OWNED_VIA_SESSION).'"
    )
    op.execute(
        "COMMENT ON COLUMN message_feedback.reasons IS "
        "'Reason codes, validated by the API Pydantic Literal, not a DB CHECK — "
        "adding a reason is a code change, not a migration (see 008_messages_extras).'"
    )

    # RLS — OWNED_VIA_SESSION shape (007_rls.py) with one extra hop, because
    # message_feedback has no session_id of its own to join conversations on.
    op.execute('ALTER TABLE "message_feedback" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY message_feedback_owner ON "message_feedback" FOR ALL '
        f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "message_feedback" TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS message_feedback_owner ON "message_feedback"')
    op.execute('ALTER TABLE IF EXISTS "message_feedback" DISABLE ROW LEVEL SECURITY')
    op.execute(f'REVOKE ALL ON "message_feedback" FROM "{APP_ROLE}"')
    op.execute("DROP TABLE IF EXISTS message_feedback")
