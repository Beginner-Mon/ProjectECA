"""Per-user plugin switches for the agent loop.

Revision ID: 020_user_plugins
Revises: 019_agent_loop_leases
Create Date: 2026-11-02

One table, with the RLS owner policy of 007_rls.py (the `OWNED_DIRECTLY` loop):
a user sees and writes only their own rows. A user who never touched a plugin
has no row; the manifest's `enabled_by_default` applies (stage 3, D3).

Grants to `eca_user` as in 019: the application role gets SELECT, INSERT,
UPDATE and DELETE on the new table, and nothing else. No serial column, so no
sequence grant. Written by the stage-3 build; applied by Tri, never by an agent.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "020_user_plugins"
down_revision: Union[str, None] = "019_agent_loop_leases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"


def upgrade() -> None:
    # One statement per op.execute(): asyncpg rejects batched prepared statements.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_plugins (
            user_id UUID NOT NULL,
            plugin_id TEXT NOT NULL,
            enabled BOOLEAN NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, plugin_id)
        )
        """
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "user_plugins" TO "{APP_ROLE}"')
    op.execute('ALTER TABLE "user_plugins" ENABLE ROW LEVEL SECURITY')
    # USING filters reads/updates/deletes, WITH CHECK constrains writes. No
    # `, true` on current_setting: a query outside user_scope must fail loudly.
    op.execute(
        'CREATE POLICY user_plugins_owner ON "user_plugins" FOR ALL '
        "USING (user_id = current_setting('app.user_id')::uuid) "
        "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS user_plugins_owner ON "user_plugins"')
    op.execute("DROP TABLE IF EXISTS user_plugins")
