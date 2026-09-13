# -*- coding: utf-8 -*-
"""Tests for sign_static_audio (finding 4 — the silent-fallback regression).

`sign_url` is monkeypatched at the module level rather than exercised for
real — same reasoning as test_motion_status.py: a real call needs an RSA key,
an AWS SSM parameter and a CloudFront key pair id, none of which this suite
has. Patching the module-level name is enough because sign_static_audio looks
it up at call time.
"""
from __future__ import annotations

import logging

import pytest
from botocore.exceptions import ClientError

from langgraph_agents.shared import asset_urls


@pytest.mark.unit
def test_signs_every_key_in_the_map(monkeypatch):
    monkeypatch.setattr(asset_urls, "sign_url", lambda key: f"https://cdn/{key}?Signature=x")

    out = asset_urls.sign_static_audio({"greeting": {"vi": "characters/anne/audio/9f2c.ogg"}})

    assert out == {"greeting": {"vi": "https://cdn/characters/anne/audio/9f2c.ogg?Signature=x"}}


@pytest.mark.unit
def test_already_a_url_is_left_untouched(monkeypatch):
    def _explode(key):
        raise AssertionError("sign_url must not be called for an already-signed URL")

    monkeypatch.setattr(asset_urls, "sign_url", _explode)
    already = "https://cdn/characters/anne/audio/old.ogg?Signature=already"

    out = asset_urls.sign_static_audio({"greeting": {"vi": already}})

    assert out == {"greeting": {"vi": already}}


@pytest.mark.unit
def test_operational_signing_failure_drops_the_key_instead_of_returning_it_raw(monkeypatch, caplog):
    """The bug: an earlier version put the raw S3 key in the field the
    frontend treats as a URL on signing failure. The browser then requested
    it as a relative path against its own origin and 404'd — indistinguishable
    from "this character has no greeting". The fix: the key must be DROPPED,
    never handed back as a value that looks like a usable URL, and the
    failure must be visible in the log.
    """
    raw_key = "characters/anne/audio/9f2c.ogg"

    def _raise_client_error(key):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetParameter",
        )

    monkeypatch.setattr(asset_urls, "sign_url", _raise_client_error)

    with caplog.at_level(logging.ERROR, logger="langgraph.shared.asset_urls"):
        out = asset_urls.sign_static_audio({"greeting": {"vi": raw_key}})

    # The key is gone entirely — not present as a raw key, not as any other
    # placeholder. getStaticAudioUrl() on the frontend already treats a
    # missing kind/lang as "no clip", the same outcome as a character that
    # never had this audio rendered at all.
    assert out == {"greeting": {}}
    assert raw_key not in str(out)

    # The failure is visible in the log with the reason, not silent.
    assert any(
        raw_key in record.getMessage() and "AccessDenied" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.unit
def test_operational_failure_on_one_key_does_not_fail_the_whole_map(monkeypatch):
    """Signing must never fail the whole /characters response over one bad
    key — that requirement from the original code is preserved; only the
    "return the raw key" part of it was the bug."""
    def _sign(key):
        if "broken" in key:
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetParameter")
        return f"https://cdn/{key}?Signature=ok"

    monkeypatch.setattr(asset_urls, "sign_url", _sign)

    out = asset_urls.sign_static_audio({
        "greeting": {"vi": "characters/anne/audio/broken.ogg", "en": "characters/anne/audio/fine.ogg"},
    })

    assert out == {"greeting": {"en": "https://cdn/characters/anne/audio/fine.ogg?Signature=ok"}}


@pytest.mark.unit
def test_a_real_bug_in_this_module_is_not_swallowed(monkeypatch):
    """The bare `except Exception:` used to swallow everything, including a
    programming error in this module itself. Narrowed to the expected
    operational exception types, so a TypeError (e.g. a future signature
    change to sign_url that this call site was not updated for) must still
    reach the caller as a real failure, not a silently-dropped audio clip.
    """
    def _sign(key):
        raise TypeError("sign_url() missing 1 required positional argument")

    monkeypatch.setattr(asset_urls, "sign_url", _sign)

    with pytest.raises(TypeError):
        asset_urls.sign_static_audio({"greeting": {"vi": "characters/anne/audio/9f2c.ogg"}})


@pytest.mark.unit
def test_missing_env_var_is_treated_as_an_operational_failure(monkeypatch, caplog):
    """KeyError from `os.environ[...]` (MOTION_SIGNING_KEY_PARAM /
    MOTION_KEY_PAIR_ID / ASSET_BASE_URL not set) is the realistic
    not-yet-configured case, not a bug in this module — it must be handled
    the same way as a ClientError: logged, key dropped, response intact.
    """
    def _sign(key):
        raise KeyError("MOTION_SIGNING_KEY_PARAM")

    monkeypatch.setattr(asset_urls, "sign_url", _sign)

    with caplog.at_level(logging.ERROR, logger="langgraph.shared.asset_urls"):
        out = asset_urls.sign_static_audio({"greeting": {"vi": "characters/anne/audio/9f2c.ogg"}})

    assert out == {"greeting": {}}
    assert any("MOTION_SIGNING_KEY_PARAM" in record.getMessage() for record in caplog.records)


@pytest.mark.unit
def test_empty_and_missing_maps_pass_through(monkeypatch):
    def _explode(key):
        raise AssertionError("sign_url must not be called for an empty/missing map")

    monkeypatch.setattr(asset_urls, "sign_url", _explode)

    assert asset_urls.sign_static_audio({}) == {}
    assert asset_urls.sign_static_audio(None) is None
