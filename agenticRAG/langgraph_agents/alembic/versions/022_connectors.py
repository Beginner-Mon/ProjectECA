"""OAuth connectors: per-user connections and pending authorisations.

Revision ID: 022_connectors
Revises: 021_agent_actions
Create Date: 2026-11-02

Two tables, both with the RLS owner policy of 007_rls.py: a user sees and
writes only their own rows.

* `user_connections`: one row per (user, plugin). `access_enc` / `refresh_enc`
  are sealed (KMS envelope, context {user_id, plugin_id, purpose}); they are
  NULL once the connection is `auth_expired`, so a dead token is not kept.
  `issuer` pins the authorisation server the user connected through.
* `oauth_pending`: one row per started authorisation. `state_sha256` (the
  sha256 of the `state` value; the value itself is never stored) is the key.
  `verifier_enc` is the sealed PKCE verifier and is set to NULL when the state
  is consumed. A state is valid once (`used`) and until `expires_at`.

Grants to `eca_user` as in 019 to 021: SELECT, INSERT, UPDATE, DELETE on the new
tables and nothing else. Written by the stage-5 build; applied by Tri, never by
an agent.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "022_connectors"
down_revision: Union[str, None] = "021_agent_actions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"


def upgrade() -> None:
    # One statement per op.execute(): asyncpg rejects batched prepared statements.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_connections (
            user_id UUID NOT NULL,
            plugin_id TEXT NOT NULL,
            access_enc BYTEA,
            refresh_enc BYTEA,
            expires_at TIMESTAMPTZ,
            scopes TEXT[] NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','auth_expired')),
            issuer TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, plugin_id)
        )
        """
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "user_connections" TO "{APP_ROLE}"')
    op.execute('ALTER TABLE "user_connections" ENABLE ROW LEVEL SECURITY')
    # No `, true` on current_setting: a query outside user_scope must fail loudly.
    op.execute(
        'CREATE POLICY user_connections_owner ON "user_connections" FOR ALL '
        "USING (user_id = current_setting('app.user_id')::uuid) "
        "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_pending (
            state_sha256 TEXT PRIMARY KEY,
            user_id UUID NOT NULL,
            plugin_id TEXT NOT NULL,
            verifier_enc BYTEA,
            issuer TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            used BOOLEAN NOT NULL DEFAULT false
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS oauth_pending_user_idx ON oauth_pending (user_id)"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "oauth_pending" TO "{APP_ROLE}"')
    op.execute('ALTER TABLE "oauth_pending" ENABLE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY oauth_pending_owner ON "oauth_pending" FOR ALL '
        "USING (user_id = current_setting('app.user_id')::uuid) "
        "WITH CHECK (user_id = current_setting('app.user_id')::uuid)"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS oauth_pending_owner ON "oauth_pending"')
    op.execute("DROP TABLE IF EXISTS oauth_pending")
    op.execute('DROP POLICY IF EXISTS user_connections_owner ON "user_connections"')
    op.execute("DROP TABLE IF EXISTS user_connections")
