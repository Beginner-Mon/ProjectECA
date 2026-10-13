"""Guards on migration 015_user_memory_ltm (plan memory-v2, C1).

No database: the migration runs against a fake `op` and the captured SQL is
checked, the same way test_turn_index_migration.py checks 014.
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
    / "015_user_memory_ltm.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_015", MIGRATION)
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


def _squashed(direction: str = "upgrade") -> list[str]:
    return [_squash(s) for s in _statements(direction)]


def _one(pattern: str) -> str:
    found = [s for s in _squashed() if re.search(pattern, s)]
    assert len(found) == 1, f"expected exactly one statement matching {pattern!r}"
    return found[0]


@pytest.mark.unit
def test_revision_chain():
    module = _load()
    assert module.revision == "015_user_memory_ltm"
    assert module.down_revision == "014_turn_index"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_every_execute_is_one_statement(direction):
    """asyncpg rejects batches; a multi-statement execute never runs."""
    for sql in _statements(direction):
        body = sql.strip().rstrip(";")
        assert ";" not in body, f"more than one statement in one execute: {sql[:80]}"


@pytest.mark.unit
def test_source_is_not_null_defaults_to_user():
    sql = _one(r"ADD COLUMN IF NOT EXISTS source TEXT")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "source TEXT NOT NULL DEFAULT 'user'" in sql


@pytest.mark.unit
def test_source_check_allows_exactly_user_and_extracted():
    sql = _one(r"ADD CONSTRAINT \w+ CHECK")
    assert sql.startswith("ALTER TABLE user_memory ADD CONSTRAINT ")
    assert re.search(
        r"CHECK \(source IN \('user', 'extracted'\)\)", sql
    ), "CHECK must allow exactly 'user' and 'extracted'"
    assert len(re.findall(r"'[^']*'", sql)) == 2


@pytest.mark.unit
def test_source_session_id_references_conversation_and_cascades():
    sql = _one(r"ADD COLUMN IF NOT EXISTS source_session_id")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert re.search(
        r"source_session_id UUID REFERENCES conversations\(session_id\) "
        r"ON DELETE CASCADE",
        sql,
    )
    assert "NOT NULL" not in sql, "source_session_id must be nullable"


@pytest.mark.unit
def test_updated_at_not_null_with_default():
    sql = _one(r"ADD COLUMN IF NOT EXISTS updated_at")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "updated_at TIMESTAMPTZ NOT NULL DEFAULT now()" in sql


@pytest.mark.unit
def test_partial_index_on_source_session_id():
    sql = _one(r"CREATE INDEX")
    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "ON user_memory (source_session_id)" in sql
    assert sql.endswith("WHERE source_session_id IS NOT NULL")


@pytest.mark.unit
def test_conversations_ltm_up_to_seq():
    sql = _one(r"ADD COLUMN IF NOT EXISTS ltm_up_to_seq")
    assert sql.startswith("ALTER TABLE conversations ")
    assert "ltm_up_to_seq BIGINT NOT NULL DEFAULT 0" in sql


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [
        "user_memory.source",
        "user_memory.source_session_id",
        "user_memory.updated_at",
        "conversations.ltm_up_to_seq",
    ],
)
def test_each_new_column_is_commented(target):
    assert any(
        s.startswith(f"COMMENT ON COLUMN {target} IS ") for s in _squashed()
    ), f"missing COMMENT ON COLUMN {target}"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_no_policy_and_no_grant(direction):
    """Policies and grants are per table; new columns need neither."""
    for s in _squashed(direction):
        assert not re.match(r"(CREATE|DROP|ALTER) POLICY", s)
        assert "ROW LEVEL SECURITY" not in s
        assert not re.match(r"(GRANT|REVOKE)\b", s)
        assert "DEFAULT PRIVILEGES" not in s


@pytest.mark.unit
def test_downgrade_removes_all_four_columns_index_and_constraint():
    down = _squashed("downgrade")
    joined = "\n".join(down)
    assert "DROP INDEX IF EXISTS" in joined
    assert re.search(r"DROP CONSTRAINT IF EXISTS \w+", joined)
    for table, column in [
        ("user_memory", "source"),
        ("user_memory", "source_session_id"),
        ("user_memory", "updated_at"),
        ("conversations", "ltm_up_to_seq"),
    ]:
        assert any(
            s.startswith(f"ALTER TABLE {table} ")
            and re.search(rf"DROP COLUMN IF EXISTS {column}$", s)
            for s in down
        ), f"downgrade must drop {table}.{column}"
    # Reverse order: the index goes before its column, the constraint before
    # the column it checks.
    idx = next(i for i, s in enumerate(down) if s.startswith("DROP INDEX"))
    ssid = next(
        i for i, s in enumerate(down) if s.endswith("DROP COLUMN IF EXISTS source_session_id")
    )
    chk = next(i for i, s in enumerate(down) if "DROP CONSTRAINT" in s)
    src = next(i for i, s in enumerate(down) if s.endswith("DROP COLUMN IF EXISTS source"))
    assert idx < ssid
    assert chk < src
