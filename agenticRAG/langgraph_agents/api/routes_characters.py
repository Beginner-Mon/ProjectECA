"""Character catalog — local shim for zero-cost dev.

Mirrors infra/lambda/characters/handler.py but as a FastAPI router backed by
asyncpg (get_pg_client) instead of pg8000. Public (no auth) — same as the
prod REST API (rest_api_stack.py:208-211).

Prod stays on the Lambda (vva-characters) + REST API Gateway; this router
exists so `VITE_API_GATEWAY_URL=http://localhost:8000` serves /characters
without deploying anything, fixing MotionContext 404 on single-port dev.

Routes:
    GET /characters
    GET /characters/{slug}
    GET /characters/{slug}/avatar-profile
    GET /characters/{slug}/audio

`persona` is never returned (LLM system prompt) — same as the Lambda.

Auth lives at the gateway in prod (rest_api_stack.py, contract A); this shim
serves all four without it, exactly as it already did for detail/profile.
"""

from __future__ import annotations

import datetime
import hashlib
import re

import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from langgraph_agents.shared import get_pg_client
from langgraph_agents.shared.asset_urls import SIGNED_URL_TTL, sign_url
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.api.characters")

router = APIRouter(tags=["characters"])

_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# T4 — mirrors _ALLOWED_CLIPS in infra/lambda/characters/handler.py verbatim.
# The two cannot import from each other, so
# tests/infra/test_characters_contract.py compares them instead.
_ALLOWED_CLIPS = (
    "greeting.morning",
    "greeting.afternoon",
    "greeting.evening",
    "greeting.night",
)

_ALLOWED_LANGS = ("vi", "en")

_MAX_CLIPS_PER_REQUEST = 10

# Lite for card grid: display + compatibility (no vrm_url/voice/ui_strings).
# T1: static_audio is GONE from every public column list. Signing it inside a
# public GET /characters response was meaningless — anyone could fetch the
# signed URL without a token. Clips now live behind GET /characters/{slug}/audio
# (Cognito, no-store), and the detail route only exposes `audio_version`
# (a hash, not the keys). voice_vi_key/_en_key are NEVER returned
# (private bucket, no CloudFront) — SpeechLLm alone reads them via S3 IAM.
_PUBLIC_COLUMNS_LITE = "slug, display_name, thumbnail_url, description, vrm_metadata"
_PUBLIC_COLUMNS = (
    "slug, display_name, description, "
    "vrm_url, thumbnail_url, vrm_metadata, "
    "voice_language, sort_order, ui_strings"
)

_CACHE_SECONDS = 300


def _cache_headers() -> dict:
    return {"Cache-Control": f"public, max-age={_CACHE_SECONDS}"}


@router.get("/characters")
async def list_characters():
    """List active characters — lite for card grid (public, no auth)."""
    pg = get_pg_client()
    await pg.connect()
    async with pg._raw_transaction() as conn:
        rows = await conn.fetch(
            f"SELECT {_PUBLIC_COLUMNS_LITE} FROM characters "
            "WHERE is_active ORDER BY sort_order, slug"
        )
    characters = []
    for r in rows:
        d = dict(r)
        # T1: no static_audio here at all — not raw, not signed. See module note.
        if isinstance(d.get("vrm_metadata"), str):
            try:
                d["vrm_metadata"] = json.loads(d["vrm_metadata"])
            except Exception:
                pass
        characters.append(d)
    return JSONResponse(
        content={"characters": characters, "total": len(characters)},
        headers=_cache_headers(),
    )


def audio_version(static_audio) -> str | None:
    """12 hex chars over the canonical static_audio JSON (contract D).

    Mirrors audio_version() in infra/lambda/characters/handler.py verbatim —
    same loads, same dumps, same slice — so both halves agree byte for byte.
    Pinned by tests/infra/test_characters_contract.py; change both together.
    """
    if isinstance(static_audio, str):
        try:
            static_audio = json.loads(static_audio)
        except Exception:
            return None
    if not isinstance(static_audio, dict) or not static_audio:
        return None
    canonical = json.dumps(static_audio, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


@router.get("/characters/{slug}")
async def get_character(slug: str):
    """One character by slug — public."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid character slug")
    pg = get_pg_client()
    await pg.connect()
    async with pg._raw_transaction() as conn:
        row = await conn.fetchrow(
            f"SELECT {_PUBLIC_COLUMNS}, avatar_profile, static_audio FROM characters "
            "WHERE slug = $1 AND is_active",
            slug,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Character not found")
    result = dict(row)
    for k in ("vrm_metadata", "ui_strings", "avatar_profile"):
        if isinstance(result.get(k), str):
            try:
                result[k] = json.loads(result[k])
            except Exception:
                pass
    # T1+T5: static_audio is READ for the version and never returned — not
    # raw, not signed. T4 serves clips via /audio.
    result["audio_version"] = audio_version(result.pop("static_audio", None))
    return JSONResponse(content=result, headers=_cache_headers())


@router.get("/characters/{slug}/audio")
async def get_character_audio(
    slug: str,
    clip: list[str] = Query(default=[]),
    lang: str = "vi",
):
    """Signed clip URLs for one character (contract B) — local twin of the
    Lambda's _get_audio. Same allowlist, same validation order, same
    no-store. Signs with the agent's asset_urls.sign_url (5-minute TTL)."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid character slug")
    if lang not in _ALLOWED_LANGS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid lang: {lang!r} (expected one of {', '.join(_ALLOWED_LANGS)})",
        )
    if not clip:
        raise HTTPException(status_code=400, detail="At least one ?clip= is required")
    if len(clip) > _MAX_CLIPS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many clips: {len(clip)} (max {_MAX_CLIPS_PER_REQUEST})",
        )
    for name in clip:
        if name not in _ALLOWED_CLIPS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown clip: {name!r} (allowed: {', '.join(_ALLOWED_CLIPS)})",
            )
    pg = get_pg_client()
    await pg.connect()
    async with pg._raw_transaction() as conn:
        row = await conn.fetchrow(
            "SELECT static_audio FROM characters WHERE slug = $1 AND is_active",
            slug,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Character not found")
    static_audio = dict(row).get("static_audio") or {}
    if isinstance(static_audio, str):
        try:
            static_audio = json.loads(static_audio)
        except Exception:
            static_audio = {}
    if not isinstance(static_audio, dict):
        static_audio = {}

    out: dict = {}
    for name in clip:
        entry = (static_audio.get(name) or {}).get(lang)
        if not isinstance(entry, dict) or not entry.get("key"):
            out[name] = None
            continue
        try:
            url = sign_url(entry["key"])
        except Exception as exc:
            logger.exception(
                "audio signing failed slug=%s clip=%s lang=%s", slug, name, lang,
            )
            raise HTTPException(
                status_code=500, detail=f"Could not sign audio: {exc}",
            ) from exc
        expires_at = (
            datetime.datetime.now(datetime.timezone.utc) + SIGNED_URL_TTL
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        out[name] = {
            "url": url,
            "expires_at": expires_at,
            "sha256": entry.get("sha256"),
            "text_sha256": entry.get("text_sha256"),
        }
    return JSONResponse(
        content={"clips": out}, headers={"Cache-Control": "no-store"},
    )


@router.get("/characters/{slug}/avatar-profile")
async def get_avatar_profile(slug: str):
    """Avatar profile JSONB — public."""
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail="Invalid character slug")
    pg = get_pg_client()
    await pg.connect()
    async with pg._raw_transaction() as conn:
        row = await conn.fetchrow(
            "SELECT avatar_profile FROM characters WHERE slug = $1 AND is_active",
            slug,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Character not found")
    profile = row["avatar_profile"] or {}
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except Exception:
            pass
    # handler.py returns the profile object directly, not wrapped
    if isinstance(profile, dict):
        return JSONResponse(content=profile, headers=_cache_headers())
    return JSONResponse(content={"avatar_profile": profile}, headers=_cache_headers())
