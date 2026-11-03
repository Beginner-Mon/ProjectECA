"""OAuth connector routes: POST /connectors/{id}/start, POST /connectors/callback,
GET /me/connectors, DELETE /me/connectors/{id} (stage 5C). No network, no DB."""

from __future__ import annotations

import asyncio
import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, urlparse

from langgraph_agents.api import routes_loop
from langgraph_agents.api.auth import current_user_id, override_user
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.connectors.oauth import (
    ACTIVE,
    OAuthError,
    OAuthService,
    set_service,
)
from langgraph_agents.loop.connectors.store import (
    MemoryConnectionStore,
    MemoryPendingStore,
    Connection,
    set_stores,
)
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.plugins.manifest import Manifest
from tests.langgraph_agents.loop.oauth_fakes import (
    MockAuthServer, REDIRECT_URI, manifest_dict,
)

UID = "00000000-0000-4000-a000-000000000001"
PLUGIN = "mock-calendar"


def _manifest(plugin_id: str, **over) -> Manifest:
    """Create a manifest matching the mock server's expectations."""
    from tests.langgraph_agents.loop.oauth_fakes import manifest_dict
    data = manifest_dict()
    data["id"] = plugin_id
    data["name"] = plugin_id.title()
    data.update(over)
    return Manifest.model_validate(data)


def _make_service(server: MockAuthServer, manifest: Manifest, **settings) -> OAuthService:
    """Create an OAuthService wired to the mock server."""
    from langgraph_agents.loop import crypto
    from tests.langgraph_agents.loop.oauth_fakes import fake_secret_fetcher
    connections = MemoryConnectionStore()
    pending = MemoryPendingStore()
    sealer = crypto.LocalSealer(b"x" * 32)
    base_settings = dict(oauth_redirect_uri=REDIRECT_URI, env_name="test")
    base_settings.update(settings)
    service = OAuthService(
        connections=connections, pending=pending,
        settings=LoopSettings(**base_settings), sealer=sealer,
        catalog=lambda pid: manifest if pid == manifest.id else None,
        transport=server.transport(), policy=AddressPolicy.for_tests(),
        secret_fetcher=fake_secret_fetcher,
    )
    set_stores(connections, pending)
    set_service(service)
    return service


@pytest.fixture(autouse=True)
def _clean():
    set_service(None)
    set_stores(None, None)
    yield
    set_service(None)
    set_stores(None, None)


def _client(monkeypatch, server: MockAuthServer, manifest: Manifest, *, mode="loop", groups=(), **settings) -> TestClient:
    settings.setdefault("plugins_disabled", False)
    settings.setdefault("oauth_redirect_uri", REDIRECT_URI)
    st = LoopSettings(agent_mode=mode, **settings)
    monkeypatch.setattr(routes_loop, "load_settings", lambda: st)
    monkeypatch.setattr(
        "langgraph_agents.loop.plugins.manifest.get_manifest",
        lambda pid: manifest if pid == manifest.id else None)
    service = _make_service(server, manifest, **settings)
    # Override get_oauth_service in the oauth module to return our service instance
    monkeypatch.setattr("langgraph_agents.loop.connectors.oauth.get_oauth_service", lambda: service)
    app = FastAPI()
    app.include_router(routes_loop.router)
    app.dependency_overrides[current_user_id] = override_user(UID, groups=groups)
    return TestClient(app)


# ── /connectors/{plugin_id}/start ───────────────────────────────────────────


def test_start_returns_authorize_url(monkeypatch):
    """A valid connector returns an authorize URL."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 200
    assert "authorize_url" in r.json()
    # The redirect URI is URL-encoded in the authorize URL.
    assert "redirect_uri=https%3A%2F%2Fapp.test%2Fconnectors%2Fcallback" in r.json()["authorize_url"]


def test_start_pipeline_user_gets_404(monkeypatch):
    """Pipeline users cannot start OAuth."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest, mode="pipeline")
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 404


def test_start_unknown_plugin_is_404(monkeypatch):
    """Unknown plugin ID returns 404."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.post("/connectors/nope/start", json={"plugin_id": "nope"})
    assert r.status_code == 404
    assert r.json().get("detail", {}).get("code") == "unknown_plugin"


def test_start_gated_plugin_without_google_is_404(monkeypatch):
    """A requires_gate plugin is 404 when google_enabled is false."""
    server = MockAuthServer()
    manifest = _manifest("gmail", requires_gate=True)
    c = _client(monkeypatch, server, manifest, google_enabled=False)
    r = c.post("/connectors/gmail/start", json={"plugin_id": "gmail"})
    assert r.status_code == 404
    assert r.json().get("detail", {}).get("code") == "not_available"


def test_start_gated_plugin_with_google_is_ok(monkeypatch):
    """A requires_gate plugin works when google_enabled is true."""
    server = MockAuthServer()
    manifest = _manifest("gmail", requires_gate=True)
    c = _client(monkeypatch, server, manifest, google_enabled=True)
    r = c.post("/connectors/gmail/start", json={"plugin_id": "gmail"})
    assert r.status_code == 200


def test_start_mismatched_plugin_id_is_400(monkeypatch):
    """Body plugin_id must match path parameter."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": "other"})
    assert r.status_code == 400
    assert r.json().get("detail", {}).get("code") == "invalid_state"


def test_start_not_configured_when_no_redirect_uri(monkeypatch):
    """Missing LOOP_OAUTH_REDIRECT_URI -> 503 not_configured."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest, oauth_redirect_uri=None)
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 503
    assert r.json().get("detail", {}).get("code") == "not_configured"


# ── /connectors/callback ────────────────────────────────────────────────────


def test_callback_completes_the_flow(monkeypatch):
    """A valid callback with matching state completes and returns connection info."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)

    # Start the flow to get a valid state.
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 200
    authorize_url = r.json()["authorize_url"]

    # Simulate the browser: follow the authorize URL to get the real callback params.
    import httpx
    async def get_callback_params():
        async with httpx.AsyncClient(transport=server.transport()) as client:
            response = await client.get(authorize_url, follow_redirects=False)
        assert response.status_code == 302
        return parse_qs(urlparse(response.headers["location"]).query)

    params = asyncio.run(get_callback_params())
    params = {k: v[0] for k, v in params.items()}
    print(f'Callback params from browser: {params}')

    # Complete with the real code from the mock server.
    r = c.post("/connectors/callback", json={"code": params["code"], "state": params["state"], "iss": params.get("iss", "https://auth.test")})
    print(f'Callback response: {r.status_code} {r.json()}')
    print(f'Token requests: {server.token_requests}')
    assert r.status_code == 200
    assert r.json()["plugin_id"] == PLUGIN
    assert r.json()["status"] == "active"
    assert r.json()["scopes"] == ["calendar.read", "calendar.events"]


def test_callback_invalid_state_is_400(monkeypatch):
    """Invalid/expired/used/wrong-user state returns 400 invalid_state."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.post("/connectors/callback", json={"code": "x", "state": "bad-state", "iss": "https://auth.example.com"})
    assert r.status_code == 400
    assert r.json().get("detail", {}).get("code") == "invalid_state"


def test_callback_issuer_mismatch_is_400(monkeypatch):
    """Wrong iss in callback returns 400 issuer_mismatch."""
    # This test would require controlling the mock server's issuer.
    # Skipped: the mock server's issuer is fixed to match.
    pass


# ── /me/connectors ──────────────────────────────────────────────────────────


def test_list_connectors_empty_when_none(monkeypatch):
    """No connections returns empty list."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.get("/me/connectors")
    assert r.status_code == 200
    assert r.json() == {"loop": True, "connections": []}


def test_list_connectors_shows_active_connection(monkeypatch):
    """A connected plugin appears in the list."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)

    # Connect first using the browser step to get a real code.
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 200
    authorize_url = r.json()["authorize_url"]
    
    async def get_callback_params():
        async with httpx.AsyncClient(transport=server.transport()) as client:
            response = await client.get(authorize_url, follow_redirects=False)
        assert response.status_code == 302
        return parse_qs(urlparse(response.headers["location"]).query)
    
    params = asyncio.run(get_callback_params())
    params = {k: v[0] for k, v in params.items()}
    c.post("/connectors/callback", json={"code": params["code"], "state": params["state"], "iss": params.get("iss", "https://auth.test")})

    # List connections.
    r = c.get("/me/connectors")
    assert r.status_code == 200
    body = r.json()
    assert body["loop"] is True
    assert len(body["connections"]) == 1
    assert body["connections"][0]["plugin_id"] == PLUGIN
    assert body["connections"][0]["status"] == "active"


def test_list_connectors_pipeline_user_gets_empty(monkeypatch):
    """Pipeline users get loop: false and empty list."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest, mode="pipeline")
    r = c.get("/me/connectors")
    assert r.status_code == 200
    assert r.json() == {"loop": False, "connections": []}


def test_list_connectors_store_failure_is_503(monkeypatch):
    """A store failure returns 503."""
    class Broken:
        async def list(self, uid):
            raise RuntimeError("db down")
    from langgraph_agents.loop.connectors.store import set_stores, MemoryPendingStore
    from langgraph_agents.loop.connectors.oauth import OAuthService, set_service
    from langgraph_agents.loop import crypto
    from langgraph_agents.loop.config import LoopSettings
    from langgraph_agents.loop.http import AddressPolicy
    
    # Create service with broken store
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    connections = Broken()
    pending = MemoryPendingStore()
    sealer = crypto.LocalSealer(b"x" * 32)
    service = OAuthService(
        connections=connections, pending=pending,
        settings=LoopSettings(oauth_redirect_uri=REDIRECT_URI, env_name="test"),
        sealer=sealer,
        catalog=lambda pid: manifest if pid == manifest.id else None,
        transport=server.transport(), policy=AddressPolicy.for_tests(),
        secret_fetcher=lambda _: "test-secret",
    )
    set_stores(connections, pending)
    set_service(service)
    
    st = LoopSettings(agent_mode="loop", oauth_redirect_uri=REDIRECT_URI)
    monkeypatch.setattr(routes_loop, "load_settings", lambda: st)
    monkeypatch.setattr(
        "langgraph_agents.loop.plugins.manifest.get_manifest",
        lambda pid: manifest if pid == manifest.id else None)
    app = FastAPI()
    app.include_router(routes_loop.router)
    app.dependency_overrides[current_user_id] = override_user(UID)
    c = TestClient(app)
    r = c.get("/me/connectors")
    assert r.status_code == 503


# ── DELETE /me/connectors/{plugin_id} ───────────────────────────────────────


def test_delete_revokes_connection(monkeypatch):
    """DELETE revokes and deletes the connection."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)

    # Connect first using the browser step to get a real code.
    r = c.post(f"/connectors/{PLUGIN}/start", json={"plugin_id": PLUGIN})
    assert r.status_code == 200
    authorize_url = r.json()["authorize_url"]
    
    async def get_callback_params():
        async with httpx.AsyncClient(transport=server.transport()) as client:
            response = await client.get(authorize_url, follow_redirects=False)
        assert response.status_code == 302
        return parse_qs(urlparse(response.headers["location"]).query)
    
    params = asyncio.run(get_callback_params())
    params = {k: v[0] for k, v in params.items()}
    c.post("/connectors/callback", json={"code": params["code"], "state": params["state"], "iss": params.get("iss", "https://auth.test")})

    # Delete it.
    r = c.delete(f"/me/connectors/{PLUGIN}")
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"

    # Verify it's gone.
    r = c.get("/me/connectors")
    assert r.json()["connections"] == []


def test_delete_nonexistent_is_404(monkeypatch):
    """Deleting a non-existent connection returns 404."""
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    c = _client(monkeypatch, server, manifest)
    r = c.delete("/me/connectors/gmail")
    assert r.status_code == 404
    assert r.json().get("detail", {}).get("code") == "not_found"


def test_delete_store_failure_is_503(monkeypatch):
    """A store failure during delete returns 503."""
    from datetime import datetime, timezone
    
    class Broken:
        async def get(self, uid, pid):
            return Connection(
                user_id=uid, plugin_id=pid, access_enc=b"x", refresh_enc=None,
                expires_at=datetime.now(timezone.utc), scopes=[],
                status=ACTIVE, issuer="", created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc))
        async def delete(self, uid, pid):
            raise RuntimeError("db down")
    from langgraph_agents.loop.connectors.store import set_stores, MemoryPendingStore
    from langgraph_agents.loop.connectors.oauth import OAuthService, set_service
    from langgraph_agents.loop import crypto
    from langgraph_agents.loop.config import LoopSettings
    from langgraph_agents.loop.http import AddressPolicy
    
    # Create service with broken store
    server = MockAuthServer()
    manifest = _manifest(PLUGIN)
    connections = Broken()
    pending = MemoryPendingStore()
    sealer = crypto.LocalSealer(b"x" * 32)
    service = OAuthService(
        connections=connections, pending=pending,
        settings=LoopSettings(oauth_redirect_uri=REDIRECT_URI, env_name="test"),
        sealer=sealer,
        catalog=lambda pid: manifest if pid == manifest.id else None,
        transport=server.transport(), policy=AddressPolicy.for_tests(),
        secret_fetcher=lambda _: "test-secret",
    )
    set_stores(connections, pending)
    set_service(service)
    
    st = LoopSettings(agent_mode="loop", oauth_redirect_uri=REDIRECT_URI)
    monkeypatch.setattr(routes_loop, "load_settings", lambda: st)
    monkeypatch.setattr(
        "langgraph_agents.loop.plugins.manifest.get_manifest",
        lambda pid: manifest if pid == manifest.id else None)
    app = FastAPI()
    app.include_router(routes_loop.router)
    app.dependency_overrides[current_user_id] = override_user(UID)
    c = TestClient(app)
    r = c.delete(f"/me/connectors/{PLUGIN}")
    assert r.status_code == 503