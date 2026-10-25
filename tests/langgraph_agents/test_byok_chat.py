"""T3 — POST /chat accepts llm, emits provider_notice, redacts logs (unit)."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

pytestmark = pytest.mark.unit

USER_BASE = "https://user.example/v1"
USER_MODEL = "user-model-x"
USER_KEY = "user-key-xyz-12345678"


def _public_dns(monkeypatch, ip="8.8.8.8"):
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (ip, 0))],
    )


def _parse_sse(raw: bytes) -> list[dict]:
    events = []
    current = None
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event:"):
            current = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_str = line[len("data:"):].strip()
            try:
                events.append({"event": current, "data": json.loads(data_str)})
            except json.JSONDecodeError:
                events.append({"event": current, "data": data_str})
            current = None
    return events


def _make_client(monkeypatch, fake_astream):
    import langgraph_agents.api.main as api_module
    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.main import create_app

    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    import langgraph_agents.llm as llm

    llm.get_chat_model.cache_clear()
    _public_dns(monkeypatch)

    mock_graph = type("G", (), {})()
    mock_graph.astream = fake_astream
    api_module._graph = mock_graph

    async def fake_write(*args, **kwargs):
        return "11111111-2222-3333-4444-555555555555"

    monkeypatch.setattr(api_module, "write_turn_question", fake_write)
    monkeypatch.setattr(api_module, "write_turn_answer", fake_write)

    async def no_housekeeping(user_id, session_id, **_budgets):
        return None

    monkeypatch.setattr(api_module, "housekeep", no_housekeeping)

    app = create_app()
    app.dependency_overrides[current_user_id] = override_user(
        "00000000-0000-0000-0000-000000000001"
    )
    client = TestClient(app)
    return client, api_module, llm


def _llm_body(**over):
    body = {
        "base_url": USER_BASE,
        "model": USER_MODEL,
        "api_key": USER_KEY,
        "capabilities": ["text", "answer", "tools"],
        "omit": [],
    }
    body.update(over)
    return body


def test_chat_uses_user_key_inside_graph(monkeypatch):
    captured: dict = {}

    async def fake_stream(state, config, stream_mode=None):
        import langgraph_agents.llm as llm

        captured["synth"] = llm.get_chat_model("synthesizer")
        captured["planner"] = llm.get_chat_model("planner")
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        resp = client.post("/chat", json={"query": "hi", "llm": _llm_body()})
        assert resp.status_code == 200
        assert captured["synth"].openai_api_base == USER_BASE
        assert captured["synth"].model_name == USER_MODEL
        assert captured["planner"].openai_api_base == USER_BASE
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()


def test_chat_without_llm_uses_eca(monkeypatch):
    captured: dict = {}

    async def fake_stream(state, config, stream_mode=None):
        import langgraph_agents.llm as llm

        captured["synth"] = llm.get_chat_model("synthesizer")
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        resp = client.post("/chat", json={"query": "hi"})
        assert resp.status_code == 200
        assert captured["synth"].openai_api_base != USER_BASE
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()


def test_chat_rejects_internal_base_url(monkeypatch):
    called = {"n": 0}

    async def fake_stream(state, config, stream_mode=None):
        called["n"] += 1
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        resp = client.post(
            "/chat", json={"query": "hi", "llm": _llm_body(base_url="https://127.0.0.1/v1")}
        )
        assert resp.status_code == 422
        assert called["n"] == 0
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()


def test_chat_rejects_extra_field_in_llm(monkeypatch):
    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        body = _llm_body()
        body["evil"] = "x"
        resp = client.post("/chat", json={"query": "hi", "llm": body})
        assert resp.status_code == 422
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()


def test_provider_notice_emitted_once_before_done(monkeypatch):
    async def fake_stream(state, config, stream_mode=None):
        from langgraph_agents.llm_byok import current_request_llm

        current_request_llm().failure = {"kind": "auth", "status": 401}
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        resp = client.post("/chat", json={"query": "hi", "llm": _llm_body()})
        assert resp.status_code == 200
        events = _parse_sse(resp.content)
        notices = [e for e in events if e["event"] == "provider_notice"]
        assert len(notices) == 1
        assert notices[0]["data"] == {
            "kind": "auth", "status": 401, "answered_by": "eca",
        }
        assert events[-1]["event"] == "done"
        assert [e for e in events].index(notices[0]) < len(events) - 1
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()


def test_log_formatter_redacts_bound_key(monkeypatch):
    import langgraph_agents.llm_byok as byok
    from langgraph_agents.shared.logging import JsonFormatter

    req = byok.RequestLLM(
        base_url=USER_BASE,
        model=USER_MODEL,
        api_key=SecretStr(USER_KEY),
        capabilities=frozenset({"text"}),
    )
    byok.bind_request_llm(req)
    try:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=f"call failed {USER_KEY}", args=(), exc_info=None,
        )
        record.extra_key = f"detail {USER_KEY}"
        out = JsonFormatter().format(record)
        assert USER_KEY not in out
        assert "[redacted]" in out
    finally:
        byok.bind_request_llm(None)


def test_http_client_closed_and_key_not_persisted(monkeypatch):
    captured: dict = {}
    written: dict = {}

    async def fake_stream(state, config, stream_mode=None):
        import langgraph_agents.llm as llm
        from langgraph_agents.llm_byok import current_request_llm

        # Build a user model so the per-request HTTP client exists.
        llm.get_chat_model("synthesizer")
        captured["req"] = current_request_llm()
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    import langgraph_agents.api.main as api_module

    client, mod, llm = _make_client(monkeypatch, fake_stream)
    try:
        async def capture_write(*args, **kwargs):
            written.update(kwargs)
            written["args"] = args
            return "id-1"

        # Both halves of the turn: neither may carry the key.
        monkeypatch.setattr(mod, "write_turn_question", capture_write)
        monkeypatch.setattr(mod, "write_turn_answer", capture_write)
        resp = client.post("/chat", json={"query": "hi", "llm": _llm_body()})
        assert resp.status_code == 200
        assert captured["req"].http_client is None

        blob = json.dumps(written, default=str)
        assert USER_KEY not in blob
    finally:
        mod._graph = None
        llm.get_chat_model.cache_clear()


def test_provider_notice_answered_by_null_when_eca_failed_too(monkeypatch):
    async def fake_stream(state, config, stream_mode=None):
        from langgraph_agents.llm_byok import current_request_llm

        current_request_llm().failure = {"kind": "auth", "status": 401}
        yield ("updates", {"error_handler": {
            "final_answer": "Sorry.", "error_code": "provider_unavailable",
        }})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    try:
        resp = client.post("/chat", json={"query": "hi", "llm": _llm_body()})
        events = _parse_sse(resp.content)
        notices = [e for e in events if e["event"] == "provider_notice"]
        assert len(notices) == 1
        assert notices[0]["data"]["answered_by"] is None
        names = [e["event"] for e in events]
        assert "error" in names and names[-1] == "done"
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()
