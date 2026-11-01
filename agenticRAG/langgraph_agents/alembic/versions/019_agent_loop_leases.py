"""Leases and rate counters for the read-only agent loop.

Revision ID: 019_agent_loop_leases
Revises: 018_plans_quota
Create Date: 2026-11-01

Two tables, no RLS (ids and counters only — the global lease count must see
every row, and rate counters are per (user, bucket, window)):

* ``agent_leases`` — one loop turn per session. PK is (user_id, session_id);
  a conditional upsert under `pg_advisory_xact_lock` claims the slot when the
  row is expired, already owned by this request_id, or fewer than
  max_concurrent live leases exist.
* ``rate_counters`` — per (user, bucket, window_start) upserted count for the
  30/min rate limit.

Grants to `eca_user` as in 007_rls.py. No serial columns → no sequence
grants.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "019_agent_loop_leases"
down_revision: Union[str, None] = "018_plans_quota"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements.

    # ── agent_leases ────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_leases (
            user_id UUID NOT NULL,
            session_id UUID NOT NULL,
            request_id UUID NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            cancel_requested BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "agent_leases" TO "{APP_ROLE}"')

    # ── rate_counters ───────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_counters (
            user_id UUID NOT NULL,
            bucket TEXT NOT NULL,
            window_start TIMESTAMPTZ NOT NULL,
            count BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, bucket, window_start)
        )
        """
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "rate_counters" TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS agent_leases')
    op.execute('DROP TABLE IF EXISTS rate_counters')
