"""
Unit tests for the SpeechLLm API endpoints.

Tests cover:
- Health check, including the warm-up state machine (loading / ready / error)

POST /synthesize (TestSynthesizeEndpoint) and GET /audio/{filename}
(TestAudioEndpoint) are both gone along with their routes: streaming
(POST /synthesize/stream) replaces "write a file, hand back a URL" entirely.
Its own request-validation coverage (empty/whitespace-only/missing text,
NDJSON framing) lives in test_synthesize_stream.py, next to the rest of the
streaming behaviour rather than here.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient


# ── Health ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestHealthEndpoint:

    def test_returns_ok(self, tts_client):
        """GET /health should return 200 with status ok.

        tts_client's TestClient has `api_server._warm_up_model` monkeypatched
        (see conftest.py) to mark the model "ready" immediately without
        touching the real one — this test is about the response shape once
        ready, not about warm-up itself.
        """
        response = tts_client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_loading_then_ready(self, monkeypatch):
        """/health must not lie: 503 while the model is loading, 200 only
        once it actually is.

        This test builds its own TestClient rather than using the
        `tts_client` fixture, so it goes through the REAL `_warm_up_model`
        (background-thread warm-up) instead of the fake `tts_client`
        installs. The model load itself is still faked (gated on a
        threading.Event) so this stays a millisecond-scale unit test rather
        than one that downloads real weights. Voice enrolment is stubbed to
        a no-op here on purpose — this test is about the model-load leg of
        warm-up in isolation; enrolment's own gating gets its own test
        below.
        """
        import api_server

        release = threading.Event()

        def fake_load_model():
            release.wait(timeout=5)

        monkeypatch.setattr(api_server.vieneu_client, "_load_model", fake_load_model)
        monkeypatch.setattr(api_server.vieneu_client, "_enrol_known_voices", lambda: None)
        monkeypatch.setitem(api_server._MODEL_STATE, "status", "loading")
        monkeypatch.setitem(api_server._MODEL_STATE, "error", None)

        with TestClient(api_server.app) as client:
            loading = client.get("/health")
            assert loading.status_code == 503
            assert loading.json() == {"status": "loading"}

            release.set()
            for _ in range(100):
                if api_server._MODEL_STATE["status"] != "loading":
                    break
                time.sleep(0.02)

            ready = client.get("/health")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ok"}

    def test_stays_loading_until_voice_enrolment_finishes(self, monkeypatch):
        """/health's "ready" has to cover voice enrolment too, not just the
        model load — a request naming a voice whose reference had not been
        pre-enrolled yet would pay the ~3-4s encode_reference() cost that
        this whole warm-up step exists to move out of the request path.

        Model load is instant here (not what this test is about); voice
        enrolment is the one gated on a threading.Event. Builds its own
        TestClient (not the `tts_client` fixture) for the same reason as
        test_loading_then_ready above — this needs the REAL `_warm_up_model`.
        """
        import api_server

        release = threading.Event()

        monkeypatch.setattr(api_server.vieneu_client, "_load_model", lambda: None)

        def fake_enrol_known_voices():
            release.wait(timeout=5)

        monkeypatch.setattr(
            api_server.vieneu_client, "_enrol_known_voices", fake_enrol_known_voices
        )
        monkeypatch.setitem(api_server._MODEL_STATE, "status", "loading")
        monkeypatch.setitem(api_server._MODEL_STATE, "error", None)

        with TestClient(api_server.app) as client:
            # Model "loaded" instantly, but enrolment is still blocked on
            # `release` — /health must still read "loading".
            still_loading = client.get("/health")
            assert still_loading.status_code == 503
            assert still_loading.json() == {"status": "loading"}

            release.set()
            for _ in range(100):
                if api_server._MODEL_STATE["status"] != "loading":
                    break
                time.sleep(0.02)

            ready = client.get("/health")
            assert ready.status_code == 200
            assert ready.json() == {"status": "ok"}

    def test_error_status_when_warmup_fails(self, monkeypatch):
        """A model that fails to load must show up as 503 + the error, not
        as a silent "ok" once it (never) finishes loading. Builds its own
        TestClient for the REAL `_warm_up_model`, same as the two tests
        above."""
        import api_server

        def failing_load_model():
            raise RuntimeError("model weights corrupted")

        monkeypatch.setattr(api_server.vieneu_client, "_load_model", failing_load_model)
        monkeypatch.setitem(api_server._MODEL_STATE, "status", "loading")
        monkeypatch.setitem(api_server._MODEL_STATE, "error", None)

        with TestClient(api_server.app) as client:
            for _ in range(100):
                if api_server._MODEL_STATE["status"] != "loading":
                    break
                time.sleep(0.02)

            response = client.get("/health")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "error"
            assert "model weights corrupted" in body["message"]
