"""The characters Lambda must route correctly under BOTH API Gateway payload formats.

It moved from a Lambda Function URL (payload format 2.0) to a REST API proxy
integration (format 1.0) on 20-08. The two formats put the method and the path in
different places, and every failure mode here is silent:

    format 2.0    event["rawPath"]                 event["requestContext"]["http"]["method"]
    format 1.0    event["path"]                    event["httpMethod"]
    format 1.0    event["requestContext"]["path"]  ← INCLUDES the stage. Never read this.

Reading `requestContext.path` under a REST API yields "/v1/characters", whose
first segment is "v1" rather than "characters", so the router 404s every request
while the function, the integration and the IAM policy all look correct.

These tests do not touch the database: they assert on the routing decision, which
is what the payload format governs.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_ROOT = Path(__file__).resolve().parents[2] / "infra" / "lambda"
sys.path.insert(0, str(_LAMBDA_ROOT / "layer"))
sys.path.insert(0, str(_LAMBDA_ROOT / "characters"))


@pytest.fixture(scope="module")
def handler_module():
    """Import the handler with its AWS/driver dependencies stubbed.

    The Lambda layer (boto3, pg8000) is built at deploy time and is not installed
    in the test environment. `pg8000.dbapi` has to be registered as its own
    sys.modules entry AND set as an attribute — `import pg8000.dbapi` needs both,
    and a bare MagicMock for "pg8000" satisfies neither.
    """
    pg8000 = MagicMock()
    pg8000.dbapi = MagicMock()
    sys.modules.setdefault("pg8000", pg8000)
    sys.modules.setdefault("pg8000.dbapi", pg8000.dbapi)
    sys.modules.setdefault("boto3", MagicMock())
    import handler
    return handler


def _v2(path: str, method: str = "GET", origin: str | None = None) -> dict:
    """A Lambda Function URL / HTTP API event."""
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": {},
    }
    if origin:
        event["headers"]["origin"] = origin
    return event


def _v1(path: str, method: str = "GET", stage: str = "v1", origin: str | None = None) -> dict:
    """A REST API proxy event.

    `path` excludes the stage; `requestContext.path` includes it. Both are
    populated exactly as API Gateway populates them, because the point of these
    tests is that the handler reads the right one.
    """
    event = {
        "path": path,
        "httpMethod": method,
        "requestContext": {"path": f"/{stage}{path}", "stage": stage},
        "headers": {},
    }
    if origin:
        event["headers"]["origin"] = origin
    return event


# ── Routing ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("make_event", [_v1, _v2], ids=["rest-1.0", "furl-2.0"])
@pytest.mark.parametrize(
    ("path", "expected_call"),
    [
        ("/characters", "_list_characters"),
        ("/characters/bronya", "_get_character"),
        ("/characters/bronya/avatar-profile", "_get_avatar_profile"),
    ],
)
def test_routes_the_same_under_both_payload_formats(
    handler_module, make_event, path, expected_call,
):
    with (
        patch.object(handler_module, "get_connection"),
        patch.object(handler_module, expected_call, return_value={"statusCode": 200}) as target,
    ):
        handler_module.handler(make_event(path), None)

    assert target.called, (
        f"{path} did not reach {expected_call} under this payload format"
    )


@pytest.mark.unit
def test_rest_api_stage_prefix_is_not_read(handler_module):
    """The specific mistake this file exists for.

    If the handler ever reads `requestContext.path`, the stage prefix makes the
    first segment "v1" and this returns 404 instead of listing characters.
    """
    with (
        patch.object(handler_module, "get_connection"),
        patch.object(handler_module, "_list_characters", return_value={"statusCode": 200}),
    ):
        response = handler_module.handler(_v1("/characters", stage="v1"), None)

    assert response["statusCode"] != 404, (
        "404 means the router saw the stage prefix — it is reading "
        "requestContext.path instead of event['path']"
    )


@pytest.mark.unit
@pytest.mark.parametrize("make_event", [_v1, _v2], ids=["rest-1.0", "furl-2.0"])
def test_non_read_methods_are_rejected_under_both_formats(handler_module, make_event):
    """A 405 here is why the gateway must answer OPTIONS itself.

    API Gateway's stage CORS configuration handles the preflight; if it did not,
    the OPTIONS would reach this function and be refused, and the browser would
    report a CORS failure rather than a 405.
    """
    response = handler_module.handler(make_event("/characters", method="POST"), None)
    assert response["statusCode"] == 405


# ── CORS ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_allowed_origin_is_echoed_not_wildcarded(handler_module, monkeypatch):
    """`*` was the effective policy until CloudFront stopped covering it.

    The wildcard lived in shared/response.py and was masked by CloudFront's
    ResponseHeadersPolicy (origin_override=True). Moving the catalog behind API
    Gateway removed that cover, so the value the function sends is now the value
    the browser sees.
    """
    from shared import response as response_module

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com,http://localhost:5173")

    with (
        patch.object(handler_module, "get_connection"),
        patch.object(handler_module, "_list_characters",
                     side_effect=lambda cur: response_module.success({"characters": []})),
    ):
        result = handler_module.handler(
            _v1("/characters", origin="https://app.example.com"), None,
        )

    origin = result["headers"]["Access-Control-Allow-Origin"]
    assert origin == "https://app.example.com", f"expected the caller's origin, got {origin!r}"
    assert "Vary" in result["headers"], (
        "Vary: Origin is required once the header varies by caller, or a shared "
        "cache can hand one origin's response to another"
    )


# ── GET /characters/{slug}/audio (T4, contract B) ─────────────────────────


def _audio_event(path, params: dict | None = None, fmt: str = "v1") -> dict:
    """An event carrying query params in the given gateway payload format."""
    from urllib.parse import urlencode

    params = params or {}
    if fmt == "v1":
        multi = {k: (v if isinstance(v, list) else [v]) for k, v in params.items()}
        single = {
            k: (v[-1] if isinstance(v, list) else v) for k, v in params.items()
        }
        return {
            "path": path,
            "httpMethod": "GET",
            "requestContext": {"path": f"/v1{path}", "stage": "v1"},
            "queryStringParameters": single,
            "multiValueQueryStringParameters": multi,
            "headers": {},
        }
    raw = urlencode(
        [(k, item) for k, v in params.items() for item in (v if isinstance(v, list) else [v])]
    )
    return {
        "rawPath": path,
        "rawQueryString": raw,
        "requestContext": {"http": {"method": "GET"}},
        "headers": {},
    }


def _audio_row(static_audio: dict) -> _FakeCursor:
    return _FakeCursor(("static_audio",), [(static_audio,)])


def _audio_body(result) -> dict:
    import json as _json

    return _json.loads(result["body"])


@pytest.mark.unit
@pytest.mark.parametrize("fmt", ["v1", "v2"], ids=["rest-1.0", "furl-2.0"])
def test_audio_happy_path_signs_present_and_nulls_missing(
    handler_module, monkeypatch, fmt,
):
    """One present clip → url + hashes; one absent clip → null; 200 overall."""
    static_audio = {
        "greeting.morning": {
            "vi": {
                "key": "characters/anne/audio/9f2c1a4b.ogg",
                "sha256": "deadbeef" * 8,
                "text_sha256": "cafef00d" * 8,
            },
        },
    }
    fixed_expiry = datetime.datetime(
        2026, 9, 17, 10, 5, 0, tzinfo=datetime.timezone.utc,
    )
    monkeypatch.setattr(
        handler_module.signing,
        "sign_cloudfront_url",
        lambda key, ttl: (f"https://cdn/{key}?Signature=x", fixed_expiry),
    )
    with _connected(handler_module, _audio_row(static_audio)):
        result = handler_module.handler(
            _audio_event(
                "/characters/anne/audio",
                {"clip": ["greeting.morning", "greeting.evening"], "lang": "vi"},
                fmt,
            ),
            None,
        )

    assert result["statusCode"] == 200
    assert result["headers"]["Cache-Control"] == "no-store"
    clips = _audio_body(result)["clips"]
    morning = clips["greeting.morning"]
    assert morning["url"] == "https://cdn/characters/anne/audio/9f2c1a4b.ogg?Signature=x"
    assert morning["expires_at"] == "2026-09-17T10:05:00Z"
    assert morning["sha256"] == "deadbeef" * 8
    assert morning["text_sha256"] == "cafef00d" * 8
    assert clips["greeting.evening"] is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("params", "fragment"),
    [
        ({"clip": "nope.clip", "lang": "vi"}, "Unknown clip"),
        ({"lang": "vi"}, "At least one"),
        (
            {"clip": [f"greeting.morning"] * 11, "lang": "vi"},
            "Too many clips",
        ),
        ({"clip": "greeting.morning", "lang": "fr"}, "Invalid lang"),
    ],
)
def test_audio_rejects_bad_input(handler_module, params, fragment):
    static_audio = {"greeting.morning": {"vi": {"key": "k", "sha256": "s", "text_sha256": "t"}}}
    with _connected(handler_module, _audio_row(static_audio)):
        result = handler_module.handler(
            _audio_event("/characters/anne/audio", params), None,
        )

    assert result["statusCode"] == 400
    assert fragment in _audio_body(result)["error"]


@pytest.mark.unit
def test_audio_unknown_slug_is_404(handler_module):
    with _connected(handler_module, _FakeCursor(("static_audio",), [])):
        result = handler_module.handler(
            _audio_event("/characters/ghost/audio", {"clip": "greeting.morning"}),
            None,
        )

    assert result["statusCode"] == 404


@pytest.mark.unit
def test_audio_invalid_slug_is_400(handler_module):
    result = handler_module.handler(
        _audio_event("/characters/../x/audio", {"clip": "greeting.morning"}), None,
    )

    assert result["statusCode"] == 400


@pytest.mark.unit
def test_audio_signing_failure_is_500_without_a_url_or_key(
    handler_module, monkeypatch,
):
    """Contract B: 500 with a logged reason — never unsigned, never raw."""
    static_audio = {
        "greeting.morning": {"vi": {"key": "characters/anne/audio/9f2c.ogg"}},
    }

    def _boom(key, ttl):
        raise RuntimeError("SSM denied")

    monkeypatch.setattr(handler_module.signing, "sign_cloudfront_url", _boom)
    with _connected(handler_module, _audio_row(static_audio)):
        result = handler_module.handler(
            _audio_event("/characters/anne/audio", {"clip": "greeting.morning"}),
            None,
        )

    assert result["statusCode"] == 500
    body = _audio_body(result)
    assert "characters/anne/audio/9f2c.ogg" not in str(body)
    assert "Signature" not in str(body)


# ── Cache-Control per route (T2, contract A) ──────────────────────────────


class _FakeCursor:
    """Enough of a pg8000 cursor for fetch_all/fetch_one, with no database."""

    def __init__(self, columns, rows):
        self.description = [(c,) for c in columns]
        self._rows = rows

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _connected(handler_module, cursor):
    """Patch get_connection to hand out a cursor over canned rows."""
    return patch.object(
        handler_module, "get_connection",
        return_value=_FakeConn(cursor),
    )


@pytest.mark.unit
def test_list_is_public_cacheable(handler_module):
    """The picker grid is identical for every viewer — the one route where a
    shared cache is safe."""
    cur = _FakeCursor(
        ("slug", "display_name", "thumbnail_url", "description", "vrm_metadata"),
        [("anne", "Anne", None, None, {})],
    )
    with _connected(handler_module, cur):
        result = handler_module.handler(_v1("/characters"), None)

    assert result["statusCode"] == 200
    assert result["headers"]["Cache-Control"] == "public, max-age=300"


@pytest.mark.unit
def test_detail_carries_audio_version_but_never_static_audio(handler_module):
    """T5: the version travels, the raw S3 keys do not (T1)."""
    static_audio = {
        "greeting.morning": {"vi": {"key": "k", "sha256": "s", "text_sha256": "t"}},
    }
    cur = _FakeCursor(
        ("slug", "display_name", "static_audio"),
        [("anne", "Anne", static_audio)],
    )
    with _connected(handler_module, cur):
        result = handler_module.handler(_v1("/characters/anne"), None)

    import json as _json

    body = _json.loads(result["body"])
    assert result["statusCode"] == 200
    assert "static_audio" not in body
    assert body["audio_version"] == handler_module.audio_version(static_audio)
    assert isinstance(body["audio_version"], str) and len(body["audio_version"]) == 12


@pytest.mark.unit
def test_detail_without_clips_versions_to_null(handler_module):
    cur = _FakeCursor(("slug", "display_name", "static_audio"), [("anne", "Anne", {})])
    with _connected(handler_module, cur):
        result = handler_module.handler(_v1("/characters/anne"), None)

    import json as _json

    body = _json.loads(result["body"])
    assert result["statusCode"] == 200
    assert body["audio_version"] is None


@pytest.mark.unit
def test_detail_is_private_cacheable(handler_module):
    """Behind Cognito: a shared cache must never serve one viewer's detail
    (and T5's audio_version) to another."""
    cur = _FakeCursor(
        ("slug", "display_name"),
        [("anne", "Anne")],
    )
    with _connected(handler_module, cur):
        result = handler_module.handler(_v1("/characters/anne"), None)

    assert result["statusCode"] == 200
    assert result["headers"]["Cache-Control"] == "private, max-age=300"


@pytest.mark.unit
def test_avatar_profile_is_private_cacheable(handler_module):
    cur = _FakeCursor(
        ("avatar_profile",),
        [({"recipes": []},)],
    )
    with _connected(handler_module, cur):
        result = handler_module.handler(_v1("/characters/anne/avatar-profile"), None)

    assert result["statusCode"] == 200
    assert result["headers"]["Cache-Control"] == "private, max-age=300"


@pytest.mark.unit
def test_unknown_origin_does_not_get_a_wildcard(handler_module, monkeypatch):
    from shared import response as response_module

    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")

    with (
        patch.object(handler_module, "get_connection"),
        patch.object(handler_module, "_list_characters",
                     side_effect=lambda cur: response_module.success({"characters": []})),
    ):
        result = handler_module.handler(
            _v1("/characters", origin="https://attacker.example"), None,
        )

    assert result["headers"]["Access-Control-Allow-Origin"] != "*"
    assert result["headers"]["Access-Control-Allow-Origin"] != "https://attacker.example"
