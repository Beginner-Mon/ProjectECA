"""Memory layer tenant-isolation tests — PR 1 acceptance criteria.

Requires Docker PostgreSQL (port 5433) with M.4 schema migrated.
Tests: tool schema (config params hidden), retriever tool list.
NOTE (memory-v2 tiers, step 5): memory_search is gone — tier-2 tenant
isolation is pinned by test_memory_tiers_integration.py (step 7) instead.
"""

import json
import uuid
import pytest

from langchain_core.runnables import RunnableConfig

HAS_PG = False
try:
    import asyncpg
    async def _ping():
        conn = await asyncpg.connect("postgresql://eca:eca_dev@localhost:5433/eca")
        await conn.close()
        return True
    import asyncio
    HAS_PG = asyncio.run(_ping())
except Exception:
    pass


def _make_config(user_id="test-user-a", session_id=None):
    return RunnableConfig(configurable={
        "user_id": user_id,
        "session_id": session_id or str(uuid.uuid4()),
        "query": "test",
        "persona_id": "anne",
        "request_id": "req-test",
    })


# ═══════════════════════════════════════════════════════════════════════════
# Retriever tool list: memory_search and resume_last_session are gone
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_memory_search_gone_from_retriever_tools():
    """memory_search must NOT be in RETRIEVER_BASE_TOOLS; resume is gone too."""
    from langgraph_agents.nodes.retriever_agent import RETRIEVER_BASE_TOOLS
    tool_names = {t.name for t in RETRIEVER_BASE_TOOLS}
    assert "memory_search" not in tool_names, "memory_search was removed in step 5"
    assert "resume_last_session" not in tool_names, "resume_last_session was removed (16/10/2026)"
    assert "kb_search" in tool_names


@pytest.mark.unit
def test_resume_gone_from_tools_init_export():
    """tools/__init__.py must not export resume_last_session."""
    import langgraph_agents.tools as tools_pkg
    assert "resume_last_session" not in tools_pkg.__all__
    assert not hasattr(tools_pkg, "resume_last_session")
