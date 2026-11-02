"""GET /me/plugins and POST /me/plugins/{id} (stage 3C). No network, no DB."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langgraph_agents.api import routes_loop
from langgraph_agents.api.auth import current_user_id, override_user
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.plugins import runtime
from langgraph_agents.loop.plugins.manifest import Manifest, load_catalog
from langgraph_agents.loop.plugins.store import MemoryPluginStore, set_store

UID = "00000000-0000-4000-a000-000000000001"
PIN = "a" * 64


def _manifest(pid: str, **over) -> Manifest:
    data = {
        "id": pid, "name": pid.title(), "version": "1", "publisher": "tests",
        "description": f"about {pid}",
        "mcp_server": {"url": "https://mcp.example.com/mcp",
                       "allowed_hosts": ["mcp.example.com"]},
        "tools": {"echo": {"egress": True, "risk": "read"}},
        "reviewed": {"by": "test", "tool_definitions_sha256": PIN},
        "enabled_by_default": False,
    }
    data.update(over)
    return Manifest.model_validate(data)


@pytest.fixture(autouse=True)
def _clean():
    runtime.reset_runtime()
    set_store(MemoryPluginStore())
    yield
    runtime.reset_runtime()
    set_store(None)


def _client(monkeypatch, *, mode="loop", catalog=None, groups=(), **settings) -> TestClient:
    settings.setdefault("plugins_disabled", False)
    st = LoopSettings(agent_mode=mode, **settings)
    monkeypatch.setattr(routes_loop, "load_settings", lambda: st)
    if catalog is not None:
        monkeypatch.setattr(
            "langgraph_agents.loop.plugins.manifest.load_catalog", lambda *a, **k: catalog)
    app = FastAPI()
    app.include_router(routes_loop.router)
    app.dependency_overrides[current_user_id] = override_user(UID, groups=groups)
    return TestClient(app)


def test_a_pipeline_user_gets_loop_false_and_an_empty_list(monkeypatch):
    c = _client(monkeypatch, mode="pipeline", catalog=[_manifest("alpha")])
    assert c.get("/me/plugins").json() == {"loop": False, "plugins": []}


def test_a_pipeline_user_cannot_toggle(monkeypatch):
    c = _client(monkeypatch, mode="pipeline", catalog=[_manifest("alpha")])
    assert c.post("/me/plugins/alpha", json={"enabled": True}).status_code == 404


def test_group_mode_lists_only_for_the_group(monkeypatch):
    out = _client(monkeypatch, mode="group", groups=("loop-testers",),
                  catalog=[_manifest("alpha")]).get("/me/plugins").json()
    assert out["loop"] is True
    out = _client(monkeypatch, mode="group", groups=(),
                  catalog=[_manifest("alpha")]).get("/me/plugins").json()
    assert out == {"loop": False, "plugins": []}


def test_list_shape_and_default_state(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest("alpha"),
                                      _manifest("beta", enabled_by_default=True)])
    body = c.get("/me/plugins").json()
    assert body["loop"] is True
    assert body["plugins"] == [
        {"id": "alpha", "name": "Alpha", "publisher": "tests", "description": "about alpha",
         "enabled": False, "status": "disabled"},
        {"id": "beta", "name": "Beta", "publisher": "tests", "description": "about beta",
         "enabled": True, "status": "active"},
    ]


def test_toggle_on_then_off_changes_what_the_next_list_says(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest("alpha")])
    on = c.post("/me/plugins/alpha", json={"enabled": True})
    assert on.status_code == 200
    assert on.json()["enabled"] is True and on.json()["status"] == "active"
    assert c.get("/me/plugins").json()["plugins"][0]["status"] == "active"
    off = c.post("/me/plugins/alpha", json={"enabled": False}).json()
    assert off["enabled"] is False and off["status"] == "disabled"
    assert c.get("/me/plugins").json()["plugins"][0]["enabled"] is False


def test_the_write_reaches_the_store_under_the_caller_id(monkeypatch):
    store = MemoryPluginStore()
    set_store(store)
    c = _client(monkeypatch, catalog=[_manifest("alpha")])
    c.post("/me/plugins/alpha", json={"enabled": True})
    assert asyncio.run(store.get_all(UID)) == {"alpha": True}
    assert asyncio.run(store.get_all("someone-else")) == {}


def test_unknown_id_is_404(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest("alpha")])
    assert c.post("/me/plugins/nope", json={"enabled": True}).status_code == 404


def test_a_gated_plugin_is_hidden_and_404_until_google_is_on(monkeypatch):
    cat = [_manifest("alpha"), _manifest("gcal", requires_gate=True)]
    c = _client(monkeypatch, catalog=cat)
    assert [p["id"] for p in c.get("/me/plugins").json()["plugins"]] == ["alpha"]
    assert c.post("/me/plugins/gcal", json={"enabled": True}).status_code == 404
    c = _client(monkeypatch, catalog=cat, google_enabled=True)
    assert [p["id"] for p in c.get("/me/plugins").json()["plugins"]] == ["alpha", "gcal"]
    assert c.post("/me/plugins/gcal", json={"enabled": True}).status_code == 200


def test_the_global_switch_lists_plugins_as_off(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest("alpha", enabled_by_default=True)],
                plugins_disabled=True)
    body = c.get("/me/plugins").json()
    assert body["loop"] is True
    assert body["plugins"][0]["status"] == "off"


def test_the_single_plugin_kill_switch_shows_off(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest("alpha", enabled_by_default=True),
                                      _manifest("beta", enabled_by_default=True)],
                plugins_off=["beta"])
    st = {p["id"]: p["status"] for p in c.get("/me/plugins").json()["plugins"]}
    assert st == {"alpha": "active", "beta": "off"}


def test_an_unpinned_plugin_says_definition_changed(monkeypatch):
    c = _client(monkeypatch, catalog=[_manifest(
        "alpha", enabled_by_default=True,
        reviewed={"by": "", "tool_definitions_sha256": "UNPINNED"})])
    assert c.get("/me/plugins").json()["plugins"][0]["status"] == "definition_changed"


def test_a_cached_hash_that_differs_from_the_pin_says_definition_changed(monkeypatch):
    m = _manifest("alpha", enabled_by_default=True)
    runtime._defs_cache["alpha"] = runtime._Defs(0.0, m.resolved_url(), [], "b" * 64)
    c = _client(monkeypatch, catalog=[m])
    assert c.get("/me/plugins").json()["plugins"][0]["status"] == "definition_changed"


def test_a_plugin_without_an_endpoint_says_unconfigured(monkeypatch):
    monkeypatch.delenv("LOOP_X_URL", raising=False)
    m = _manifest("alpha", enabled_by_default=True,
                  mcp_server={"url": "", "url_env": "LOOP_X_URL", "allowed_hosts": []})
    c = _client(monkeypatch, catalog=[m])
    assert c.get("/me/plugins").json()["plugins"][0]["status"] == "unconfigured"


def test_the_shipped_catalog_lists_web_search_and_deepwiki(monkeypatch):
    monkeypatch.delenv("LOOP_WEB_SEARCH_MCP_URL", raising=False)
    c = _client(monkeypatch)
    st = {p["id"]: p["status"] for p in c.get("/me/plugins").json()["plugins"]}
    assert set(st) == {m.id for m in load_catalog() if not m.requires_gate}
    assert st["deepwiki"] == "disabled"


def test_a_store_failure_is_a_503_not_an_empty_list(monkeypatch):
    class Broken:
        async def get_all(self, uid):
            raise RuntimeError("db down")

        async def set_enabled(self, *a):
            raise RuntimeError("db down")

    set_store(Broken())
    c = _client(monkeypatch, catalog=[_manifest("alpha")])
    assert c.get("/me/plugins").status_code == 503
    assert c.post("/me/plugins/alpha", json={"enabled": True}).status_code == 503


def test_the_router_is_mounted_by_the_real_app():
    from langgraph_agents.api.main import create_app
    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/me/plugins" in paths and "/me/plugins/{plugin_id}" in paths
