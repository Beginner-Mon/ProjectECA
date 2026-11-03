"""Plugins: store contract, switches, gate rules, failures. No network, no DB
(except the PG contract run, which exists only in CI as `eca_user`)."""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from langgraph_agents.loop import gate
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.errors import APPROVAL_UNAVAILABLE, BUDGET, REFUSED_BY_POLICY, TRANSIENT
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.limits import Caps, new_turn
from langgraph_agents.loop.mcp_client import definitions_sha256
from langgraph_agents.loop.plugins import runtime
from langgraph_agents.loop.plugins.manifest import Manifest, get_manifest, load_catalog
from langgraph_agents.loop.plugins.store import (
    MemoryPluginStore, PgPluginStore, get_plugin_store, set_store as set_plugin_store,
)
from langgraph_agents.loop.rate import MemoryRateCounter, set_store as set_rate_store
from langgraph_agents.loop.tools.registry import build_turn_tools
from tests.langgraph_agents.loop.mcp_fakes import SERVER_HOST, SERVER_URL, Fake


@pytest.fixture(autouse=True)
def _clean():
    runtime.reset_runtime()
    set_plugin_store(None)
    set_rate_store(MemoryRateCounter())
    yield
    runtime.reset_runtime()
    set_plugin_store(None)


def _manifest(pin: str = "UNPINNED", **over) -> Manifest:
    data = {
        "id": "fake", "name": "Fake", "version": "1", "publisher": "tests",
        "mcp_server": {"url": SERVER_URL, "allowed_hosts": [SERVER_HOST]},
        "tools": {"echo": {"egress": True, "risk": "read"}},
        "reviewed": {"by": "test", "tool_definitions_sha256": pin},
        "enabled_by_default": True,
    }
    data.update(over)
    return Manifest.model_validate(data)


async def _pin_of(fake: Fake) -> str:
    async with fake.running():
        c = _client_for(fake)
        try:
            return definitions_sha256(await c.list_tools())
        finally:
            await c.close()


def _client_for(fake: Fake):
    from langgraph_agents.loop.mcp_client import McpClient
    return McpClient(SERVER_URL, [SERVER_HOST], policy=AddressPolicy.for_tests(),
                     transport=httpx.ASGITransport(app=fake.app))


def _point(plugin_id: str, transport) -> None:
    runtime.set_client_overrides({plugin_id: {
        "transport": transport, "policy": AddressPolicy.for_tests()}})


def _settings(**kw) -> LoopSettings:
    kw.setdefault("plugins_disabled", False)
    return LoopSettings(**kw)


async def _prepare(catalog, store=None, settings=None, web_search=True):
    return await runtime.prepare_turn(
        user_id="u1", request_id="r1", settings=settings or _settings(),
        catalog=catalog, store=store or MemoryPluginStore(), web_search=web_search)


class _ExplodingStore:
    """A store that must not be read."""

    async def get_all(self, user_id):
        raise AssertionError("the store was read")

    async def set_enabled(self, *a):
        raise AssertionError("the store was written")


# ── the store: one contract, memory and Postgres ─────────────────────────


@pytest.fixture(params=[
    pytest.param("memory", id="memory"),
    pytest.param("pg", id="pg", marks=pytest.mark.integration),
])
def store_and_users(request):
    """`(store, user_a, user_b)`; PG rows are removed afterwards (as eca_user)."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    if request.param == "memory":
        return MemoryPluginStore(), a, b
    pg = request.getfixturevalue("test_pg_client")

    async def _has_table():
        async with pg.get_client().transaction() as conn:
            return bool(await conn.fetchval("SELECT to_regclass('user_plugins')"))

    if not asyncio.run(_has_table()):
        pytest.skip("migration 020 not applied")

    async def _cleanup():
        for uid in (a, b):
            async with pg.get_client().user_scope(uid) as conn:
                await conn.execute("DELETE FROM user_plugins")

    request.addfinalizer(lambda: _swallow(_cleanup))
    return PgPluginStore(get_pg_client=pg.get_client), a, b


def _swallow(coro_fn) -> None:
    try:
        asyncio.run(coro_fn())
    except Exception:  # noqa: BLE001 - cleanup must not mask the result
        pass


def test_store_a_user_with_no_rows_gets_an_empty_map(store_and_users):
    store, a, _ = store_and_users
    assert asyncio.run(store.get_all(a)) == {}


def test_store_set_and_override(store_and_users):
    store, a, _ = store_and_users

    async def go():
        await store.set_enabled(a, "deepwiki", True)
        await store.set_enabled(a, "web-search", False)
        first = await store.get_all(a)
        await store.set_enabled(a, "deepwiki", False)       # a row overrides
        return first, await store.get_all(a)

    first, second = asyncio.run(go())
    assert first == {"deepwiki": True, "web-search": False}
    assert second == {"deepwiki": False, "web-search": False}


def test_store_users_are_separate(store_and_users):
    store, a, b = store_and_users

    async def go():
        await store.set_enabled(a, "deepwiki", True)
        await store.set_enabled(b, "deepwiki", False)
        return await store.get_all(a), await store.get_all(b)

    got_a, got_b = asyncio.run(go())
    assert got_a == {"deepwiki": True}
    assert got_b == {"deepwiki": False}


@pytest.mark.integration
def test_pg_rls_hides_other_users_rows_even_without_a_where(test_pg_client):
    """As eca_user the owner policy filters by itself: a bare SELECT inside
    user_scope(A) sees A's rows only."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    pg = test_pg_client

    async def go():
        store = PgPluginStore(get_pg_client=pg.get_client)
        try:
            await store.set_enabled(a, "deepwiki", True)
            await store.set_enabled(b, "deepwiki", True)
            async with pg.get_client().user_scope(a) as conn:
                return await conn.fetch("SELECT user_id FROM user_plugins")
        finally:
            for uid in (a, b):
                async with pg.get_client().user_scope(uid) as conn:
                    await conn.execute("DELETE FROM user_plugins")

    try:
        rows = asyncio.run(go())
    except Exception as exc:  # noqa: BLE001
        if "user_plugins" in str(exc) and "does not exist" in str(exc):
            pytest.skip("migration 020 not applied")
        raise
    assert {str(r["user_id"]) for r in rows} == {a}


def test_get_plugin_store_memory_switch(monkeypatch):
    set_plugin_store(None)
    monkeypatch.setenv("VVA_LOOP_LEASE_STORE", "memory")
    assert isinstance(get_plugin_store(), MemoryPluginStore)


# ── enable and disable without a code change ─────────────────────────────


async def test_enable_and_disable_change_the_next_turn_with_no_code_change():
    fake = Fake()
    pin = await _pin_of(Fake())
    manifest = _manifest(pin, enabled_by_default=False)
    store = MemoryPluginStore()
    async with fake.running():
        _point("fake", httpx.ASGITransport(app=fake.app))

        pt = await _prepare([manifest], store)           # no row, off by default
        assert pt.statuses == {"fake": runtime.DISABLED} and pt.specs == []

        await store.set_enabled("u1", "fake", True)       # the user switches it on
        pt = await _prepare([manifest], store)
        assert pt.statuses == {"fake": runtime.ACTIVE}
        assert [s.name for s in pt.specs] == ["fake__echo"]
        await pt.close()

        await store.set_enabled("u1", "fake", False)      # and off again
        pt = await _prepare([manifest], store)
        assert pt.statuses == {"fake": runtime.DISABLED} and pt.specs == []


async def test_a_row_can_turn_off_a_default_on_plugin():
    pin = await _pin_of(Fake())
    manifest = _manifest(pin, enabled_by_default=True)
    store = MemoryPluginStore()
    await store.set_enabled("u1", "fake", False)
    pt = await _prepare([manifest], store)
    assert pt.statuses == {"fake": runtime.DISABLED}


async def test_the_store_is_read_once_per_turn_and_listing_is_cached():
    fake = Fake()
    pin = await _pin_of(Fake())
    manifest = _manifest(pin)
    reads = []

    class _Counting(MemoryPluginStore):
        async def get_all(self, user_id):
            reads.append(user_id)
            return await super().get_all(user_id)

    transport = httpx.ASGITransport(app=fake.app)
    async with fake.running():
        _point("fake", transport)
        pt1 = await _prepare([manifest], _Counting())
        await pt1.close()
        assert len(reads) == 1
        # second turn: definitions come from the cache, so no session is opened
        pt2 = await _prepare([manifest], _Counting())
        assert pt2.statuses == {"fake": runtime.ACTIVE}
        assert pt2.open_sessions == 0
        await pt2.close()


async def test_the_cache_expires_after_600_seconds(monkeypatch):
    fake = Fake()
    pin = await _pin_of(Fake())
    manifest = _manifest(pin)
    now = [1000.0]
    monkeypatch.setattr(runtime, "_clock", lambda: now[0])
    async with fake.running():
        _point("fake", httpx.ASGITransport(app=fake.app))
        pt = await _prepare([manifest])
        await pt.close()
        pt = await _prepare([manifest])
        assert pt.open_sessions == 0                  # still cached
        now[0] += runtime.DEFS_TTL_S + 1
        pt = await _prepare([manifest])
        assert pt.open_sessions == 1                  # listed again
        await pt.close()


# ── kill switches (D8) ───────────────────────────────────────────────────


async def test_plugins_disabled_offers_nothing_and_reads_nothing():
    manifest = _manifest()
    pt = await runtime.prepare_turn(
        user_id="u1", request_id="r", settings=_settings(plugins_disabled=True),
        catalog=[manifest], store=_ExplodingStore())
    assert pt.specs == [] and pt.statuses == {"fake": runtime.OFF}


async def test_loop_plugins_off_turns_off_single_plugins(monkeypatch):
    monkeypatch.setenv("LOOP_PLUGINS_OFF", "fake, other")
    settings = LoopSettings.from_env().model_copy(update={"plugins_disabled": False})
    assert settings.plugins_off == ["fake", "other"]

    fake = Fake()
    pin = await _pin_of(Fake())
    keep = _manifest(pin, id="keep")
    async with fake.running():
        runtime.set_client_overrides({
            "keep": {"transport": httpx.ASGITransport(app=fake.app),
                     "policy": AddressPolicy.for_tests()}})
        pt = await _prepare([_manifest(pin), keep], settings=settings)
        try:
            assert pt.statuses == {"fake": runtime.OFF, "keep": runtime.ACTIVE}
            assert [s.name for s in pt.specs] == ["keep__echo"]
        finally:
            await pt.close()


def test_the_defaults_keep_plugins_off(monkeypatch):
    monkeypatch.delenv("LOOP_PLUGINS_OFF", raising=False)
    monkeypatch.delenv("AGENT_PLUGINS_DISABLED", raising=False)
    s = LoopSettings.from_env()
    assert s.plugins_disabled is True and s.plugins_off == []


# ── requires_gate, deepwiki, web-search ──────────────────────────────────


async def test_a_requires_gate_manifest_stays_hidden_without_google_enabled():
    manifest = _manifest(requires_gate=True)
    pt = await runtime.prepare_turn(
        user_id="u1", request_id="r", settings=_settings(google_enabled=False),
        catalog=[manifest], store=_ExplodingStore())
    assert pt.statuses == {} and pt.specs == []        # not even listed


async def test_a_requires_gate_manifest_appears_when_the_gate_is_open():
    manifest = _manifest(requires_gate=True)           # unpinned → changed, but visible
    fake = Fake()
    async with fake.running():
        _point("fake", httpx.ASGITransport(app=fake.app))
        pt = await _prepare([manifest], settings=_settings(google_enabled=True))
        try:
            assert pt.statuses == {"fake": runtime.DEFINITION_CHANGED}
        finally:
            await pt.close()


def test_the_shipped_deepwiki_manifest():
    m = get_manifest("deepwiki")
    assert m.resolved_url() == "https://mcp.deepwiki.com/mcp"
    assert m.effective_hosts() == ["mcp.deepwiki.com"]
    assert m.reviewed.tool_definitions_sha256 == "UNPINNED"
    assert m.is_pinned() is False
    assert m.requires_gate is False and m.has_write_tools() is False


async def test_the_unpinned_deepwiki_manifest_is_not_offered():
    """The real catalog entry, pointed at an in-process server: UNPINNED counts
    as a mismatch, so no tool is offered. Nothing contacts deepwiki."""
    deepwiki = get_manifest("deepwiki")
    fake = Fake()
    store = MemoryPluginStore()
    await store.set_enabled("u1", "deepwiki", True)
    async with fake.running():
        _point("deepwiki", httpx.ASGITransport(app=fake.app))
        pt = await _prepare([deepwiki], store)
        try:
            assert pt.statuses == {"deepwiki": runtime.DEFINITION_CHANGED}
            assert pt.specs == []
        finally:
            await pt.close()


def _web_search_manifest(pin: str) -> Manifest:
    base = get_manifest("web-search").model_dump()
    base["reviewed"] = {"by": "test", "tool_definitions_sha256": pin}
    return Manifest.model_validate(base)


async def test_web_search_plugin_replaces_the_builtin_when_configured(monkeypatch):
    from langgraph_agents.mcp import web_search_server as ws

    async def fake_search(query, max_results=3, domain_filter=None):
        return [{"title": "T", "snippet": query, "url": "https://x.test",
                 "source_domain": "e"}]

    monkeypatch.setattr(ws, "_search_searxng", fake_search)
    app = ws.build_http_app()
    monkeypatch.setenv("LOOP_WEB_SEARCH_MCP_URL", SERVER_URL)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        from langgraph_agents.loop.mcp_client import McpClient
        c = McpClient(SERVER_URL, [SERVER_HOST], policy=AddressPolicy.for_tests(),
                      transport=transport)
        try:
            pin = definitions_sha256(await c.list_tools())
        finally:
            await c.close()
        manifest = _web_search_manifest(pin)
        _point("web-search", transport)
        pt = await _prepare([manifest])
        turn = new_turn()
        turn.plugins = pt
        try:
            assert pt.statuses == {"web-search": runtime.ACTIVE}
            names = {s.name for s in build_turn_tools(
                {"turn_ctx": turn, "web_search": True, "persona_id": ""})}
            assert "web-search__search_medical" in names
            assert "web_search" not in names             # replaced
            spec = next(s for s in pt.specs if s.name == "web-search__search_medical")
            out = json.loads(await spec.call({"query": "bird dog"}, {}))
            assert out[0]["snippet"] == "bird dog"
        finally:
            await pt.close()


async def test_web_search_keeps_the_builtin_when_the_plugin_has_no_url(monkeypatch):
    monkeypatch.delenv("LOOP_WEB_SEARCH_MCP_URL", raising=False)
    manifest = _web_search_manifest("whatever")
    pt = await _prepare([manifest])
    assert pt.statuses == {"web-search": runtime.UNCONFIGURED}
    turn = new_turn()
    turn.plugins = pt
    names = {s.name for s in build_turn_tools(
        {"turn_ctx": turn, "web_search": True, "persona_id": ""})}
    assert "web_search" in names                         # the builtin stays
    assert not any(n.startswith("web-search__") for n in names)


async def test_web_search_plugin_follows_the_web_search_flag(monkeypatch):
    monkeypatch.setenv("LOOP_WEB_SEARCH_MCP_URL", SERVER_URL)
    pt = await _prepare([_web_search_manifest("x")], web_search=False)
    assert pt.statuses == {"web-search": runtime.WEB_SEARCH_OFF} and pt.specs == []


def test_shipped_catalog_loads_and_names_follow_the_rules():
    # Stage 5: the two Google connector manifests ship too (hidden by
    # requires_gate while google_enabled is false).
    ids = {m.id for m in load_catalog()}
    assert ids == {"web-search", "deepwiki", "gmail", "google-calendar"}
    taken: set[str] = set()
    assert runtime.tool_name("deepwiki", "read_wiki.structure", taken) \
        == "deepwiki__read_wiki_structure"
    long = runtime.tool_name("p" * 30, "t" * 60, set())
    assert len(long) <= 64 and long.startswith("p" * 30)
    # a clash after mapping is disambiguated, not overwritten
    t2: set[str] = set()
    a = runtime.tool_name("p", "a.b", t2)
    b = runtime.tool_name("p", "a b", t2)
    assert a != b and len(t2) == 2


# ── the gate: rate, breaker, writes, lifecycle ───────────────────────────


def _plugin_spec(name="fake__echo", risk="egress_read", call=None, plugin_id="fake"):
    from langgraph_agents.loop.tools.registry import ToolSpec

    async def _noop(args, config):
        return "ok"

    return ToolSpec(
        name=name, description="d",
        parameters={"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
        risk=risk, source="plugin", call=call or _noop, plugin_id=plugin_id)


def _turn(**kw):
    t = new_turn(Caps(max_rounds=6, max_calls_per_round=5, max_calls_per_turn=12,
                      max_writes_per_round=1))
    t.user_id = "u1"
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _call(name="fake__echo", args=None, call_id="c1"):
    return {"id": call_id, "name": name,
            "arguments": args if args is not None else {"text": "x"}}


async def test_plugin_calls_count_against_the_plugin_bucket_at_30_per_minute():
    seen = []

    class _Recorder:
        async def hit(self, user_id, bucket, limit=30, window_s=60):
            seen.append((user_id, bucket, limit, window_s))
            return True

    set_rate_store(_Recorder())
    d = await gate.check(_call(), _plugin_spec(), _turn())
    assert d.kind == "allow"
    assert seen == [("u1", "plugin:fake", 30, 60)]


async def test_the_rate_limit_refuses_the_31st_call():
    set_rate_store(MemoryRateCounter())
    spec = _plugin_spec()
    turn = _turn()
    kinds = [(await gate.check(_call(call_id=f"c{i}"), spec, turn)).kind
             for i in range(31)]
    assert kinds[:30] == ["allow"] * 30
    assert kinds[30] == BUDGET
    # another plugin has its own bucket
    other = await gate.check(_call(name="o__echo"),
                             _plugin_spec(name="o__echo", plugin_id="other"), turn)
    assert other.kind == "allow"


async def test_the_breaker_opens_after_three_failures_and_closes_after_the_cool_down(
        monkeypatch):
    from langgraph_agents.core import circuit_breaker as cb
    from langgraph_agents.loop import tools_node

    async def boom(args, config):
        raise httpx.ConnectError("down")

    spec = _plugin_spec(call=boom)
    turn = _turn()
    config = {"configurable": {"request_id": "r1"}}

    async def run_once(i):
        return await tools_node._run_one(_call(call_id=f"c{i}"), spec, turn, config, 1000)

    for i in range(3):
        res = await run_once(i)
        assert res["action"]["status"] == "failed"
    assert runtime.get_breaker("fake").state == "open"

    refused = await run_once(3)
    assert refused["action"]["status"] == "refused"
    assert refused["action"]["kind"] == TRANSIENT

    # the cool-down passes: one probe is allowed, and a success closes the breaker
    real = cb.time.time
    monkeypatch.setattr(cb.time, "time", lambda: real() + runtime.BREAKER_COOL_DOWN_S + 1)
    assert runtime.get_breaker("fake").state == "half_open"

    async def fine(args, config):
        return "ok"

    spec_ok = _plugin_spec(call=fine)
    res = await tools_node._run_one(_call(call_id="c9"), spec_ok, turn, config, 1000)
    assert res["action"]["status"] == "ok"
    assert runtime.get_breaker("fake").state == "closed"


async def test_a_tool_level_error_answer_does_not_trip_the_breaker():
    from langgraph_agents.loop import tools_node

    async def tool_error(args, config):
        return json.dumps({"error": "tool_error", "message": "bad query"})

    spec = _plugin_spec(call=tool_error)
    turn = _turn()
    for i in range(5):
        res = await tools_node._run_one(
            _call(call_id=f"c{i}"), spec, turn, {"configurable": {}}, 1000)
        assert res["action"]["status"] == "failed"
    assert runtime.get_breaker("fake").state == "closed"


async def test_a_write_tool_in_a_manifest_is_refused_with_approval_unavailable():
    manifest = _manifest(
        tools={"echo": {"egress": True, "risk": "read"},
               "send": {"egress": True, "risk": "write", "idempotency_arg": "rid"}})
    assert manifest.has_write_tools()
    pin = await _pin_of(Fake())
    manifest = manifest.model_copy(update={"reviewed": manifest.reviewed.model_copy(
        update={"tool_definitions_sha256": pin})})

    spec = _plugin_spec(name="fake__send", risk="write")
    d = await gate.check(_call(name="fake__send"), spec, _turn())
    assert d.kind == APPROVAL_UNAVAILABLE and d.refusal


async def test_a_declared_write_tool_the_server_lists_becomes_a_write_spec():
    """The manifest's risk decides the spec's risk. (The Fake has no `send`
    tool, so declare `fail` as the write tool: it exists on the server.)"""
    fake = Fake()
    pin = await _pin_of(Fake())
    manifest = _manifest(pin, tools={"fail": {"egress": True, "risk": "write"}})
    async with fake.running():
        _point("fake", httpx.ASGITransport(app=fake.app))
        pt = await _prepare([manifest])
        try:
            assert [(s.name, s.risk) for s in pt.specs] == [("fake__fail", "write")]
            d = await gate.check(_call(name="fake__fail", args={}), pt.specs[0], _turn())
            assert d.kind == APPROVAL_UNAVAILABLE
        finally:
            await pt.close()


async def test_a_plugin_invalidated_mid_turn_is_refused_by_the_gate():
    from langgraph_agents.loop.plugins.runtime import PluginTurn

    turn = _turn()
    turn.plugins = PluginTurn("r")
    spec = _plugin_spec()
    assert (await gate.check(_call(), spec, turn)).kind == "allow"
    turn.plugins.invalidate("fake", runtime.DEFINITION_CHANGED)
    d = await gate.check(_call(call_id="c2"), spec, turn)
    assert d.kind == REFUSED_BY_POLICY and d.refusal


async def test_a_plugin_egress_call_waits_for_a_routine_verdict():
    """Plugin reads are egress: a non-routine verdict allows internal tools only."""
    class _V:
        level = "soon"

    d = await gate.check(_call(), _plugin_spec(), _turn(verdict=_V()))
    assert d.kind == REFUSED_BY_POLICY


# ── failures: the turn goes on ───────────────────────────────────────────


class _HangTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        await asyncio.sleep(3600)


async def test_a_list_tools_timeout_leaves_the_plugin_out_and_the_turn_completes(
        monkeypatch):
    monkeypatch.setattr(runtime, "LIST_TIMEOUT_S", 0.2)
    manifest = _manifest("x")
    _point("fake", _HangTransport())
    pt = await _prepare([manifest])               # does not raise, does not hang
    assert pt.statuses == {"fake": runtime.UNAVAILABLE}
    assert pt.unavailable == ["fake"] and pt.specs == []
    assert pt.open_sessions == 0
    assert runtime.get_breaker("fake").snapshot()["failures"] == 1


async def test_an_open_breaker_skips_listing_so_a_dead_plugin_costs_nothing(monkeypatch):
    monkeypatch.setattr(runtime, "LIST_TIMEOUT_S", 0.2)
    manifest = _manifest("x")
    _point("fake", _HangTransport())
    for _ in range(3):
        await _prepare([manifest])
    assert runtime.get_breaker("fake").state == "open"
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    pt = await _prepare([manifest])
    assert loop.time() - t0 < 0.1                  # no attempt, no 0.2 s wait
    assert pt.statuses == {"fake": runtime.UNAVAILABLE}


async def test_a_store_failure_offers_no_plugins():
    class _Down:
        async def get_all(self, user_id):
            raise RuntimeError("db down")

    pt = await _prepare([_manifest("x")], _Down())
    assert pt.specs == [] and pt.statuses == {"fake": runtime.UNAVAILABLE}
