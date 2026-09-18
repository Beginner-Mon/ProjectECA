"""Tests for /health and /health/detailed endpoints (Phase 6 P0.2)."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def api_client(monkeypatch):
    mock_redis = MagicMock()
    mock_redis.ping.return_value = True

    mock_graph = MagicMock()
    mock_graph.astream = AsyncMock()

    # Reset all LLM circuit breakers to closed (other tests may have opened them)
    from langgraph_agents.llm import _cb_map
    for breaker in _cb_map.values():
        breaker._state.failures = 0
        breaker._state.state = "closed"
        breaker._state.opened_at = 0.0

    # Reset MCP breaker
    import langgraph_agents.mcp.client as mcp_mod
    mcp_mod._mcp_breaker._state.failures = 0
    mcp_mod._mcp_breaker._state.state = "closed"
    mcp_mod._mcp_breaker._state.opened_at = 0.0

    import langgraph_agents.api.main as api_module
    api_module._graph = mock_graph
    api_module._redis = mock_redis

    from langgraph_agents.api.main import create_app
    from fastapi.testclient import TestClient
    app = create_app()
    client = TestClient(app)

    yield client, mock_redis, mock_graph

    api_module._graph = None
    api_module._redis = None


# ── Liveness ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_health_liveness_always_200(api_client):
    """Liveness returns 200 even if all deps are down."""
    client, _, _ = api_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.unit
def test_health_liveness_is_fast(api_client):
    """Liveness must respond in under 50ms (no dep checks)."""
    import time
    client, _, _ = api_client
    t0 = time.perf_counter()
    resp = client.get("/health")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert resp.status_code == 200
    assert elapsed_ms < 100, f"Liveness took {elapsed_ms:.0f}ms, expected <100ms"


# ── Readiness: all OK ────────────────────────────────────────────────


@pytest.mark.unit
def test_health_detailed_all_ok(api_client):
    """When all deps are healthy, returns 200 with all ok."""
    client, _, _ = api_client

    with patch("langgraph_agents.api.health.check_postgres",
               new_callable=AsyncMock) as mock_pg, \
         patch("langgraph_agents.api.health.check_redis",
               new_callable=AsyncMock) as mock_redis, \
         patch("langgraph_agents.api.health.check_llm",
               new_callable=AsyncMock) as mock_llm, \
         patch("langgraph_agents.api.health.check_mcp",
               new_callable=AsyncMock) as mock_mcp, \
         patch("langgraph_agents.api.health.check_graph",
               new_callable=AsyncMock) as mock_graph, \
         patch("langgraph_agents.api.health.check_speechllm",
               new_callable=AsyncMock) as mock_speech, \
         patch("langgraph_agents.api.health.check_searxng",
               new_callable=AsyncMock) as mock_searxng:

        from langgraph_agents.api.health import CheckResult
        ok_pg = CheckResult(name="postgres", ok=True, latency_ms=1.0)
        ok_redis = CheckResult(name="redis", ok=True, latency_ms=0.5)
        ok_llm = CheckResult(name="llm", ok=True, latency_ms=0.0, detail="skipped")
        ok_mcp = CheckResult(name="mcp", ok=True, latency_ms=2.0, detail="2 tool(s)")
        ok_graph = CheckResult(name="graph", ok=True, latency_ms=0.0)
        ok_speech = CheckResult(name="speechllm", ok=True, latency_ms=0.0, detail="skipped")
        ok_searxng = CheckResult(name="searxng", ok=True, latency_ms=0.0, detail="skipped")

        mock_pg.return_value = ok_pg
        mock_redis.return_value = ok_redis
        mock_llm.return_value = ok_llm
        mock_mcp.return_value = ok_mcp
        mock_graph.return_value = ok_graph
        mock_speech.return_value = ok_speech
        mock_searxng.return_value = ok_searxng

        resp = client.get("/health/detailed")
        assert resp.status_code == 200
        body = resp.json()
        if isinstance(body, list):
            body = body[0]
        assert body["status"] == "ready"
        assert body["checks"]["postgres"]["ok"] is True
        assert body["checks"]["redis"]["ok"] is True


# ── Readiness: degraded ──────────────────────────────────────────────


def _patch_all_checks(**failures):
    """Patch every check to pass, except the named ones.

    `failures` maps check name → detail string. Returns a context manager.
    Each mock keeps its own real name: the response is keyed by CheckResult.name,
    so reusing one shared result would collapse several checks into one entry
    and quietly stop testing the thing under test.
    """
    import contextlib

    from langgraph_agents.api.health import CheckResult

    names = ["redis", "postgres", "graph", "llm", "mcp", "speechllm", "searxng"]

    @contextlib.contextmanager
    def _cm():
        with contextlib.ExitStack() as stack:
            for name in names:
                mock = stack.enter_context(
                    patch(f"langgraph_agents.api.health.check_{name}",
                          new_callable=AsyncMock)
                )
                failed = name in failures
                mock.return_value = CheckResult(
                    name=name,
                    ok=not failed,
                    latency_ms=1.0,
                    detail=failures.get(name),
                )
            yield

    return _cm()


@pytest.mark.unit
def test_health_detailed_unhealthy_when_redis_down(api_client):
    """A CRITICAL dep down → 503, so the load balancer stops sending traffic."""
    client, _, _ = api_client

    with _patch_all_checks(redis="connection refused"):
        resp = client.get("/health/detailed")

    assert resp.status_code == 503
    body = resp.json()
    if isinstance(body, list):
        body = body[0]
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"]["critical"] is True


@pytest.mark.unit
def test_health_detailed_stays_live_when_optional_dep_down(api_client):
    """An OPTIONAL dep down → still 200.

    Regression guard: TTS and SearXNG used to count towards the readiness probe.
    Because every instance talks to the same TTS box, one TTS outage returned 503
    from all of them at once and emptied the load balancer — over a feature whose
    only effect is that replies are not spoken.
    """
    client, _, _ = api_client

    with _patch_all_checks(speechllm="connection refused", searxng="HTTP 502"):
        resp = client.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()
    if isinstance(body, list):
        body = body[0]
    assert body["status"] == "degraded"
    assert body["checks"]["speechllm"]["critical"] is False
    assert sorted(body["degraded"]) == ["searxng", "speechllm"]


# ── LLM_HEALTHCHECK toggle ───────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_health_skipped_when_env_zero():
    """When LLM_HEALTHCHECK=0 (default), check_llm returns ok with detail=skipped."""
    with patch.dict(os.environ, {"LLM_HEALTHCHECK": "0"}, clear=False):
        from langgraph_agents.api.health import check_llm
        result = await check_llm(timeout=1.0)
        assert result.ok is True
        assert result.detail == "skipped"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_llm_health_runs_when_env_one():
    """When LLM_HEALTHCHECK=1, check_llm actually calls the LLM."""
    with patch.dict(os.environ, {"LLM_HEALTHCHECK": "1"}, clear=False):
        with patch("langgraph_agents.llm.get_chat_model") as mock_gcm:
            mock_llm = MagicMock()
            mock_llm.ainvoke = AsyncMock(return_value="pong")
            mock_gcm.return_value = mock_llm

            from langgraph_agents.api.health import check_llm
            result = await check_llm(timeout=5.0)
            assert result.ok is True
            assert result.detail != "skipped"
            mock_llm.ainvoke.assert_called_once()


# ── Parallel execution ───────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_all_checks_runs_in_parallel():
    """All checks must run concurrently — total elapsed < sum of individual latencies."""
    import time
    import asyncio

    async def slow_ok(name, delay):
        await asyncio.sleep(delay)
        from langgraph_agents.api.health import CheckResult
        return CheckResult(name=name, ok=True, latency_ms=delay * 1000)

    mock_graph = MagicMock()
    mock_redis = MagicMock()

    with patch("langgraph_agents.api.health.check_redis",
               new_callable=AsyncMock) as mock_redis_chk, \
         patch("langgraph_agents.api.health.check_postgres",
               new_callable=AsyncMock) as mock_pg, \
         patch("langgraph_agents.api.health.check_llm",
               new_callable=AsyncMock) as mock_llm, \
         patch("langgraph_agents.api.health.check_mcp",
               new_callable=AsyncMock) as mock_mcp, \
         patch("langgraph_agents.api.health.check_graph",
               new_callable=AsyncMock) as mock_graph_chk, \
         patch("langgraph_agents.api.health.check_speechllm",
               new_callable=AsyncMock) as mock_speech, \
         patch("langgraph_agents.api.health.check_searxng",
               new_callable=AsyncMock) as mock_searxng:

        mock_redis_chk.return_value = slow_ok("redis", 0.05)
        mock_pg.return_value = slow_ok("postgres", 0.05)
        mock_llm.return_value = slow_ok("llm", 0.05)
        mock_mcp.return_value = slow_ok("mcp", 0.05)
        mock_graph_chk.return_value = slow_ok("graph", 0.05)
        mock_speech.return_value = slow_ok("speechllm", 0.05)
        mock_searxng.return_value = slow_ok("searxng", 0.05)

        from langgraph_agents.api.health import run_all_checks
        t0 = time.perf_counter()
        result = await run_all_checks(mock_graph, mock_redis)
        elapsed_s = time.perf_counter() - t0

        # 7 checks each sleep 50ms → sequential = 350ms, parallel < 150ms
        assert elapsed_s < 0.20, f"Took {elapsed_s:.2f}s, expected <0.20s (parallel)"
        assert result["all_ok"] is True


# ── redis check is conditional on STM_BACKEND ────────────────────────────────
#
# feature/tts-streaming removed TTS's Redis usage entirely, leaving
# shared/stm.py's RedisStore as the only remaining consumer — and only when
# STM_BACKEND=redis (the default). "dynamodb" and "none" never touch Redis.
# Before this fix, /health/detailed pinged (and required) Redis
# unconditionally, so it 503'd on every machine with no Redis running,
# including the deployed service itself (STM_BACKEND=dynamodb, no VPC, no
# Redis reachable at all — a pre-existing bug this surfaced rather than
# caused). These tests exercise the REAL check_redis / redis_in_use, unlike
# every test above that mocks check_redis away.


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["none", "dynamodb"])
async def test_check_redis_not_pinged_when_stm_backend_is_not_redis(monkeypatch, backend):
    """STM_BACKEND=none/dynamodb: check_redis must not touch the client at
    all (it may be None) and must report ok=True — "not used", not "down"."""
    monkeypatch.setenv("STM_BACKEND", backend)
    from langgraph_agents.api.health import check_redis

    never_touched = MagicMock()
    never_touched.ping = AsyncMock(side_effect=AssertionError("must not be called"))

    result = await check_redis(never_touched)

    assert result.ok is True
    assert backend in result.detail
    never_touched.ping.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["redis", None])  # None = unset → default "redis"
async def test_check_redis_pings_when_stm_backend_is_redis(monkeypatch, backend):
    """STM_BACKEND=redis (or unset — "redis" is shared/stm.py's default too):
    check_redis must actually ping, and a healthy ping reports ok=True with no
    "not used" detail."""
    if backend is None:
        monkeypatch.delenv("STM_BACKEND", raising=False)
    else:
        monkeypatch.setenv("STM_BACKEND", backend)
    from langgraph_agents.api.health import check_redis

    healthy = MagicMock()
    healthy.ping = AsyncMock(return_value=True)

    result = await check_redis(healthy)

    assert result.ok is True
    assert result.detail is None
    healthy.ping.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_redis_down_is_unhealthy_when_stm_backend_is_redis(monkeypatch):
    """The property the fix must NOT remove: STM_BACKEND=redis with a
    genuinely unreachable Redis still reports ok=False."""
    monkeypatch.setenv("STM_BACKEND", "redis")
    from langgraph_agents.api.health import check_redis

    down = MagicMock()
    down.ping = AsyncMock(side_effect=ConnectionError("connection refused"))

    result = await check_redis(down)

    assert result.ok is False
    assert "connection refused" in result.detail


@pytest.mark.unit
@pytest.mark.parametrize("backend,expected", [
    ("redis", True), ("none", False), ("dynamodb", False),
    ("REDIS", True),  # _build_store()/_stm_backend() both lowercase it
])
def test_redis_in_use_reflects_stm_backend(monkeypatch, backend, expected):
    monkeypatch.setenv("STM_BACKEND", backend)
    from langgraph_agents.api.health import redis_in_use
    assert redis_in_use() is expected


@pytest.mark.unit
def test_health_detailed_503_when_stm_backend_redis_and_redis_down(api_client, monkeypatch):
    """End-to-end: STM_BACKEND=redis + Redis actually down → 503, unchanged
    from before this fix. Every check except redis is patched to ok, so a 503
    here can only be the real check_redis (not mocked in this test) doing its
    job."""
    client, _, _ = api_client
    monkeypatch.setenv("STM_BACKEND", "redis")

    import langgraph_agents.api.main as api_module
    down = MagicMock()
    down.ping = AsyncMock(side_effect=ConnectionError("connection refused"))
    # Pre-seed the module-global so _get_health_redis() reuses this fake
    # instead of building a real aioredis client against REDIS_URL.
    monkeypatch.setattr(api_module, "_health_redis", down)

    from langgraph_agents.api.health import CheckResult
    with patch("langgraph_agents.api.health.check_postgres", new_callable=AsyncMock) as m_pg, \
         patch("langgraph_agents.api.health.check_graph", new_callable=AsyncMock) as m_graph, \
         patch("langgraph_agents.api.health.check_llm", new_callable=AsyncMock) as m_llm, \
         patch("langgraph_agents.api.health.check_mcp", new_callable=AsyncMock) as m_mcp, \
         patch("langgraph_agents.api.health.check_speechllm", new_callable=AsyncMock) as m_speech, \
         patch("langgraph_agents.api.health.check_searxng", new_callable=AsyncMock) as m_searxng:
        m_pg.return_value = CheckResult(name="postgres", ok=True, latency_ms=1.0)
        m_graph.return_value = CheckResult(name="graph", ok=True, latency_ms=0.0)
        m_llm.return_value = CheckResult(name="llm", ok=True, latency_ms=0.0, detail="skipped")
        m_mcp.return_value = CheckResult(name="mcp", ok=True, latency_ms=0.0, detail="0 tool(s)")
        m_speech.return_value = CheckResult(name="speechllm", ok=True, latency_ms=0.0, detail="skipped")
        m_searxng.return_value = CheckResult(name="searxng", ok=True, latency_ms=0.0, detail="skipped")

        resp = client.get("/health/detailed")

    assert resp.status_code == 503
    body = resp.json()
    if isinstance(body, list):
        body = body[0]
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"]["ok"] is False
    assert body["checks"]["redis"]["critical"] is True
    down.ping.assert_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("backend", ["none", "dynamodb"])
def test_health_detailed_ok_when_stm_backend_not_redis_and_no_redis_client(api_client, monkeypatch, backend):
    """End-to-end: STM_BACKEND=none/dynamodb → 200 even though no Redis client
    was ever built. Also pins the "don't construct the client" half of the
    fix: `_health_redis` must still be None after the call."""
    client, _, _ = api_client
    monkeypatch.setenv("STM_BACKEND", backend)

    import langgraph_agents.api.main as api_module
    # Explicit reset (monkeypatch restores this after the test regardless of
    # what any other test left behind) rather than asserting on whatever
    # cross-test state happened to be there already.
    monkeypatch.setattr(api_module, "_health_redis", None)

    from langgraph_agents.api.health import CheckResult
    with patch("langgraph_agents.api.health.check_postgres", new_callable=AsyncMock) as m_pg, \
         patch("langgraph_agents.api.health.check_graph", new_callable=AsyncMock) as m_graph, \
         patch("langgraph_agents.api.health.check_llm", new_callable=AsyncMock) as m_llm, \
         patch("langgraph_agents.api.health.check_mcp", new_callable=AsyncMock) as m_mcp, \
         patch("langgraph_agents.api.health.check_speechllm", new_callable=AsyncMock) as m_speech, \
         patch("langgraph_agents.api.health.check_searxng", new_callable=AsyncMock) as m_searxng:
        m_pg.return_value = CheckResult(name="postgres", ok=True, latency_ms=1.0)
        m_graph.return_value = CheckResult(name="graph", ok=True, latency_ms=0.0)
        m_llm.return_value = CheckResult(name="llm", ok=True, latency_ms=0.0, detail="skipped")
        m_mcp.return_value = CheckResult(name="mcp", ok=True, latency_ms=0.0, detail="0 tool(s)")
        m_speech.return_value = CheckResult(name="speechllm", ok=True, latency_ms=0.0, detail="skipped")
        m_searxng.return_value = CheckResult(name="searxng", ok=True, latency_ms=0.0, detail="skipped")

        resp = client.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()
    if isinstance(body, list):
        body = body[0]
    assert body["status"] == "ready"
    assert body["checks"]["redis"]["ok"] is True
    assert backend in body["checks"]["redis"]["detail"]
    # Nothing in this request path should have built a Redis client.
    assert api_module._health_redis is None


# ── check_postgres must not build its own pool ──────────────────────────────
#
# Regression guard for the connection leak found 05/08: the probe used to do
# `PostgresClient()` per call and never close it — 2 connections leaked per
# probe against Neon, never reclaimed. These tests pin the two properties that
# fix depends on, because every other test in this file mocks check_postgres
# away and so covers none of its internals.


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_postgres_reuses_shared_client():
    """It must go through get_pg_client(), never construct a PostgresClient."""
    shared = MagicMock()
    shared.connect = AsyncMock()
    shared.fetchval = AsyncMock(return_value=1)

    from langgraph_agents.api import health

    with patch("langgraph_agents.shared.get_pg_client", return_value=shared), \
         patch("langgraph_agents.db.postgres.PostgresClient") as ctor:
        result = await health.check_postgres()

    assert result.ok is True
    assert ctor.call_count == 0, "check_postgres built its own pool again"
    shared.connect.assert_awaited_once()
    shared.fetchval.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_check_postgres_actually_queries_not_just_connects():
    """A reachable pool with a dead database must report not-ok.

    `connect()` succeeding only proves a pool exists. The probe has to ask the
    database something.
    """
    shared = MagicMock()
    shared.connect = AsyncMock()
    shared.fetchval = AsyncMock(side_effect=RuntimeError("server closed connection"))

    from langgraph_agents.api import health

    with patch("langgraph_agents.shared.get_pg_client", return_value=shared):
        result = await health.check_postgres()

    assert result.ok is False
    assert "server closed connection" in (result.detail or "")
