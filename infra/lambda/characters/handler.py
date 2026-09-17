"""Lambda handler: the character catalog, served behind CloudFront.

Routes (Lambda Function URL, so the path arrives in event["rawPath"]):

    GET /characters                          list active characters (public)
    GET /characters/{slug}                   one character (Cognito)
    GET /characters/{slug}/avatar-profile    just the avatar_profile JSONB (Cognito)
    GET /characters/{slug}/audio             signed clip URLs (Cognito, contract B)

One function rather than three: these are three reads of the same resource,
and CloudFront caches the responses at the edge, so the origin is hit about
once per TTL. Splitting them would triple the CDK surface to serve a catalog
of four rows that changes approximately never.

`persona` is never returned. It is the LLM system prompt — the backend reads
it straight from the DB at startup (nodes/_persona_loader.preload_personas_from_db)
and no client has any use for it.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qsl

from shared import signing
from shared.db import get_connection, fetch_all, fetch_one
from shared.response import success as _success, error, begin_request

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Browser cache lifetime for catalog responses. Deliberately set here and not in
# shared/response.py: that helper is shared with the session CRUD handlers, whose
# responses are per-user and must never be stored anywhere. Only this catalog is
# public and identical for every viewer.
#
# CloudFront caches at the edge regardless (its cache policy decides that). What
# this adds is the browser's own cache, which turns a page reload from five
# requests into zero. Matched to the distribution's 300s TTL so the two layers
# cannot disagree about how stale a response may be.
_CACHE_SECONDS = 300


def success(body: dict, status_code: int = 200, cache_control: str | None = None) -> dict:
    """Wrap a body with per-route Cache-Control (contract A).

    The caller names the policy — list responses are identical for every
    viewer (`public`), detail/profile responses travel with a token
    (`private`), and /audio must never be stored (`no-store`, T4). There is
    deliberately no single default that fits all three: handler.py:44 used
    to stamp `public, max-age=300` on EVERY response, which would let a
    shared cache serve one user's signed clip URLs to another.
    """
    resp = _success(body, status_code)
    resp["headers"] = {
        **resp["headers"],
        "Cache-Control": cache_control or f"public, max-age={_CACHE_SECONDS}",
    }
    return resp

# Same shape the backend enforces on ChatRequest.persona_id, because a slug is
# exactly what the frontend sends back as persona_id once a character is picked.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# T4 — the only clip names GET /characters/{slug}/audio will serve (contract B).
# A module-level constant, mirrored verbatim in
# agenticRAG/langgraph_agents/api/routes_characters.py (the local dev twin):
# the two cannot import from each other, so tests/infra/test_characters_contract.py
# compares them instead — same mechanism that already guards _PUBLIC_COLUMNS.
_ALLOWED_CLIPS = (
    "greeting.morning",
    "greeting.afternoon",
    "greeting.evening",
    "greeting.night",
)

_ALLOWED_LANGS = ("vi", "en")

# At most 10 `clip` values per request (contract B). More is a client bug, not
# ten signed URLs.
_MAX_CLIPS_PER_REQUEST = 10

# Signed-URL lifetime for /audio clips, in seconds. 5 minutes — same as the
# agent's SIGNED_URL_TTL. Returned per clip as `expires_at` so callers know
# when to refetch instead of playing into a 403.
_AUDIO_URL_TTL_SECONDS = 300

# Every column the client is allowed to see. Listed explicitly rather than
# SELECT * so that adding a column to `characters` can never leak it by default.
#
# `ui_strings` is the character's chat-surface copy — greeting, stage labels,
# error lines. It is deliberately a separate column from `persona`: persona is
# the system prompt and must never reach a browser, while this exists only to be
# rendered in one. Keep prompt text out of it.
_PUBLIC_COLUMNS = """
    slug, display_name, description,
    vrm_url, thumbnail_url, vrm_metadata,
    voice_language, sort_order, ui_strings
"""

# What the picker grid needs to draw a card, and no more. The list is read on
# every visit while the fields it drops are wanted only once a character has been
# chosen, so they are served by /characters/{slug} instead. `vrm_metadata` stays
# because the card greys itself out for models the device cannot run.
#
# This must match _PUBLIC_COLUMNS_LITE in
# agenticRAG/langgraph_agents/api/routes_characters.py. The two cannot import
# from each other — this file is deployed alone, as a CDK asset directory
# (character_stack.py), and that one runs inside the FastAPI app — so
# tests/infra/test_characters_contract.py compares them instead. They drifted
# once already: 863458d trimmed the FastAPI copy, which is the local development
# shim, and left production returning every column.
_PUBLIC_COLUMNS_LITE = "slug, display_name, thumbnail_url, description, vrm_metadata"


def _list_characters(cur) -> dict:
    cur.execute(
        f"SELECT {_PUBLIC_COLUMNS_LITE} FROM characters "
        "WHERE is_active ORDER BY sort_order, slug"
    )
    rows = fetch_all(cur)
    # Public catalog, identical for every viewer — the one route where a
    # shared cache is safe.
    return success(
        {"characters": rows, "total": len(rows)},
        cache_control=f"public, max-age={_CACHE_SECONDS}",
    )


def _get_character(cur, slug: str) -> dict:
    cur.execute(
        f"SELECT {_PUBLIC_COLUMNS}, avatar_profile FROM characters "
        "WHERE slug = %s AND is_active",
        (slug,),
    )
    row = fetch_one(cur)
    if row is None:
        return error("Character not found", 404)
    # Behind Cognito (contract A): private so a shared cache can never serve
    # one viewer's detail (and T5's audio_version) to another.
    return success(row, cache_control=f"private, max-age={_CACHE_SECONDS}")


def _get_avatar_profile(cur, slug: str) -> dict:
    cur.execute(
        "SELECT avatar_profile FROM characters WHERE slug = %s AND is_active",
        (slug,),
    )
    row = fetch_one(cur)
    if row is None:
        return error("Character not found", 404)
    return success(
        row["avatar_profile"] or {},
        cache_control=f"private, max-age={_CACHE_SECONDS}",
    )


def _query_all(event, name: str) -> list[str]:
    """Every `?name=` value, under EITHER gateway payload format.

    Format 1.0 (REST API) splits repeats into multiValueQueryStringParameters
    (queryStringParameters keeps only the last); format 2.0 (Function URL)
    packs them into rawQueryString. multiValue wins when present so one value
    is never counted twice.
    """
    mv = event.get("multiValueQueryStringParameters") or {}
    if name in mv and mv[name] is not None:
        v = mv[name]
        return list(v) if isinstance(v, list) else [v]
    qs = event.get("queryStringParameters") or {}
    if qs.get(name) is not None:
        return [qs[name]]
    raw = event.get("rawQueryString") or ""
    if raw:
        return [v for k, v in parse_qsl(raw, keep_blank_values=True) if k == name]
    return []


def _get_audio(cur, slug: str, event) -> dict:
    """Signed clip URLs for one character (contract B, Cognito, no-store).

    `static_audio` is READ here and never returned: the response carries
    signed URLs plus content hashes, not the raw S3 keys. A clip the
    character does not have is `null`, with the whole request still 200.
    A signing failure is 500 with a logged reason — never an unsigned URL,
    never a raw key.
    """
    lang = (_query_all(event, "lang") or ["vi"])[0]
    if lang not in _ALLOWED_LANGS:
        return error(f"Invalid lang: {lang!r} (expected one of {', '.join(_ALLOWED_LANGS)})", 400)

    clips = _query_all(event, "clip")
    if not clips:
        return error("At least one ?clip= is required", 400)
    if len(clips) > _MAX_CLIPS_PER_REQUEST:
        return error(
            f"Too many clips: {len(clips)} (max {_MAX_CLIPS_PER_REQUEST})", 400,
        )
    for name in clips:
        if name not in _ALLOWED_CLIPS:
            return error(
                f"Unknown clip: {name!r} (allowed: {', '.join(_ALLOWED_CLIPS)})", 400,
            )

    cur.execute(
        "SELECT static_audio FROM characters WHERE slug = %s AND is_active",
        (slug,),
    )
    row = fetch_one(cur)
    if row is None:
        return error("Character not found", 404)
    static_audio = row.get("static_audio") or {}
    if isinstance(static_audio, str):
        try:
            static_audio = json.loads(static_audio)
        except Exception:
            static_audio = {}
    if not isinstance(static_audio, dict):
        static_audio = {}

    out: dict = {}
    for name in clips:
        entry = (static_audio.get(name) or {}).get(lang)
        if not isinstance(entry, dict) or not entry.get("key"):
            out[name] = None
            continue
        try:
            url, expires_at = signing.sign_cloudfront_url(
                entry["key"], _AUDIO_URL_TTL_SECONDS,
            )
        except Exception as exc:
            logger.exception(
                "audio signing failed slug=%s clip=%s lang=%s", slug, name, lang,
            )
            return error(f"Could not sign audio: {exc}", 500)
        out[name] = {
            "url": url,
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": entry.get("sha256"),
            "text_sha256": entry.get("text_sha256"),
        }
    # no-store: these URLs are signed per viewer and expire in minutes —
    # a shared cache must never store them.
    return success({"clips": out}, cache_control="no-store")


def _request_method(event) -> str:
    """The HTTP method, from either API Gateway payload format.

    Format 2.0 (Lambda Function URLs, HTTP APIs) puts it at
    requestContext.http.method; format 1.0 (REST API proxy integrations) puts it
    at the top level as httpMethod. Reading both means the same deployment
    package works behind either, which is the point: this function moved from a
    Function URL to a REST API on 20-08, and pinning it to one shape would have
    made that a code change rather than an infrastructure one.
    """
    v2 = event.get("requestContext", {}).get("http", {}).get("method")
    return (v2 or event.get("httpMethod") or "GET").upper()


def _request_path(event) -> str:
    """The request path WITHOUT the API Gateway stage prefix.

    The stage is the trap here. A REST API is always served under one — the URL
    is /v1/characters — and three fields carry a path:

        event["rawPath"]                     format 2.0, no stage
        event["path"]                        format 1.0, no stage      ← correct
        event["requestContext"]["path"]      format 1.0, WITH stage    ← wrong

    Reading the third one yields "/v1/characters", whose first segment is "v1"
    rather than "characters", and the router below then 404s every single
    request. Nothing else fails, which is what makes it worth naming.
    """
    raw = event.get("rawPath") or event.get("path") or "/characters"
    # Trailing slashes are stripped so /characters and /characters/ are one route.
    return raw.rstrip("/") or "/characters"


def handler(event, context):
    # First, so that every return below — including the 405 and 404 paths —
    # carries the right Access-Control-Allow-Origin.
    begin_request(event)

    method = _request_method(event)
    if method not in ("GET", "HEAD"):
        return error("Method not allowed", 405)

    raw_path = _request_path(event)
    segments = [s for s in raw_path.split("/") if s]

    # segments is one of:
    #   ["characters"]                            → list
    #   ["characters", slug]                      → detail
    #   ["characters", slug, "avatar-profile"]     → profile
    #   ["characters", slug, "audio"]              → signed clips (T4)
    if not segments or segments[0] != "characters":
        return error(f"Unknown path: {raw_path}", 404)

    slug = segments[1] if len(segments) > 1 else None
    if slug is not None and not _SLUG_RE.match(slug):
        # Rejected before it reaches SQL. The query is parameterised anyway, so
        # this is about returning an honest 400 rather than an empty 404.
        return error("Invalid character slug", 400)

    if len(segments) > 3 or (
        len(segments) == 3 and segments[2] not in ("avatar-profile", "audio")
    ):
        return error(f"Unknown path: {raw_path}", 404)

    try:
        conn = get_connection()
        cur = conn.cursor()
        try:
            if slug is None:
                return _list_characters(cur)
            if len(segments) == 3:
                if segments[2] == "audio":
                    return _get_audio(cur, slug, event)
                return _get_avatar_profile(cur, slug)
            return _get_character(cur, slug)
        finally:
            cur.close()

    except Exception as exc:
        logger.exception("characters handler failed path=%s", raw_path)
        return error(f"Internal server error: {exc}", 500)
