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
# LLM tool schema (config params hidden)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_resume_last_session_schema_hides_user_id():
    """resume_last_session tool schema must NOT expose user_id/config to LLM."""
    from langgraph_agents.tools.pgvector_tool import resume_last_session
    schema = resume_last_session.tool_call_schema
    from pydantic import BaseModel
    assert isinstance(schema, type) and issubclass(schema, BaseModel)
    fields = schema.model_fields
    assert "since_days" in fields, "since_days must be visible to LLM"
    assert "user_id" not in fields, "user_id must NOT be in LLM schema"
    assert "current_session_id" not in fields, "current_session_id must NOT be in LLM schema"
    assert "config" not in fields, "config must NOT be in LLM schema (RunnableConfig is hidden)"


# ═══════════════════════════════════════════════════════════════════════════
# Retriever tool list: memory_search is gone, resume stays
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
def test_memory_search_gone_from_retriever_tools():
    """memory_search must NOT be in RETRIEVER_BASE_TOOLS; resume stays."""
    from langgraph_agents.nodes.retriever_agent import RETRIEVER_BASE_TOOLS
    tool_names = {t.name for t in RETRIEVER_BASE_TOOLS}
    assert "memory_search" not in tool_names, "memory_search was removed in step 5"
    assert "resume_last_session" in tool_names, "resume_last_session must be in RETRIEVER_BASE_TOOLS"
    assert "kb_search" in tool_names
