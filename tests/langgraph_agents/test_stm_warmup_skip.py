"""/chat skips the STM warm-up read when there is no cache (17/10, no Redis).

Production runs STM_BACKEND=none. The warm-up read (6 recent messages, then
copy them into the cache) filled nothing there and cost a query every turn.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

UID = "00000000-0000-0000-0000-000000000001"


class CachingStore:
    caches = True

    def __init__(self):
        self.get = AsyncMock(return_value=None)
        self.set = AsyncMock()
        self.delete = AsyncMock()


def _client(monkeypatch, store, loader):
    import langgraph_agents.api.main as api_module
    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.main import create_app

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    graph = type("G", (), {})()
    graph.astream = fake_stream
    api_module._graph = graph
    monkeypatch.setattr(api_module, "get_stm", lambda: store)
    monkeypatch.setattr(api_module, "load_session_messages", loader)
    monkeypatch.setattr(api_module, "populate_stm_from_messages", AsyncMock())
    monkeypatch.setattr(api_module, "write_session_turn", AsyncMock(return_value="m-1"))

    async def no_housekeeping(user_id, session_id):
        return None

    monkeypatch.setattr(api_module, "housekeep", no_housekeeping)
    app = create_app()
    app.dependency_overrides[current_user_id] = override_user(UID)
    return TestClient(app)


def test_no_cache_means_no_warm_up_query(monkeypatch):
    from langgraph_agents.shared.stm import NullStore
    loader = AsyncMock(return_value={"messages": [{"role": "user", "content": "hi"}]})
    resp = _client(monkeypatch, NullStore(), loader).post("/chat", json={"query": "hi"})
    assert resp.status_code == 200
    loader.assert_not_awaited()


def test_a_real_cache_is_still_warmed(monkeypatch):
    store = CachingStore()
    loader = AsyncMock(return_value={"messages": [{"role": "user", "content": "hi"}]})
    resp = _client(monkeypatch, store, loader).post("/chat", json={"query": "hi"})
    assert resp.status_code == 200
    loader.assert_awaited_once()
    store.get.assert_awaited()


def test_only_the_null_store_says_it_does_not_cache():
    from langgraph_agents.shared.stm import DynamoStore, NullStore, RedisStore
    assert NullStore.caches is False
    assert RedisStore.caches is True and DynamoStore.caches is True
