"""Guards on migration 016_memory_tiers (plan memory-v2 tiers).

No database: the migration runs against a fake `op` and the captured SQL is
checked, the same way test_user_memory_ltm_migration.py checks 015.

The upgrade only ADDS columns and backfills them, drops the derived
turn_index table last, and never DELETEs a user_memory row — downgrade drops
what upgrade added, in reverse, and rebuilds turn_index from 014's statements.
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
    / "016_memory_tiers.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_016", MIGRATION)
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


def _one(pattern: str, direction: str = "upgrade") -> str:
    found = [s for s in _squashed(direction) if re.search(pattern, s)]
    assert len(found) == 1, f"expected exactly one statement matching {pattern!r}"
    return found[0]


@pytest.mark.unit
def test_revision_chain():
    module = _load()
    assert module.revision == "016_memory_tiers"
    assert module.down_revision == "015_user_memory_ltm"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_every_execute_is_one_statement(direction):
    """asyncpg rejects batches; a multi-statement execute never runs.

    The trigger function body legitimately holds semicolons inside its
    $$-quoted plpgsql block — the server still sees a single CREATE FUNCTION
    command, so only that statement is exempt from the semicolon check.
    """
    for sql in _statements(direction):
        body = sql.strip().rstrip(";")
        if re.match(r"CREATE (OR REPLACE )?FUNCTION\b", body):
            assert body.rstrip().endswith("LANGUAGE plpgsql")
            continue
        assert ";" not in body, f"more than one statement in one execute: {sql[:80]}"


# ── user_memory columns ─────────────────────────────────────────────────


@pytest.mark.unit
def test_kind_is_not_null_defaults_to_fact_with_check():
    sql = _one(r"ADD COLUMN IF NOT EXISTS kind TEXT")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "kind TEXT NOT NULL DEFAULT 'fact'" in sql
    check = _one(r"ADD CONSTRAINT \w*kind\w* CHECK")
    assert re.search(r"CHECK \(kind IN \('instruction', 'fact'\)\)", check)


@pytest.mark.unit
def test_tier_is_not_null_defaults_to_2_with_check():
    sql = _one(r"ADD COLUMN IF NOT EXISTS tier SMALLINT")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "tier SMALLINT NOT NULL DEFAULT 2" in sql
    check = _one(r"ADD CONSTRAINT \w*tier\w* CHECK")
    assert re.search(r"CHECK \(tier IN \(1, 2\)\)", check)


@pytest.mark.unit
def test_status_is_not_null_defaults_to_active_with_check():
    sql = _one(r"ADD COLUMN IF NOT EXISTS status TEXT")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "status TEXT NOT NULL DEFAULT 'active'" in sql
    check = _one(r"ADD CONSTRAINT \w*status\w* CHECK")
    assert re.search(r"CHECK \(status IN \('candidate', 'active'\)\)", check)


@pytest.mark.unit
def test_project_is_nullable_with_60_char_check():
    sql = _one(r"ADD COLUMN IF NOT EXISTS project TEXT")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "NOT NULL" not in sql
    check = _one(r"ADD CONSTRAINT \w*project\w* CHECK")
    assert re.search(
        r"CHECK \(project IS NULL OR char_length\(project\) <= 60\)", check)


@pytest.mark.unit
def test_seen_count_defaults_to_1():
    sql = _one(r"ADD COLUMN IF NOT EXISTS seen_count INT")
    assert "seen_count INT NOT NULL DEFAULT 1" in sql


@pytest.mark.unit
def test_seen_projects_defaults_to_empty_array():
    sql = _one(r"ADD COLUMN IF NOT EXISTS seen_projects TEXT")
    assert "seen_projects TEXT[] NOT NULL DEFAULT '{}'" in sql


@pytest.mark.unit
def test_last_seen_session_id_references_conversation_and_sets_null():
    sql = _one(r"ADD COLUMN IF NOT EXISTS last_seen_session_id")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert re.search(
        r"last_seen_session_id UUID REFERENCES conversations\(session_id\) "
        r"ON DELETE SET NULL",
        sql,
    )
    assert "NOT NULL" not in sql, "last_seen_session_id must be nullable"


@pytest.mark.unit
def test_said_at_is_not_null_with_default():
    sql = _one(r"ADD COLUMN IF NOT EXISTS said_at TIMESTAMPTZ")
    assert "said_at TIMESTAMPTZ NOT NULL DEFAULT now()" in sql


@pytest.mark.unit
def test_embedding_is_nullable_vector_384():
    sql = _one(r"ADD COLUMN IF NOT EXISTS embedding vector\(384\)")
    assert sql.startswith("ALTER TABLE user_memory ")
    assert "NOT NULL" not in sql, "embedding NULL means still to embed"


@pytest.mark.unit
@pytest.mark.parametrize(
    "target",
    [
        "user_memory.kind",
        "user_memory.tier",
        "user_memory.status",
        "user_memory.project",
        "user_memory.seen_count",
        "user_memory.seen_projects",
        "user_memory.last_seen_session_id",
        "user_memory.said_at",
        "user_memory.embedding",
        "conversations.stm_summary",
        "conversations.stm_up_to_seq",
    ],
)
def test_each_new_column_is_commented(target):
    assert any(
        s.startswith(f"COMMENT ON COLUMN {target} IS ") for s in _squashed()
    ), f"missing COMMENT ON COLUMN {target}"


# ── index + trigger ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_partial_index_on_tier():
    sql = _one(r"CREATE INDEX IF NOT EXISTS idx_user_memory_tier")
    assert "ON user_memory (user_id, tier, status)" in sql
    assert sql.endswith("WHERE valid = true")
    assert "vector" not in sql and "hnsw" not in sql


@pytest.mark.unit
def test_trigger_clears_embedding_on_fact_or_project_change():
    fn = _one(r"CREATE (OR REPLACE )?FUNCTION user_memory_clear_embedding\b")
    assert "BEFORE UPDATE" in fn or "TRIGGER" in fn.upper() or "RETURNS trigger" in fn
    assert "fact_text" in fn and "project" in fn
    assert re.search(r"NEW\.embedding\s*:=\s*NULL", fn)
    trig = _one(r"CREATE TRIGGER user_memory_clear_embedding")
    assert "BEFORE UPDATE ON user_memory" in trig
    assert "EXECUTE FUNCTION user_memory_clear_embedding()" in trig


# ── conversations STM columns ───────────────────────────────────────────


@pytest.mark.unit
def test_conversations_stm_columns():
    summary = _one(r"ADD COLUMN IF NOT EXISTS stm_summary TEXT")
    assert summary.startswith("ALTER TABLE conversations ")
    assert "NOT NULL" not in summary
    up_to = _one(r"ADD COLUMN IF NOT EXISTS stm_up_to_seq BIGINT")
    assert up_to.startswith("ALTER TABLE conversations ")
    assert "stm_up_to_seq BIGINT NOT NULL DEFAULT 0" in up_to


# ── backfill ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_backfill_restriction_becomes_tier1_instruction():
    sql = _one(r"UPDATE user_memory SET.*tier = 1 WHERE category = 'restriction'")
    assert "kind = 'instruction'" in sql


@pytest.mark.unit
def test_backfill_condition_becomes_tier1():
    sql = _one(r"UPDATE user_memory SET tier = 1 WHERE category = 'condition'")
    assert "kind" not in sql, "condition keeps kind='fact'"


@pytest.mark.unit
def test_backfill_said_at_and_last_seen():
    sql = _one(r"UPDATE user_memory SET said_at")
    assert "COALESCE(created_at, now())" in sql
    assert "last_seen_session_id = source_session_id" in sql


@pytest.mark.unit
def test_stm_backfill_joins_active_summaries():
    sql = _one(r"UPDATE conversations SET stm_summary")
    assert "status = 'active'" in sql
    assert "covers_from_seq" in sql
    assert "MAX(covers_up_to_seq)" in sql


# ── turn_index ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_upgrade_drops_turn_index_last():
    stmts = _squashed()
    drop = _one(r"DROP TABLE IF EXISTS turn_index CASCADE")
    assert stmts[-1] == drop, "the destructive statement goes last"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_no_delete_statement_on_user_memory(direction):
    """Backfill writes columns; nothing here deletes a user's rows."""
    for sql in _squashed(direction):
        assert not re.match(r"DELETE\s+(FROM\s+)?(\"?)user_memory\b", sql, re.IGNORECASE), (
            f"a DELETE on user_memory is not allowed here: {sql[:80]}"
        )


@pytest.mark.unit
def test_downgrade_recreates_turn_index_first():
    down = _squashed("downgrade")
    assert down[0].startswith("CREATE TABLE"), (
        "reverse order: turn_index comes back before anything is dropped"
    )
    joined = "\n".join(down)
    assert "CREATE TABLE IF NOT EXISTS turn_index" in joined
    assert "ROW LEVEL SECURITY" in joined
    assert "turn_index_owner" in joined


@pytest.mark.unit
def test_downgrade_removes_what_upgrade_added():
    down = _squashed("downgrade")
    joined = "\n".join(down)
    assert "DROP INDEX IF EXISTS idx_user_memory_tier" in joined
    assert "DROP TRIGGER IF EXISTS user_memory_clear_embedding" in joined
    assert "DROP FUNCTION IF EXISTS user_memory_clear_embedding()" in joined
    for table, column in [
        ("user_memory", "kind"),
        ("user_memory", "tier"),
        ("user_memory", "status"),
        ("user_memory", "project"),
        ("user_memory", "seen_count"),
        ("user_memory", "seen_projects"),
        ("user_memory", "last_seen_session_id"),
        ("user_memory", "said_at"),
        ("user_memory", "embedding"),
        ("conversations", "stm_summary"),
        ("conversations", "stm_up_to_seq"),
    ]:
        assert any(
            s.startswith(f"ALTER TABLE {table} ")
            and re.search(rf"DROP COLUMN IF EXISTS {column}$", s)
            for s in down
        ), f"downgrade must drop {table}.{column}"
    for name in ("kind", "tier", "status", "project"):
        assert re.search(rf"DROP CONSTRAINT IF EXISTS \w*{name}\w*", joined), (
            f"downgrade must drop the {name} CHECK"
        )
    # Reverse order: the index goes before the columns it covers.
    idx = next(i for i, s in enumerate(down) if s.startswith("DROP INDEX"))
    kind = next(
        i for i, s in enumerate(down) if s.endswith("DROP COLUMN IF EXISTS kind")
    )
    assert idx < kind
