"""Guards on migration 012_message_feedback.py.

Same technique as test_messages_extras_column.py and test_rls_policies.py:
execute upgrade()/downgrade() against a fake `op` and assert on the SQL that
was actually emitted, not on the file's text. A text search would also match
the docstring prose explaining these same decisions.

Running the migration against a live Postgres is a separate, integration-
marked concern; there is no Neon connection in unit CI.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest

VERSIONS = (
    Path(__file__).resolve().parents[2]
    / "agenticRAG" / "langgraph_agents" / "alembic" / "versions"
)
MIGRATION = VERSIONS / "012_message_feedback.py"
PARENT_REVISION = "011_character_voices"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"mig_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str = "upgrade") -> list[str]:
    module = _load(MIGRATION)
    captured: list[str] = []
    with patch.object(module.op, "execute", side_effect=captured.append):
        getattr(module, direction)()
    return captured


# ── Chain ────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_the_migration_lives_where_alembic_reads_from():
    assert MIGRATION.is_file()


@pytest.mark.unit
def test_revision_id_matches_the_filename():
    assert _load(MIGRATION).revision == MIGRATION.stem


@pytest.mark.unit
def test_it_chains_from_011_character_voices():
    assert _load(MIGRATION).down_revision == PARENT_REVISION


@pytest.mark.unit
def test_nothing_else_claims_011_as_its_parent():
    """Two children of one revision is a branch, and `alembic upgrade head`
    then fails with 'Multiple head revisions are present' — the migration
    would not run at all."""
    children = [
        p.name for p in VERSIONS.glob("*.py")
        if _load(p).down_revision == PARENT_REVISION
    ]
    assert children == ["012_message_feedback.py"], children


# ── upgrade() SQL ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_one_statement_per_execute():
    """asyncpg rejects a batch with 'cannot insert multiple commands into a
    prepared statement'. Migration 004 never ran at all for this reason."""
    for stmt in _statements() + _statements("downgrade"):
        assert stmt.strip().rstrip(";").count(";") == 0, stmt


@pytest.mark.unit
def test_creates_the_table_with_cascade_and_no_user_id():
    create_stmts = [
        s for s in _statements()
        if s.strip().lower().startswith("create table")
    ]
    assert len(create_stmts) == 1, create_stmts
    create_sql = create_stmts[0].lower()
    assert "create table if not exists message_feedback" in create_sql, create_sql
    assert "references messages(id) on delete cascade" in create_sql, create_sql
    assert "user_id" not in create_sql, (
        "message_feedback must not carry a user_id column — owner is derived "
        "via message -> conversation -> user, see the migration docstring. "
        f"CREATE TABLE statement: {create_sql}"
    )


@pytest.mark.unit
def test_enables_row_level_security():
    sql = " ".join(_statements())
    assert 'ALTER TABLE "message_feedback" ENABLE ROW LEVEL SECURITY' in sql


@pytest.mark.unit
def test_policy_predicate_joins_conversations_and_checks_both_using_and_with_check():
    policies = [s for s in _statements() if "CREATE POLICY" in s]
    assert len(policies) == 1, policies
    policy = policies[0]
    assert "JOIN conversations" in policy, policy
    assert "USING (" in policy, policy
    assert "WITH CHECK (" in policy, policy
    # Same predicate string must appear in both clauses — that's what makes a
    # user unable to write a row they could never read back. Strip exactly
    # the one outer paren each clause wrapper owns (the predicate itself is
    # `EXISTS (...)`, so naive rstrip(")") would eat its closing paren too).
    using_clause = policy.split("USING (", 1)[1].split(") WITH CHECK")[0]
    with_check_clause = policy.split("WITH CHECK (", 1)[1][:-1]
    assert using_clause == with_check_clause, policy


@pytest.mark.unit
def test_current_setting_has_no_missing_ok_argument():
    """Same guard as 007_rls: a second argument turns a forgotten
    user_scope() into an empty result instead of an error."""
    offenders = [
        sql for sql in _statements()
        if re.search(r"current_setting\(\s*'app\.user_id'\s*,", sql)
    ]
    assert not offenders, offenders
    assert any("current_setting('app.user_id')" in s for s in _statements())


@pytest.mark.unit
def test_grants_full_crud_to_the_app_role():
    sql = " ".join(_statements())
    assert (
        'GRANT SELECT, INSERT, UPDATE, DELETE ON "message_feedback" TO "eca_user"'
        in sql
    ), sql


# ── downgrade() SQL ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_downgrade_reverses_upgrade_in_order():
    down = _statements("downgrade")
    joined = " ".join(down)
    assert 'DROP POLICY IF EXISTS message_feedback_owner ON "message_feedback"' in joined
    assert 'DISABLE ROW LEVEL SECURITY' in joined
    assert 'REVOKE ALL ON "message_feedback" FROM "eca_user"' in joined
    assert "DROP TABLE IF EXISTS message_feedback" in joined

    # Order matters: dropping the table before the policy/grant would still
    # work, but revoking before dropping the policy is the pattern 009 uses,
    # so mirror it — policy first, then RLS off, then revoke, then drop.
    policy_idx = next(i for i, s in enumerate(down) if "DROP POLICY" in s)
    disable_idx = next(i for i, s in enumerate(down) if "DISABLE ROW LEVEL SECURITY" in s)
    revoke_idx = next(i for i, s in enumerate(down) if s.startswith("REVOKE"))
    drop_table_idx = next(i for i, s in enumerate(down) if s.startswith("DROP TABLE"))
    assert policy_idx < disable_idx < revoke_idx < drop_table_idx, down
