"""Async HTTP client for VieNeu-TTS speech synthesis service."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx

from langgraph_agents.core.circuit_breaker import CircuitBreaker
from langgraph_agents.services.exceptions import ServiceUnavailableError


def _load_vieneu_config() -> dict:
    import yaml
    config_path = Path(__file__).resolve().parents[4] / "config" / "langgraph.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f).get("langgraph", {}).get("services", {}).get("vieneu_tts", {})
    return {}


async def _client_error_reason(exc: httpx.HTTPStatusError) -> str:
    """Build a diagnostic message for a 4xx response, carrying SpeechLLm's own
    `detail` through when the body can be read.

    `httpx.HTTPStatusError.__str__` is a fixed template — status code and URL
    only (confirmed against httpx 0.28.1's `Response.raise_for_status`, which
    builds the message with no reference to the body at all) — so used alone
    it throws away exactly the message `VoiceResolutionError` goes to the
    trouble of putting an absolute path into (see
    `SpeechLLm/src/services/vieneu_client.py`). This is what actually carries
    that message through to `_stream_speech`'s `speech_failed` event and the
    log, instead of a failure that is equally silent about why.

    Guarded end to end: this runs on a request that is already failing, and
    must never itself raise a second exception on that path — an empty or
    unreadable body (a mangled proxy response, a truncated write) still has
    to produce a sensible message.
    """
    base = f"{type(exc).__name__}: {exc}"
    resp = exc.response
    try:
        # A client.stream()-mode response has not read its body yet at this
        # point; a client.post()-mode one already has, and aread() on an
        # already-read response just returns the cached content — safe
        # either way.
        await resp.aread()
    except Exception:
        return base
    detail = None
    try:
        detail = resp.json().get("detail")
    except Exception:
        pass
    if not detail:
        try:
            detail = resp.text.strip() or None
        except Exception:
            detail = None
    return f"{base} — {detail}" if detail else base


class VieNeuTTSClient:
    """Async HTTP client for VieNeu-TTS speech synthesis REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:5000",
        endpoint: str = "/synthesize",
        stream_endpoint: str = "/synthesize/stream",
        timeout: float = 60,  # CPU TTS for ~1500 chars Vietnamese takes 10-20s
        circuit_breaker_cfg: dict = None,
        stream_connect_timeout: float = 10.0,
        # Gap between two NDJSON lines, not a total-call deadline. A long
        # clinical answer legitimately streams for 60-80s (measured); what
        # actually signals "SpeechLLm died" is silence for this long between
        # chunks, not the call's total duration. See synthesize_stream().
        stream_read_timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.stream_endpoint = stream_endpoint
        self.timeout = timeout
        self._stream_connect_timeout = stream_connect_timeout
        self._stream_read_timeout = stream_read_timeout
        breaker_cfg = circuit_breaker_cfg or {}
        self._breaker = CircuitBreaker(
            name="vieneu_tts",
            failure_threshold=breaker_cfg.get("failure_threshold", 3),
            cool_down_seconds=breaker_cfg.get("cool_down_seconds", 60),
        )

    def is_healthy(self) -> bool:
        return self._breaker.state != "open"

    @property
    def circuit_state(self) -> str:
        return self._breaker.state

    async def synthesize(self, text: str, voice_path: str = None,
                         language: str = None) -> dict:
        """POST /synthesize → return audio result dict.

        `language` does not change how VieNeu synthesises — the model is
        bilingual and reads the text as it finds it. It names the output file
        (`vieneu_<lang>_<id>.wav`) and comes back in the response, and until now
        nobody sent it, so every file on disk claimed to be English.
        """
        if not self._breaker.allow():
            raise ServiceUnavailableError("vieneu_tts", "circuit breaker open")

        url = f"{self.base_url}{self.endpoint}"
        payload = self._payload(text, voice_path, language)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                self._breaker.record_success()
                return data
        except httpx.HTTPStatusError as exc:
            # A 4xx is SpeechLLm rejecting THIS request — e.g. VoiceResolutionError
            # for a character with no reference recording yet. That is a
            # service-level outcome, not a transport one: retrying cannot fix
            # it, and it says nothing about SpeechLLm's own health, so unlike a
            # 5xx or a real transport failure it must not count towards
            # tripping the breaker for every other character/request. See the
            # identical split in synthesize_stream() below.
            if exc.response.status_code >= 500:
                self._breaker.record_failure()
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            else:
                reason = await _client_error_reason(exc)
            raise ServiceUnavailableError("vieneu_tts", reason) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._breaker.record_failure()
            # Some httpx exceptions have empty str repr — include class name for diagnostics.
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise ServiceUnavailableError("vieneu_tts", reason) from exc
        except Exception as exc:
            self._breaker.record_failure()
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise ServiceUnavailableError("vieneu_tts", reason) from exc

    async def synthesize_stream(
        self, text: str, voice_path: str = None, language: str = None,
    ) -> AsyncIterator[dict]:
        """POST /synthesize/stream and yield each parsed NDJSON line as it arrives.

        Replaces the old synthesize()-then-poll-Redis path: SpeechLLm now
        streams `{"type": "start"|"chunk"|"end"|"error", ...}` lines
        (`application/x-ndjson`, one JSON object per line) as it generates
        audio instead of holding the connection open for ~38-78s and handing
        back one complete wav. The caller (api/main.py) forwards each parsed
        line as an SSE event the moment it shows up — this method does no
        buffering of its own.

        ONE deliberate choice: a per-read timeout, not a single `timeout=` for
        the whole call. `synthesize()` above can use one blanket timeout
        because it waits for exactly one response. Here a long clinical answer
        legitimately keeps this connection open for over a minute (measured:
        78s for a full reply) — a single total timeout would kill a stream that
        was working fine the whole time. What actually needs a bound is the GAP
        between two chunks: if SpeechLLm hangs or the model process dies
        mid-stream, the caller should find out in `_stream_read_timeout`
        seconds of silence, not however long the browser's own patience is.
        httpx's `read` timeout is exactly that — the max time between two
        socket reads, reset on every byte received, not a deadline on the
        whole request — paired with a short, separate `connect` timeout for
        "SpeechLLm never picked up at all".

        Circuit breaker: a connect/timeout failure, a 5xx response, or the
        stream dropping/sending unparseable NDJSON mid-flight all record a
        breaker failure, exactly like `synthesize()`. Reaching the terminal
        `end` line records success. Two outcomes are SERVICE-level rather than
        transport-level and touch neither side of the breaker: a well-formed
        `{"type": "error", ...}` line (SpeechLLm reporting it could not
        synthesize this text) and, since 12-09-2026, a 4xx response (SpeechLLm
        rejecting THIS request before the stream even opened — e.g.
        VoiceResolutionError for a character with no reference recording yet).
        A 4xx used to be lumped in with connect/timeout failures here, which
        meant three chats with a character that simply has no recording
        tripped the breaker and silenced speech for every character — a
        condition that never changes with time, so the breaker would open,
        cool down, and immediately trip again, forever. One bad request — of
        either shape — must not start counting towards tripping it for every
        other request in flight.

        Two things an earlier version of this method got wrong, both only
        visible when the caller does what api/main.py's _stream_speech
        actually does — `return` immediately after handling the "end" line,
        never resuming this generator again — rather than draining it to
        exhaustion the way a naive test does:

        1. `record_success()` must run BEFORE `yield`ing the "end" event, not
           after. Code placed after a `yield` only runs once the caller asks
           for the NEXT item (`__anext__`/`asend`) — and a caller that returns
           right after "end" never does, so this generator is torn down via
           GeneratorExit and that line never executes. Recorded first, then
           yielded, it runs unconditionally.
        2. A stream that closes WITHOUT ever sending "end" or "error" (a
           dropped connection, a crashed SpeechLLm process mid-utterance) must
           not end silently. Silence here means _stream_speech's `async for`
           loop simply finishes with no speech_end and no speech_failed — the
           browser is left assuming the turn is still in progress forever, for
           the very outage this breaker exists to catch. So: falling out of
           the line loop without having seen a terminal line is itself treated
           as a stream failure.

        Raises:
            ServiceUnavailableError: breaker open, connect/timeout failure, a
                4xx or 5xx response, the stream dropping/sending unparseable
                NDJSON mid-flight, or the stream closing without a terminal
                "end"/"error" line. Only a 4xx leaves the breaker untouched;
                everything else on this list records a failure.
        """
        if not self._breaker.allow():
            raise ServiceUnavailableError("vieneu_tts", "circuit breaker open")

        url = f"{self.base_url}{self.stream_endpoint}"
        payload = self._payload(text, voice_path, language)
        # write/pool share the connect budget — neither is the axis we care
        # about here, they just need *a* bound so httpx does not fall back to
        # its own (longer) default.
        stream_timeout = httpx.Timeout(
            connect=self._stream_connect_timeout,
            read=self._stream_read_timeout,
            write=self._stream_connect_timeout,
            pool=self._stream_connect_timeout,
        )

        try:
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        # Same split as synthesize() above, and for the same
                        # reason: a 4xx is SpeechLLm rejecting THIS request
                        # (e.g. VoiceResolutionError for a missing reference),
                        # not a sign the service itself is unhealthy. Handled
                        # here, still inside the `client.stream()` context, so
                        # `_client_error_reason` can still read the body — once
                        # this `async with` exits the connection is closed and
                        # the body is gone.
                        if resp.status_code >= 500:
                            self._breaker.record_failure()
                            reason = (f"{type(exc).__name__}: {exc}"
                                      if str(exc) else type(exc).__name__)
                        else:
                            reason = await _client_error_reason(exc)
                        raise ServiceUnavailableError("vieneu_tts", reason) from exc
                    saw_terminal_line = False  # set on "end" or "error"
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue  # NDJSON framing: blank lines carry nothing
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            # Malformed output from SpeechLLm is a stream-shaped
                            # failure, not a "skip this line and hope" one — the
                            # rest of the line-by-line framing can no longer be
                            # trusted, so this trips the breaker like a dropped
                            # connection would.
                            self._breaker.record_failure()
                            raise ServiceUnavailableError(
                                "vieneu_tts", f"malformed NDJSON line: {exc}",
                            ) from exc
                        event_type = event.get("type")
                        if event_type in ("end", "error"):
                            saw_terminal_line = True
                            if event_type == "end":
                                # BEFORE yield — see the docstring above.
                                self._breaker.record_success()
                        yield event
                    if not saw_terminal_line:
                        # The connection closed clean (no exception from
                        # aiter_lines/httpx) but SpeechLLm never said it was
                        # done. Treat that the same as any other transport
                        # failure: it is one, just a quieter one.
                        self._breaker.record_failure()
                        raise ServiceUnavailableError(
                            "vieneu_tts", "stream ended without end/error line",
                        )
        except ServiceUnavailableError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            # httpx.HTTPStatusError is deliberately not caught here any more —
            # it is fully handled above, inside the client.stream() block,
            # where the response body is still readable.
            self._breaker.record_failure()
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise ServiceUnavailableError("vieneu_tts", reason) from exc
        except Exception as exc:
            self._breaker.record_failure()
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            raise ServiceUnavailableError("vieneu_tts", reason) from exc

    @staticmethod
    def _payload(text: str, voice_path: str | None, language: str | None) -> dict:
        """One place, so the sync path cannot drift from the async one."""
        payload = {"text": text}
        if voice_path:
            payload["voice_path"] = voice_path
        if language:
            payload["language"] = language
        return payload

    def synthesize_sync(self, text: str, voice_path: str = None,
                        language: str = None) -> dict:
        """Synchronous wrapper for Celery tasks."""
        if not self._breaker.allow():
            raise ServiceUnavailableError("vieneu_tts", "circuit breaker open")

        url = f"{self.base_url}{self.endpoint}"
        payload = self._payload(text, voice_path, language)

        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            self._breaker.record_success()
            return data
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            self._breaker.record_failure()
            raise ServiceUnavailableError("vieneu_tts", str(exc)) from exc
        except Exception as exc:
            self._breaker.record_failure()
            raise ServiceUnavailableError("vieneu_tts", str(exc)) from exc


# Module-level singleton
_client: Optional[VieNeuTTSClient] = None


def get_vieneu_tts_client() -> VieNeuTTSClient:
    global _client
    if _client is None:
        cfg = _load_vieneu_config()
        # Single source of truth for "where is SpeechLLm": VIENEU_TTS_URL wins,
        # the config file's `url` is only the fallback for a machine that never
        # set the env var. Before this, three different places disagreed —
        # api/main.py::tts_enabled() gated on VIENEU_TTS_URL, THIS client read
        # config/langgraph.yaml's `services.vieneu_tts.url`, and
        # api/health.py::check_speechllm read a third variable, VIENEU_URL. A
        # developer who set only VIENEU_TTS_URL (the "is TTS on" switch) but
        # left the config file's localhost default in place got a client that
        # dialed the wrong place while tts_enabled() confidently said yes.
        base_url = (
            os.getenv("VIENEU_TTS_URL", "").strip()
            or cfg.get("url", "http://localhost:5000")
        )
        _client = VieNeuTTSClient(
            base_url=base_url,
            endpoint=cfg.get("endpoint", "/synthesize"),
            stream_endpoint=cfg.get("stream_endpoint", "/synthesize/stream"),
            timeout=cfg.get("timeout", 15),
            circuit_breaker_cfg=cfg.get("circuit_breaker", {}),
        )
    return _client
