"""Tier-1 / tier-2 reads (memory-v2 tiers, step 5).

Tier 1 is always in the prompt, verbatim. Tier 2 is retrieved by similarity
to this turn's query, skips rows last seen in this conversation (the STM
summary already covers them), and pulls the hit rows' whole project along.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import memory as mem

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

_CFG = {"timeout_ms": 300, "top_k": 5, "min_similarity": 0.86}


def _row(id="r1", fact="f", project=None, sim=0.9, said_at=_AT):
    return {"id": id, "fact_text": fact, "project": project,
            "said_at": said_at, "similarity": sim}


class _Pg:
    def __init__(self, vector_rows=(), mates=()):
        self.vector_rows = list(vector_rows)
        self.mates = list(mates)
        self.statements: list[str] = []

    async def connect(self):
        return None

    async def fetch(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        if "project = ANY" in sql:
            return self.mates
        return self.vector_rows


def _embed(vec=None, error=None):
    svc = MagicMock()
    if error is not None:
        svc.aembed_query = AsyncMock(side_effect=error)
    else:
        svc.aembed_query = AsyncMock(return_value=vec or [0.1] * 8)
    return svc


@pytest.mark.asyncio
async def test_tier2_hit_is_dated_and_labelled():
    pg = _Pg(vector_rows=[_row("r1", "Tranh squat sau", project="A", sim=0.91)])
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service",
               return_value=_embed()), \
         patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        lines, top_sim, skipped, _ms = await mem._load_tier2_facts(
            "u-1", "s-now", "dau goi")
    assert lines == ["- [2026-10-14] (A) Tranh squat sau"]
    assert top_sim == 0.91
    assert skipped is None


@pytest.mark.asyncio
async def test_tier2_query_filters_the_current_conversation():
    pg = AsyncMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=[])
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service",
               return_value=_embed()), \
         patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        lines, top_sim, skipped, _ms = await mem._load_tier2_facts(
            "u-1", "s-now", "dau goi")
    assert lines == [] and top_sim is None and skipped is None
    sql = pg.fetch.await_args.args[0]
    assert "last_seen_session_id IS DISTINCT FROM $3::uuid" in " ".join(sql.split())


@pytest.mark.asyncio
async def test_tier2_hit_pulls_its_whole_project():
    pg = _Pg(
        vector_rows=[_row("r1", "Chay 5km", project="Run", sim=0.9)],
        # As the mates query filters: only rows carrying a hit label, newest
        # said first; the hit itself must not repeat.
        mates=[_row("r1", "Chay 5km", project="Run", sim=0.9),
               _row("r2", "Nghi 1 ngay", project="Run",
                    said_at=datetime(2026, 10, 13, tzinfo=timezone.utc))],
    )
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service",
               return_value=_embed()), \
         patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        lines, _top, _skipped, _ms = await mem._load_tier2_facts(
            "u-1", "s-now", "chay bo")
    # Direct hit first, then the project mate (hit itself not repeated).
    assert lines == ["- [2026-10-14] (Run) Chay 5km",
                     "- [2026-10-13] (Run) Nghi 1 ngay"]
    mate_sql = pg.statements[1]
    assert "project = ANY" in mate_sql
    assert "last_seen_session_id IS DISTINCT FROM" in mate_sql


@pytest.mark.asyncio
async def test_tier2_hit_without_a_label_pulls_nothing_along():
    pg = _Pg(vector_rows=[_row("r1", "Thich pho", project=None, sim=0.92)])
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service",
               return_value=_embed()), \
         patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        lines, _top, _skipped, _ms = await mem._load_tier2_facts(
            "u-1", "s-now", "an sang")
    assert lines == ["- [2026-10-14] Thich pho"]
    assert len(pg.statements) == 1  # no project follow-up without a label


@pytest.mark.asyncio
async def test_tier2_below_floor_returns_nothing_but_reports_top_sim():
    pg = _Pg(vector_rows=[_row("r1", "Chuyen khac", sim=0.5)])
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service",
               return_value=_embed()), \
         patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        lines, top_sim, skipped, _ms = await mem._load_tier2_facts(
            "u-1", "s-now", "thoi tiet")
    assert lines == [] and top_sim == 0.5 and skipped is None
    assert len(pg.statements) == 1  # no project follow-up without a hit


@pytest.mark.asyncio
async def test_tier2_embed_timeout_leaves_the_turn_running(caplog):
    import asyncio
    slow = MagicMock()

    async def hang(_query):
        await asyncio.sleep(10)
        return [0.1]

    slow.aembed_query = hang
    pg = AsyncMock()
    pg.connect = AsyncMock()
    with patch.object(mem, "get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service", return_value=slow), \
         patch.object(mem, "_tier2_config",
                      return_value={"timeout_ms": 5, "top_k": 5,
                                    "min_similarity": 0.86}):
        with caplog.at_level(logging.DEBUG):
            lines, top_sim, skipped, _ms = await mem._load_tier2_facts(
                "u-1", "s-now", "dau goi")
    assert lines == [] and top_sim is None and skipped == "embed_timeout"
    pg.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_tier2_without_a_query_is_skipped():
    with patch.object(mem, "_tier2_config", return_value=dict(_CFG)):
        assert await mem._load_tier2_facts("u-1", "s-now", None) == (
            [], None, "no_query", None)


@pytest.mark.asyncio
async def test_memory_node_reports_both_tiers(caplog):
    from langchain_core.runnables import RunnableConfig

    async def fake_instructions(_user):
        return ["No overhead lifting"]

    async def fake_stm(_session, _exclude=None):
        return [], []

    async def fake_tier2(_user, _session, _query):
        return (["- [2026-10-14] (Run) Chay 5km"], 0.9, None, 12)

    config = RunnableConfig(configurable={
        "user_id": "u-1", "session_id": "s-1", "query": "chay bo",
        "persona_id": "anne", "request_id": "r-1"})
    with patch.object(mem, "_load_user_instructions",
                      side_effect=fake_instructions), \
         patch.object(mem, "_load_stm", side_effect=fake_stm), \
         patch.object(mem, "_load_tier2_facts", side_effect=fake_tier2):
        with caplog.at_level(logging.INFO, logger="langgraph.memory"):
            out = await mem.memory_node({"messages": []}, config)
    assert out["user_instructions"] == ["No overhead lifting"]
    assert out["user_facts"] == ["- [2026-10-14] (Run) Chay 5km"]
    assert out["session_summary"] == []
    (done,) = [r for r in caplog.records if r.getMessage() == "node_complete"]
    assert done.user_instructions_count == 1
    assert done.user_facts_count == 1
    assert done.tier2_top_similarity == 0.9
    assert done.tier2_embed_ms == 12
    assert done.tier2_skipped is None
    assert done.summary_present is False


def test_render_user_instructions_cuts_from_the_end_whole_lines():
    from langgraph_agents.shared.context import budget_chars, render_user_instructions

    assert render_user_instructions([]) == ""
    assert render_user_instructions(["No overhead lifting"]) == "- No overhead lifting"
    lines = ["a" * 100, "b" * 100, "c" * 100]
    text = render_user_instructions(lines)
    assert text.startswith("- " + "a" * 100)
    assert len(text) <= budget_chars("user_instructions")
    for line in text.split("\n"):
        assert line[2:] in lines
