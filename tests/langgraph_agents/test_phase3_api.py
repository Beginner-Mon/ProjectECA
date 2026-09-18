"""Unit tests for FastAPI endpoints (v2.5: SSE /chat)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _set_health_redis(mock_redis):
    """Override the module-global lazily-built /health/detailed redis client.

    feature/tts-streaming: Redis no longer has anything to do with TTS (no
    task, no task_result:{id} key, no polling) — the only remaining Redis
    client in api/main.py is `_health_redis`, used solely by GET
    /health/detailed's "redis" readiness check. Kept here under the old
    fixture shape (a mock the tests can hand canned .get()/.ping() results to)
    for whichever of these tests still exercise that endpoint.
    """
    import langgraph_agents.api.main as api_module
    api_module._health_redis = mock_redis


@pytest.fixture
def api_client():
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)
    _set_health_redis(mock_redis)

    from langgraph_agents.api.main import create_app
    from fastapi.testclient import TestClient
    app = create_app()
    client = TestClient(app)

    yield client, mock_redis

    import langgraph_agents.api.main as api_module
    api_module._health_redis = None


class TestSchemas:
    @pytest.mark.unit
    def test_chat_request_defaults(self):
        from langgraph_agents.api.schemas import ChatRequest
        req = ChatRequest(query="Xin chào")
        assert req.output_mode == "text"
        assert req.persona_id == "anne"  # eca_default deleted 04-09
        assert req.locale == "en"  # declared default; drives voice + safety text
        # Identity is not part of the request. It comes from the Bearer token
        # via Depends(current_user_id); a body field would be a client-supplied
        # claim about who is calling.
        assert not hasattr(req, "user_id")

    @pytest.mark.unit
    def test_chat_response_serialization(self):
        from langgraph_agents.api.schemas import ChatResponse
        resp = ChatResponse(
            request_id="r1",
            final_answer="Xin chào!",
            required_outputs=["exercise_protocol"],
            needs_retrieval=True,
        )
        data = resp.model_dump()
        assert data["request_id"] == "r1"
        assert data["required_outputs"] == ["exercise_protocol"]


@pytest.mark.unit
def test_health_returns_ok(api_client):
    client, _ = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# GET /tts/{task_id}/result — deleted along with the Redis-backed task it
# polled for (feature/tts-streaming). POST /tts is now itself an SSE stream;
# see test_sse_chat_speech_* and test_tts_endpoint_* in test_phase5_sse.py.
