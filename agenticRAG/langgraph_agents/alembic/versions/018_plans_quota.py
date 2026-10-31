"""Tiers, daily token quota, and the admin audit log.

Revision ID: 018_plans_quota
Revises: 017_memory_types
Create Date: 2026-10-31

Three tables (docs/plans/roles-and-quota.md):

* ``plans`` — one row per tier (free / pro / ultra …) with its daily token
  limit. Everyone reads it; only an admin writes it.
* ``user_plan`` — one row per user: which tier, an optional per-user limit,
  and today's usage. The day is a calendar day in QUOTA_RESET_TZ (default
  Asia/Ho_Chi_Minh), so everyone resets together at local midnight.
* ``admin_audit`` — append-only record of every admin change. Admins only.

WHO IS AN ADMIN, AS FAR AS THE DATABASE IS CONCERNED

``current_setting('app.role', true) = 'admin'``. The setting is bound by
PostgresClient.user_scope from the ``cognito:groups`` claim of the verified
token (api/auth.py), exactly like ``app.user_id`` — never from request input.
The ``, true`` (missing_ok) is deliberate here, unlike app.user_id in 007: a
missing role must mean "not admin", and NULL = 'admin' is NULL, i.e. false.
The owner half of each predicate keeps 007's fail-loud behaviour.

WHY A TRIGGER ON user_plan

RLS lets a user read and update their OWN row (the chat route charges usage
there), but a row-level policy cannot tell which COLUMNS changed. Without the
trigger, any bug that let a request write its own row could also move that
user to `ultra`. The trigger rejects a change to plan_name or limit_override
unless the session is admin.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "018_plans_quota"
down_revision: Union[str, None] = "017_memory_types"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APP_ROLE = "eca_user"
IS_ADMIN = "current_setting('app.role', true) = 'admin'"
IS_OWNER = "user_id = current_setting('app.user_id')::uuid"


def upgrade() -> None:
    # One statement per op.execute() — asyncpg rejects batched prepared statements.

    # ── plans ───────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS plans (
            name TEXT PRIMARY KEY CHECK (name ~ '^[a-z0-9_-]{1,32}$'),
            daily_token_limit BIGINT NOT NULL CHECK (daily_token_limit >= 0),
            is_default BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Exactly one default tier: new users need somewhere to land.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS plans_one_default ON plans (is_default) "
        "WHERE is_default"
    )
    op.execute(
        "INSERT INTO plans (name, daily_token_limit, is_default) VALUES "
        "('free', 20000, true), ('pro', 100000, false), ('ultra', 500000, false) "
        "ON CONFLICT (name) DO NOTHING"
    )
    op.execute('ALTER TABLE "plans" ENABLE ROW LEVEL SECURITY')
    op.execute('CREATE POLICY plans_read ON "plans" FOR SELECT USING (true)')
    op.execute(f'CREATE POLICY plans_admin_insert ON "plans" FOR INSERT WITH CHECK ({IS_ADMIN})')
    op.execute(
        f'CREATE POLICY plans_admin_update ON "plans" FOR UPDATE '
        f"USING ({IS_ADMIN}) WITH CHECK ({IS_ADMIN})"
    )
    op.execute(f'CREATE POLICY plans_admin_delete ON "plans" FOR DELETE USING ({IS_ADMIN})')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "plans" TO "{APP_ROLE}"')

    # ── user_plan ───────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_plan (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            -- RESTRICT: a tier that still has users cannot be deleted.
            plan_name TEXT NOT NULL REFERENCES plans(name)
                ON UPDATE CASCADE ON DELETE RESTRICT,
            limit_override BIGINT CHECK (limit_override IS NULL OR limit_override >= 0),
            usage_day DATE,
            tokens_used BIGINT NOT NULL DEFAULT 0 CHECK (tokens_used >= 0),
            -- From the ID token on each turn, so the admin page can find people.
            email TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_plan_email ON user_plan (lower(email))")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION user_plan_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('app.role', true) = 'admin' THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.limit_override IS NOT NULL
                   OR NEW.plan_name IS DISTINCT FROM
                      (SELECT name FROM plans WHERE is_default) THEN
                    RAISE EXCEPTION 'only an admin may choose a tier or a limit';
                END IF;
            ELSIF NEW.plan_name IS DISTINCT FROM OLD.plan_name
               OR NEW.limit_override IS DISTINCT FROM OLD.limit_override THEN
                RAISE EXCEPTION 'only an admin may change a tier or a limit';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER user_plan_guard BEFORE INSERT OR UPDATE ON user_plan "
        "FOR EACH ROW EXECUTE FUNCTION user_plan_guard()"
    )
    op.execute('ALTER TABLE "user_plan" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY user_plan_owner_or_admin ON "user_plan" FOR ALL '
        f"USING ({IS_ADMIN} OR {IS_OWNER}) WITH CHECK ({IS_ADMIN} OR {IS_OWNER})"
    )
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON "user_plan" TO "{APP_ROLE}"')

    # ── admin_audit ─────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_audit (
            id BIGSERIAL PRIMARY KEY,
            at TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor_id UUID NOT NULL,
            actor_email TEXT,
            action TEXT NOT NULL,
            target TEXT NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_at ON admin_audit (at DESC)")
    op.execute('ALTER TABLE "admin_audit" ENABLE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY admin_audit_read ON "admin_audit" FOR SELECT USING ({IS_ADMIN})')
    op.execute(f'CREATE POLICY admin_audit_write ON "admin_audit" FOR INSERT WITH CHECK ({IS_ADMIN})')
    # Append-only: no UPDATE or DELETE grant.
    op.execute(f'GRANT SELECT, INSERT ON "admin_audit" TO "{APP_ROLE}"')
    op.execute(f'GRANT USAGE ON SEQUENCE admin_audit_id_seq TO "{APP_ROLE}"')


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS admin_audit')
    op.execute('DROP TRIGGER IF EXISTS user_plan_guard ON user_plan')
    op.execute('DROP FUNCTION IF EXISTS user_plan_guard()')
    op.execute('DROP TABLE IF EXISTS user_plan')
    op.execute('DROP TABLE IF EXISTS plans')
