"""recall_self: kiến thức về bản thân qua similarity search (plan T8e)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.tools import pgvector_tool


def _mocks(rows):
    mock_pg = AsyncMock()
    mock_pg.connect = AsyncMock()
    mock_pg.fetch = AsyncMock(return_value=[dict(r) for r in rows])
    mock_embed = MagicMock()
    mock_embed.aembed_query = AsyncMock(return_value=[0.1] * 384)
    return mock_pg, mock_embed


def _config(persona_id="anne"):
    return {"configurable": {
        "user_id": "u1", "session_id": "s1", "persona_id": persona_id,
        "request_id": "r1",
    }}


async def _run(rows, persona_id="anne", threshold=None):
    from langgraph_agents.tools.pgvector_tool import recall_self

    mock_pg, mock_embed = _mocks(rows)
    with patch.object(pgvector_tool, "get_pg_client", return_value=mock_pg), \
         patch.object(pgvector_tool, "get_embedding_service", return_value=mock_embed), \
         patch.object(pgvector_tool, "_self_min_similarity", return_value=threshold):
        result = await recall_self.ainvoke(
            {"query": "ban cao bao nhieu"}, _config(persona_id))
    return result, mock_pg


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_self_binds_persona_slug_not_param():
    """Không có tham số slug trong schema — slug từ config của lượt."""
    from langgraph_agents.tools.pgvector_tool import recall_self

    assert "slug" not in recall_self.args
    assert "persona" not in recall_self.args

    rows = [{"content": "cao", "title": "Appearance",
             "kind": "sheet", "similarity": 0.9}]
    result, mock_pg = await _run(rows, persona_id="bronya")
    sql, vec, slug = mock_pg.fetch.await_args.args
    assert slug == "bronya"
    assert "character_slug" in sql
    assert result["found"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_self_empty_is_found_false():
    result, _ = await _run([])
    assert result == {"found": False}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_recall_self_own_threshold():
    rows = [{"content": "a", "title": "T", "kind": "sheet", "similarity": 0.9},
            {"content": "b", "title": "T", "kind": "sheet", "similarity": 0.4}]
    result, _ = await _run(rows, threshold=0.8)
    assert [r["content"] for r in result["results"]] == ["a"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_tools_gates_self_on_sheet(monkeypatch):
    """Không sheet.md → agent không được đưa recall_self."""
    from langgraph_agents.nodes import retriever_agent as ra

    monkeypatch.setattr(ra, "_persona_has_sheet", lambda _slug: False)
    tools = await ra._build_tools(web_search_enabled=False)
    assert "recall_self" not in [t.name for t in tools]

    monkeypatch.setattr(ra, "_persona_has_sheet", lambda _slug: True)
    tools = await ra._build_tools(web_search_enabled=False)
    assert "recall_self" in [t.name for t in tools]


@pytest.mark.unit
def test_recall_self_in_base_tools():
    from langgraph_agents.nodes.retriever_agent import RETRIEVER_BASE_TOOLS

    assert "recall_self" in [t.name for t in RETRIEVER_BASE_TOOLS]
