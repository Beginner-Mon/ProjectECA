"""Account erase (GDPR): `db.gdpr.delete_user` and the `DELETE /me` route.

Every patch goes through `monkeypatch`, inside the test, so nothing outlives it.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from langgraph_agents.api.auth import current_user_id, override_user
from langgraph_agents.api.main import create_app
from langgraph_agents.db import gdpr as gdpr_module

USER = "user-123"
ERASE_TABLES = (
    "oauth_pending", "user_connections", "rate_counters", "agent_leases",
    "user_plugins", "loop_turns", "agent_actions",
)


class _Conn:
    def __init__(self, statements, in_user_scope):
        self._statements = statements
        self._in_user_scope = in_user_scope

    async def execute(self, sql, *args):
        self._statements.append((sql, args, self._in_user_scope))

    async def fetchval(self, sql, *args):
        self._statements.append((sql, args, self._in_user_scope))
        return 0

    async def fetch(self, sql, *args):
        self._statements.append((sql, args, self._in_user_scope))
        return []


class _FakePg:
    """Records `(sql, args, in_user_scope)` for every statement."""

    def __init__(self):
        self.statements = []
        self.outside = _Conn(self.statements, False)

    async def connect(self):
        return None

    @asynccontextmanager
    async def user_scope(self, user_id):
        yield _Conn(self.statements, True)

    async def fetch(self, sql, *args):
        return await self.outside.fetch(sql, *args)


def test_delete_user_runs_every_statement_in_user_scope(monkeypatch):
    fake = _FakePg()
    monkeypatch.setattr(gdpr_module, "get_pg_client", lambda: fake)

    asyncio.run(gdpr_module.delete_user(USER))

    assert all(in_scope for _, _, in_scope in fake.statements), fake.statements
    for table in ERASE_TABLES:
        deletes = [s for s, _, _ in fake.statements if s.startswith(f"DELETE FROM {table} ")]
        assert len(deletes) == 1, table
    counts = [s for s, _, _ in fake.statements if "COUNT" in s and "conversations" in s]
    assert len(counts) == 1
    users = [s for s, _, _ in fake.statements if s.startswith("DELETE FROM users")]
    assert len(users) == 1


class _FakeService:
    def __init__(self, events, plugin_ids, failing=(), list_error=None):
        self._events = events
        self._plugin_ids = plugin_ids
        self._failing = failing
        self._list_error = list_error

    async def list_connections(self, user_id):
        if self._list_error:
            raise self._list_error
        return [SimpleNamespace(plugin_id=p) for p in self._plugin_ids]

    async def revoke(self, user_id, plugin_id):
        if plugin_id in self._failing:
            raise RuntimeError("provider down")
        self._events.append(("revoke", plugin_id))


@pytest.fixture()
def erase_client(monkeypatch, caplog):
    """A client for `DELETE /me` plus the shared event list the fakes write to."""
    events: list = []
    stm_deleted: list = []

    class _Stm:
        async def delete(self, sid):
            stm_deleted.append(sid)

    async def _delete_user(uid):
        events.append(("delete", uid))
        return {"deleted": True, "user_id": uid}

    monkeypatch.setenv("ENABLE_GDPR_ROUTES", "true")
    monkeypatch.setattr("langgraph_agents.api.main.get_pg_client", lambda: _FakePg())
    monkeypatch.setattr("langgraph_agents.api.main.get_stm", lambda: _Stm())
    monkeypatch.setattr("langgraph_agents.db.gdpr.delete_user", _delete_user)

    caplog.set_level(logging.WARNING, logger="langgraph.api")

    def install(service):
        monkeypatch.setattr(
            "langgraph_agents.loop.connectors.oauth.get_oauth_service", lambda: service)
        app = create_app()
        app.dependency_overrides[current_user_id] = override_user(USER)
        return TestClient(app)

    return install, events


def test_route_revokes_every_connection_before_erase(erase_client):
    install, events = erase_client
    client = install(_FakeService(events, ["gmail", "google-calendar"]))

    resp = client.delete("/me")

    assert resp.status_code == 200, resp.text
    assert sorted(e for e in events if e[0] == "revoke") == [
        ("revoke", "gmail"), ("revoke", "google-calendar")]
    assert events[-1] == ("delete", USER)
    assert [e[0] for e in events] == ["revoke", "revoke", "delete"]


def test_route_failing_revoke_is_logged_and_erase_still_runs(erase_client, caplog):
    install, events = erase_client
    client = install(_FakeService(events, ["failing-plugin", "gmail"], failing=("failing-plugin",)))

    resp = client.delete("/me")

    assert resp.status_code == 200, resp.text
    assert events == [("revoke", "gmail"), ("delete", USER)]
    [record] = [r for r in caplog.records if r.getMessage() == "gdpr_revoke_failed"]
    assert record.levelno == logging.WARNING
    assert record.plugin_id == "failing-plugin"
    assert record.error == "RuntimeError"


def test_route_failing_list_is_logged_and_erase_still_runs(erase_client, caplog):
    install, events = erase_client
    client = install(_FakeService(events, [], list_error=ConnectionError("down")))

    resp = client.delete("/me")

    assert resp.status_code == 200, resp.text
    assert events == [("delete", USER)]
    [record] = [r for r in caplog.records if r.getMessage() == "gdpr_revoke_list_failed"]
    assert record.levelno == logging.WARNING
    assert record.error == "ConnectionError"
