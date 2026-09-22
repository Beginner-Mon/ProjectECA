"""
Shared fixtures for SpeechLLm unit tests.

Handles:
- Setting CWD to SpeechLLm root (required by fixtures and voice paths)
- Replacing the real model warm-up with a fake that marks it ready
  immediately, so no test blocks on or triggers a real VieNeu load
- Providing a plain TestClient — there is no router layer left to mock

This file used to do considerably more. It stubbed `elevenlabs`, `TTS`, `torch`,
`sounddevice` and `soundfile` with MagicMocks whenever they were not installed,
and it injected a fake ELEVENLABS_API_KEY for the session. Both existed to serve
the retired Coqui/ElevenLabs pipeline, whose modules were deleted on 10-09-2026.

It later carried `mock_tts_router` / `synthesis_result` / `make_synthesis_result`,
for the POST /synthesize route and src/services/tts_router.py. Both are gone —
streaming (POST /synthesize/stream) calls VieNeuClient directly, so there is no
router to mock and no "/synthesize response shape" left to build fixtures for.
Tests that need synthesis behaviour now monkeypatch
`api_server.vieneu_client._tts` with a fake model directly (see
test_synthesize_stream.py).

It also used to set a module-level `SPEECHLLM_SKIP_WARMUP=1` environment
variable, read by api_server.py's lifespan handler to skip warm-up entirely.
Removed on 11-09-2026: it was a test-only switch reachable from production
through one stray environment variable, which is the exact "/health answers
ok while nothing is loaded" bug the warm-up mechanism exists to prevent.
Tests now patch `api_server._warm_up_model` directly instead (see
`tts_client` below) — production code no longer branches on anything test
suites control.

The live import chain is now exactly two modules —

    api_server.py -> src/services/vieneu_client.py

— neither of which imports elevenlabs/TTS/torch/sounddevice, so stubbing them
here would stub nothing. A `sys.modules` entry pointing at a MagicMock is a
booby trap for the next person who adds a real dependency with one of those
names, because the import silently succeeds and every attribute access returns
a Mock instead of failing. Deleted rather than left "just in case".
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Session-scoped CWD fixture
#
# KEEP THIS. Even though api_server.py's own config/output-dir reads are now
# anchored to Path(__file__) rather than the CWD, `voices/*.wav` fixtures and
# ad-hoc relative paths used across this suite still assume SpeechLLm/ is the
# working directory, and the runtime-import guard in
# .github/workflows/release-tests.yml likewise `cd SpeechLLm` first.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _speechllm_cwd(request):
    """Run the whole session from the SpeechLLm root."""
    speechllm_root = Path(request.config.rootdir) / "SpeechLLm"
    original_cwd = os.getcwd()
    os.chdir(speechllm_root)

    yield

    os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tts_client(monkeypatch):
    """
    Provide a FastAPI TestClient with the real model warm-up replaced by a
    fake that marks it ready immediately — no test in this suite should
    load the real ~30s-cold/~8s-cached VieNeu model.

    api_server.py's `lifespan` starts warm-up as
    `threading.Thread(target=_warm_up_model, ...)`, which resolves
    `_warm_up_model` as a module global at call time (i.e. when
    `TestClient(app)` triggers startup below) — so patching the module
    attribute here, before that happens, is enough. Tests that need to
    exercise the REAL warm-up state machine (loading -> ready / error) build
    their own TestClient instead and patch `vieneu_client._load_model` /
    `_enrol_known_voices` — see test_api_endpoints.py's TestHealthEndpoint.

    No router or model to mock beyond that — POST /synthesize/stream calls
    `vieneu_client.synthesize_stream()` directly. A test that needs specific
    synthesis behaviour monkeypatches `api_server.vieneu_client._tts` with a
    fake (see test_synthesize_stream.py).
    """
    import api_server

    def fake_warm_up_model():
        api_server._MODEL_STATE["status"] = "ready"
        api_server._MODEL_STATE["error"] = None

    monkeypatch.setattr(api_server, "_warm_up_model", fake_warm_up_model)

    with TestClient(api_server.app) as client:
        yield client
