"""Tests for SSE /chat endpoint + session endpoints (Phase 5 + P0.1/P0.2)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain_core.messages import ToolMessage


def _parse_sse_stream(raw: bytes) -> list[dict]:
    """Parse SSE text into [{event, data}] list."""
    events = []
    current_event = None
    for line in raw.decode("utf-8").splitlines():
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_str = line[len("data:"):].strip()
            try:
                events.append({"event": current_event, "data": json.loads(data_str)})
            except json.JSONDecodeError:
                events.append({"event": current_event, "data": data_str})
            current_event = None
    return events


def _make_fake_astream_stage_only():
    """Minimal fake astream — updates (no custom token stream).

    Phase 6.9: conversation node deleted, synthesizer produces final_answer
    for all intents (including chat/clarify).
    """

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"planner": {}})
        yield ("updates", {"synthesizer": {
            "final_answer": "Xin chao!",
            "intent": "conversation",
            "total_tokens": 42,
        }})

    return fake_stream


def _make_fake_astream_with_tokens():
    """Astream that emits real token events via the custom stream channel."""

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"planner": {"intent": "conversation"}})
        for tok in ["Xin ", "chào ", "bạn", "!"]:
            yield ("custom", {"content": tok})
        yield ("updates", {"synthesizer": {
            "final_answer": "Xin chào bạn!",
            "intent": "conversation",
            "total_tokens": 4,
        }})

    return fake_stream


def _make_fake_astream_with_tools():
    """Astream with retriever_agent node for exercise queries."""

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"planner": {"intent": "exercise_recommendation"}})
        yield ("updates", {"retriever_agent": {}})
        yield ("updates", {"synthesizer": {
            "final_answer": "Bai tap cho lung: cat-cow, child pose...",
            "intent": "exercise_recommendation",
            "total_tokens": 120,
        }})

    return fake_stream


def _set_graph(mock_graph):
    """Set the module-global graph for a test. Returns the mock."""
    import langgraph_agents.api.main as api_module
    api_module._graph = mock_graph
    return mock_graph


def _set_health_redis(mock_redis):
    """Set the module-global /health/detailed redis client for a test.

    feature/tts-streaming: TTS no longer touches Redis at all (no task, no
    task_result:{id} key, no polling) — the only Redis client left in
    api/main.py is `_health_redis`, used solely by GET /health/detailed's
    "redis" readiness check. Named accordingly so this fixture does not read
    as though it is still wiring up the TTS path.
    """
    import langgraph_agents.api.main as api_module
    api_module._health_redis = mock_redis
    return mock_redis


@pytest.fixture
def api_client():
    """Fixture: mock graph + redis + a fixed authenticated user.

    The identity override is what lets these tests POST /chat at all. Every
    route now takes its user from a verified Bearer token — there is no
    anonymous path in any environment — so without it the request is rejected
    before the graph is ever reached and every assertion below sees an empty
    stream.

    dependency_overrides is the right seam for that: it belongs to this app
    object, so it cannot leak out of the test process the way an environment
    variable could. See api/auth.py.
    """
    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)

    mock_graph = MagicMock()
    mock_graph.astream = _make_fake_astream_stage_only()

    _set_graph(mock_graph)
    _set_health_redis(mock_redis)

    from langgraph_agents.api.main import create_app
    from langgraph_agents.api.auth import current_user_id, override_user
    from fastapi.testclient import TestClient
    app = create_app()
    # override_user rather than a bare lambda: the override replaces everything
    # current_user_id does, including binding the user for row-level security.
    app.dependency_overrides[current_user_id] = override_user(
        "00000000-0000-0000-0000-000000000001"
    )
    client = TestClient(app)

    yield client, mock_redis, mock_graph

    # Reset module globals after test
    import langgraph_agents.api.main as api_module
    api_module._graph = None
    api_module._health_redis = None


# ── Health ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_health_returns_ok(api_client):
    client, _, _ = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.unit
def test_health_detailed_returns_checks(api_client):
    client, _, _ = api_client
    resp = client.get("/health/detailed")
    assert resp.status_code in (200, 503)
    body = resp.json()
    # FastAPI can return (body, status_code) tuple directly;
    # if the JSON body is a list, unwrap it.
    if isinstance(body, list):
        body = body[0] if len(body) > 0 else {}
    assert "checks" in body
    assert "status" in body


# ── SSE stage events ───────────────────────────────────────────────


@pytest.mark.unit
def test_sse_chat_emits_stage_events(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chao"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    stage_events = [e for e in events if e["event"] == "stage"]
    nodes_seen = {e["data"]["node"] for e in stage_events}
    assert "memory" in nodes_seen
    assert "planner" in nodes_seen
    assert "synthesizer" in nodes_seen


@pytest.mark.unit
def test_sse_chat_emits_done_event_last(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["total_tokens"] == 42


# ── Token streaming ────────────────────────────────────────────────


@pytest.mark.unit
def test_sse_chat_emits_token_events(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_tokens()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 4
    contents = "".join(e["data"]["content"] for e in token_events)
    assert contents == "Xin chào bạn!"


@pytest.mark.unit
def test_sse_chat_no_tokens_when_no_custom_events(api_client):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)
    token_events = [e for e in events if e["event"] == "token"]
    assert len(token_events) == 0


@pytest.mark.unit
def test_sse_chat_stage_started_before_tokens(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_tokens()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)

    first_token_idx = next((i for i, e in enumerate(events) if e["event"] == "token"), -1)
    assert first_token_idx > 0, "Expected at least 1 token event"

    prior_stage_started = [
        e for e in events[:first_token_idx]
        if e["event"] == "stage"
        and e["data"].get("node") == "synthesizer"
        and e["data"].get("status") == "started"
    ]
    assert len(prior_stage_started) == 1


# ── Retriever stage ────────────────────────────────────────────────


@pytest.mark.unit
def test_sse_chat_emits_retriever_stage(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_tools()
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Bài tập cho đau lưng"})
    events = _parse_sse_stream(resp.content)
    stage_events = [e for e in events if e["event"] == "stage"]
    nodes_seen = {e["data"]["node"] for e in stage_events}
    assert "retriever_agent" in nodes_seen
    assert "planner" in nodes_seen


# ── Speech mode ────────────────────────────────────────────────────
#
# feature/tts-streaming: SpeechLLm now streams NDJSON lines
# ({"type": "start"|"chunk"|"end"|"error"}) instead of returning one complete
# wav after ~38s. The agent forwards each line as an SSE event the moment it
# arrives via client.synthesize_stream() (services/vieneu_tts/client.py) and
# api/main.py::_stream_speech — no background task, no task id, no Redis.
# speech_pending/speech_ready are gone; speech_start/speech_chunk/speech_end
# replace them (see the CONTRACT table these tests are checked against).
#
# _FakeTTSClient stands in for get_vieneu_tts_client()'s return value so these
# tests exercise the real translation logic in _stream_speech without an
# actual SpeechLLm process — same spirit as mock_graph standing in for the
# LangGraph graph above.


class _FakeTTSClient:
    """A fake VieNeuTTSClient whose synthesize_stream yields canned NDJSON events."""

    def __init__(self, events):
        self._events = events

    async def synthesize_stream(self, text, voice_path=None, language=None):
        for event in self._events:
            yield event


class _FakeTTSClientRaising:
    """A fake client whose synthesize_stream raises before yielding anything —
    stands in for a breaker-open / connect-failure / mid-stream-drop, all of
    which client.synthesize_stream turns into ServiceUnavailableError."""

    def __init__(self, exc):
        self._exc = exc

    async def synthesize_stream(self, text, voice_path=None, language=None):
        raise self._exc
        yield  # pragma: no cover — unreachable; makes this an async generator function


def _set_fake_tts_client(monkeypatch, fake_client):
    import langgraph_agents.api.main as api_module
    monkeypatch.setattr(api_module, "get_vieneu_tts_client", lambda: fake_client)
    monkeypatch.setattr(api_module, "get_persona", lambda pid: {})


@pytest.mark.unit
def test_sse_chat_speech_mode_emits_start_chunks_end_in_order(api_client, monkeypatch):
    """The success path: speech_start, N speech_chunk in seq order, speech_end,
    then done — and done carries no speech_task_id, because there is no task."""
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    # VIENEU_TTS_URL is what switches TTS on since 21-08 — see main.tts_enabled.
    # The deployed agent runs without it (TTS is unhosted), so this test has to
    # say explicitly that it wants the enabled path.
    monkeypatch.setenv("VIENEU_TTS_URL", "http://localhost:5000")
    fake_events = [
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc123"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.32},
        {"type": "chunk", "seq": 1, "codec": "opus", "audio": "Qg==", "duration": 0.30},
        {"type": "chunk", "seq": 2, "codec": "opus", "audio": "Qw==", "duration": 0.28},
        {"type": "end", "chunks": 3, "duration": 0.90},
    ]
    _set_fake_tts_client(monkeypatch, _FakeTTSClient(fake_events))

    resp = client.post("/chat", json={"query": "Hãy đọc", "output_mode": "speech"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert "speech_pending" not in kinds
    assert "speech_ready" not in kinds

    start_events = [e for e in events if e["event"] == "speech_start"]
    assert len(start_events) == 1
    # lang comes from resolve_voice()'s own detection over (final_answer,
    # query), not from SpeechLLm's "start" line — this turn's answer and
    # query are both Vietnamese, so "vi" (see test_lang_detect.py for the
    # detector itself; this just pins that _stream_speech forwards it).
    # truncated/spoken_chars/estimated_audio_s come from the spoken-length
    # budget (text_budget.py): this turn's 9-char answer is under budget, so
    # truncated is False and the estimate covers the whole reply.
    data = start_events[0]["data"]
    assert data["voice_version"] == "abc123"
    assert data["codec"] == "opus"
    assert data["sample_rate"] == 48000
    assert data["lang"] == "vi"
    assert data["truncated"] is False
    assert data["spoken_chars"] == 9
    assert data["estimated_audio_s"] == pytest.approx(9 / 17.9)

    chunk_events = [e for e in events if e["event"] == "speech_chunk"]
    assert [c["data"]["seq"] for c in chunk_events] == [0, 1, 2]
    assert [c["data"]["audio"] for c in chunk_events] == ["QQ==", "Qg==", "Qw=="]

    end_events = [e for e in events if e["event"] == "speech_end"]
    assert len(end_events) == 1
    assert end_events[0]["data"] == {"chunks": 3}

    # Ordering: speech_start before any chunk, all chunks before speech_end.
    idx = {e["event"]: i for i, e in enumerate(events)}
    first_chunk_idx = next(i for i, e in enumerate(events) if e["event"] == "speech_chunk")
    assert idx["speech_start"] < first_chunk_idx
    last_chunk_idx = max(i for i, e in enumerate(events) if e["event"] == "speech_chunk")
    assert last_chunk_idx < idx["speech_end"]

    assert kinds[-1] == "done"
    assert "speech_task_id" not in events[-1]["data"]


@pytest.mark.unit
def test_sse_chat_speech_error_line_emits_speech_failed(api_client, monkeypatch):
    """A mid-stream {"type": "error"} line — SpeechLLm's own report that it
    could not finish — becomes exactly one speech_failed, then the turn ends."""
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    monkeypatch.setenv("VIENEU_TTS_URL", "http://localhost:5000")
    fake_events = [
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc123"},
        {"type": "error", "message": "model crashed mid-utterance"},
    ]
    _set_fake_tts_client(monkeypatch, _FakeTTSClient(fake_events))

    resp = client.post("/chat", json={"query": "Hãy đọc", "output_mode": "speech"})
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert kinds.count("speech_failed") == 1
    failed = [e for e in events if e["event"] == "speech_failed"][0]
    assert "model crashed mid-utterance" in failed["data"]["error"]
    assert "speech_end" not in kinds
    assert kinds[-1] == "done"


@pytest.mark.unit
def test_sse_chat_speech_service_unavailable_emits_speech_failed(api_client, monkeypatch):
    """A transport failure (breaker open, connect refused, stream dropped) —
    client.synthesize_stream raises ServiceUnavailableError — also becomes
    exactly one speech_failed, and the turn still finishes with `done`."""
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    from langgraph_agents.services.exceptions import ServiceUnavailableError
    monkeypatch.setenv("VIENEU_TTS_URL", "http://localhost:5000")
    _set_fake_tts_client(
        monkeypatch,
        _FakeTTSClientRaising(ServiceUnavailableError("vieneu_tts", "circuit breaker open")),
    )

    resp = client.post("/chat", json={"query": "Hãy đọc", "output_mode": "speech"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert kinds.count("speech_failed") == 1
    failed = [e for e in events if e["event"] == "speech_failed"][0]
    assert "circuit breaker open" in failed["data"]["error"]
    assert kinds[-1] == "done"


@pytest.mark.unit
def test_sse_chat_speech_truncated_stream_emits_exactly_one_speech_failed(api_client, monkeypatch):
    """End-to-end regression, through the REAL client.synthesize_stream (an
    httpx.MockTransport, no network — not _FakeTTSClient) and the real
    _stream_speech: a stream that closes without ever sending "end" or "error"
    must still produce exactly one terminal speech_failed, not silence. Before
    client.synthesize_stream() was fixed to treat a missing terminal line as a
    failure, this case produced no speech_end AND no speech_failed — the
    browser had no way to learn the turn was over.
    """
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    import langgraph_agents.services.vieneu_tts.client as vieneu_client_module

    monkeypatch.setenv("VIENEU_TTS_URL", "http://speechllm.test")
    monkeypatch.setattr(api_module, "get_persona", lambda pid: {})
    # get_vieneu_tts_client() is a process-wide singleton (see client.py); a
    # client cached by an earlier test would ignore the transport patch below
    # (it is looked up fresh per httpx.AsyncClient() call, but starting clean
    # keeps this test's breaker state and base_url its own).
    monkeypatch.setattr(vieneu_client_module, "_client", None)

    body = "".join(json.dumps(e) + "\n" for e in [
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.3},
        # no "end", no "error" — response body just stops here.
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(vieneu_client_module.httpx, "AsyncClient", _patched)

    resp = client.post("/chat", json={"query": "Hãy đọc", "output_mode": "speech"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert kinds.count("speech_failed") == 1
    assert kinds.count("speech_end") == 0
    assert kinds.count("speech_start") == 1  # the "start" line did get through
    assert kinds[-1] == "done"


@pytest.mark.unit
def test_sse_chat_speech_mode_without_tts_configured(api_client, monkeypatch):
    """No VIENEU_TTS_URL: say so once and finish, never promise audio.

    speech_disabled is not a promise that a later event follows — unlike
    speech_start, nothing else is coming. The old speech_pending WAS such a
    promise (speech_ready or speech_failed had to follow), which is exactly
    why disabled must never emit it: with no TTS service, nothing could ever
    follow and the UI would sit on a spinner forever.

    The `done` assertion is the other half. The stream must still terminate
    normally, because a caller that asked for speech and cannot have it should
    still get its answer — and `done` no longer carries a speech_task_id key
    at all, since there is no task any more.
    """
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    monkeypatch.delenv("VIENEU_TTS_URL", raising=False)
    # If the disabled branch leaks and tries to synthesize anyway, this blows
    # up instead of silently passing.
    monkeypatch.setattr(api_module, "get_vieneu_tts_client", None)

    resp = client.post("/chat", json={"query": "Hãy đọc", "output_mode": "speech"})
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert "speech_disabled" in kinds
    assert "speech_start" not in kinds
    assert "speech_pending" not in kinds
    assert kinds[-1] == "done"
    assert "speech_task_id" not in events[-1]["data"]


@pytest.mark.unit
def test_tts_endpoint_503_when_not_configured(api_client, monkeypatch):
    """POST /tts must refuse rather than open a stream nothing will feed."""
    client, _, _ = api_client
    monkeypatch.delenv("VIENEU_TTS_URL", raising=False)

    resp = client.post("/tts", json={"text": "xin chào", "persona_id": "anne"})
    assert resp.status_code == 503


@pytest.mark.unit
def test_tts_endpoint_streams_speech_events_when_enabled(api_client, monkeypatch):
    """POST /tts (the per-message speaker button) streams the same speech_*
    shape /chat does, via the shared _stream_speech helper."""
    client, _, _ = api_client
    monkeypatch.setenv("VIENEU_TTS_URL", "http://localhost:5000")
    fake_events = [
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "def456"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.5},
        {"type": "end", "chunks": 1, "duration": 0.5},
    ]
    _set_fake_tts_client(monkeypatch, _FakeTTSClient(fake_events))

    resp = client.post("/tts", json={"text": "xin chào", "persona_id": "anne"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    kinds = [e["event"] for e in events]

    assert kinds == ["speech_start", "speech_chunk", "speech_end"]
    assert events[0]["data"]["voice_version"] == "def456"
    assert events[0]["data"]["lang"] == "vi"  # "xin chào" — resolve_voice()'s own detection
    assert events[1]["data"]["seq"] == 0
    assert events[2]["data"]["chunks"] == 1


# ── Session persisted ──────────────────────────────────────────────


@pytest.mark.unit
def test_sse_chat_session_persisted_before_done(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    async def fake_write(*args, **kwargs):
        pass

    monkeypatch.setattr(api_module, "write_session_turn", fake_write)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)
    non_done_indices = [i for i, e in enumerate(events) if e["event"] != "done"]
    done_index = next((i for i, e in enumerate(events) if e["event"] == "done"), -1)
    if non_done_indices:
        assert max(non_done_indices) < done_index


# ── Session persist failure does not break stream ──────────────────


@pytest.mark.unit
def test_sse_chat_persist_failure_does_not_block(api_client, monkeypatch):
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    async def fake_write_fail(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(api_module, "write_session_turn", fake_write_fail)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"


@pytest.mark.unit
def test_slow_summarizer_does_not_delay_session_persisted_or_done(api_client, monkeypatch):
    """Regression for the 11-09 "send button stuck in stop for 6.5-7.9s" bug
    (owner's vva.log — text-mode turns, no TTS involved). maybe_summarize is
    now `asyncio.create_task`'d right after session_persisted, not awaited —
    a slow summarizer check must not hold up session_persisted OR done.

    Before this fix, `await maybe_summarize(...)` sat on the critical path
    between session_persisted and done; a summarizer this slow would have
    made the whole response take >1s to even start returning.

    Measured as a DELTA between two runs of the same request against the
    same fixtures — one with a summarizer that returns immediately, one
    that sleeps 1.0s — rather than an absolute elapsed<0.5 threshold. Every
    /chat call in this suite pays a per-request STM warm-up read
    (`stm_warmup_failed` retries) that costs ~4s on CI; that fixed cost
    lands on both runs equally, so subtracting it out isolates the
    summarizer's own contribution to the response time, which is what
    "not on the critical path" actually means.
    """
    import asyncio
    import time

    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_stage_only()
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module

    async def fake_write(*args, **kwargs):
        pass

    monkeypatch.setattr(api_module, "write_session_turn", fake_write)

    async def fast_summarizer(session_id):
        return

    async def slow_summarizer(session_id):
        # Long enough that awaiting it inline would be trivially detectable;
        # short enough not to make the suite slow when the fix holds.
        await asyncio.sleep(1.0)

    def run_once(summarizer):
        monkeypatch.setattr(api_module, "maybe_summarize", summarizer)
        t0 = time.perf_counter()
        resp = client.post("/chat", json={"query": "Xin chào"})
        elapsed = time.perf_counter() - t0

        assert resp.status_code == 200
        events = _parse_sse_stream(resp.content)
        kinds = [e["event"] for e in events]
        assert "session_persisted" in kinds
        assert kinds[-1] == "done"
        return elapsed

    fast_elapsed = run_once(fast_summarizer)
    slow_elapsed = run_once(slow_summarizer)

    delta = slow_elapsed - fast_elapsed
    assert delta < 0.5, (
        f"slow-summarizer run took {delta:.2f}s longer than the "
        f"fast-summarizer baseline (fast={fast_elapsed:.2f}s, "
        f"slow={slow_elapsed:.2f}s) despite the fake sleeping only 1.0s — "
        "maybe_summarize appears to still be on the critical path"
    )

    # Let the background task actually finish before the fixture tears the
    # app down, rather than leaving it pending mid-sleep.
    time.sleep(1.1)


# ── Kimodo job id capture (R26) ─────────────────────────────────────
#
# kimodo runs as its own graph step before synthesizer and is deliberately
# NOT in _STAGE_NODES, so it must never produce a "stage" SSE event — but its
# job_id (for queued/cache_hit) must still reach write_session_turn. These
# tests exercise main.py's dedicated capture branch that makes that true
# without touching the wire contract.


def _kimodo_tool_message(payload: dict) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id="kimodo_motion",
        name="generate_motion",
    )


def _make_fake_astream_with_kimodo(kimodo_payload: dict):
    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"planner": {}})
        yield ("updates", {"kimodo": {"messages": [_kimodo_tool_message(kimodo_payload)]}})
        yield ("updates", {"synthesizer": {
            "final_answer": "Here's a stretch for you.",
            "intent": "exercise_recommendation",
            "total_tokens": 10,
        }})

    return fake_stream


@pytest.mark.unit
@pytest.mark.parametrize("state", ["queued", "cache_hit"])
def test_kimodo_job_id_reaches_write_session_turn(api_client, monkeypatch, state):
    """queued/cache_hit job ids must be captured and passed through to persistence."""
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_kimodo({"state": state, "job_id": "job-abc-123"})
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    captured = {}

    async def fake_write(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_module, "write_session_turn", fake_write)

    resp = client.post("/chat", json={"query": "show me a stretch"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"
    assert captured.get("motion_job_id") == "job-abc-123"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["busy", "unavailable"])
def test_kimodo_no_job_id_for_busy_or_unavailable(api_client, monkeypatch, state):
    """busy/unavailable carry no job_id — motion_job_id must stay None, not crash."""
    client, _, mock_graph = api_client
    payload = {"state": state}
    if state == "busy":
        payload["retry_after_seconds"] = 30
    mock_graph.astream = _make_fake_astream_with_kimodo(payload)
    _set_graph(mock_graph)

    import langgraph_agents.api.main as api_module
    captured = {}

    async def fake_write(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api_module, "write_session_turn", fake_write)

    resp = client.post("/chat", json={"query": "show me a stretch"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"
    assert captured.get("motion_job_id") is None


@pytest.mark.unit
def test_kimodo_never_emits_a_stage_event(api_client, monkeypatch):
    """kimodo is deliberately absent from _STAGE_NODES (R26) — persisting the
    job id must not put a new event on the wire that no client listens for."""
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_kimodo({"state": "queued", "job_id": "job-xyz"})
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "show me a stretch"})
    events = _parse_sse_stream(resp.content)
    stage_nodes = {e["data"]["node"] for e in events if e["event"] == "stage"}
    assert "kimodo" not in stage_nodes
    # sanity: the nodes that ARE stage nodes still show up as before
    assert "memory" in stage_nodes
    assert "planner" in stage_nodes
    assert "synthesizer" in stage_nodes


@pytest.mark.unit
@pytest.mark.parametrize("state", ["queued", "cache_hit"])
def test_motion_event_carries_the_job_id_to_the_browser(api_client, state):
    """The frontend cannot poll for a render it has no id for.

    Before this event existed the id was captured into final_state and written
    to Postgres, and the only way a client could learn it was to re-fetch the
    session after the turn ended — a round trip added to a render that already
    takes seconds. R26 deferred the event because nothing consumed it; the
    frontend motion handler consumes it now.
    """
    client, _, mock_graph = api_client
    mock_graph.astream = _make_fake_astream_with_kimodo({"state": state, "job_id": "job-abc-123"})
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "show me a stretch"})
    events = _parse_sse_stream(resp.content)
    motion = [e for e in events if e["event"] == "motion"]
    assert len(motion) == 1, "exactly one motion event per turn"
    assert motion[0]["data"]["state"] == state
    assert motion[0]["data"]["job_id"] == "job-abc-123"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["busy", "unavailable"])
def test_motion_event_is_emitted_for_busy_and_unavailable_too(api_client, state):
    """These two were dropped entirely: the capture branch only looked at
    queued/cache_hit, and they carry no job_id to persist, so "the GPU worker is
    off" and "this turn had no motion in it" were indistinguishable to the UI.

    They are exactly the states a user needs told — the worker is scaled to zero
    by default, so `unavailable` is the common path, not the rare one.
    """
    client, _, mock_graph = api_client
    payload = {"state": state}
    if state == "busy":
        payload["retry_after_seconds"] = 30
    mock_graph.astream = _make_fake_astream_with_kimodo(payload)
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "show me a stretch"})
    events = _parse_sse_stream(resp.content)
    motion = [e for e in events if e["event"] == "motion"]
    assert len(motion) == 1
    assert motion[0]["data"]["state"] == state
    assert "job_id" not in motion[0]["data"]
    if state == "busy":
        assert motion[0]["data"]["retry_after_seconds"] == 30


@pytest.mark.unit
def test_kimodo_malformed_content_emits_no_motion_event(api_client):
    """Malformed content is swallowed for the same reason it always was — but
    now that swallowing must also mean no half-formed motion event goes out."""
    client, _, mock_graph = api_client

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"kimodo": {"messages": [
            ToolMessage(content="not json at all", tool_call_id="x", name="generate_motion"),
        ]}})
        yield ("updates", {"synthesizer": {
            "final_answer": "Still works.", "intent": "conversation", "total_tokens": 3,
        }})

    mock_graph.astream = fake_stream
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"
    assert not [e for e in events if e["event"] == "motion"]


@pytest.mark.unit
def test_kimodo_malformed_content_does_not_break_the_stream(api_client, monkeypatch):
    """A ToolMessage with non-JSON content must be swallowed, not raised —
    a missing motion id is a far smaller failure than a broken chat turn."""
    client, _, mock_graph = api_client

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"kimodo": {"messages": [
            ToolMessage(content="not json at all", tool_call_id="x", name="generate_motion"),
        ]}})
        yield ("updates", {"synthesizer": {
            "final_answer": "Still works.",
            "intent": "conversation",
            "total_tokens": 3,
        }})

    mock_graph.astream = fake_stream
    _set_graph(mock_graph)

    resp = client.post("/chat", json={"query": "Xin chào"})
    assert resp.status_code == 200
    events = _parse_sse_stream(resp.content)
    assert events[-1]["event"] == "done"


# GET /tts/{task_id}/result — deleted along with the Redis-backed task it
# polled for (feature/tts-streaming). See test_tts_endpoint_* above.


# ── Schema tests ───────────────────────────────────────────────────


class TestSchemas:
    @pytest.mark.unit
    def test_chat_request_defaults(self):
        from langgraph_agents.api.schemas import ChatRequest
        req = ChatRequest(query="Hello")
        assert req.output_mode == "text"
        # No user_id: identity comes from the token, never the body.
        assert not hasattr(req, "user_id")

    @pytest.mark.unit
    def test_session_list_item(self):
        from langgraph_agents.api.schemas import SessionListItem
        item = SessionListItem(
            session_id="s1", created_at="2026-01-01T00:00:00",
            updated_at="2026-01-02T00:00:00",
            first_user_message_preview="Hello...", message_count=5,
        )
        assert item.session_id == "s1"
        assert item.message_count == 5

    @pytest.mark.unit
    def test_session_resume_response(self):
        from langgraph_agents.api.schemas import SessionResumeResponse
        resp = SessionResumeResponse(
            session_id="s1", messages=[],
            stm_populated=True, last_updated="2026-01-02T00:00:00",
        )
        assert resp.stm_populated is True
