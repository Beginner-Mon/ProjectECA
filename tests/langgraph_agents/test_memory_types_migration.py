"""Guards on migration 017_memory_types (memory-store S1a)."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import patch

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "agenticRAG" / "langgraph_agents" / "alembic" / "versions"
    / "017_memory_types.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("migration_017", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statements(direction="upgrade"):
    module = _load()
    captured = []
    with patch.object(module.op, "execute", side_effect=captured.append):
        getattr(module, direction)()
    return captured


def _squashed(direction="upgrade"):
    return [re.sub(r"\s+", " ", s).strip() for s in _statements(direction)]


@pytest.mark.unit
def test_revision_chain():
    module = _load()
    assert module.revision == "017_memory_types"
    assert module.down_revision == "016_memory_tiers"


@pytest.mark.unit
@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_every_execute_is_one_statement(direction):
    for sql in _statements(direction):
        body = sql.strip().rstrip(";")
        assert ";" not in body


@pytest.mark.unit
def test_safety_backfill_uses_coalesce():
    joined = "\n".join(_squashed())
    assert "COALESCE(category IN ('condition', 'restriction'), false)" in joined


@pytest.mark.unit
def test_status_candidate_backfill():
    joined = "\n".join(_squashed())
    assert "UPDATE user_memory SET status = 'active' WHERE status = 'candidate'" in joined