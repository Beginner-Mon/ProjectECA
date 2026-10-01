"""kb_search lọc nguồn + ngưỡng similarity (plan T5)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.tools import pgvector_tool
from langgraph_agents.tools.pgvector_tool import LIBRARY_SOURCE_TYPES, kb_search


def _row(sim, source_type="exercise_db"):
    return {"content": f"chunk {sim}", "chunk_index": 0, "similarity": sim,
            "source_type": source_type, "title": "Doc"}


def _mocks(rows):
    mock_pg = AsyncMock()
    mock_pg.connect = AsyncMock()
    mock_pg.fetch = AsyncMock(return_value=[dict(r) for r in rows])
    mock_embed = MagicMock()
    mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 384)
    return mock_pg, mock_embed


async def _run(rows, threshold=None):
    mock_pg, mock_embed = _mocks(rows)
    with patch.object(pgvector_tool, "get_pg_client", return_value=mock_pg), \
         patch.object(pgvector_tool, "get_embedding_service", return_value=mock_embed), \
         patch.object(pgvector_tool, "_kb_min_similarity", return_value=threshold):
        result = await kb_search.ainvoke({"query": "dau lung", "top_k": 5})
    return result, mock_pg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sql_filters_library_source_types():
    result, mock_pg = await _run([_row(0.9)])
    assert result != []
    sql, vec, top_k, source_types = mock_pg.fetch.await_args.args
    assert "source_type = ANY" in sql
    assert tuple(source_types) == LIBRARY_SOURCE_TYPES
    assert LIBRARY_SOURCE_TYPES == ("exercise_db",)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rows_below_threshold_are_dropped():
    result, _ = await _run([_row(0.95), _row(0.5)], threshold=0.9)
    sims = [r["similarity"] for r in result]
    assert sims == [0.95]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_stays_empty_not_error():
    result, _ = await _run([], threshold=0.9)
    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_threshold_by_default():
    assert pgvector_tool._kb_min_similarity() is None
    result, _ = await _run([_row(0.5)])
    assert len(result) == 1
