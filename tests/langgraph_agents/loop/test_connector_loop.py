"""A connector in the real loop (stage 5B).

What 5A built at service level, seen through the pieces the turn uses: the
plugin runtime's listing (a user token at listing time), the tools node's calls
(a user token at call time), `auth_expired` when a token dies (F8), the
per-user definitions cache from the GĐ2 review, taint rule 1 on the marks the
tools node writes, and the shipped Google manifests (F11). The MCP server is
the bearer-checking fake in `oauth_fakes.ConnectorWorld`; no network, no DB,
no model.

Every token travels from the service to the wire and nowhere else (F6); a
failure to present one is the USER's connection fault, never the plugin's
breaker (F8).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from langgraph_agents.loop import crypto, gate, turns
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.connectors import oauth as O
from langgraph_agents.loop.connectors.store import (
    MemoryConnectionStore, MemoryPendingStore,
)
from langgraph_agents.loop.graph import build_loop_graph
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.limits import new_turn
from langgraph_agents.loop.mcp_client import McpClient, definitions_sha256
from langgraph_agents.loop.plugins import runtime
from langgraph_agents.loop.plugins.manifest import Manifest, load_catalog
from langgraph_agents.loop.plugins.store import (
    MemoryPluginStore, set_store as set_plugin_store,
)
from langgraph_agents.loop.rate import MemoryRateCounter, set_store as set_rate_store
from langgraph_agents.loop.tools.registry import ToolSpec
from tests.langgraph_agents.loop.fakes import AgentReply, FakeAgentModel
from tests.langgraph_agents.loop.oauth_fakes import (
    AUTH_HOST, ISSUER, MCP_HOST, MCP_URL, ConnectorWorld, MockAuthServer,
    REDIRECT_URI, fake_secret_fetcher, manifest_dict,
)

PLUGIN = "mock-calendar"
USER_A = "u1"          # connection ids are opaque strings to the stores
USER_B = "u2"
PIN_USER = "pin"       # a third user, only ever used to compute the manifest pin
THREAD = "7e3d1f9a-8b2c-4d0e-b1a2-c3d4e5f60718"
EVENTS_TEXT = "3 events: physio at 09:00, walk at 12:00, swim at 17:00."


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No Lambda, no dev secret override; fresh runtime, stores and service
    per test (the suite's db_guard resets the module-level connector stores)."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.delenv("LOOP_DEV_CLIENT_SECRET_MOCK_CALENDAR", raising=False)
    import langgraph_agents.loop.graph as graph_mod
    import langgraph_agents.loop.persona as persona_mod
    monkeypatch.setattr(graph_mod, "memory_node", lambda state, config: {})
    monkeypatch.setattr(graph_mod, "vision_node", lambda state, config: {})
    monkeypatch.setattr(persona_mod, "current_verdict",
                        lambda config: SimpleNamespace(level="routine"))
    runtime.reset_runtime()
    set_plugin_store(MemoryPluginStore())
    set_rate_store(MemoryRateCounter())
    O.set_service(None)
    yield
    O.set_service(None)
    runtime.reset_runtime()
    set_plugin_store(None)


# ── the environment ───────────────────────────────────────────────────────


class _WorldEnv:
    """Mock authorization server + bearer-checking MCP server, wired to the
    OAuth service the loop's connector code actually reads
    (`get_oauth_service`)."""

    def __init__(self, world: ConnectorWorld, *, settings: dict | None = None,
                 extra_catalog: dict[str, Manifest] | None = None) -> None:
        self.world = world
        self.now = datetime.now(timezone.utc)
        self.connections = MemoryConnectionStore()
        self.pending = MemoryPendingStore()
        self.sealer = crypto.LocalSealer(os.urandom(32))
        self.manifest = Manifest.model_validate(manifest_dict())
        extra = extra_catalog or {}
        self.extra_catalog = extra
        base = dict(oauth_redirect_uri=REDIRECT_URI, env_name="test",
                    plugins_disabled=False)
        base.update(settings or {})
        self.settings = LoopSettings(**base)
        self.service = O.OAuthService(
            connections=self.connections, pending=self.pending,
            settings=self.settings, sealer=self.sealer,
            catalog=lambda pid: (self.manifest if pid == self.manifest.id
                                 else extra.get(pid)),
            transport=world.transport(), policy=AddressPolicy.for_tests(),
            secret_fetcher=fake_secret_fetcher, clock=lambda: self.now)
        O.set_service(self.service)

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    async def connect(self, user: str = USER_A) -> None:
        url = await self.service.start(user, PLUGIN)
        params = await self.world.auth.browser(url)
        await self.service.complete(user, params["code"], params["state"],
                                    params.get("iss"))


async def _connected_and_pinned(env: _WorldEnv, user: str,
                                manifest: Manifest | None = None,
                                ) -> tuple[str, str]:
    """Connect `user`, name its tokens after the user, and return `(pin,
    token)`: the pin is the digest of the server's listing under that user's
    own token. When `manifest` is given, the same fetch is seeded into the
    runtime's per-user defs cache (the raw McpClient here fills nothing), so
    a prepared turn for `user` serves the cache instead of listing again."""
    await env.connect(user)
    env.world.label_new_tokens(user)
    token = await env.service.access_token(user, PLUGIN)
    async with env.world.running():
        client = McpClient(MCP_URL, [MCP_HOST, AUTH_HOST],
                           policy=AddressPolicy.for_tests(),
                           transport=env.world.transport())
        try:
            tools = await client.list_tools(token=token)
        finally:
            await client.close()
    sha = definitions_sha256(tools)
    if manifest is not None:
        runtime._defs_cache[runtime.defs_key(manifest, user)] = runtime._Defs(
            runtime._clock(), manifest.resolved_url(), tools, sha)
    return sha, token


async def _pin_by_stranger(env: _WorldEnv) -> str:
    """The manifest pin, computed by a user who then has no connection left."""
    pin, _token = await _connected_and_pinned(env, PIN_USER)
    await env.service.revoke(PIN_USER, PLUGIN)
    return pin


def _pinned_manifest(pin: str, **over) -> Manifest:
    return Manifest.model_validate(manifest_dict(
        enabled_by_default=True,
        reviewed={"by": "tests", "tool_definitions_sha256": pin},
        **over))


async def _warmed_prepare(env: _WorldEnv, manifest: Manifest, request_id: str,
                          user: str = USER_A):
    """One `prepare_turn` with the runtime's own store and overrides set, so
    the listing lands in the per-user definitions cache."""
    runtime.set_client_overrides({PLUGIN: {
        "transport": env.world.transport(),
        "policy": AddressPolicy.for_tests()}})
    return await runtime.prepare_turn(
        user_id=user, request_id=request_id, settings=env.settings,
        catalog=[manifest], store=MemoryPluginStore(), web_search=False)


class _ExplodingStore:
    """A plugin store that must not be read."""

    async def get_all(self, user_id):
        raise AssertionError("the plugin store was read")

    async def set_enabled(self, *a):
        raise AssertionError("the plugin store was written")


def _script(*replies):
    return FakeAgentModel([AgentReply(content=r.get("content", ""),
                                      tool_calls=r.get("tool_calls", []))
                           for r in replies])


def _config(turn, agent_model, persona_model, manifest, user_id: str = USER_A,
            **extra):
    cfg = {
        "turn_ctx": turn, "agent_model": agent_model,
        "persona_model": persona_model, "user_id": user_id,
        "session_id": "s1", "query": "what is on my calendar today",
        "locale": "en", "request_id": "req-connector-1", "persona_id": "anne",
        "loop_settings": LoopSettings(plugins_disabled=False),
        "plugin_catalog": [manifest], "web_search": False,
    }
    cfg.update(extra)
    return {"configurable": cfg}


def _call(call_id: str, name: str, **args) -> dict:
    return {"id": call_id, "name": name,
            "arguments": json.dumps(args, sort_keys=True)}


def _msg_with_id(state, call_id: str) -> str:
    for m in state["transcript"]:
        if isinstance(m, dict):
            role, tid, txt = m.get("role"), m.get("tool_call_id"), m["content"]
        else:
            role, tid, txt = (getattr(m, "type", None),
                              getattr(m, "tool_call_id", None), m.content)
        if role == "tool" and tid == call_id:
            return txt
    raise AssertionError(f"no tool message answers {call_id}")


def _actions(state) -> dict[str, dict]:
    return {a["tool_call_id"]: a for a in state["actions"]}


async def _token_of(env, user: str) -> str:
    """The user's current access token (no refresh: the mock server's tokens
    live an hour here)."""
    return await env.service.access_token(user, PLUGIN)


def _no_secrets(env: _WorldEnv, *parts) -> str:
    """Concatenate every place a token could land and assert none of the
    server's secrets (and nothing of the `MOCK-` shape) is there."""
    haystack = "\n".join(json.dumps(p, default=str) for p in parts)
    for secret in env.world.auth.all_issued_secrets():
        assert secret not in haystack, f"leaked: {secret[:12]}"
    assert "MOCK-" not in haystack
    return haystack


# ── F11: the Google manifests ship off, read/label/draft only ─────────────


def test_google_manifests_are_read_label_and_draft_only():
    shipped = {m.id: m for m in load_catalog()}
    assert {"gmail", "google-calendar"} <= set(shipped)
    gmail, calendar = shipped["gmail"], shipped["google-calendar"]
    assert set(gmail.tools) == {"search_messages", "read_message",
                                "list_labels", "label_message", "create_draft"}
    assert set(calendar.tools) == {"list_calendars", "list_events", "get_event"}
    for m in (gmail, calendar):
        assert m.requires_gate is True
        assert m.enabled_by_default is False
        assert m.data_class == "personal"
        assert m.needs_user_token()
        assert m.mcp_server.url == "TO_VERIFY"
        assert m.is_configured() is False          # K fills the URL later
        assert m.auth["client_registration"] == "pre_registered"
        assert m.auth["client_secret_ref"] == f"secretsmanager:vva/<env>/plugins/{m.id}"
        assert "TO_VERIFY" in m.mcp_server.allowed_hosts
    # write tools declare their card arguments (E2) and cannot send
    draft = gmail.tools["create_draft"]
    assert draft.risk == "write" and draft.recipients_arg == "to"
    assert draft.display == ["to", "subject", "body"]
    assert all(t.risk in ("read", "write") for m in (gmail, calendar)
               for t in m.tools.values())
    label = gmail.tools["label_message"]
    assert label.risk == "write" and label.recipients_arg is None


async def test_google_connectors_off_are_neither_offered_nor_connectable():
    """`requires_gate` with `google_enabled=False`: invisible in a turn (the
    store is not even read) and refused at `start`."""
    gmail = Manifest.model_validate(manifest_dict(
        id="gmail", requires_gate=True, enabled_by_default=True))
    env = _WorldEnv(ConnectorWorld(), extra_catalog={"gmail": gmail})

    pt = await runtime.prepare_turn(
        user_id=USER_A, request_id="r1",
        settings=LoopSettings(plugins_disabled=False),
        catalog=[gmail], store=_ExplodingStore(), web_search=False)
    assert pt.specs == [] and pt.statuses == {}
    assert pt.unavailable == [] and pt.reconnect == []

    with pytest.raises(O.OAuthError) as err:
        await env.service.start(USER_A, "gmail")
    assert err.value.code == "not_available"


async def test_google_connectors_on_run_until_the_user_connects():
    """The same manifest with the flag on: `start` works and the turn leaves
    the plugin out with `not_connected` (nobody has connected)."""
    gmail = Manifest.model_validate(manifest_dict(
        id="gmail", requires_gate=True, enabled_by_default=True))
    env = _WorldEnv(ConnectorWorld(auth=MockAuthServer()),
                    settings={"google_enabled": True},
                    extra_catalog={"gmail": gmail})

    # start works when the flag is on: the gate was the only thing that refused
    url = await env.service.start(USER_A, "gmail")
    assert url.startswith(ISSUER)

    pt = await runtime.prepare_turn(
        user_id=USER_A, request_id="r1", settings=env.settings,
        catalog=[gmail], store=MemoryPluginStore(), web_search=False)
    await pt.close()
    assert pt.statuses == {"gmail": "not_connected"}
    assert pt.specs == [] and pt.reconnect == ["gmail"]
    assert pt.open_sessions == 0
    assert env.world.list_bearers == []            # never listed anything


async def test_google_manifests_stay_off_until_the_urls_are_real():
    """The shipped manifests: `requires_gate` (off with the flag off) AND the
    `TO_VERIFY` placeholder. Even leaderboard flag on with the user's own
    enable rows, a placeholder server never loads."""
    google = [m for m in load_catalog() if m.id in ("gmail", "google-calendar")]
    pt = await runtime.prepare_turn(
        user_id=USER_A, request_id="r1",
        settings=LoopSettings(plugins_disabled=False, google_enabled=True),
        catalog=google, store=MemoryPluginStore(), web_search=False)
    await pt.close()
    assert pt.statuses == {"gmail": "disabled", "google-calendar": "disabled"}
    assert pt.specs == []

    store = MemoryPluginStore()
    await store.set_enabled(USER_A, "gmail", True)
    await store.set_enabled(USER_A, "google-calendar", True)
    pt2 = await runtime.prepare_turn(
        user_id=USER_A, request_id="r2",
        settings=LoopSettings(plugins_disabled=False, google_enabled=True),
        catalog=google, store=store, web_search=False)
    await pt2.close()
    assert pt2.statuses == {"gmail": "unconfigured",
                            "google-calendar": "unconfigured"}
    assert pt2.specs == []


# ── the happy turn: token at call time, marks, no leak ────────────────────


async def test_a_connector_read_marks_the_turn_and_the_token_never_leaks(caplog):
    env = _WorldEnv(ConnectorWorld())
    env.world.events[USER_A] = EVENTS_TEXT
    pin, token_a = await _connected_and_pinned(env, USER_A)
    manifest = _pinned_manifest(pin)

    turn = new_turn()
    agent = _script(
        {"tool_calls": [_call("t1", "mock-calendar__list_events",
                              day="2026-11-03")]},
        {"content": "You have three appointments that day."},
    )
    persona = _script({"content": "You have three appointments that day."})
    graph = build_loop_graph()
    runtime.set_client_overrides({PLUGIN: {"transport": env.world.transport(),
                                           "policy": AddressPolicy.for_tests()}})

    async with env.world.running():
        with caplog.at_level(logging.DEBUG):
            state = await graph.ainvoke(
                {"transcript": [], "errors": [], "total_tokens": 0},
                _config(turn, agent, persona, manifest))

    # 1. the connector tool was offered, under its D4 name
    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert "mock-calendar__list_events" in offered

    # 2. the call ran under user A's token: the server saw one bearer (its
    #    digest) and recorded the owner label, never the token itself
    assert env.world.calls == [(USER_A, "list_events")]
    assert set(env.world.call_bearers) == {env.world.digest(token_a)}
    assert set(env.world.list_bearers) == {env.world.digest(token_a)}

    # 3. the result is in the transcript
    content = _msg_with_id(state, "t1")
    assert EVENTS_TEXT in content

    # 4. the action is ok, and the read marks the turn for taint rule 1
    act = _actions(state)["t1"]
    assert act["status"] == "ok" and act["tool"] == "mock-calendar__list_events"
    assert turn.personal_sources == [
        {"tool": "mock-calendar__list_events", "data_class": "personal",
         "plugin_id": PLUGIN}]

    # 5. the token is in no log, in no state, nowhere
    _no_secrets(env, caplog.text, state["transcript"], state["actions"],
                state["loop_trace"], state.get("final_answer"))

    # 6. a sealed transcript stays clean too (F6: open it and search)
    blob = await turns.seal_transcript(state["transcript"], user_id=USER_A,
                                       thread_id=THREAD, sealer=env.sealer)
    opened = await turns.open_transcript(blob, user_id=USER_A, thread_id=THREAD,
                                         sealer=env.sealer)
    opened_text = json.dumps(opened, default=str)
    for secret in env.world.auth.all_issued_secrets():
        assert secret not in opened_text
    assert "MOCK-" not in opened_text
    with pytest.raises(crypto.SealError):          # bound to the user
        await turns.open_transcript(blob, user_id=USER_B, thread_id=THREAD,
                                    sealer=env.sealer)

    # 7. the session was closed when the turn ended
    assert turn.plugins.open_sessions == 0
    assert turn.plugins.statuses == {PLUGIN: "active"}


# ── taint rule 1: the read's destination vs another destination ───────────


async def test_a_connector_read_then_another_egress_destination_is_refused(caplog):
    """A read from a personal connector, then an egress read elsewhere: rule 1
    refuses with `approval_unavailable` (no approval path covers it yet)."""
    env = _WorldEnv(ConnectorWorld())
    env.world.events[USER_A] = EVENTS_TEXT
    pin, _token = await _connected_and_pinned(env, USER_A)
    manifest = _pinned_manifest(pin)

    turn = new_turn()
    agent = _script(
        {"tool_calls": [_call("t1", "mock-calendar__list_events",
                              day="2026-11-03")]},
        {"tool_calls": [_call("t2", "youtube_transcript",
                              url="https://video.example/clip")]},
        {"content": "I stopped: sending your calendar data elsewhere needs approval."},
    )
    persona = _script({"content": "Nothing from YouTube today."})
    graph = build_loop_graph()
    runtime.set_client_overrides({PLUGIN: {"transport": env.world.transport(),
                                           "policy": AddressPolicy.for_tests()}})

    async with env.world.running():
        with caplog.at_level(logging.DEBUG):
            state = await graph.ainvoke(
                {"transcript": [], "errors": [], "total_tokens": 0},
                _config(turn, agent, persona, manifest))

    act1 = _actions(state)["t1"]
    assert act1["status"] == "ok"
    assert turn.personal_sources == [
        {"tool": "mock-calendar__list_events", "data_class": "personal",
         "plugin_id": PLUGIN}]

    act2 = _actions(state)["t2"]
    assert act2["status"] == "refused"
    assert act2["kind"] == "approval_unavailable"
    assert "rule 1" in act2["reason"]
    refusal = json.loads(_msg_with_id(state, "t2").splitlines()[0])
    assert refusal["refused"] == "approval_unavailable"

    assert env.world.calls == [(USER_A, "list_events")]   # nothing else ran
    assert set(env.world.call_bearers) == {env.world.digest(
        (await _token_of(env, USER_A)))}


async def test_rule_1_same_connector_is_the_same_destination():
    """The mark carries the plugin that holds the data. The SAME connector is
    not another destination; a second connector, a builtin destination, and
    an old mark without one all count as another."""
    env = _WorldEnv(ConnectorWorld())
    pin, _token = await _connected_and_pinned(env, USER_A)
    manifest = _pinned_manifest(pin)

    async with env.world.running():
        pt = await _warmed_prepare(env, manifest, "r1")
    turn = new_turn()
    turn.plugins = pt
    spec = pt.specs[0]
    assert spec.plugin_id == PLUGIN and spec.data_class == "personal"

    turn.personal_sources = [{"tool": spec.name, "data_class": "personal",
                              "plugin_id": PLUGIN}]
    same = await gate.check(_call("c1", spec.name, day="2026-11-03"), spec, turn)
    assert same.kind == "allow" and not same.refusal

    other_connector = ToolSpec(
        name="gmail__read_message", description="read a mail",
        parameters={"type": "object", "properties": {},
                    "required": []},
        risk="egress_read", source="plugin", call=lambda *a, **k: None,
        data_class="personal", plugin_id="gmail")
    another = await gate.check(_call("c2", "gmail__read_message"),
                               other_connector, turn)
    assert another.kind == "approval_unavailable" and another.refusal

    builtin = ToolSpec(
        name="web_search", description="search the web",
        parameters={"type": "object",
                    "properties": {"q": {"type": "string"}}, "required": ["q"]},
        risk="egress_read", source="builtin", call=lambda *a, **k: None,
        data_class="none")
    out = await gate.check(_call("c3", "web_search", q="b exercises"), builtin, turn)
    assert out.kind == "approval_unavailable" and out.refusal

    # an old mark (no plugin_id — the pre-stage-5 shape) refuses everything
    turn.personal_sources = [{"tool": spec.name, "data_class": "personal"}]
    legacy = await gate.check(_call("c4", spec.name, day="2026-11-03"), spec, turn)
    assert legacy.kind == "approval_unavailable" and legacy.refusal
    await pt.close()


# ── the per-user definitions cache (GĐ2 review) ───────────────────────────


def test_the_definitions_cache_is_keyed_per_user_for_a_connector():
    connector = Manifest.model_validate(manifest_dict())
    assert runtime.defs_key(connector, USER_A) == f"{USER_A}\x1f{PLUGIN}"
    assert runtime.defs_key(connector, USER_B) == f"{USER_B}\x1f{PLUGIN}"
    assert runtime.defs_key(connector, "") == f"\x1f{PLUGIN}"
    plain = Manifest.model_validate(manifest_dict(auth=None))
    assert runtime.defs_key(plain, USER_A) == PLUGIN
    assert runtime.defs_key(plain, "") == PLUGIN


async def test_listings_are_cached_per_user_and_never_cross_served():
    """Each user's listing was fetched with that user's token; a second turn
    for the same user reuses it (no new listing); another user's cache is
    never served."""
    env = _WorldEnv(ConnectorWorld())
    pin, token_pin = await _connected_and_pinned(env, PIN_USER)
    await env.service.revoke(PIN_USER, PLUGIN)
    manifest = _pinned_manifest(pin)
    _, token_b = await _connected_and_pinned(env, USER_B, manifest)
    _, token_a = await _connected_and_pinned(env, USER_A, manifest)

    async with env.world.running():
        for user, token, request_id in ((USER_B, token_b, "r2"),
                                        (USER_B, token_b, "r2b"),
                                        (USER_A, token_a, "r1")):
            pt = await _warmed_prepare(env, manifest, request_id, user=user)
            assert pt.statuses == {PLUGIN: "active"}
            assert [s.name for s in pt.specs] == ["mock-calendar__list_events"]
            assert pt.specs[0].plugin_id == PLUGIN
            assert pt.specs[0].data_class == "personal"
            await pt.close()

    digests = env.world.list_bearers
    assert digests == [env.world.digest(token_pin),    # the pin's own listing
                       env.world.digest(token_b),      # B listed
                       env.world.digest(token_a)]      # A listed with its OWN token
    # the second B turn served the cache: exactly one listing per user
    assert digests.count(env.world.digest(token_b)) == 1
    assert env.world.digest(token_a) != env.world.digest(token_b)
    _no_secrets(env, [])


# ── not connected / auth expired (F8) ─────────────────────────────────────


async def test_an_unconnected_user_is_left_out_before_any_listing():
    """User A did not connect, user B did: A's turn leaves the plugin out as
    `not_connected`, describes it for the UI, never lists, never breaker-faults;
    B's own turn works."""
    env = _WorldEnv(ConnectorWorld())
    pin, _token_b = await _connected_and_pinned(env, USER_B)
    manifest = _pinned_manifest(pin)

    async with env.world.running():
        pt = await _warmed_prepare(env, manifest, "r1", user=USER_A)
        await pt.close()
        pt_b = await _warmed_prepare(env, manifest, "r2", user=USER_B)
        await pt_b.close()

    assert pt.statuses == {PLUGIN: "not_connected"}
    assert pt.specs == [] and pt.reconnect == ["mock-calendar"]
    assert pt.unavailable == []
    assert pt.open_sessions == 0
    assert pt_b.statuses == {PLUGIN: "active"} and len(pt_b.specs) == 1
    assert runtime.get_breaker(PLUGIN).snapshot()["failures"] == 0
    # a connection fault counts nothing against the plugin's breaker


async def test_an_unconnected_user_sees_reconnect_in_the_trace(caplog):
    env = _WorldEnv(ConnectorWorld())
    manifest = _pinned_manifest(await _pin_by_stranger(env))

    turn = new_turn()
    agent = _script({"content": "Your calendar is not connected yet."})
    persona = _script({"content": "Your calendar is not connected yet."})
    graph = build_loop_graph()

    async with env.world.running():
        with caplog.at_level(logging.INFO):
            state = await graph.ainvoke(
                {"transcript": [], "errors": [], "total_tokens": 0},
                _config(turn, agent, persona, manifest))

    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert not any(n.startswith(f"{PLUGIN}__") for n in offered)
    assert turn.plugins.reconnect == [PLUGIN]
    assert turn.plugins.statuses == {PLUGIN: "not_connected"}
    assert state["loop_trace"][-1]["plugins_reconnect"] == [PLUGIN]
    _no_secrets(env, caplog.text, state["loop_trace"])


async def test_at_listing_a_dead_token_is_auth_expired_and_not_a_breaker_fault():
    """The listing itself gets 401: `auth_expired` on the plugin, `reconnect`
    for the UI, no specs, no breaker harm. Reconnecting fixes the NEXT turn."""
    env = _WorldEnv(ConnectorWorld())
    pin, token_a = await _connected_and_pinned(env, USER_A)
    manifest = _pinned_manifest(pin)
    env.world.reject(token_a)

    async with env.world.running():
        pt = await _warmed_prepare(env, manifest, "r1")
        await pt.close()

    assert pt.statuses == {PLUGIN: "auth_expired"}
    assert pt.specs == [] and pt.reconnect == [PLUGIN]
    assert pt.open_sessions == 0
    assert runtime.get_breaker(PLUGIN).snapshot()["failures"] == 0

    # reconnect: A connects again, a fresh listing succeeds
    await env.connect(USER_A)
    env.world.label_new_tokens(USER_A)
    async with env.world.running():
        pt2 = await _warmed_prepare(env, manifest, "r2")
        await pt2.close()
    assert pt2.statuses == {PLUGIN: "active"}
    assert len(pt2.specs) == 1
    assert env.world.digest(await _token_of(env, USER_A)) \
        in env.world.list_bearers


async def test_a_dead_token_mid_turn_auth_expired_and_the_plugin_is_left_out(caplog):
    """The listing passed, the token dies before the call: the tools node
    answers with the fixed `auth_expired` text, invalidates the plugin for the
    rest of the turn (gate 1b), records `reconnect`, and never blames the
    plugin's breaker."""
    env = _WorldEnv(ConnectorWorld())
    pin, token_a = await _connected_and_pinned(env, USER_A)
    # the first call is `read_inbox`: the manifest must declare it too with
    # the pin computed over the server's full listing (both tools)
    manifest = _pinned_manifest(
        pin, tools={"list_events": {"egress": True, "risk": "read"},
                    "read_inbox": {"egress": True, "risk": "read"}})

    async with env.world.running():                 # warm the per-user cache
        pt0 = await _warmed_prepare(env, manifest, "w0")
        assert pt0.statuses == {PLUGIN: "active"}
        await pt0.close()
    env.world.reject(token_a)

    turn = new_turn()
    agent = _script(
        {"tool_calls": [_call("t1", "mock-calendar__read_inbox")]},
        {"tool_calls": [_call("t2", "mock-calendar__list_events",
                              day="2026-11-03")]},
        {"content": "I stopped after the calendar lost its connection."},
    )
    persona = _script({"content": "The calendar needs you to connect it again."})
    graph = build_loop_graph()
    runtime.set_client_overrides({PLUGIN: {"transport": env.world.transport(),
                                           "policy": AddressPolicy.for_tests()}})

    async with env.world.running():
        with caplog.at_level(logging.DEBUG):
            state = await graph.ainvoke(
                {"transcript": [], "errors": [], "total_tokens": 0},
                _config(turn, agent, persona, manifest))

    offered = [t["function"]["name"] for t in agent.calls[0]["tools"]]
    assert "mock-calendar__read_inbox" in offered     # the cache was still warm

    act1 = _actions(state)["t1"]
    assert act1["status"] == "failed"
    assert act1["kind"] == "auth_expired"
    assert act1["error"] == "McpUnauthorized"
    payload = json.loads(_msg_with_id(state, "t1").splitlines()[0])
    assert payload == {
        "error": "auth_expired",
        "message": f"{PLUGIN} needs the user to connect it again. "
                   "It is not available in this turn."}

    nothing_ran = env.world.calls == []
    assert nothing_ran
    assert env.world.unauthorised >= 1

    # the plugin is out for the rest of the turn and the trace names it
    assert turn.plugins.reconnect == [PLUGIN]
    assert turn.plugins.statuses[PLUGIN] == "auth_expired"
    assert state["loop_trace"][-1]["plugins_reconnect"] == [PLUGIN]

    # the second attempt is refused by the gate (1b), answered, not run
    act2 = _actions(state)["t2"]
    assert act2["status"] == "refused"
    assert act2["kind"] == "refused_by_policy"
    assert f"plugin {PLUGIN} is off for this turn" in act2["reason"]

    # a connection fault is never a plugin fault
    assert runtime.get_breaker(PLUGIN).snapshot()["failures"] == 0

    _no_secrets(env, caplog.text, state["transcript"], state["actions"],
                state["loop_trace"])


# ── refresh under the lock, from inside the runtime (F7) ──────────────────


async def test_two_parallel_calls_with_an_expiring_token_refresh_once_and_both_succeed():
    """The connection's token has just fallen inside the 60 s refresh skew.
    Two parallel calls share ONE refresh under the per-connection lock: the
    second caller re-reads the row inside the lock, finds it fresh, and both
    carry the same (new) token."""
    server = MockAuthServer(refresh_delay_s=0.05)
    env = _WorldEnv(ConnectorWorld(server))
    pin, _token_pin = await _connected_and_pinned(env, PIN_USER)
    await env.service.revoke(PIN_USER, PLUGIN)
    await env.connect(USER_A)
    env.world.label_new_tokens(USER_A)
    manifest = _pinned_manifest(pin)

    async with env.world.running():
        pt0 = await _warmed_prepare(env, manifest, "w0")
        assert pt0.statuses == {PLUGIN: "active"}
        await pt0.close()
        pt1 = await _warmed_prepare(env, manifest, "w1")   # cache hit, no listing
        assert pt1.statuses == {PLUGIN: "active"}
        spec = pt1.specs[0]
        env.world.events["?"] = EVENTS_TEXT   # the refreshed token is unlabelled

        env.advance(3541)      # the hour-old token now sits inside the skew
        before = server.refresh_calls
        outs = await asyncio.gather(
            spec.call({"day": "2026-11-03"}, {}),
            spec.call({"day": "2026-11-03"}, {}))
    await pt1.close()

    assert server.refresh_calls - before == 1
    assert outs[0] == outs[1] == EVENTS_TEXT           # both succeeded
    assert [tool for _who, tool in env.world.calls] == ["list_events", "list_events"]
    assert len(set(env.world.call_bearers)) == 1       # one bearer: one token
    conn = await env.connections.get(USER_A, PLUGIN)
    assert conn.status == "active"                     # nobody fell auth_expired
