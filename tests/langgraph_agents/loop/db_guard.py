"""The no-database guard shared by the loop and `agent_loop_probe` suites.

Twice a loop test reached the database behind `VVA_PG_DSN` (prod) through a
default store. `install()` makes that impossible for every test of a suite:

* `PostgresClient.connect` raises, naming the test, unless the client carries
  `_eca_test_client = True` (only `test_pg_client` sets it);
* `shared.get_pg_client` raises the same way;
* the lease, rate, action and turn stores are the in-memory ones, so a test that
  forgets to install them does not fall back to Postgres.

Module scope imports stdlib only (the root-conftest rule).
"""

from __future__ import annotations

TEST_CLIENT_FLAG = "_eca_test_client"


class DatabaseAccessError(RuntimeError):
    """A test tried to reach a database it did not ask for."""


def install(monkeypatch, test_id: str) -> None:
    import langgraph_agents.shared as shared
    from langgraph_agents.db.postgres import PostgresClient
    from langgraph_agents.loop import actions, crypto, leases, rate, turns

    real_connect = PostgresClient.connect

    async def guarded_connect(self, *args, **kwargs):
        if getattr(self, TEST_CLIENT_FLAG, False):
            return await real_connect(self, *args, **kwargs)
        raise DatabaseAccessError(
            f"{test_id}: PostgresClient.connect() without the test_pg_client "
            "fixture. This test reached a database (the app's, if VVA_PG_DSN "
            "is set). Give it a memory store.")

    def guarded_get_pg_client():
        raise DatabaseAccessError(
            f"{test_id}: get_pg_client() is the app database. Use the "
            "test_pg_client fixture, or give the test a memory store.")

    monkeypatch.setattr(PostgresClient, "connect", guarded_connect)
    monkeypatch.setattr(shared, "get_pg_client", guarded_get_pg_client)
    monkeypatch.setattr(leases, "_store", leases.MemoryLeaseStore())
    monkeypatch.setattr(rate, "_counter", rate.MemoryRateCounter())
    monkeypatch.setattr(actions, "_store", actions.MemoryActionStore())
    monkeypatch.setattr(turns, "_store", turns.MemoryTurnStore())
    # No sealer unless the test installs one (fail closed, E4).
    monkeypatch.setattr(crypto, "_sealer", None)
