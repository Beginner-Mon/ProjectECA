"""user_memory: type + safety + topic rename (memory-store S1a).

Revision ID: 017_memory_types
Revises: 016_memory_tiers
Create Date: 2026-10-23

WHAT
----
1. `user_memory` gains `memory_type` (one of seven types) and `safety`
   (whether ignoring the item could harm the user). `memory_type` is added
   nullable, backfilled from the legacy `kind`/`category`, then set NOT NULL
   with DEFAULT 'profile' and a CHECK. `safety` is NOT NULL DEFAULT false
   and backfilled from `category`.
2. Candidate status is retired: every `candidate` row becomes `active`
   (D1 — no new row is ever a candidate).
3. `project` keeps its value (it becomes the item's topic); its comment is
   rewritten. `category` stays nullable and legacy; `kind` is kept in sync
   with `memory_type`.

Downgrade drops what upgrade added and restores the old `project` comment.
It does NOT restore `candidate` status (rows stay `active`).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "017_memory_types"
down_revision: Union[str, None] = "016_memory_tiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared
    # statements (004 never ran for exactly this reason).

    # memory_type: nullable first, backfilled below, then constrained.
    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS memory_type TEXT"
    )
    op.execute(
        "UPDATE user_memory SET memory_type = 'instruction' "
        "WHERE kind = 'instruction' OR category IN ('style', 'restriction')"
    )
    op.execute(
        "UPDATE user_memory SET memory_type = 'area' "
        "WHERE category IN ('goal', 'project')"
    )
    op.execute(
        "UPDATE user_memory SET memory_type = 'preference' "
        "WHERE category = 'preference'"
    )
    op.execute(
        "UPDATE user_memory SET memory_type = 'profile' "
        "WHERE memory_type IS NULL"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ALTER COLUMN memory_type SET DEFAULT 'profile'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ALTER COLUMN memory_type SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "ADD CONSTRAINT user_memory_memory_type_check "
        "CHECK (memory_type IN ('profile', 'preference', 'area', 'event', "
        "'person', 'reference', 'instruction'))"
    )

    op.execute(
        "ALTER TABLE user_memory "
        "ADD COLUMN IF NOT EXISTS safety BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        "UPDATE user_memory SET safety = (category IN ('condition', 'restriction'))"
    )

    op.execute(
        "UPDATE user_memory SET status = 'active' WHERE status = 'candidate'"
    )

    op.execute(
        "COMMENT ON COLUMN user_memory.memory_type IS "
        "'Type of the item: profile, preference, area, event, person, "
        "reference or instruction.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.safety IS "
        "'True when ignoring the item could harm the user.'"
    )
    op.execute(
        "COMMENT ON COLUMN user_memory.project IS "
        "'Topic of the item within its type (for person: who). "
        "NULL only on rows written before 017.'"
    )


def downgrade() -> None:
    op.execute(
        "COMMENT ON COLUMN user_memory.project IS "
        "'Project or topic label, at most 60 characters. NULL means no project.'"
    )
    op.execute(
        "ALTER TABLE user_memory "
        "DROP CONSTRAINT IF EXISTS user_memory_memory_type_check"
    )
    op.execute(
        "ALTER TABLE user_memory DROP COLUMN IF EXISTS safety"
    )
    op.execute(
        "ALTER TABLE user_memory DROP COLUMN IF EXISTS memory_type"
    )
