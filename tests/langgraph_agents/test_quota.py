"""Tiers and daily token quota (migration 018, db/quota.py, api/routes_quota.py).

The database side (row-level policies, the user_plan guard trigger) needs a
real Postgres and is not exercised here; these cover the rules in Python and
the route-level guarantees: admin-only routes refuse non-admins, every admin
write is audited, and the role comes from the token's cognito:groups.
"""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

UID = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    import langgraph_agents.api.auth as auth_mod
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_testpool")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "test-client-id")
    monkeypatch.delenv("QUOTA_RESET_TZ", raising=False)
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()
    yield auth_mod
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()


def _app(*, is_admin: bool, email: str = "a@example.com"):
    from langgraph_agents.api.auth import current_user, override_auth_user
    from langgraph_agents.api.routes_quota import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = override_auth_user(UID, email=email, is_admin=is_admin)
    return app


def _mock_pg(conn):
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=conn)
    tx.__aexit__ = AsyncMock(return_value=None)
    pg = MagicMock()
    pg.transaction.return_value = tx
    return pg


def _conn(**kw):
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=kw.get("fetch", []))
    conn.fetchrow = AsyncMock(side_effect=kw.get("fetchrow")) if "fetchrow" in kw else AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=kw.get("fetchval"))
    return conn


def _audited(conn) -> list:
    return [c for c in conn.execute.call_args_list if "INSERT INTO admin_audit" in c.args[0]]


# ── The quota day and the rules ─────────────────────────────────────────

@pytest.mark.unit
class TestQuotaRules:
    def test_day_is_local_vietnam_date(self):
        from langgraph_agents.db.quota import quota_day
        # 18:00 UTC on 31/10 is 01:00 on 01/11 in Ho Chi Minh City.
        assert quota_day(datetime(2026, 10, 31, 18, 0, tzinfo=timezone.utc)) == date(2026, 11, 1)
        assert quota_day(datetime(2026, 10, 31, 16, 59, tzinfo=timezone.utc)) == date(2026, 10, 31)

    def test_next_reset_is_local_midnight_in_utc(self):
        from langgraph_agents.db.quota import next_reset
        reset = next_reset(datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc))
        assert reset == datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)

    def test_reset_zone_is_configurable(self, monkeypatch):
        from langgraph_agents.db.quota import next_reset
        monkeypatch.setenv("QUOTA_RESET_TZ", "UTC")
        assert next_reset(datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)) == \
            datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

    def test_usage_from_another_day_counts_as_zero(self):
        from langgraph_agents.db.quota import used_today
        today = date(2026, 10, 31)
        assert used_today(today, 1500, today) == 1500
        assert used_today(date(2026, 10, 30), 1500, today) == 0
        assert used_today(None, 0, today) == 0

    def test_limit_override_admin_and_plan(self):
        from langgraph_agents.db.quota import effective_limit
        assert effective_limit(20000, None, is_admin=False) == 20000
        assert effective_limit(20000, 5000, is_admin=False) == 5000
        assert effective_limit(20000, 0, is_admin=False) == 0
        assert effective_limit(20000, 5000, is_admin=True) is None

    def test_status_exhausted_and_remaining(self):
        from langgraph_agents.db.quota import QuotaStatus
        now = datetime(2026, 10, 31, tzinfo=timezone.utc)
        assert QuotaStatus("free", 100, 100, now).exhausted
        assert QuotaStatus("free", 100, 40, now).remaining == 60
        unlimited = QuotaStatus("free", None, 10**9, now)
        assert not unlimited.exhausted and unlimited.remaining is None


# ── Role from the token ─────────────────────────────────────────────────

@pytest.mark.unit
class TestRoleFromToken:
    def _auth(self, auth_env, claims):
        from fastapi.security import HTTPAuthorizationCredentials
        with patch.object(auth_env, "_verify_token", return_value=claims):
            return auth_env._authenticate(HTTPAuthorizationCredentials(scheme="Bearer", credentials="t"))

    def test_admin_group_makes_admin_and_binds_role(self, auth_env):
        from langgraph_agents.db.postgres import current_request_role
        user = self._auth(auth_env, {"sub": UID, "email": "x@y.z", "cognito:groups": ["admin"]})
        assert user.is_admin and user.email == "x@y.z"
        assert current_request_role() == "admin"
        assert auth_env.request_is_admin() and auth_env.request_email() == "x@y.z"

    def test_no_group_is_a_user(self, auth_env):
        from langgraph_agents.db.postgres import current_request_role
        user = self._auth(auth_env, {"sub": UID})
        assert not user.is_admin
        assert current_request_role() == "user"

    def test_other_groups_are_not_admin(self, auth_env):
        assert not self._auth(auth_env, {"sub": UID, "cognito:groups": ["admins", "staff"]}).is_admin
        # A string, not a list, is not trusted as a group list.
        assert not self._auth(auth_env, {"sub": UID, "cognito:groups": "admin"}).is_admin


# ── Routes ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestAdminRoutes:
    @pytest.mark.parametrize("method, path", [
        ("get", "/admin/plans"),
        ("post", "/admin/plans"),
        ("post", "/admin/plans/free"),
        ("delete", "/admin/plans/free"),
        ("get", "/admin/users"),
        ("post", f"/admin/users/{OTHER}"),
        ("post", f"/admin/users/{OTHER}/reset"),
        ("get", "/admin/audit"),
    ])
    def test_non_admin_gets_403(self, method, path):
        client = TestClient(_app(is_admin=False))
        kwargs = {"json": {"name": "x", "daily_token_limit": 1}} if method == "post" else {}
        assert getattr(client, method)(path, **kwargs).status_code == 403

    def test_create_plan_is_audited(self):
        conn = _conn(fetchval="clinic", fetchrow=[
            {"name": "clinic", "daily_token_limit": 1_000_000, "is_default": False, "users": 0},
        ])
        with patch("langgraph_agents.api.routes_quota.get_pg_client", return_value=_mock_pg(conn)):
            resp = TestClient(_app(is_admin=True)).post(
                "/admin/plans", json={"name": "clinic", "daily_token_limit": 1_000_000})
        assert resp.status_code == 201
        assert resp.json()["daily_token_limit"] == 1_000_000
        assert len(_audited(conn)) == 1

    def test_plan_name_and_limit_are_validated(self):
        client = TestClient(_app(is_admin=True))
        assert client.post("/admin/plans", json={"name": "Bad Name", "daily_token_limit": 1}).status_code == 422
        assert client.post("/admin/plans", json={"name": "ok", "daily_token_limit": -1}).status_code == 422
        assert client.post("/admin/plans", json={"name": "ok", "daily_token_limit": 10**12}).status_code == 422

    def test_tier_with_users_cannot_be_deleted(self):
        conn = _conn(fetchrow=[{"name": "pro", "daily_token_limit": 100000, "is_default": False, "users": 3}])
        with patch("langgraph_agents.api.routes_quota.get_pg_client", return_value=_mock_pg(conn)):
            resp = TestClient(_app(is_admin=True)).delete("/admin/plans/pro")
        assert resp.status_code == 409
        assert not _audited(conn)

    def test_default_tier_cannot_be_unset(self):
        resp = TestClient(_app(is_admin=True)).post("/admin/plans/free", json={"is_default": False})
        assert resp.status_code == 422

    def test_change_user_tier_is_audited(self):
        before = {"user_id": OTHER, "email": "b@example.com", "plan_name": "free", "limit_override": None,
                  "usage_day": None, "tokens_used": 0, "daily_token_limit": 20000}
        after = {**before, "plan_name": "pro", "daily_token_limit": 100000}
        conn = _conn(fetchval=1, fetchrow=[before, after])
        with patch("langgraph_agents.api.routes_quota.get_pg_client", return_value=_mock_pg(conn)):
            resp = TestClient(_app(is_admin=True)).post(f"/admin/users/{OTHER}", json={"plan_name": "pro"})
        assert resp.status_code == 200
        assert resp.json()["plan"] == "pro" and resp.json()["limit"] == 100000
        assert len(_audited(conn)) == 1

    def test_user_id_must_be_a_uuid(self):
        resp = TestClient(_app(is_admin=True)).post("/admin/users/not-a-uuid", json={"plan_name": "pro"})
        assert resp.status_code == 422


@pytest.mark.unit
def test_my_quota_reports_admin_as_unlimited():
    row = {"plan_name": "free", "limit_override": None, "usage_day": None,
           "tokens_used": 0, "daily_token_limit": 20000}
    conn = _conn(fetchrow=[row])
    with patch("langgraph_agents.db.quota.get_pg_client", return_value=_mock_pg(conn)):
        body = TestClient(_app(is_admin=True)).get("/me/quota").json()
    assert body["limit"] is None and body["is_admin"] is True


# ── /chat charging rules ────────────────────────────────────────────────

@pytest.mark.unit
class TestEcaPaid:
    def test_eca_model_turn_is_charged(self):
        from langgraph_agents.api.main import _eca_paid
        assert _eca_paid(None, {}, error_sent=False)

    def test_byok_turn_is_not_charged(self):
        from langgraph_agents.api.main import _eca_paid
        assert not _eca_paid(MagicMock(failure=None), {"final_answer": "x"}, error_sent=False)

    def test_byok_failure_answered_by_eca_is_charged(self):
        from langgraph_agents.api.main import _eca_paid
        req = MagicMock(failure={"kind": "auth"})
        assert _eca_paid(req, {"final_answer": "x"}, error_sent=False)
        assert not _eca_paid(req, {"final_answer": "x"}, error_sent=True)
