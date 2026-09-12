# SpeechLLm — VieNeu-TTS service

A single FastAPI service that turns text into speech in a chosen character's
voice, streamed chunk by chunk. Port 5000. CPU only.

> This README described a much larger system until 10-09-2026: Whisper STT,
> emotion detection, Phi-3 via Ollama, Coqui and ElevenLabs TTS, a streaming
> orchestrator and a motion stage. None of it was reachable from the server —
> that pipeline was retired when the project moved to LangGraph — and it has now
> been deleted. `POST /synthesize` (write a wav, hand back a filename) and
> `GET /audio/{filename}` followed it on 11-09-2026, replaced by the streaming
> route below. What follows is what actually exists.

## What it does

```
POST /synthesize/stream   {text, voice_path?, language?}  ->  NDJSON stream
GET  /health                                               ->  200 once ready, 503 until then
```

There is no `GET /audio/{filename}` any more, and no CORS middleware — see
"Who calls this" below.

### `POST /synthesize/stream`

Body: `{text, voice_path?, language?}` — this is what the LangGraph service
actually sends (`services/vieneu_tts/client.py`'s `_payload`). A legacy
`voice_prompt: {text, emotion?}` shape is also accepted as an alternative to
`text`, left over from the request schema `POST /synthesize` used; nothing
current sends it. Response is `application/x-ndjson` — one JSON object per
line:

```
{"type":"start", "codec":"flac", "sample_rate":48000, "voice_version":"<hex>"}
{"type":"chunk", "seq":0, "codec":"flac", "audio":"<base64>", "duration":0.32}
...
{"type":"end",   "chunks":24, "duration":78.1}
{"type":"error", "message":"..."}      <- instead of "end", if synthesis fails mid-stream
```

(`codec` here follows `configs/models.yaml`'s `vieneu.codec` — see "Codec" below;
`flac` is shown as the current default.)

- The first chunk (`seq: 0`) goes out the moment `infer_stream()` yields
  anything — that is what keeps first-audio latency low. Every chunk after
  that is buffered until it holds at least 2.0s of audio (or the stream
  ends). In practice this settles at close to one chunk per ~2s of audio,
  because `infer_stream()`'s own voiced yields already run up to 2.0s long —
  measured: 84.5s of audio -> 39 chunks; 29.3s -> 14 chunks.
- Each chunk is a **complete, independently-encoded** audio file (base64),
  so a client can decode any one chunk without the others.
- `voice_version` is a content hash of the reference `.wav` — `"preset"` when
  none was used. It changes only when a voice is re-recorded, not when its
  filename changes. The LangGraph service forwards it as-is; it is the
  **frontend's** cache (IndexedDB) that actually reads it, to know when a
  locally-cached clip is stale.
- If synthesis fails after `start` has gone out, the stream ends with an
  `error` line instead of `end` rather than just dying — see
  `VieNeuClient.synthesize_stream`'s docstring for why that also covers
  failures *before* `start` (headers are already committed by then).

### `GET /health`

503 `{"status":"loading"}` from process start; 503
`{"status":"error","message":...}` if warm-up failed; 200 `{"status":"ok"}`
only once warm-up has fully finished. "Finished" means both the model is
loaded **and** every reference under `voices/*.wav` has been pre-enrolled
(see "Voices" below) — on a cached model this is currently **~14s**
end-to-end (measured: ~9s model load + a few seconds total across three
voices). Before this, `/health` answered "ok" throughout that whole window,
which is a lie a caller could act on.

## Who calls this

Only the LangGraph service, server-to-server — there is no CORSMiddleware,
and none is coming back "for the frontend". Audio reaches the browser as
bytes inside the *LangGraph* service's own SSE stream; nothing running in a
browser ever talks to SpeechLLm directly.

The LangGraph service only calls this when `VIENEU_TTS_URL` is set. Unset, it
emits `speech_disabled` and the product runs text-only.

## The module

| File | Role |
|---|---|
| `api_server.py` | the two routes above; cleans formatting artefacts out of the text; owns the startup warm-up / `/health` state machine |
| `src/services/vieneu_client.py` | model loading, voice enrolment + cache, chunked/streamed synthesis |
| `configs/models.yaml` | all configuration (there are no environment variables) |
| `record_voice.py` | dev tool for recording a reference voice |
| `voices/*.wav` | reference recordings, one per character per language |

## Voices

The caller (the LangGraph service, on another process and in the deployed layout
another host) builds a name of the form `voices/<character>_<lang>.wav` and sends
it as `voice_path`. This service resolves it against **its own root**, not the
working directory, and falls back to VieNeu's preset voice — with a WARNING
naming the absolute path — when the file is absent. A character with no recording
yet still has to be able to speak.

Every file under `voices/*.wav` is **pre-enrolled at startup**, as part of
warm-up (`VieNeuClient._enrol_known_voices`, called from `/health`'s warm-up
thread before it reports ready). Enrolling a full-length reference
(`encode_reference`) costs several seconds; paying that once here — before
any request — is what keeps a voice's first real request fast. A missing or
corrupt file is logged as a WARNING and skipped rather than failing the
whole startup: characters land in the catalog before anyone records audio
for them, same as the request-time fallback above.

Give the enrolment the **highest-quality source you have**; do not pre-convert.
`encode_reference` resamples internally and accepted 44.1 kHz stereo directly. The
24 kHz shape of the older reference files is a leftover from the retired v2
backend, which output 24 kHz — v3turbo outputs 48 kHz, so downsampling a reference
to 24 kHz throws away half the band for nothing.

## Codec

Each stream chunk is encoded independently — a complete, self-standing file
per chunk (see `vieneu_client.py`'s `_encode_chunk`) — per
`configs/models.yaml`'s `vieneu.codec`:

- `flac` (**default**, since 11-09-2026) — lossless (`soundfile`, PCM_16).
  The owner's decision, by ear: real-model checks across three separate runs
  found chunk joins exceeding the ordinary-sample click threshold after Opus
  re-encodes each chunk independently — 2 of 38, 0 of 13, and 1 of 13 (one
  direct, two end-to-end through the agent), roughly 5% of joins,
  intermittent rather than every join or none — against 0 of 76 on the
  equivalent uncompressed-PCM stream, before chunking existed. Restarting a
  *lossy* encoder per chunk is what can seam audibly at the join; restarting
  a *lossless* one cannot, because it always decodes back to the exact
  samples it was given regardless of where its encoder started. That
  guarantee costs size: measured ~309 kbps, ~4.15 MB for an 84.5s answer,
  vs. ~1.79 MB for opus at `opus_compression_level` 0.5 and ~10.3 MB for wav.
- `opus` — OGG/Opus via `soundfile`, at `opus_compression_level` (default
  `0.5`, unpicked — left in place for anyone switching back). Measured on
  Anne's voice: `0.5` -> 133 kbps (5.8x smaller than the equivalent WAV);
  `0.9` -> 33 kbps (23x smaller). Smallest option; carries the click risk
  above.
- `wav` — PCM_16, uncompressed, largest; for a client that cannot decode
  Opus or FLAC.

See the worklog rather than this file for the full click-analysis numbers.

## Running it

```bash
cd SpeechLLm
conda activate tts                 # a Python 3.12 env — "tts" is just this repo's convention
pip install -r requirements.txt    # add -r requirements-dev.txt to record voices
python -m uvicorn api_server:app --port 5000
```

`configs/models.yaml` and every relative path this service resolves are
anchored to `api_server.py`'s own directory, not the process's working
directory — so this no longer strictly has to run from `SpeechLLm/`, but
that is still the convention above and in this repo's CI.

Model weights download from HuggingFace on first use (~30s extra on a cold start;
about 8s once cached). The deploy target is Lambda, not a hub-reachable
always-on host, so a deployment must bake the weights into the image rather
than download them at cold start — that also enables Lambda SnapStart.

## Verifying a voice actually cloned

Do **not** trust the `voice=cloned` line in the log on its own. Compare speaker
embeddings between the output and the reference: **≳0.5 is a real clone, ~0.2 is
the preset**. The reason is written up in `src/services/vieneu_client.py` — the
enrolment call returns a tuple that v3turbo silently ignores if it is passed
straight through, and the log line used to report "cloned" for exactly that case.

## Tests

```bash
cd ..
PYTHONPATH=SpeechLLm pytest tests/SpeechLLm -q
```

Unit tests never load the real model: the `tts_client` fixture in
`tests/SpeechLLm/conftest.py` monkeypatches `api_server._warm_up_model`
itself with a fake that marks `/health` ready immediately, rather than a
test-only environment variable — production code has no test-only branches.
Tests that need specific synthesis behaviour monkeypatch
`api_server.vieneu_client._tts` with a fake model directly.
