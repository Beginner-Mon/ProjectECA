"""Guards on migration 014_turn_index (plan memory-v2, B1).

No database: the migration runs against a fake `op` and the captured SQL is
checked, the same way test_rls_policies.py checks 007 and 013.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "agenticRAG" / "langgraph_agents" / "alembic" / "versions"
    / "014_turn_index.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_014", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction: str = "upgrade") -> list[str]:
    module = _load()
    captured: list[str] = []
    with patch.object(module.op, "execute", side_effect=captured.append):
        getattr(module, direction)()
    return captured


def _squash(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def _create_table() -> str:
    found = [s for s in _statements() if "CREATE TABLE" in s and "turn_index" in s]
    assert len(found) == 1, "expected exactly one CREATE TABLE turn_index"
    return _squash(found[0])


@pytest.mark.unit
def test_revision_chain():
    module = _load()
    assert module.revision == "014_turn_index"
    assert module.down_revision == "013_character_knowledge"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_every_execute_is_one_statement(direction):
    """asyncpg rejects batches; a multi-statement execute never runs."""
    for sql in _statements(direction):
        body = sql.strip().rstrip(";")
        assert ";" not in body, f"more than one statement in one execute: {sql[:80]}"


@pytest.mark.unit
def test_table_columns():
    ddl = _create_table()
    assert "CREATE TABLE IF NOT EXISTS turn_index" in ddl
    assert "id UUID PRIMARY KEY DEFAULT gen_random_uuid()" in ddl
    assert "content TEXT NOT NULL" in ddl
    assert "embedding vector(384) NOT NULL" in ddl
    assert "turn_at TIMESTAMPTZ NOT NULL" in ddl
    assert "created_at TIMESTAMPTZ NOT NULL DEFAULT now()" in ddl


@pytest.mark.unit
@pytest.mark.parametrize(
    "column,target",
    [
        ("session_id", "conversations(session_id)"),
        ("user_message_id", "messages(id)"),
        ("assistant_message_id", "messages(id)"),
    ],
)
def test_foreign_keys_cascade(column, target):
    ddl = _create_table()
    pattern = (
        rf"{column} UUID NOT NULL REFERENCES {re.escape(target)} ON DELETE CASCADE"
    )
    assert re.search(pattern, ddl), f"{column} must reference {target} ON DELETE CASCADE"


@pytest.mark.unit
def test_assistant_message_is_unique_and_named():
    ddl = _create_table()
    assert re.search(
        r"CONSTRAINT \w+ UNIQUE \(assistant_message_id\)", ddl
    ), "assistant_message_id needs a named UNIQUE constraint"


@pytest.mark.unit
def test_hnsw_cosine_index_and_session_index():
    sql = [_squash(s) for s in _statements()]
    assert any(
        "CREATE INDEX IF NOT EXISTS" in s
        and "ON turn_index USING hnsw (embedding vector_cosine_ops)" in s
        for s in sql
    )
    assert any(
        "CREATE INDEX IF NOT EXISTS" in s and "ON turn_index (session_id)" in s
        for s in sql
    )


@pytest.mark.unit
def test_rls_enabled_with_through_conversations_policy():
    sql = [_squash(s) for s in _statements()]
    assert 'ALTER TABLE "turn_index" ENABLE ROW LEVEL SECURITY' in sql
    policies = [s for s in sql if s.startswith("CREATE POLICY")]
    assert len(policies) == 1
    policy = policies[0]
    assert policy.startswith('CREATE POLICY turn_index_owner ON "turn_index" FOR ALL')
    assert "USING (" in policy and "WITH CHECK (" in policy
    using = policy.split("USING (", 1)[1].split(") WITH CHECK (", 1)[0]
    check = policy.split("WITH CHECK (", 1)[1].rsplit(")", 1)[0]
    assert using == check, "USING and WITH CHECK must be identical"
    assert "FROM conversations c" in using
    assert 'c.session_id = "turn_index".session_id' in using
    assert "c.user_id = current_setting('app.user_id')::uuid" in using
    assert not re.search(r"current_setting\(\s*'app\.user_id'\s*,", policy), (
        "current_setting must take one argument (no missing_ok)"
    )


@pytest.mark.unit
def test_grant_has_no_update():
    grants = [_squash(s) for s in _statements() if s.strip().startswith("GRANT")]
    assert grants == ['GRANT SELECT, INSERT, DELETE ON "turn_index" TO "eca_user"']
    assert "UPDATE" not in grants[0]
    assert not any("ALTER DEFAULT PRIVILEGES" in s for s in _statements())


@pytest.mark.unit
def test_table_comment():
    assert any(s.startswith("COMMENT ON TABLE turn_index IS") for s in _statements())


@pytest.mark.unit
def test_downgrade_removes_what_upgrade_added():
    down = [_squash(s) for s in _statements("downgrade")]
    joined = "\n".join(down)
    assert 'DROP POLICY IF EXISTS turn_index_owner ON "turn_index"' in joined
    assert 'ALTER TABLE IF EXISTS "turn_index" DISABLE ROW LEVEL SECURITY' in joined
    assert 'REVOKE ALL ON "turn_index" FROM "eca_user"' in joined
    assert "DROP TABLE IF EXISTS turn_index" in joined
    assert down[-1] == "DROP TABLE IF EXISTS turn_index"
    assert sum("DROP INDEX IF EXISTS" in s for s in down) == 2
