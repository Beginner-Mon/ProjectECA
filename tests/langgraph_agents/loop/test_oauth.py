"""OAuth connectors, stage 5A: the flow against an in-process mock authorization
server (`oauth_fakes.py`). No network, no Google, no database, no LLM.

Owned here (PLAN II.3 stage 5 `test_oauth.py`): a full connect, a reused state, a
wrong `iss`, an expired pending record, refresh with rotation from two concurrent
calls, revoke, tokens absent from logs, user A cannot use user B's connection.
The MCP-client, transcript and taint items belong to card 5B.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from langgraph_agents.loop import crypto
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.connectors import oauth as O
from langgraph_agents.loop.connectors.oauth import (
    AuthExpired, NotConnected, OAuthError, OAuthService,
)
from langgraph_agents.loop.connectors.store import (
    ACTIVE, AUTH_EXPIRED, MemoryConnectionStore, MemoryPendingStore,
    connection_context, pending_context,
)
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.plugins.manifest import Manifest
from tests.langgraph_agents.loop.oauth_fakes import (
    AUTH_HOST, CLIENT_SECRET, ISSUER, MCP_HOST, REDIRECT_URI, MockAuthServer,
    fake_secret_fetcher, manifest_dict,
)

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
PLUGIN = "mock-calendar"


class Env:
    """One service wired to one mock server and memory stores."""

    def __init__(self, server: MockAuthServer, *, manifest: dict | None = None,
                 settings: dict | None = None, sealer: bool = True,
                 policy: AddressPolicy | None = None, fetcher=fake_secret_fetcher) -> None:
        self.server = server
        self.now = datetime.now(timezone.utc)
        self.connections = MemoryConnectionStore()
        self.pending = MemoryPendingStore()
        self.sealer = crypto.LocalSealer(os.urandom(32)) if sealer else None
        self.manifest = Manifest.model_validate(manifest or manifest_dict())
        base = dict(oauth_redirect_uri=REDIRECT_URI, env_name="test")
        base.update(settings or {})
        self.service = OAuthService(
            connections=self.connections, pending=self.pending,
            settings=LoopSettings(**base), sealer=self.sealer,
            catalog=lambda pid: self.manifest if pid == self.manifest.id else None,
            transport=server.transport(), policy=policy or AddressPolicy.for_tests(),
            secret_fetcher=fetcher, clock=lambda: self.now)

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    async def connect(self, user: str = USER_A) -> tuple[dict, str]:
        """Run start -> browser -> complete. Returns (callback params, authorize url)."""
        url = await self.service.start(user, PLUGIN)
        params = await self.server.browser(url)
        await self.service.complete(user, params["code"], params["state"], params.get("iss"))
        return params, url


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("LOOP_DEV_CLIENT_SECRET_MOCK_CALENDAR", raising=False)


def _q(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


async def _code(env: Env, user: str = USER_A):
    url = await env.service.start(user, PLUGIN)
    return url, await env.server.browser(url)


# ── a full connect ────────────────────────────────────────────────────────

async def test_a_full_connect_works():
    env = Env(MockAuthServer())
    params, url = await env.connect()

    q = _q(url)
    assert url.startswith(f"{ISSUER}/authorize?")
    assert q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256"
    assert q["redirect_uri"] == REDIRECT_URI
    assert q["resource"] == "https://mcp.test/mcp"        # RFC 8707 on the authorize request
    assert q["scope"] == "calendar.read calendar.events"
    assert q["access_type"] == "offline"                   # manifest extra param

    conn = await env.connections.get(USER_A, PLUGIN)
    assert conn is not None and conn.status == ACTIVE
    assert conn.scopes == ["calendar.read", "calendar.events"]
    assert conn.issuer == ISSUER and conn.expires_at is not None
    token = await env.service.access_token(USER_A, PLUGIN)
    assert token.startswith("MOCK-access-")

    exchange = env.server.token_requests[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["resource"] == "https://mcp.test/mcp"  # RFC 8707 on the token request
    assert exchange["redirect_uri"] == REDIRECT_URI
    assert "code_verifier" in exchange


async def test_tokens_are_sealed_and_bound_to_user_plugin_and_column():
    env = Env(MockAuthServer())
    await env.connect()
    conn = await env.connections.get(USER_A, PLUGIN)
    assert b"MOCK-" not in conn.access_enc and b"MOCK-" not in conn.refresh_enc
    opened = await env.sealer.open(conn.access_enc, connection_context(USER_A, PLUGIN, "access"))
    assert opened.startswith(b"MOCK-access-")
    for wrong in (connection_context(USER_B, PLUGIN, "access"),
                  connection_context(USER_A, "other", "access"),
                  connection_context(USER_A, PLUGIN, "refresh")):
        with pytest.raises(crypto.SealError):
            await env.sealer.open(conn.access_enc, wrong)


async def test_extra_authorize_params_cannot_override_scope_or_challenge():
    over = manifest_dict()
    over["auth"]["authorize_extra_params"] = {
        "scope": "https://evil/all", "code_challenge": "x", "prompt": "consent",
        "request_uri": "https://evil/r"}
    env = Env(MockAuthServer(), manifest=over)
    q = _q(await env.service.start(USER_A, PLUGIN))
    assert q["scope"] == "calendar.read calendar.events"   # from the manifest only
    assert q["code_challenge"] != "x" and q["prompt"] == "consent"
    assert "request_uri" not in q


async def test_state_is_stored_only_as_its_hash_and_the_verifier_is_sealed():
    env = Env(MockAuthServer())
    url = await env.service.start(USER_A, PLUGIN)
    q = _q(url)
    digest = hashlib.sha256(q["state"].encode()).hexdigest()
    assert list(env.pending._rows) == [digest]
    row = env.pending._rows[digest]
    assert q["state"].encode() not in (row.verifier_enc or b"")
    assert row.expires_at - env.now == timedelta(seconds=O.STATE_TTL_S)
    verifier = (await env.sealer.open(
        row.verifier_enc, pending_context(USER_A, PLUGIN, digest))).decode()
    assert len(verifier) >= 43
    import base64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"]                # PKCE S256
    with pytest.raises(crypto.SealError):                  # bound to the state hash
        await env.sealer.open(row.verifier_enc, pending_context(USER_A, PLUGIN, "0" * 64))
    assert q["state"] not in repr(env.pending._rows)       # the raw state is nowhere


async def test_state_and_verifier_are_fresh_each_time():
    env = Env(MockAuthServer())
    a, b = _q(await env.service.start(USER_A, PLUGIN)), _q(await env.service.start(USER_A, PLUGIN))
    assert a["state"] != b["state"] and a["code_challenge"] != b["code_challenge"]
    assert len(a["state"]) >= 43


# ── state, iss, expiry ────────────────────────────────────────────────────

async def test_a_reused_state_is_refused():
    env = Env(MockAuthServer())
    params, _ = await env.connect()
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert err.value.code == "invalid_state"
    assert len(env.server.token_requests) == 1             # no second exchange


async def test_two_callbacks_with_one_state_have_one_winner():
    env = Env(MockAuthServer())
    _, params = await _code(env)
    results = await asyncio.gather(
        *[env.service.complete(USER_A, params["code"], params["state"], params["iss"])
          for _ in range(2)], return_exceptions=True)
    assert sum(isinstance(r, O.ConnectionInfo) for r in results) == 1
    assert sum(isinstance(r, OAuthError) and r.code == "invalid_state" for r in results) == 1


async def test_a_wrong_iss_is_refused_and_the_state_is_burned():
    env = Env(MockAuthServer())
    _, params = await _code(env)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], "https://evil.test")
    assert err.value.code == "issuer_mismatch"
    assert env.server.token_requests == []                 # the code was never exchanged
    assert await env.connections.get(USER_A, PLUGIN) is None
    with pytest.raises(OAuthError) as again:               # replay with the right iss
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert again.value.code == "invalid_state"


async def test_a_missing_iss_is_refused_when_the_server_advertises_it():
    env = Env(MockAuthServer(advertise_iss=True))
    _, params = await _code(env)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], None)
    assert err.value.code == "iss_missing"
    assert env.server.token_requests == []


async def test_a_missing_iss_is_fine_when_the_server_does_not_advertise_it():
    env = Env(MockAuthServer(advertise_iss=False, send_iss=False))
    _, params = await _code(env)
    assert "iss" not in params
    info = await env.service.complete(USER_A, params["code"], params["state"], None)
    assert info.status == ACTIVE


async def test_a_wrong_iss_is_refused_even_when_not_advertised():
    env = Env(MockAuthServer(advertise_iss=False, send_iss=True, iss_value="https://evil.test"))
    _, params = await _code(env)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert err.value.code == "issuer_mismatch"


async def test_an_expired_pending_record_is_refused():
    env = Env(MockAuthServer())
    _, params = await _code(env)
    env.advance(O.STATE_TTL_S + 1)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert err.value.code == "invalid_state"
    assert env.server.token_requests == []


async def test_a_state_still_works_just_before_it_expires():
    env = Env(MockAuthServer())
    _, params = await _code(env)
    env.advance(O.STATE_TTL_S - 5)
    info = await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert info.status == ACTIVE


async def test_another_users_state_is_refused_and_does_not_burn_the_owners():
    env = Env(MockAuthServer())
    _, params = await _code(env, USER_A)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_B, params["code"], params["state"], params["iss"])
    assert err.value.code == "invalid_state"
    assert await env.connections.get(USER_B, PLUGIN) is None
    info = await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert info.status == ACTIVE


@pytest.mark.parametrize("code,state", [("", "s"), ("c", ""), ("c" * 5000, "s"), ("c", "s" * 5000)])
async def test_malformed_callback_fields_are_refused(code, state):
    env = Env(MockAuthServer())
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, code, state, None)
    assert err.value.code == "invalid_state"


async def test_a_code_from_another_flow_fails_pkce():
    """The mock checks the verifier against the challenge of the code's own flow."""
    env = Env(MockAuthServer())
    _, first = await _code(env)
    _, second = await _code(env)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, first["code"], second["state"], second["iss"])
    assert err.value.code == "token_exchange_failed"
    assert await env.connections.get(USER_A, PLUGIN) is None


# ── discovery fails closed ────────────────────────────────────────────────

async def test_metadata_that_names_another_issuer_is_refused():
    env = Env(MockAuthServer(metadata_issuer="https://evil.test"))
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "issuer_mismatch"
    assert env.pending._rows == {}


async def test_a_server_without_pkce_s256_is_refused():
    for methods in ([], ["plain"]):
        env = Env(MockAuthServer(pkce_methods=methods))
        with pytest.raises(OAuthError) as err:
            await env.service.start(USER_A, PLUGIN)
        assert err.value.code == "discovery_failed"


async def test_discovery_can_start_from_the_manifest_issuer():
    m = manifest_dict()
    m["auth"]["issuer"] = ISSUER
    env = Env(MockAuthServer(), manifest=m)
    assert (await env.service.start(USER_A, PLUGIN)).startswith(ISSUER)


async def test_an_authorization_server_outside_allowed_hosts_is_blocked():
    m = manifest_dict()
    m["auth"]["issuer"] = ISSUER
    m["mcp_server"]["allowed_hosts"] = [MCP_HOST]          # auth.test is not listed
    env = Env(MockAuthServer(), manifest=m)
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "egress_blocked"


async def test_the_protected_resource_must_list_an_allowed_authorization_server():
    m = manifest_dict()
    m["mcp_server"]["allowed_hosts"] = [MCP_HOST]
    env = Env(MockAuthServer(), manifest=m)
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "discovery_failed"


async def test_a_private_address_for_the_auth_server_is_blocked():
    env = Env(MockAuthServer(), policy=AddressPolicy.for_tests({AUTH_HOST: ["10.0.0.5"]}))
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "egress_blocked"


# ── what start refuses ────────────────────────────────────────────────────

async def test_start_refuses_unknown_gated_and_non_oauth_plugins():
    env = Env(MockAuthServer())
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, "nope")
    assert err.value.code == "unknown_plugin"

    gated = Env(MockAuthServer(), manifest=manifest_dict(requires_gate=True))
    with pytest.raises(OAuthError) as err:                 # google_enabled is False
        await gated.service.start(USER_A, PLUGIN)
    assert err.value.code == "not_available"
    allowed = Env(MockAuthServer(), manifest=manifest_dict(requires_gate=True),
                  settings={"google_enabled": True})
    assert (await allowed.service.start(USER_A, PLUGIN)).startswith(ISSUER)

    plain = Env(MockAuthServer(), manifest=manifest_dict(auth=None))
    with pytest.raises(OAuthError) as err:
        await plain.service.start(USER_A, PLUGIN)
    assert err.value.code == "not_available"

    dcr = manifest_dict()
    dcr["auth"]["client_registration"] = "dynamic"
    with pytest.raises(OAuthError) as err:
        await Env(MockAuthServer(), manifest=dcr).service.start(USER_A, PLUGIN)
    assert err.value.code == "unsupported_registration"


async def test_start_needs_a_redirect_uri_and_a_sealer():
    env = Env(MockAuthServer(), settings={"oauth_redirect_uri": None})
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "not_configured"

    env = Env(MockAuthServer(), sealer=False)              # db_guard: no global sealer either
    with pytest.raises(OAuthError) as err:
        await env.service.start(USER_A, PLUGIN)
    assert err.value.code == "sealer_unavailable"
    assert env.pending._rows == {}


# ── client credentials ────────────────────────────────────────────────────

async def test_the_client_secret_comes_from_secrets_manager_basic_or_post():
    for methods in (["client_secret_basic"], ["client_secret_post"]):
        server = MockAuthServer(auth_methods=methods)
        env = Env(server)
        await env.connect()
        assert (await env.connections.get(USER_A, PLUGIN)).status == ACTIVE
        form = server.token_requests[0]
        assert ("client_secret" in form) == (methods == ["client_secret_post"])


async def test_a_wrong_client_secret_fails_the_exchange():
    async def wrong(name):
        return "not-the-secret"
    env = Env(MockAuthServer(), fetcher=wrong)
    _, params = await _code(env)
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert err.value.code == "token_exchange_failed"


async def test_a_public_client_sends_client_id_and_no_secret():
    m = manifest_dict()
    del m["auth"]["client_secret_ref"]
    server = MockAuthServer(client_secret=None)
    env = Env(server, manifest=m)
    await env.connect()
    assert server.token_requests[0]["client_id"] == "eca-client"
    assert "client_secret" not in server.token_requests[0]


async def test_dev_secret_override_works_off_lambda_and_is_ignored_on_lambda(monkeypatch):
    calls = []

    async def fetcher(name):
        calls.append(name)
        return CLIENT_SECRET
    monkeypatch.setenv("LOOP_DEV_CLIENT_SECRET_MOCK_CALENDAR", CLIENT_SECRET)
    env = Env(MockAuthServer(), fetcher=fetcher)
    await env.connect()
    assert calls == []                                     # the override answered

    # On Lambda the override is ignored and Secrets Manager answers. The Env (and its
    # LocalSealer) is built first: LocalSealer itself refuses to exist on Lambda.
    monkeypatch.setenv("LOOP_DEV_CLIENT_SECRET_MOCK_CALENDAR", "wrong-dev-secret")
    env2 = Env(MockAuthServer(), fetcher=fetcher)
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "vva-agent")
    secret = await env2.service._client_secret(env2.manifest)
    assert secret == CLIENT_SECRET
    assert calls == ["vva/test/plugins/mock-calendar"]


async def test_a_secret_ref_with_env_needs_env_name_and_fetch_errors_do_not_leak():
    env = Env(MockAuthServer(), settings={"env_name": None})
    with pytest.raises(OAuthError) as err:
        await env.service._client_secret(env.manifest)
    assert err.value.code == "not_configured"

    async def boom(name):
        raise RuntimeError("secret-value-in-sdk-message")
    env = Env(MockAuthServer(), fetcher=boom)
    with pytest.raises(OAuthError) as err:
        await env.service._client_secret(env.manifest)
    assert "secret-value" not in str(err.value) and err.value.__cause__ is None


# ── refresh (F7, F8) ──────────────────────────────────────────────────────

async def _stale_connect(server: MockAuthServer | None = None) -> Env:
    """Connected, then the access token is within the 60 s skew of expiry."""
    server = server or MockAuthServer(expires_in=30)
    env = Env(server)
    await env.connect()
    server.expires_in = 3600                               # tokens issued from now on last
    return env


async def test_a_fresh_token_is_not_refreshed():
    env = Env(MockAuthServer(expires_in=3600))
    await env.connect()
    await env.service.access_token(USER_A, PLUGIN)
    assert env.server.refresh_calls == 0


async def test_refresh_rotates_the_refresh_token():
    env = await _stale_connect()
    before = await env.connections.get(USER_A, PLUGIN)
    old_refresh = (await env.sealer.open(
        before.refresh_enc, connection_context(USER_A, PLUGIN, "refresh"))).decode()
    token = await env.service.access_token(USER_A, PLUGIN)
    assert env.server.refresh_calls == 1
    after = await env.connections.get(USER_A, PLUGIN)
    new_refresh = (await env.sealer.open(
        after.refresh_enc, connection_context(USER_A, PLUGIN, "refresh"))).decode()
    assert new_refresh != old_refresh and new_refresh == env.server.last_refresh_token()
    assert env.server.refresh_tokens[old_refresh] is False
    assert token.startswith("MOCK-access-") and after.expires_at > env.now + timedelta(minutes=30)
    assert env.server.token_requests[-1]["resource"] == "https://mcp.test/mcp"


async def test_two_concurrent_calls_with_an_expiring_token_refresh_once_and_both_succeed():
    server = MockAuthServer(expires_in=30, refresh_delay_s=0.05)
    env = await _stale_connect(server)
    tokens = await asyncio.gather(*[env.service.access_token(USER_A, PLUGIN) for _ in range(2)])
    assert server.refresh_calls == 1                       # the loser re-read after the lock
    assert tokens[0] == tokens[1] and tokens[0].startswith("MOCK-access-")
    conn = await env.connections.get(USER_A, PLUGIN)
    assert conn.status == ACTIVE                           # nobody fell into auth_expired


async def test_five_concurrent_refresh_calls_refresh_once():
    server = MockAuthServer(expires_in=30, refresh_delay_s=0.05)
    env = await _stale_connect(server)
    results = await asyncio.gather(*[env.service.refresh(USER_A, PLUGIN) for _ in range(5)])
    assert server.refresh_calls == 1
    assert len({r.access_enc for r in results}) == 1


async def test_a_provider_that_does_not_rotate_keeps_the_old_refresh_token():
    env = await _stale_connect(MockAuthServer(expires_in=30, rotate=False))
    before = (await env.connections.get(USER_A, PLUGIN)).refresh_enc
    await env.service.access_token(USER_A, PLUGIN)
    assert (await env.connections.get(USER_A, PLUGIN)).refresh_enc == before


async def test_a_refused_refresh_marks_auth_expired_and_drops_the_tokens():
    env = await _stale_connect()
    env.server.expire_refresh_tokens()
    with pytest.raises(AuthExpired):
        await env.service.access_token(USER_A, PLUGIN)
    conn = await env.connections.get(USER_A, PLUGIN)
    assert conn.status == AUTH_EXPIRED
    assert conn.access_enc is None and conn.refresh_enc is None
    calls = env.server.refresh_calls
    with pytest.raises(AuthExpired):                       # no new attempt against the provider
        await env.service.access_token(USER_A, PLUGIN)
    assert env.server.refresh_calls == calls
    infos = await env.service.list_connections(USER_A)
    assert [(i.plugin_id, i.status) for i in infos] == [(PLUGIN, AUTH_EXPIRED)]


async def test_reconnecting_replaces_an_expired_connection():
    env = await _stale_connect()
    env.server.expire_refresh_tokens()
    with pytest.raises(AuthExpired):
        await env.service.access_token(USER_A, PLUGIN)
    await env.connect()
    assert (await env.connections.get(USER_A, PLUGIN)).status == ACTIVE
    assert (await env.service.access_token(USER_A, PLUGIN)).startswith("MOCK-access-")


async def test_a_provider_outage_during_refresh_is_transient_and_changes_nothing():
    env = await _stale_connect()
    before = await env.connections.get(USER_A, PLUGIN)
    env.server.fail_token_endpoint_with = 503
    with pytest.raises(OAuthError) as err:
        await env.service.access_token(USER_A, PLUGIN)
    assert err.value.code == "transient" and not isinstance(err.value, AuthExpired)
    after = await env.connections.get(USER_A, PLUGIN)
    assert after.status == ACTIVE and after.refresh_enc == before.refresh_enc
    env.server.fail_token_endpoint_with = None            # and it recovers
    assert (await env.service.access_token(USER_A, PLUGIN)).startswith("MOCK-access-")


async def test_access_token_without_a_connection_raises_not_connected():
    env = Env(MockAuthServer())
    with pytest.raises(NotConnected):
        await env.service.access_token(USER_A, PLUGIN)
    with pytest.raises(NotConnected):
        await env.service.refresh(USER_A, PLUGIN)


async def test_a_tampered_stored_token_is_unreadable_not_returned():
    env = Env(MockAuthServer())
    await env.connect()
    conn = await env.connections.get(USER_A, PLUGIN)
    other = await env.sealer.seal(b"MOCK-forged", connection_context(USER_B, PLUGIN, "access"))
    await env.connections.upsert(dataclasses.replace(conn, access_enc=other))
    with pytest.raises(OAuthError) as err:
        await env.service.access_token(USER_A, PLUGIN)
    assert err.value.code == "token_unreadable"


# ── users ─────────────────────────────────────────────────────────────────

async def test_user_a_cannot_use_user_bs_connection():
    env = Env(MockAuthServer())
    await env.connect(USER_B)
    with pytest.raises(NotConnected):
        await env.service.access_token(USER_A, PLUGIN)
    with pytest.raises(NotConnected):
        await env.service.refresh(USER_A, PLUGIN)
    assert await env.service.list_connections(USER_A) == []
    assert await env.service.revoke(USER_A, PLUGIN) is False
    assert (await env.connections.get(USER_B, PLUGIN)).status == ACTIVE   # untouched
    # And a blob copied from B's row into A's row does not open (context binds the user).
    b_row = await env.connections.get(USER_B, PLUGIN)
    await env.connections.upsert(dataclasses.replace(b_row, user_id=USER_A))
    with pytest.raises(OAuthError) as err:
        await env.service.access_token(USER_A, PLUGIN)
    assert err.value.code == "token_unreadable"


# ── revoke (F9) ───────────────────────────────────────────────────────────

async def test_revoke_calls_the_provider_and_deletes_the_row():
    env = Env(MockAuthServer())
    await env.connect()
    conn = await env.connections.get(USER_A, PLUGIN)
    refresh = (await env.sealer.open(
        conn.refresh_enc, connection_context(USER_A, PLUGIN, "refresh"))).decode()
    assert await env.service.revoke(USER_A, PLUGIN) is True
    assert env.server.revoked == [refresh]
    assert env.server.refresh_tokens[refresh] is False
    assert await env.connections.get(USER_A, PLUGIN) is None
    assert await env.service.revoke(USER_A, PLUGIN) is False


async def test_revoke_deletes_the_row_when_there_is_no_revocation_endpoint():
    env = Env(MockAuthServer(with_revocation=False))
    await env.connect()
    assert await env.service.revoke(USER_A, PLUGIN) is True
    assert env.server.revoked == []
    assert await env.connections.get(USER_A, PLUGIN) is None


async def test_revoke_deletes_the_row_when_the_provider_is_down():
    env = Env(MockAuthServer())
    await env.connect()
    env.manifest = Manifest.model_validate(manifest_dict(id=PLUGIN))
    env.service._catalog = lambda pid: (_ for _ in ()).throw(RuntimeError("boom"))
    assert await env.service.revoke(USER_A, PLUGIN) is True
    assert await env.connections.get(USER_A, PLUGIN) is None


async def test_revoke_works_on_an_expired_connection():
    env = await _stale_connect()
    env.server.expire_refresh_tokens()
    with pytest.raises(AuthExpired):
        await env.service.access_token(USER_A, PLUGIN)
    assert await env.service.revoke(USER_A, PLUGIN) is True
    assert env.server.revoked == []                        # no token left to revoke


# ── nothing secret leaves ─────────────────────────────────────────────────

async def test_no_token_verifier_code_or_secret_appears_in_logs_errors_or_returns(caplog):
    caplog.set_level(logging.DEBUG)
    server = MockAuthServer(expires_in=30)
    env = Env(server)
    seen: list[str] = []

    url = await env.service.start(USER_A, PLUGIN)
    q = _q(url)
    digest = hashlib.sha256(q["state"].encode()).hexdigest()
    verifier = (await env.sealer.open(
        env.pending._rows[digest].verifier_enc, pending_context(USER_A, PLUGIN, digest))).decode()
    params = await server.browser(url)
    info = await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    seen.append(repr(info) + repr(dataclasses.asdict(info)))

    # Failure paths, collecting what their exceptions say.
    for coro in (
        env.service.complete(USER_A, params["code"], params["state"], params["iss"]),   # replay
        env.service.complete(USER_A, "c", "s", "https://evil.test"),
    ):
        with pytest.raises(OAuthError) as err:
            await coro
        seen.append(str(err.value) + repr(err.value))

    server.expires_in = 3600
    token = await env.service.access_token(USER_A, PLUGIN)                       # refreshes
    server.fail_token_endpoint_with = 503
    env.advance(7200)
    with pytest.raises(OAuthError) as err:
        await env.service.access_token(USER_A, PLUGIN)
    seen.append(str(err.value) + repr(err.value))
    server.fail_token_endpoint_with = None
    server.expire_refresh_tokens()
    with pytest.raises(AuthExpired) as err:
        await env.service.access_token(USER_A, PLUGIN)
    seen.append(str(err.value) + repr(err.value))
    await env.service.revoke(USER_A, PLUGIN)

    secrets_ = [q["state"], verifier, token, CLIENT_SECRET, params["code"],
                *server.all_issued_secrets()]
    haystack = caplog.text + "\n".join(seen)
    for secret in secrets_:
        assert secret not in haystack, secret[:12]
    assert "MOCK-" not in haystack
    assert "oauth_connected plugin=mock-calendar" in caplog.text   # the logging did run


async def test_a_bad_token_response_does_not_echo_the_body(caplog):
    caplog.set_level(logging.DEBUG)
    server = MockAuthServer()
    env = Env(server)
    _, params = await _code(env)
    # Make the mock answer 200 with JSON that is not a token, holding a marker.
    original = server.app.router.routes
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def bad(request):
        return JSONResponse({"access_token": 12345, "secret_marker": "MOCK-leak-me"})
    server.app.router.routes = [r if r.path != "/token" else Route("/token", bad, methods=["POST"])
                                for r in original]
    with pytest.raises(OAuthError) as err:
        await env.service.complete(USER_A, params["code"], params["state"], params["iss"])
    assert err.value.code == "bad_token_response"
    assert "MOCK-leak-me" not in caplog.text + str(err.value) + repr(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


# ── the module-level service ──────────────────────────────────────────────

def test_get_oauth_service_is_a_singleton_that_set_service_replaces():
    O.set_service(None)
    a = O.get_oauth_service()
    assert a is O.get_oauth_service()
    sentinel = OAuthService(settings=LoopSettings())
    O.set_service(sentinel)
    assert O.get_oauth_service() is sentinel
    O.set_service(None)
