"""The no-database guard (conftest autouse fixture, `db_guard.py`)."""

from __future__ import annotations

import asyncio

import pytest

from tests.langgraph_agents.loop.db_guard import DatabaseAccessError


def test_get_pg_client_is_refused_and_names_the_test():
    from langgraph_agents.shared import get_pg_client
    with pytest.raises(DatabaseAccessError) as exc:
        get_pg_client().connect()
    assert "test_get_pg_client_is_refused_and_names_the_test" in str(exc.value)


def test_connect_on_any_client_is_refused_and_names_the_test():
    from langgraph_agents.db.postgres import PostgresClient
    client = PostgresClient(dsn="postgresql://u:p@localhost:1/none")
    with pytest.raises(DatabaseAccessError) as exc:
        asyncio.run(client.connect())
    assert "test_connect_on_any_client_is_refused_and_names_the_test" in str(exc.value)


def test_default_stores_are_in_memory():
    from langgraph_agents.loop import leases, rate
    assert isinstance(leases.get_lease_store(), leases.MemoryLeaseStore)
    assert isinstance(rate.get_rate_counter(), rate.MemoryRateCounter)


def test_test_pg_client_is_the_only_opt_out(test_pg_client):
    # Reached only where ECA_TEST_PG_DSN is set and reachable (CI); a missing
    # database skips in the fixture ("PG unreachable"), it does not hit the guard.
    assert getattr(test_pg_client, "_eca_test_client", False) is True
