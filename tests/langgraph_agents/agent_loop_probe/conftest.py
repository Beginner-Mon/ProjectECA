"""Same no-database guard as the loop suite (see
`tests/langgraph_agents/loop/db_guard.py`)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_database(request, monkeypatch):
    from tests.langgraph_agents.loop import db_guard
    db_guard.install(monkeypatch, request.node.nodeid)
