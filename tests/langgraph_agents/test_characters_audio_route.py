# -*- coding: utf-8 -*-
"""T4 — the local FastAPI twin of GET /characters/{slug}/audio (contract B).

Same validation order and same allowlist as infra/lambda/characters/handler.py
(allowlist equality itself is pinned in test_characters_contract.py). The pg
client and the signer are both stubbed — no database, no SSM, no RSA key.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agenticRAG"))

from langgraph_agents.api import routes_characters


def _static_audio() -> dict:
    return {
        "greeting.morning": {
            "vi": {
                "key": "characters/anne/audio/9f2c1a4b.ogg",
                "sha256": "deadbeef" * 8,
                "text_sha256": "cafef00d" * 8,
            },
        },
    }


class _FakeConn:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *args, **kwargs):
        return self._row


@contextlib.asynccontextmanager
async def _tx(conn):
    yield conn


class _FakePg:
    def __init__(self, row):
        self._row = row

    async def connect(self):
        pass

    def _raw_transaction(self):
        return _tx(_FakeConn(self._row))


@pytest.fixture
def wired(monkeypatch):
    """Route with a canned static_audio row and a deterministic signer."""
    monkeypatch.setattr(
        routes_characters,
        "get_pg_client",
        lambda: _FakePg({"static_audio": _static_audio()}),
    )
    # Patch the name the ROUTE looks up (it did `from ... import sign_url`,
    # so patching asset_urls.sign_url would leave the route's binding intact).
    monkeypatch.setattr(
        routes_characters, "sign_url", lambda key: f"https://cdn/{key}?Signature=x",
    )
    return routes_characters


def _body(response) -> dict:
    return json.loads(response.body.decode())


@pytest.mark.unit
async def test_audio_happy_path_and_missing_clip_is_null(wired):
    response = await wired.get_character_audio(
        "anne", clip=["greeting.morning", "greeting.evening"], lang="vi",
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    clips = _body(response)["clips"]
    morning = clips["greeting.morning"]
    assert morning["url"] == "https://cdn/characters/anne/audio/9f2c1a4b.ogg?Signature=x"
    assert morning["expires_at"].endswith("Z")
    assert morning["sha256"] == "deadbeef" * 8
    assert morning["text_sha256"] == "cafef00d" * 8
    assert clips["greeting.evening"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("clip", "lang", "fragment"),
    [
        (["nope.clip"], "vi", "Unknown clip"),
        ([], "vi", "At least one"),
        (["greeting.morning"] * 11, "vi", "Too many clips"),
        (["greeting.morning"], "fr", "Invalid lang"),
    ],
)
async def test_audio_rejects_bad_input(wired, clip, lang, fragment):
    with pytest.raises(HTTPException) as exc_info:
        await wired.get_character_audio("anne", clip=clip, lang=lang)

    assert exc_info.value.status_code == 400
    assert fragment in exc_info.value.detail


@pytest.mark.unit
async def test_audio_unknown_slug_is_404(monkeypatch):
    monkeypatch.setattr(
        routes_characters, "get_pg_client", lambda: _FakePg(None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await routes_characters.get_character_audio(
            "ghost", clip=["greeting.morning"], lang="vi",
        )

    assert exc_info.value.status_code == 404


@pytest.mark.unit
async def test_audio_invalid_slug_is_400(wired):
    with pytest.raises(HTTPException) as exc_info:
        await wired.get_character_audio(
            "../x", clip=["greeting.morning"], lang="vi",
        )

    assert exc_info.value.status_code == 400


@pytest.mark.unit
async def test_audio_signing_failure_is_500_without_a_url_or_key(monkeypatch):
    """Contract B: 500, never unsigned, never raw."""
    monkeypatch.setattr(
        routes_characters,
        "get_pg_client",
        lambda: _FakePg({"static_audio": _static_audio()}),
    )

    def _boom(key):
        raise RuntimeError("SSM denied")

    monkeypatch.setattr(routes_characters, "sign_url", _boom)

    with pytest.raises(HTTPException) as exc_info:
        await routes_characters.get_character_audio(
            "anne", clip=["greeting.morning"], lang="vi",
        )

    assert exc_info.value.status_code == 500
    assert "characters/anne/audio/9f2c1a4b.ogg" not in str(exc_info.value.detail)
