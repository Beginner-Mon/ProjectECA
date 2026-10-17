"""POST /me/uploads: a short-lived presigned POST for one private upload (SCRUM-45 P1).

The browser uploads straight to S3, so API Gateway's 10 MB payload limit never
applies and the CRUD Lambda never holds the bytes. What the route controls is
the policy it signs: the key (under the caller's own prefix), the content type
and the size range. A presigned PUT cannot bound the size; a POST policy can.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

UID = "11111111-2222-3333-4444-555555555555"
BUCKET = "vva-user-uploads-test"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    import langgraph_agents.api.auth as auth_mod
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_testpool")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("UPLOADS_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()
    yield
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()


def _client(uid: str | None = UID) -> TestClient:
    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.routes_uploads import router
    app = FastAPI()
    app.include_router(router)
    if uid is not None:
        app.dependency_overrides[current_user_id] = override_user(uid)
    return TestClient(app)


def _policy(ticket: dict) -> dict:
    return json.loads(base64.b64decode(ticket["fields"]["policy"]))


def _ask(client, **body):
    payload = {"kind": "image", "content_type": "image/jpeg", "size_bytes": 200_000}
    payload.update(body)
    return client.post("/me/uploads", json=payload)


def test_issues_a_post_policy_under_the_callers_own_prefix():
    resp = _ask(_client())
    assert resp.status_code == 200, resp.text
    ticket = resp.json()
    assert ticket["key"].startswith(f"images/{UID}/") and ticket["key"].endswith(".jpg")
    assert ticket["fields"]["key"] == ticket["key"]
    assert ticket["fields"]["Content-Type"] == "image/jpeg"
    assert BUCKET in ticket["url"]
    assert ticket["expires_in"] == 300
    assert ticket["max_bytes"] == 5 * 1024 * 1024


def test_the_signed_policy_bounds_size_type_and_key():
    ticket = _ask(_client()).json()
    conditions = _policy(ticket)["conditions"]
    assert ["content-length-range", 1, 5 * 1024 * 1024] in conditions
    assert {"Content-Type": "image/jpeg"} in conditions
    assert {"key": ticket["key"]} in conditions
    assert {"bucket": BUCKET} in conditions


def test_each_upload_gets_its_own_key():
    client = _client()
    assert _ask(client).json()["key"] != _ask(client).json()["key"]


@pytest.mark.parametrize("content_type,ext", [("image/png", ".png"), ("image/webp", ".webp")])
def test_other_image_types_keep_their_extension(content_type, ext):
    ticket = _ask(_client(), content_type=content_type).json()
    assert ticket["key"].endswith(ext)


def test_rejects_a_type_that_is_not_an_image():
    resp = _ask(_client(), content_type="application/x-msdownload")
    assert resp.status_code == 415


def test_rejects_a_file_over_the_limit_before_signing():
    resp = _ask(_client(), size_bytes=5 * 1024 * 1024 + 1)
    assert resp.status_code == 413


def test_rejects_an_unknown_kind():
    assert _ask(_client(), kind="video").status_code == 422


def test_needs_a_signed_in_user():
    assert _ask(_client(uid=None)).status_code == 401


def test_says_so_when_the_bucket_is_not_configured(monkeypatch):
    monkeypatch.delenv("UPLOADS_BUCKET")
    assert _ask(_client()).status_code == 503


NAME = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize("key,ok", [
    (f"images/{UID}/{NAME}.jpg", True),
    (f"images/99999999-2222-3333-4444-555555555555/{NAME}.jpg", False),
    (f"images/{UID}/../{NAME}.jpg", False),
    (f"docs/{UID}/{NAME}.jpg", False),
    (f"images/{UID}/{NAME}.exe", False),
    (f"images/{UID}/abc.jpg", False),
])
def test_key_belongs_to_checks_kind_and_owner(key, ok):
    from langgraph_agents.api.routes_uploads import key_belongs_to
    assert key_belongs_to(UID, key, "image") is ok


def test_the_policy_is_accepted_by_s3():
    """Round trip against moto: the signed policy is a real one."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        ticket = _ask(_client()).json()
        import requests
        files = {"file": ("x.jpg", b"\xff\xd8\xff" + b"0" * 100, "image/jpeg")}
        resp = requests.post(ticket["url"], data=ticket["fields"], files=files, timeout=5)
        assert resp.status_code in (200, 204), resp.text
        head = boto3.client("s3", region_name="us-east-1").head_object(Bucket=BUCKET, Key=ticket["key"])
        assert head["ContentType"] == "image/jpeg"
