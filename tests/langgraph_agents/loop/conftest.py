"""Fixtures for the loop suite: the test Postgres client.

The PG contract runs (`pg` lease params, marked `integration`) must never
touch the application database. They connect through `test_pg_client`,
which builds its own `PostgresClient(dsn=ECA_TEST_PG_DSN)` and refuses a DSN
whose host is the app's (`VVA_PG_DSN`). Nothing here calls `get_pg_client()`.

Module scope imports stdlib only (the root-conftest rule): everything else is
imported inside the function that needs it.
"""

from __future__ import annotations

import asyncio
from typing import Mapping
from urllib.parse import urlparse

import pytest

_APP_DSN_VARS = ("VVA_PG_DSN", "POSTGRES_DSN")


def _host(dsn: str | None) -> str | None:
    """Host of a DSN, with Neon's `-pooler` suffix folded away (the pooler and
    direct endpoints are the same database)."""
    if not dsn:
        return None
    try:
        host = urlparse(dsn).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower().replace("-pooler", "")


def resolve_test_dsn(env: Mapping[str, str] | None = None
                     ) -> tuple[str | None, str | None]:
    """Return `(dsn, skip_reason)` for the PG contract runs.

    - `ECA_TEST_PG_DSN` unset            -> (None, "ECA_TEST_PG_DSN not set")
    - its host equals the app DB's host  -> (None, "ECA_TEST_PG_DSN points at
      the app database")
    - otherwise                          -> (dsn, None)

    With no `env` given, `agenticRAG/.env` is loaded first and `os.environ`
    is read. The app DSN is only compared by host; it is never connected to.
    """
    if env is None:
        import os
        from langgraph_agents.shared.env import load_env
        load_env()
        env = os.environ
    dsn = env.get("ECA_TEST_PG_DSN")
    if not dsn:
        return None, "ECA_TEST_PG_DSN not set"
    test_host = _host(dsn)
    for var in _APP_DSN_VARS:
        app_host = _host(env.get(var))
        if app_host is not None and app_host == test_host:
            return None, "ECA_TEST_PG_DSN points at the app database"
    return dsn, None


@pytest.fixture(autouse=True)
def _no_database(request, monkeypatch):
    """No test of this suite may open a database connection, unless its
    client came from `test_pg_client`. See `db_guard.py`."""
    from tests.langgraph_agents.loop import db_guard
    db_guard.install(monkeypatch, request.node.nodeid)


@pytest.fixture()
def test_pg_client():
    """A `PostgresClient` on the test database, closed after the test.

    Skips with a clear reason when the DSN is missing or is the app's, when
    PG is unreachable, or when migration 019 (`agent_leases`) is missing.
    Each test body runs its own `asyncio.run`, so the pool is rebuilt when
    the running loop changes (an asyncpg pool is bound to its loop).
    """
    dsn, reason = resolve_test_dsn()
    if dsn is None:
        pytest.skip(reason or "ECA_TEST_PG_DSN not set")

    from langgraph_agents.db.postgres import PostgresClient
    client = PostgresClient(dsn=dsn)
    setattr(client, "_eca_test_client", True)   # opts out of the db guard
    state: dict = {"loop": None}

    def _factory():
        loop = asyncio.get_running_loop()
        if state["loop"] is not loop:
            client._pool = None          # the old pool died with its loop
            client._connect_lock = None
            state["loop"] = loop
        return client

    async def _probe() -> bool:
        pg = _factory()
        async with pg.transaction() as conn:
            return bool(await conn.fetchval(
                "SELECT to_regclass('agent_leases')"))

    try:
        has_table = asyncio.run(_probe())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG unreachable: {type(exc).__name__}")
    if not has_table:
        pytest.skip("migration 019 not applied")

    client.get_client = _factory  # type: ignore[attr-defined]
    yield client

    async def _close():
        if client._pool is not None:
            await client.close()

    try:
        asyncio.run(_close())
    except Exception:  # noqa: BLE001 - the pool may belong to a closed loop
        client._pool = None
