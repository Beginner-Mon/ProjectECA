# -*- coding: utf-8 -*-
"""4xx vs 5xx classification for VieNeuTTSClient (synthesize + synthesize_stream).

Regression coverage for the bug in the first cut of feature/tts-streaming's
fail-loudly change (commit 3fff88e7): SpeechLLm's new VoiceResolutionError
surfaces as an HTTP 422, and `resp.raise_for_status()` turned every non-2xx
response — 422 included — into the same bucket as a real transport failure
(connect refused, timeout, 5xx). With `failure_threshold=3`, three chats with
a character that simply has no reference recording yet tripped the breaker
and silenced speech for EVERY character, including ones with a perfectly good
recording — a condition that never changes with time, so the breaker would
open, cool down, and immediately trip again, forever. That contradicts this
client's own documented rule (see synthesize_stream's docstring): transport
failures trip the breaker, service-level outcomes do not — and "this
character has no reference recording" is a service-level outcome about the
persona catalogue, not about SpeechLLm's health.

Separately: `httpx.HTTPStatusError.__str__()` never includes the response
body, so the absolute path VoiceResolutionError deliberately puts in its
message reached neither the log nor the browser — replacing one silent
failure with another. Both are fixed together here since they were introduced
together: the breaker split needed the body read anyway to build the 4xx's
message, and the same read is what carries `detail` through.

Uses `httpx.MockTransport`, same technique as test_tts_streaming_client.py —
these tests assert breaker state directly (`client.circuit_state` /
`client._breaker.snapshot()`), not indirectly through side effects.
"""

from __future__ import annotations

import json

import httpx
import pytest

from langgraph_agents.services.exceptions import ServiceUnavailableError
from langgraph_agents.services.vieneu_tts.client import VieNeuTTSClient
from langgraph_agents.services.vieneu_tts import client as client_module


def _install_mock_transport(monkeypatch, handler) -> None:
    """Make every `httpx.AsyncClient()` the client module constructs use
    `handler` instead of hitting the network — same helper as
    test_tts_streaming_client.py, duplicated locally so this file has no
    import-order dependency on that one."""
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _patched)


def _ndjson_body(*events: dict) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


async def _drain_stream(client: VieNeuTTSClient, text: str = "xin chào"):
    return [event async for event in client.synthesize_stream(text)]


# ── synthesize_stream(): the production path (_stream_speech uses only this) ─

@pytest.mark.unit
class TestSynthesizeStream4xxVs5xx:

    async def test_422_does_not_record_a_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422,
                json={"detail": "Reference voice not found: requested=voices/miki_en.wav "
                                 "resolved=D:\\SpeechLLm\\voices\\miki_en.wav"},
            )

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await _drain_stream(client)

        assert client._breaker.snapshot()["failures"] == 0
        assert client.circuit_state == "closed"
        # The absolute path survives into the raised error — this is the
        # entire point of reading the body: without it only "422" reaches
        # anyone, and that is indistinguishable from any other rejection.
        assert "miki_en.wav" in str(exc_info.value)

    async def test_500_records_a_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"internal error")

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError):
            await _drain_stream(client)

        assert client._breaker.snapshot()["failures"] == 1

    async def test_three_consecutive_422s_leave_the_breaker_closed(self, monkeypatch):
        """The regression exactly as it happened: repeated requests for a
        character with no reference recording must not degrade TTS for every
        other character. failure_threshold defaults to 3, so if this were
        still miscounted the breaker would be OPEN by the fourth call."""
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(422, json={"detail": "Reference voice not found"})

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        for _ in range(3):
            with pytest.raises(ServiceUnavailableError):
                await _drain_stream(client)

        assert client._breaker.snapshot()["failures"] == 0
        assert client.circuit_state == "closed"

        # A fourth request must still reach the transport — a genuinely open
        # breaker would refuse it in synthesize_stream()'s own `allow()`
        # check before any request went out at all.
        with pytest.raises(ServiceUnavailableError):
            await _drain_stream(client)
        assert len(calls) == 4

    async def test_4xx_with_unreadable_body_still_produces_a_message(self, monkeypatch):
        """Guard case: an empty/non-JSON body on the error path must never
        itself raise a second exception — it must still produce a usable
        ServiceUnavailableError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, content=b"")

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await _drain_stream(client)

        assert client._breaker.snapshot()["failures"] == 0
        assert "400" in str(exc_info.value)


# ── synthesize(): the non-streaming twin — must not drift from the above ────

@pytest.mark.unit
class TestSynthesize4xxVs5xx:

    async def test_422_does_not_record_a_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                422, json={"detail": "Reference voice not found: requested=voices/miki_en.wav"}
            )

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await client.synthesize("hello")

        assert client._breaker.snapshot()["failures"] == 0
        assert client.circuit_state == "closed"
        assert "miki_en.wav" in str(exc_info.value)

    async def test_500_records_a_failure(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"internal error")

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError):
            await client.synthesize("hello")

        assert client._breaker.snapshot()["failures"] == 1

    async def test_three_consecutive_422s_leave_the_breaker_closed(self, monkeypatch):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(422, json={"detail": "Reference voice not found"})

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        for _ in range(3):
            with pytest.raises(ServiceUnavailableError):
                await client.synthesize("hello")

        assert client._breaker.snapshot()["failures"] == 0
        assert client.circuit_state == "closed"

        with pytest.raises(ServiceUnavailableError):
            await client.synthesize("hello")
        assert len(calls) == 4

    async def test_4xx_with_unreadable_body_still_produces_a_message(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, content=b"")

        _install_mock_transport(monkeypatch, handler)
        client = VieNeuTTSClient(base_url="http://speechllm.test")

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await client.synthesize("hello")

        assert client._breaker.snapshot()["failures"] == 0
        assert "400" in str(exc_info.value)
