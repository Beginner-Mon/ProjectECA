---
title: "SSE contract for POST /chat"
description: "Every event /chat emits, its payload, ordering rules, and the error-code table."
tags:
  - api
  - contract
  - sse
  - errors
---

# SSE contract for `POST /chat`

Source of truth: `agenticRAG/langgraph_agents/api/main.py` (`_stream_chat`,
`_stream_speech`). Each event is `event: <name>` plus `data: <json>`.

## Ordering rules

- `done` is **always the last event** of a `/chat` response, including after an
  `error`.
- At most one `error` event per response.
- `session_persisted` comes before `done`; `speech_*` events come before `done`.
- The server never puts error text in any event. The client chooses the words.

## Events

| event | when | payload |
| --- | --- | --- |
| `stage` | a graph node starts (synthesizer only) or completes (`memory`, `vision`, `planner`, `retriever_agent`, `synthesizer`, `grader`, `error_handler`) | `{node, status: "started"\|"complete", ...extra}`. Extra on `planner`: `required_outputs`, `needs_retrieval`, `needs_clarification`; on `retriever_agent`: `sources` (list of source ids); on `grader`: `result` |
| `token` | each streamed chunk of the answer | `{content}` |
| `emotion` | before the text, when the reply carries an emotion | the emotion object from `shared/reply_emotion.py` (name + intensity) |
| `motion` | the kimodo node returned a job (any state: `queued`, `cache_hit`, `busy`, `unavailable`) | the job payload as returned by the tool: `{state, job_id?, prompt?, ...}` |
| `provider_notice` | the user's own LLM key (BYOK) failed this turn | `{kind, status, answered_by}`. `kind`: `auth\|quota\|model_not_found\|bad_request\|unreachable\|server\|other`. `answered_by`: `"eca"` when ECA's own model produced the answer, `null` when the turn ended in an error (ECA failed too) |
| `error` | the turn failed (see below) | `{code, retryable, request_id}` |
| `session_persisted` | the answer row was written | `{session_id, assistant_message_id}` |
| `speech_disabled` | speech requested but TTS is not configured | `{reason}` |
| `speech_start` | TTS stream opened | `{voice_version, codec, sample_rate, lang, truncated, spoken_chars, estimated_audio_s}` |
| `speech_chunk` | one audio chunk | `{seq, codec, audio}` (audio is base64) |
| `speech_end` | TTS stream finished | `{chunks}` |
| `speech_failed` | TTS failed | `{error}` |
| `done` | end of response | `{request_id, total_tokens, required_outputs}` |

## The `error` event

Payload: `{ "code": string, "retryable": boolean, "request_id": string }`.
`retryable` is `true` for every code the backend emits: each is a turn the user
can send again.

Backend sequences:

- **Graph ended in `error_handler`** (a CRITICAL error): `... stage(error_handler) -> error{provider_*|internal} -> [provider_notice] -> session_persisted -> [speech_*] -> done`.
  `final_answer` is still the persona's `error_system` line (history and speech
  use it); the assistant row is saved with `meta.error_code`.
- **Graph finished with no text and no error**: `... -> error{no_answer} -> done`. No assistant row.
- **Uncaught exception in the graph stream**: `... -> error{internal} -> done`
  (the stream is no longer cut). No assistant row.

Each failed turn logs one `chat_error` line with `{code, kind, status, node}` and
no text.

## Error codes

Wording lives in the client. "persona" means the character's `ui_strings` key;
"neutral" is a fixed i18n string.

| code | emitted by | meaning | client wording |
| --- | --- | --- | --- |
| `provider_billing` | backend | ECA's key: HTTP 402 / out of balance | persona `error_system` |
| `provider_auth` | backend | ECA's key: 401/403 | persona `error_system` |
| `provider_unavailable` | backend | 5xx, timeout, connection lost, circuit breaker open, model not found, provider 429 | persona `error_system` |
| `internal` | backend | uncaught exception, or a CRITICAL error with no classified kind | persona `error_system` |
| `no_answer` | backend | graph finished with no text and no error | persona `error_unavailable` |
| `interrupted` | **frontend only** | stream cut mid-way / no `done`; keep the text already shown | persona `error_stream` |
| `network` | **frontend only** | fetch failed / offline | persona `error_stream` |
| `timeout` | **frontend only** | no SSE byte for 45 s (server pings every 15 s) | persona `error_stream` |
| `session_expired` | **frontend only** | HTTP 401 before the stream | neutral: sign in again |
| `rate_limited` | **frontend only** | HTTP 429 before the stream | neutral: wait a moment |
| `bad_request` | **frontend only** | HTTP 400/422 | neutral: message could not be sent |
| `server` | **frontend only** | HTTP 5xx before the stream | persona `error_system` |

`error_handler` maps the most severe CRITICAL error in `state.errors` to a code
by its `kind`/`status` (`nodes/_errors.py:error_code_for`): `quota` with status
402 -> `provider_billing`; `auth` -> `provider_auth`; any other kind ->
`provider_unavailable`; no kind -> `internal`. Billing outranks auth outranks the
rest when several CRITICAL errors exist.

## History (`GET /sessions/{id}`)

Each message carries `error_code` and `interrupted` (both `null` when absent),
taken from the row's `meta`. The memory node and the STM cache skip assistant
rows with an `error_code`, so the next turn does not treat an apology as its own
answer.
