"""Embedding warm-up at start-up, and stage timings in kb_search_done.

Prod logs 20/09–03/10: the first kb_search in each Lambda container took a
median 9.3 s, every later one 0.05 s — loading the ONNX model, not searching.
Warmer pings hit /health, which never touched the model, so even containers
kept alive for 95 minutes paid it on the user's first search.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import langgraph_agents.api.main as main_mod

pytestmark = pytest.mark.unit


class _FakeOnnx:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return np.zeros((len(texts), 384), dtype=np.float32)


def test_warm_up_loads_the_backend_and_encodes_once():
    from langgraph_agents.shared.embedding import E5EmbeddingService

    svc = E5EmbeddingService(backend="onnx")
    fake = _FakeOnnx()
    svc._onnx_backend = fake
    svc.warm_up()
    assert fake.calls == [["query: warm-up"]]


def _mocked_lifespan():
    # configure_root_logger is patched too: it replaces the root handlers, and
    # with them the handler caplog listens on.
    return (
        patch.object(main_mod, "configure_root_logger"),
        patch.object(main_mod, "verify_auth_config"),
        patch.object(main_mod, "run_preflight"),
        patch.object(main_mod, "build_graph_async", new=AsyncMock(return_value=MagicMock())),
        patch.object(main_mod, "preload_personas_from_db", new=AsyncMock(return_value=0)),
    )


async def _drive_lifespan():
    async with main_mod.lifespan(MagicMock()):
        await asyncio.gather(*list(main_mod._warmup_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_lifespan_warms_the_embedder_when_enabled(monkeypatch):
    monkeypatch.setenv("EMBEDDING_WARMUP", "1")
    svc = MagicMock()
    p_log, p_auth, p_preflight, p_graph, p_personas = _mocked_lifespan()
    with p_log, p_auth, p_preflight, p_graph, p_personas, \
         patch.object(main_mod, "get_embedding_service", return_value=svc):
        await _drive_lifespan()
    svc.warm_up.assert_called_once_with()


@pytest.mark.asyncio
async def test_lifespan_skips_warm_up_by_default(monkeypatch):
    monkeypatch.delenv("EMBEDDING_WARMUP", raising=False)
    svc = MagicMock()
    p_log, p_auth, p_preflight, p_graph, p_personas = _mocked_lifespan()
    with p_log, p_auth, p_preflight, p_graph, p_personas, \
         patch.object(main_mod, "get_embedding_service", return_value=svc):
        await _drive_lifespan()
    svc.warm_up.assert_not_called()


@pytest.mark.asyncio
async def test_failed_warm_up_is_logged_and_start_up_goes_on(monkeypatch, caplog):
    monkeypatch.setenv("EMBEDDING_WARMUP", "1")
    svc = MagicMock()
    svc.warm_up.side_effect = OSError("model file missing")
    p_log, p_auth, p_preflight, p_graph, p_personas = _mocked_lifespan()
    with p_log, p_auth, p_preflight, p_graph, p_personas, \
         patch.object(main_mod, "get_embedding_service", return_value=svc), \
         caplog.at_level(logging.WARNING, logger="langgraph.api"):
        await _drive_lifespan()
    assert any(r.getMessage() == "embedding_warm_failed" and r.error == "OSError"
               for r in caplog.records)


@pytest.mark.asyncio
async def test_kb_search_logs_connect_embed_and_db_times(caplog):
    from langgraph_agents.tools import pgvector_tool

    embed = MagicMock()
    embed.aembed_query = AsyncMock(return_value=[0.0] * 384)
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=[{
        "content": "Squat: 3 sets of 10.", "chunk_index": 0, "similarity": 0.9,
        "source_type": "exercise_db", "title": "Squat",
    }])
    with patch.object(pgvector_tool, "get_embedding_service", return_value=embed), \
         patch.object(pgvector_tool, "get_pg_client", return_value=pg), \
         caplog.at_level(logging.INFO, logger="langgraph.tools"):
        results = await pgvector_tool.kb_search.ainvoke({"query": "squat"})
    assert results[0]["document_title"] == "Squat"
    (done,) = [r for r in caplog.records if r.getMessage() == "kb_search_done"]
    for field in ("connect_ms", "embed_ms", "db_ms"):
        assert isinstance(getattr(done, field), int) and getattr(done, field) >= 0
