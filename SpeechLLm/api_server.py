import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import yaml

from src.services.vieneu_client import VieNeuClient, VoiceResolutionError

logger = logging.getLogger("speechllm.api")


# =========================
# Paths
# =========================
# Anchored to THIS FILE's own directory, not the process's working directory.
# Before this fix, load_yaml("configs/models.yaml") ran at IMPORT TIME against
# a relative path resolved against the CWD, so the service only worked
# because every run happened to `cd SpeechLLm` first — README.md, conftest.py
# and the CI import guard all did this as a workaround rather than a fix.
# vieneu_client.py already had to solve the identical problem for voice
# reference paths (see its `_ROOT`); this is the same fix for api_server.py's
# own two CWD-relative reads.
_MODULE_DIR = Path(__file__).resolve().parent


# =========================
# Model warm-up / health state
# =========================
# /health used to always answer "ok", including during the ~13s the model
# takes to load on first use — a lie: the first real request paid that cost
# silently. This makes /health tell the truth by loading the model at
# STARTUP, in a background thread (so the socket can bind and answer /health
# immediately instead of blocking on the load), and tracking the outcome here.
_MODEL_STATE = {"status": "loading", "error": None}  # "loading" | "ready" | "error"


def _warm_up_model():
    """Load the model AND pre-enrol every known voice, in the background.

    Runs off the event loop entirely (a plain OS thread, not an asyncio
    task) because both steps are synchronous, CPU/IO-bound work with no
    `await` points of their own.

    Enrolling voices/*.wav here — not just loading the model — is what
    /health's "ready" actually has to mean: a real-model check found
    time-to-first-chunk at 4.36s on a voice's first-ever request against
    0.25s once its reference was cached, because `encode_reference` (~3-4s)
    was being paid inside that first request. `_enrol_known_voices` pays it
    here instead, through the same `_resolve_voice`/`_voice_version` cache a
    request uses, so /health only reports "ok" once a request naming any
    cataloged voice would actually be fast.
    """
    try:
        vieneu_client._load_model()
        vieneu_client._enrol_known_voices()
        _MODEL_STATE["status"] = "ready"
        _MODEL_STATE["error"] = None
    except Exception as e:
        logger.exception("Model warm-up failed")
        _MODEL_STATE["status"] = "error"
        _MODEL_STATE["error"] = str(e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Always starts the real warm-up thread — no env-var bypass. One used to
    # live here, so unit tests never loaded the real model; it was removed
    # because it was a test-only switch sitting in the production path,
    # reachable through one stray environment variable (e.g. copied into a
    # deployed config) — exactly the "/health lies while nothing is loaded"
    # bug this whole warm-up mechanism exists to prevent. Tests that must
    # not touch the real model now monkeypatch `_warm_up_model` itself (see
    # tests/SpeechLLm/conftest.py) rather than short-circuiting this
    # function.
    threading.Thread(target=_warm_up_model, daemon=True, name="vieneu-warmup").start()
    yield


# =========================
# App Init
# =========================
app = FastAPI(title="SpeechLLM VieNeu TTS API", lifespan=lifespan)

# No CORSMiddleware. It existed for a browser <audio> tag that called this
# service's (long-gone) GET /audio/{filename} directly, cross-origin. Now
# the only caller is the LangGraph service, server-to-server, and audio
# reaches the browser as bytes inside ITS SSE stream — nothing running in a
# browser ever originates a request to this API. Do not re-add this "for the
# frontend"; the frontend does not talk to SpeechLLm.


# =========================
# Request Schemas
# =========================
class VoicePrompt(BaseModel):
    text: str
    emotion: Optional[str] = None


class TTSRequest(BaseModel):
    # Simple mode (dashboard)
    text: Optional[str] = None
    emotion: Optional[str] = None

    # LLM mode (pipeline)
    voice_prompt: Optional[VoicePrompt] = None

    language: Optional[str] = "en"
    user_id: Optional[str] = None
    voice_path: Optional[str] = None  # path to reference .wav for voice cloning


# =========================
# Helpers
# =========================
def load_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)


def clean_text_for_tts(text: str) -> str:
    """
    Clean formatting artifacts while preserving meaning.
    Safe for multilingual text.
    """
    text = text.replace("\\n", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"[*•]+", "", text)
    text = re.sub(r"\s-\s", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =========================
# Services Init
# =========================
model_config = load_yaml(_MODULE_DIR / "configs" / "models.yaml")
vieneu_client = VieNeuClient(model_config["vieneu"])


# =========================
# Routes
# =========================
@app.post("/synthesize/stream")
async def synthesize_stream(req: TTSRequest):
    """
    Stream synthesized audio chunk by chunk instead of writing a file.

    Response body is `application/x-ndjson` — one JSON object per line, the
    shapes documented on vieneu_client.VieNeuClient.synthesize_stream: `start`,
    then one or more `chunk`, then `end` (or `error` instead of `end` if
    synthesis fails partway through).

    Validation that can still produce a clean HTTP status happens HERE,
    before StreamingResponse is constructed — both the text-emptiness check
    below and, since voice fallback was removed, resolving/encoding the
    reference voice. Once that response exists, status 200 is already
    effectively committed — Starlette's StreamingResponse sends the ASGI
    `http.response.start` message before it pulls the first item out of the
    body iterator, not after — so anything that goes wrong past this point
    can only be reported as an `error` line inside the stream, which is
    exactly what synthesize_stream does.
    """
    if req.voice_prompt:
        text = req.voice_prompt.text.strip()
    else:
        text = (req.text or "").strip()

    language = req.language or "en"

    if not text:
        raise HTTPException(status_code=400, detail="Voice text cannot be empty")

    clean_text = clean_text_for_tts(text)

    # Resolve (and encode) the reference voice HERE, before StreamingResponse
    # exists — this is the last point where a failure can still become a
    # normal HTTP error. A missing/unreadable reference, or no voice_path at
    # all, now raises instead of silently substituting VieNeu's preset voice
    # (see VoiceResolutionError) — that silence is what once let a character
    # answer in the wrong voice with nothing in the product saying so. The
    # result is cached, so synthesize_stream()'s own resolution below is a
    # cache hit, not duplicated work.
    try:
        vieneu_client._resolve_voice(req.voice_path)
    except VoiceResolutionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    def ndjson_lines():
        # A plain (sync) generator, deliberately: infer_stream() underneath
        # is CPU-bound ONNX/Python work with no `await` points, so there is
        # nothing here for `async def` to gain. Starlette runs a sync
        # generator's body in a threadpool, which is what keeps this from
        # blocking the event loop while it works — no run_in_executor needed.
        for event in vieneu_client.synthesize_stream(clean_text, language, req.voice_path):
            yield json.dumps(event) + "\n"

    return StreamingResponse(ndjson_lines(), media_type="application/x-ndjson")


@app.get("/health")
async def health():
    """Tell the truth about whether the model is actually usable yet.

    Before this, /health always answered "ok" — including during the ~13s
    the model takes to load on first use, so it lied for the entire startup
    window. 503 while `_MODEL_STATE` says "loading" or "error"; 200 only once
    warm-up (see the lifespan handler above) has actually finished.
    """
    status = _MODEL_STATE["status"]
    if status == "ready":
        return {"status": "ok"}
    if status == "error":
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": _MODEL_STATE["error"]},
        )
    return JSONResponse(status_code=503, content={"status": "loading"})


# =========================
# Run
# =========================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
