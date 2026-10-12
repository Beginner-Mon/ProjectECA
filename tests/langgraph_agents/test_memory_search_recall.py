"""memory_search: scored on the user's own words, thresholded, dated by the talk (12/10).

The retriever writes memory_search queries in English (prod logs 14/09–10/10);
summaries are written in the conversation's language. Measured on e5-small:
English query vs Vietnamese summary put the right summary first in 1 of 7
cases. And with no threshold, any summary came back for any query — prod
10/10, "who are you and what is your back story" returned one.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

from langgraph_agents.tools import pgvector_tool

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


def _config(user_words="lần trước bạn gợi ý bài gì cho đầu gối?"):
    return RunnableConfig(configurable={
        "user_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
        "query": user_words, "request_id": "r-1",
    })


def _row(sim, session="s-1"):
    return {"summary_text": f"summary {sim}", "session_id": session,
            "created_at": _NOW, "similarity": sim}


def _mocks(rows):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(side_effect=[[{"session_id": "s-1"}], rows])
    embed = MagicMock()
    embed.aembed_query = AsyncMock(side_effect=lambda t: [float(len(t))] * 384)
    return pg, embed


async def _search(rows, args, config, min_sim=0.84):
    pg, embed = _mocks(rows)
    with patch.object(pgvector_tool, "get_pg_client", return_value=pg), \
         patch.object(pgvector_tool, "get_embedding_service", return_value=embed), \
         patch.object(pgvector_tool, "_memory_min_similarity", return_value=min_sim):
        result = await pgvector_tool.memory_search.ainvoke(args, config)
    return result, pg, embed


@pytest.mark.asyncio
async def test_scores_the_users_own_words_as_well_as_the_retrievers_query():
    config = _config()
    result, pg, embed = await _search([_row(0.88)], {"query": "knee exercises"}, config)
    assert result["found"] is True
    assert [c.args[0] for c in embed.aembed_query.await_args_list] == [
        "knee exercises", "lần trước bạn gợi ý bài gì cho đầu gối?"]
    sql, q_vec, words_vec, *_ = pg.fetch.await_args.args
    assert "GREATEST(1 - (s.embedding <=> $1), 1 - (s.embedding <=> $2))" in sql
    assert q_vec != words_vec


@pytest.mark.asyncio
async def test_one_embedding_when_the_query_is_the_users_words():
    config = _config(user_words="knee exercises")
    _, pg, embed = await _search([_row(0.88)], {"query": "knee exercises"}, config)
    assert embed.aembed_query.await_count == 1
    _, q_vec, words_vec, *_ = pg.fetch.await_args.args
    assert q_vec == words_vec


@pytest.mark.asyncio
async def test_summaries_below_the_threshold_are_not_returned(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="langgraph.tools"):
        result, _, _ = await _search([_row(0.81), _row(0.80, "s-2")],
                                     {"query": "who are you"}, _config("Bạn là ai"))
    assert result == {"found": False}
    (done,) = [r for r in caplog.records if r.getMessage() == "memory_search_done"]
    assert done.top_similarity == 0.81 and done.dropped_below_min == 2


@pytest.mark.asyncio
async def test_threshold_keeps_what_clears_it():
    result, _, _ = await _search([_row(0.90), _row(0.83, "s-2")],
                                 {"query": "knee exercises"}, _config())
    assert [r["similarity"] for r in result["results"]] == [0.90]


@pytest.mark.asyncio
async def test_no_threshold_configured_returns_everything():
    result, _, _ = await _search([_row(0.70)], {"query": "x"}, _config(), min_sim=None)
    assert result["found"] is True


@pytest.mark.asyncio
async def test_dates_come_from_the_conversation_not_the_summary():
    result, pg, _ = await _search([_row(0.9)], {"query": "knee", "since_days": 7}, _config())
    sql, *params = pg.fetch.await_args.args
    assert "m.seq_id = s.covers_up_to_seq" in sql
    assert "COALESCE(m.created_at, s.created_at) >= now() - ($5 || ' days')" in sql
    assert params[4:] == ["7", 3]
    assert result["results"][0]["created_at"] == _NOW.isoformat()


def test_config_sets_the_measured_threshold():
    assert pgvector_tool._memory_min_similarity() == 0.84
