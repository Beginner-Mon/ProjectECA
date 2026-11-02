"""Approval actions and paused turns for the agent loop.

Revision ID: 021_agent_actions
Revises: 020_user_plugins
Create Date: 2026-11-02

Two tables, both with the RLS owner policy of 007_rls.py (the `OWNED_DIRECTLY`
loop): a user sees and writes only their own rows.

* `agent_actions`: one row per proposed write action (plan §3). `status` moves
  only by compare-and-set (`UPDATE ... WHERE status = <old> RETURNING`).
  `payload_enc` is KMS-sealed and is set to NULL when the action reaches a
  final status.
* `loop_turns`: one row per paused loop turn (`thread_id`). `transcript_enc`
  is sealed; `state_json` holds no payload text.

Grants to `eca_user` as in 019/020: SELECT, INSERT, UPDATE, DELETE on the new
tables and nothing else. No serial column, so no sequence grant. Written by the
stage-4 build; applied by Tri, never by an agent.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "021_agent_actions"
down_revision: Union[str, None] = "020_user_plugins"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"

_ACTION_STATUSES = (
    "'proposed','approved','executing','succeeded','failed','unknown',"
    "'declined','expired','invalidated'"
)
_TURN_STATUSES = "'paused','resumed','finished','invalidated'"


def upgrade() -> None:
    # One statement per op.execute(): asyncpg rejects batched prepared statements.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS agent_actions (
            action_id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            session_id UUID NOT NULL,
            thread_id UUID NOT NULL,
            plugin_id TEXT NOT NULL,
            plugin_version TEXT NOT NULL DEFAULT '',
            tool TEXT NOT NULL,
            tool_def_sha256 TEXT NOT NULL DEFAULT '',
            payload_sha256 TEXT NOT NULL,
            payload_display JSONB NOT NULL DEFAULT '[]'::jsonb,
            recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
            provenance JSONB NOT NULL DEFAULT '[]'::jsonb,
            payload_enc BYTEA,
            risk TEXT NOT NULL DEFAULT 'write',
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ({_ACTION_STATUSES})),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            result_ref TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS agent_actions_session_status_idx "
        "ON agent_actions (user_id, session_id, status)"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "agent_actions" TO "{APP_ROLE}"')
    op.execute('ALTER TABLE "agent_actions" ENABLE ROW LEVEL SECURITY')
    # USING filters reads/updates/deletes, WITH CHECK constrains writes. No
    # `, true` on current_setting: a query outside user_scope must fail loudly.
    op.execute(
        'CREATE POLICY agent_actions_owner ON "agent_actions" FOR ALL '
        "USING (user_id = current_setting('app.user_id')::uuid) "
        "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
    )

    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS loop_turns (
            thread_id UUID PRIMARY KEY,
            user_id UUID NOT NULL,
            session_id UUID NOT NULL,
            request_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'paused'
                CHECK (status IN ({_TURN_STATUSES})),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            transcript_enc BYTEA,
            state_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS loop_turns_session_status_idx "
        "ON loop_turns (user_id, session_id, status)"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "loop_turns" TO "{APP_ROLE}"')
    op.execute('ALTER TABLE "loop_turns" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY loop_turns_owner ON "loop_turns" FOR ALL '
        "USING (user_id = current_setting('app.user_id')::uuid) "
        "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS loop_turns_owner ON "loop_turns"')
    op.execute("DROP TABLE IF EXISTS loop_turns")
    op.execute('DROP POLICY IF EXISTS agent_actions_owner ON "agent_actions"')
    op.execute("DROP TABLE IF EXISTS agent_actions")
