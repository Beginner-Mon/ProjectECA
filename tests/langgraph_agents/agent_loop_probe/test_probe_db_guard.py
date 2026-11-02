"""The probe suite carries the same no-database guard as the loop suite."""

from __future__ import annotations

import pytest

from tests.langgraph_agents.loop.db_guard import DatabaseAccessError


def test_get_pg_client_is_refused_here_too():
    from langgraph_agents.shared import get_pg_client
    with pytest.raises(DatabaseAccessError):
        get_pg_client()
