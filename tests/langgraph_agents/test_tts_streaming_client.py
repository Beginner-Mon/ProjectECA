# -*- coding: utf-8 -*-
"""Tests for VieNeuTTSClient.synthesize_stream (feature/tts-streaming).

SpeechLLm now streams NDJSON (`application/x-ndjson`, one JSON object per
line: start/chunk/.../end, or start/.../error on a mid-stream failure) instead
of returning one complete wav after the whole answer has been synthesized.
These tests exercise the parsing + circuit-breaker wiring in
`synthesize_stream` against a fake transport (`httpx.MockTransport`) rather
than a real SpeechLLm process — same reasoning as `test_phase3_mcp_client.py`
faking its transport instead of requiring a live server.

`client.synthesize_stream()` builds its own `httpx.AsyncClient()` per call
(same as `synthesize()` above it), so there is no constructor seam to hand it
a transport. These tests monkeypatch `httpx.AsyncClient` itself, inside the
`client` module only, to inject a `MockTransport` — the client code's own
`timeout=` kwarg passes through unchanged, only the transport is swapped.
"""

from __future__ import annotations

import contextlib
import json

import httpx
import pytest

from langgraph_agents.services.exceptions import ServiceUnavailableError
from langgraph_agents.services.vieneu_tts.client import VieNeuTTSClient
from langgraph_agents.services.vieneu_tts import client as client_module


def _ndjson_body(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def _install_mock_transport(monkeypatch, handler) -> None:
    """Make every `httpx.AsyncClient()` the client module constructs use
    `handler` instead of hitting the network, without changing any of the
    kwargs (timeout, etc.) the client code already passes."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _patched)


async def _collect(client: VieNeuTTSClient, text: str = "xin chào"):
    return [event async for event in client.synthesize_stream(text)]


@pytest.mark.unit
async def test_synthesize_stream_parses_ndjson_lines_in_order(monkeypatch):
    """The happy path: start, N chunks (in the order the server sent them),
    end — each line handed back as the dict it decoded to, unmodified."""
    body = _ndjson_body(
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc123"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.32},
        {"type": "chunk", "seq": 1, "codec": "opus", "audio": "Qg==", "duration": 0.30},
        {"type": "end", "chunks": 2, "duration": 0.62},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/synthesize/stream"
        sent = json.loads(request.content)
        assert sent == {"text": "xin chào"}
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")

    events = await _collect(client)

    assert [e["type"] for e in events] == ["start", "chunk", "chunk", "end"]
    assert [e["seq"] for e in events if e["type"] == "chunk"] == [0, 1]
    assert events[-1] == {"type": "end", "chunks": 2, "duration": 0.62}


@pytest.mark.unit
async def test_synthesize_stream_sends_voice_path_and_language(monkeypatch):
    """Same payload shape as synthesize() — voice_path/language included only
    when given, same _payload() the non-streaming path already uses."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=_ndjson_body({"type": "end", "chunks": 0, "duration": 0.0}),
            headers={"content-type": "application/x-ndjson"},
        )

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")

    events = [
        e async for e in
        client.synthesize_stream("hello", voice_path="voices/anne_en.wav", language="en")
    ]

    assert captured["body"] == {
        "text": "hello", "voice_path": "voices/anne_en.wav", "language": "en",
    }
    assert events == [{"type": "end", "chunks": 0, "duration": 0.0}]


@pytest.mark.unit
async def test_synthesize_stream_records_breaker_success_on_end(monkeypatch):
    """Reaching the terminal `end` line records a breaker success — the same
    signal `synthesize()` gets from a 2xx response."""
    body = _ndjson_body(
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc"},
        {"type": "end", "chunks": 0, "duration": 0.0},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")
    # Start from a breaker that already has one recorded failure, so success
    # resetting it to 0 is actually observable.
    client._breaker.record_failure()
    assert client._breaker.snapshot()["failures"] == 1

    await _collect(client)

    assert client.circuit_state == "closed"
    assert client._breaker.snapshot()["failures"] == 0


@pytest.mark.unit
async def test_synthesize_stream_records_success_even_when_consumer_stops_right_after_end(monkeypatch):
    """Regression: the REAL consumption pattern is api/main.py::_stream_speech,
    which `return`s the instant it sees "end" — it never calls __anext__ again,
    so this generator is torn down via GeneratorExit rather than resumed past
    a `yield`. record_success() must therefore run BEFORE yielding the "end"
    event, not after. An earlier version got this backwards: it recorded
    success on the line AFTER `yield event`, which only ever ran when a
    caller drained the generator to exhaustion (as
    test_synthesize_stream_records_breaker_success_on_end above does) — a
    caller that stops at "end", which is every real caller, saw the code after
    `yield` simply never execute, so sporadic transport failures accumulated
    with no success ever resetting the breaker.
    """
    body = _ndjson_body(
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.3},
        {"type": "end", "chunks": 1, "duration": 0.3},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")
    client._breaker.record_failure()
    assert client._breaker.snapshot()["failures"] == 1

    # Mirrors _stream_speech exactly: aclosing() + stop the moment "end"
    # arrives, never asking the generator for anything more.
    async with contextlib.aclosing(client.synthesize_stream("xin chào")) as events:
        async for event in events:
            if event["type"] == "end":
                break

    assert client._breaker.snapshot()["failures"] == 0
    assert client.circuit_state == "closed"


@pytest.mark.unit
async def test_synthesize_stream_truncated_without_terminal_line_raises_and_trips_breaker(monkeypatch):
    """SpeechLLm closes the connection after "start"/"chunk" lines without ever
    sending "end" or "error" (crashed process, dropped connection mid-utterance).
    This must not end silently: a consumer that only reacts to "end"/"error"
    (i.e. _stream_speech) would otherwise see the generator simply stop, with
    no way to tell "done speaking" from "the service vanished mid-sentence" —
    and the browser would never get a terminal speech event at all.
    """
    body = _ndjson_body(
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc"},
        {"type": "chunk", "seq": 0, "codec": "opus", "audio": "QQ==", "duration": 0.3},
        # no "end", no "error" — the response body just ends here.
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")

    with pytest.raises(ServiceUnavailableError, match="end/error line"):
        await _collect(client)

    assert client._breaker.snapshot()["failures"] == 1


@pytest.mark.unit
async def test_synthesize_stream_error_line_does_not_touch_breaker(monkeypatch):
    """A well-formed {"type": "error"} line is SpeechLLm reporting it could
    not synthesize THIS text — a content-level outcome, not a transport one.
    It must not record a breaker success (there was no `end`) and must not
    record a breaker failure either (the connection was fine throughout) —
    one bad request must not start counting towards tripping the breaker for
    every other request in flight.
    """
    body = _ndjson_body(
        {"type": "start", "codec": "opus", "sample_rate": 48000, "voice_version": "abc"},
        {"type": "error", "message": "text too long"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")
    client._breaker.record_failure()
    failures_before = client._breaker.snapshot()["failures"]

    events = await _collect(client)

    assert events[-1] == {"type": "error", "message": "text too long"}
    assert client._breaker.snapshot()["failures"] == failures_before
    assert client.circuit_state == "closed"


@pytest.mark.unit
async def test_synthesize_stream_malformed_line_raises_and_trips_breaker(monkeypatch):
    """A line that is not valid JSON can no longer be trusted as NDJSON
    framing at all — treated as a stream failure, same as a dropped
    connection, and it DOES record a breaker failure."""
    body = "not json at all\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")

    with pytest.raises(ServiceUnavailableError, match="malformed NDJSON"):
        await _collect(client)

    assert client._breaker.snapshot()["failures"] == 1


@pytest.mark.unit
async def test_synthesize_stream_http_error_raises_service_unavailable(monkeypatch):
    """A non-2xx response (SpeechLLm itself down/erroring) raises
    ServiceUnavailableError and records a breaker failure — the transport
    path, same as synthesize()'s own httpx.HTTPStatusError handling."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"internal error")

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(base_url="http://speechllm.test")

    with pytest.raises(ServiceUnavailableError):
        await _collect(client)

    assert client._breaker.snapshot()["failures"] == 1


@pytest.mark.unit
async def test_synthesize_stream_breaker_open_raises_without_a_request(monkeypatch):
    """An already-open breaker must refuse before dialing out at all — no
    request should reach the transport."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=_ndjson_body({"type": "end", "chunks": 0, "duration": 0.0}))

    _install_mock_transport(monkeypatch, handler)
    client = VieNeuTTSClient(
        base_url="http://speechllm.test",
        circuit_breaker_cfg={"failure_threshold": 1, "cool_down_seconds": 999},
    )
    client._breaker.record_failure()  # threshold=1 → breaker now open
    assert client.circuit_state == "open"

    with pytest.raises(ServiceUnavailableError, match="circuit breaker open"):
        await _collect(client)

    assert called is False
