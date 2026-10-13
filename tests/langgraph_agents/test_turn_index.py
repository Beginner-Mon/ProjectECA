"""Turn index (memory v2, B1b): the switch, the indexer, housekeep, memory_search, evidence.

The switch is off by default, so the first group of tests pins that with it off
nothing changes; the rest drive the new path with the database mocked.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig

from langgraph_agents.db import turn_index
from langgraph_agents.evidence import evidence_items, render_evidence
from langgraph_agents.nodes import summarizer as summ
from langgraph_agents.tools import pgvector_tool

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)


# ── The switch ────────────────────────────────────────────────────────────────


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("VVA_HISTORY_INDEX", raising=False)
    with patch.object(turn_index, "_yaml_enabled", return_value=False):
        assert turn_index.enabled() is False


def test_switch_reads_the_yaml_when_the_env_is_unset(monkeypatch):
    monkeypatch.delenv("VVA_HISTORY_INDEX", raising=False)
    with patch.object(turn_index, "_yaml_enabled", return_value=True):
        assert turn_index.enabled() is True


@pytest.mark.parametrize("env, yaml_value, expected", [
    ("1", False, True), ("0", True, False), ("1", True, True), ("0", False, False),
])
def test_env_beats_yaml(monkeypatch, env, yaml_value, expected):
    monkeypatch.setenv("VVA_HISTORY_INDEX", env)
    with patch.object(turn_index, "_yaml_enabled", return_value=yaml_value):
        assert turn_index.enabled() is expected


def test_the_shipped_yaml_has_the_switch_off_and_the_floor_at_087(monkeypatch):
    monkeypatch.delenv("VVA_HISTORY_INDEX", raising=False)
    assert turn_index.enabled() is False
    assert pgvector_tool._history_min_similarity() == 0.87


# ── The indexer ───────────────────────────────────────────────────────────────


def _pending(n, question="Q", answer="A"):
    return [{
        "assistant_id": uuid.uuid4(), "session_id": uuid.uuid4(),
        "answer": f"{answer}{i}", "answered_at": _NOW,
        "question_id": uuid.uuid4(), "question": f"{question}{i}",
    } for i in range(n)]


def _index_mocks(read_rows, written=None):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(side_effect=[
        read_rows,
        [{"id": uuid.uuid4()} for _ in range(len(read_rows) if written is None else written)],
    ])
    embed = MagicMock()
    embed.aembed_passages = AsyncMock(
        side_effect=lambda texts: [[0.1] * 384 for _ in texts])
    return pg, embed


async def _index(read_rows, limit=8, written=None):
    pg, embed = _index_mocks(read_rows, written)
    with patch("langgraph_agents.shared.get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service", return_value=embed):
        count = await turn_index.index_new_turns("user-1", limit=limit)
    return count, pg, embed


@pytest.mark.asyncio
async def test_text_is_question_newline_answer_and_embedding_input_is_cut_at_1500():
    row = _pending(1)[0]
    row["question"] = "q" * 100
    row["answer"] = "a" * 5000
    count, pg, embed = await _index([row])
    (texts,) = embed.aembed_passages.await_args.args
    assert texts == [("q" * 100 + "\n" + "a" * 5000)[:1500]]
    write_args = pg.fetch.await_args_list[1].args
    stored = [a for a in write_args[1:] if isinstance(a, list) and a and isinstance(a[0], str)
              and a[0].startswith("q")]
    assert len(stored[0][0]) == 4000
    assert stored[0][0] == ("q" * 100 + "\n" + "a" * 5000)[:4000]
    assert count == 1


@pytest.mark.asyncio
async def test_one_read_one_write_and_one_embedding_batch():
    rows = _pending(5)
    count, pg, embed = await _index(rows)
    assert pg.fetch.await_count == 2
    assert embed.aembed_passages.await_count == 1
    assert len(embed.aembed_passages.await_args.args[0]) == 5
    assert count == 5


@pytest.mark.asyncio
async def test_read_is_scoped_to_the_user_limited_and_pairs_the_nearest_earlier_question():
    _, pg, _ = await _index(_pending(2), limit=3)
    sql, *params = pg.fetch.await_args_list[0].args
    assert params == ["user-1", 3]
    assert "NOT EXISTS" in sql and "turn_index" in sql
    assert "m.seq_id < a.seq_id" in sql and "ORDER BY m.seq_id DESC LIMIT 1" in sql
    assert "ORDER BY a.seq_id DESC" in sql


@pytest.mark.asyncio
async def test_write_is_idempotent_on_the_assistant_message_and_stamps_turn_at():
    rows = _pending(1)
    _, pg, _ = await _index(rows)
    sql, *params = pg.fetch.await_args_list[1].args
    assert "ON CONFLICT ON CONSTRAINT turn_index_assistant_message_key DO NOTHING" in sql
    assert [rows[0]["answered_at"]] in params
    assert [rows[0]["assistant_id"]] in params and [rows[0]["question_id"]] in params


@pytest.mark.asyncio
async def test_nothing_pending_writes_and_embeds_nothing():
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=[])
    embed = MagicMock()
    embed.aembed_passages = AsyncMock()
    with patch("langgraph_agents.shared.get_pg_client", return_value=pg), \
         patch("langgraph_agents.shared.get_embedding_service", return_value=embed):
        assert await turn_index.index_new_turns("user-1") == 0
    assert pg.fetch.await_count == 1
    embed.aembed_passages.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_what_was_written_not_what_was_tried(caplog):
    with caplog.at_level(logging.INFO, logger="langgraph.turn_index"):
        count, _, _ = await _index(_pending(3), written=2)
    assert count == 2
    (done,) = [r for r in caplog.records if r.getMessage() == "turn_index_done"]
    assert done.count == 2 and done.elapsed_ms >= 0


def test_importing_the_module_does_not_import_the_embedding_model():
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "agenticRAG"
    code = ("import sys, langgraph_agents.db.turn_index; "
            "sys.exit(1 if 'langgraph_agents.shared.embedding' in sys.modules else 0)")
    assert subprocess.run(
        [sys.executable, "-c", code], env={**os.environ, "PYTHONPATH": str(root)}).returncode == 0


# ── housekeep ─────────────────────────────────────────────────────────────────


def _housekeep_patches(on, index_error=None):
    calls = []

    async def record(name, *args, **kwargs):
        calls.append(name)
        if name == "index" and index_error is not None:
            raise index_error
        return 2

    def step(name):
        async def run(*args, **kwargs):
            return await record(name)
        return AsyncMock(side_effect=run)

    finished, compact, index = step("finished"), step("compact"), step("index")
    return calls, finished, compact, index, [
        patch("langgraph_agents.db.turn_index.enabled", return_value=on),
        patch.object(summ, "summarize_finished_sessions", new=finished),
        patch.object(summ, "maybe_summarize", new=compact),
        patch("langgraph_agents.db.turn_index.index_new_turns", new=index),
    ]


@pytest.mark.asyncio
async def test_housekeep_with_the_switch_off_is_what_it_was():
    calls, finished, compact, index, patches = _housekeep_patches(False)
    for p in patches:
        p.start()
    try:
        await summ.housekeep("user-1", "s-1", history_budget=5, compact_at=9)
    finally:
        patch.stopall()
    assert calls == ["finished", "compact"]
    finished.assert_awaited_once_with("user-1", "s-1")
    compact.assert_awaited_once_with("s-1", 5, 9)
    index.assert_not_awaited()


@pytest.mark.asyncio
async def test_housekeep_with_the_switch_on_indexes_first_skips_summaries_and_compacts():
    calls, finished, compact, index, patches = _housekeep_patches(True)
    for p in patches:
        p.start()
    try:
        await summ.housekeep("user-1", "s-1", history_budget=5, compact_at=9)
    finally:
        patch.stopall()
    assert calls == ["index", "compact"]
    index.assert_awaited_once_with("user-1")
    finished.assert_not_awaited()
    compact.assert_awaited_once_with("s-1", 5, 9)


@pytest.mark.asyncio
async def test_housekeep_survives_an_indexing_error(caplog):
    calls, finished, compact, index, patches = _housekeep_patches(
        True, index_error=RuntimeError("db down"))
    for p in patches:
        p.start()
    try:
        with caplog.at_level(logging.WARNING, logger="langgraph.summarizer"):
            await summ.housekeep("user-1", "s-1")
    finally:
        patch.stopall()
    compact.assert_awaited_once_with("s-1", None, None)
    assert any(r.getMessage() == "turn_index_failed" for r in caplog.records)


# ── memory_search ─────────────────────────────────────────────────────────────


def _config(user_words="lần trước bạn gợi ý bài gì cho đầu gối?", session_id=None):
    return RunnableConfig(configurable={
        "user_id": str(uuid.uuid4()), "session_id": session_id or str(uuid.uuid4()),
        "query": user_words, "request_id": "r-1",
    })


def _turn_row(sim, session="s-1", title="Knee pain", text="Q\nA", turn_at=_NOW):
    return {"content": text, "session_id": session, "title": title,
            "turn_at": turn_at, "similarity": sim}


async def _search(rows, args=None, config=None, min_sim=0.87):
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(return_value=rows)
    embed = MagicMock()
    embed.aembed_query = AsyncMock(side_effect=lambda t: [float(len(t))] * 384)
    with patch("langgraph_agents.db.turn_index.enabled", return_value=True), \
         patch.object(pgvector_tool, "get_pg_client", return_value=pg), \
         patch.object(pgvector_tool, "get_embedding_service", return_value=embed), \
         patch.object(pgvector_tool, "_history_min_similarity", return_value=min_sim):
        result = await pgvector_tool.memory_search.ainvoke(
            args or {"query": "knee exercises"}, config or _config())
    return result, pg, embed


@pytest.mark.asyncio
async def test_new_path_returns_the_listed_fields():
    result, _, _ = await _search([_turn_row(0.91, text="x" * 900)])
    (r,) = result["results"]
    assert set(r) == {"snippet", "session_id", "session_title", "date", "similarity"}
    assert len(r["snippet"]) == 700
    assert r["session_title"] == "Knee pain" and r["date"] == "2026-10-05"
    assert r["similarity"] == 0.91


@pytest.mark.asyncio
async def test_new_path_null_title_is_kept_as_none():
    result, _, _ = await _search([_turn_row(0.91, title=None)])
    assert result["results"][0]["session_title"] is None


@pytest.mark.asyncio
async def test_new_path_applies_the_floor(caplog):
    with caplog.at_level(logging.INFO, logger="langgraph.tools"):
        result, _, _ = await _search([_turn_row(0.90), _turn_row(0.86, "s-2")])
    assert [r["similarity"] for r in result["results"]] == [0.90]
    (done,) = [r for r in caplog.records if r.getMessage() == "memory_search_done"]
    assert done.top_similarity == 0.90 and done.dropped_below_min == 1
    assert done.source == "turn_index"


@pytest.mark.asyncio
async def test_new_path_nothing_above_the_floor_is_not_found():
    result, _, _ = await _search([_turn_row(0.80)])
    assert result == {"found": False}


@pytest.mark.asyncio
async def test_new_path_caps_at_three_even_when_asked_for_more():
    result, pg, _ = await _search([_turn_row(0.95, f"s-{i}") for i in range(3)],
                                  {"query": "knee", "top_k": 10})
    assert pg.fetch.await_args.args[-1] == 3
    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_new_path_excludes_the_current_conversation_and_other_users():
    config = _config(session_id="11111111-1111-1111-1111-111111111111")
    _, pg, _ = await _search([_turn_row(0.9)], config=config)
    sql, q_vec, words_vec, user_id, current, *_ = pg.fetch.await_args.args
    assert "turn_index" in sql and "summaries" not in sql
    assert "c.user_id = $3" in sql and "t.session_id != $4" in sql
    assert "GREATEST(1 - (t.embedding <=> $1), 1 - (t.embedding <=> $2))" in sql
    assert user_id == config["configurable"]["user_id"]
    assert current == "11111111-1111-1111-1111-111111111111"
    assert q_vec != words_vec


@pytest.mark.asyncio
async def test_new_path_since_days_filters_on_turn_at():
    _, pg, _ = await _search([_turn_row(0.9)], {"query": "knee", "since_days": 7})
    sql, *params = pg.fetch.await_args.args
    assert "t.turn_at >= now()" in sql and "7" in params
    _, pg2, _ = await _search([_turn_row(0.9)], {"query": "knee"})
    assert "turn_at >=" not in pg2.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_new_path_embeds_the_users_words_too_and_once_when_equal():
    _, _, embed = await _search([_turn_row(0.9)])
    assert embed.aembed_query.await_count == 2
    _, _, embed = await _search([_turn_row(0.9)], config=_config(user_words="knee exercises"))
    assert embed.aembed_query.await_count == 1


@pytest.mark.asyncio
async def test_new_path_keeps_the_ambiguity_fields():
    result, _, _ = await _search([_turn_row(0.92, "s-1"), _turn_row(0.90, "s-2")])
    assert result["ambiguous"] is True
    assert [c["session_id"] for c in result["candidates"]] == ["s-1", "s-2"]
    result, _, _ = await _search([_turn_row(0.92, "s-1"), _turn_row(0.90, "s-1")])
    assert "ambiguous" not in result


@pytest.mark.asyncio
async def test_old_path_is_untouched_with_the_switch_off():
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch = AsyncMock(side_effect=[[{"session_id": "s-1"}], []])
    embed = MagicMock()
    embed.aembed_query = AsyncMock(return_value=[0.0] * 384)
    with patch("langgraph_agents.db.turn_index.enabled", return_value=False), \
         patch.object(pgvector_tool, "get_pg_client", return_value=pg), \
         patch.object(pgvector_tool, "get_embedding_service", return_value=embed):
        result = await pgvector_tool.memory_search.ainvoke({"query": "knee"}, _config())
    assert result == {"found": False}
    assert pg.fetch.await_count == 2
    assert "FROM summaries s" in pg.fetch.await_args.args[0]


# ── evidence ──────────────────────────────────────────────────────────────────


def _tm(payload):
    return ToolMessage(content=json.dumps(payload), tool_call_id="tc-1", name="memory_search")


def test_evidence_renders_turn_results_as_text_items():
    payload = {"found": True, "results": [
        {"snippet": "Q one\nA one", "session_id": "s-1", "session_title": "Knee pain",
         "date": "2026-10-05", "similarity": 0.9},
        {"snippet": "Q two\nA two", "session_id": "s-2", "session_title": None,
         "date": "2026-10-01", "similarity": 0.88},
    ]}
    items = evidence_items([_tm(payload)])
    assert len(items) == 2
    assert items[0].header == "[From your earlier conversations with this user]"
    assert items[0].text == "2026-10-05 — Knee pain\nQ one\nA one"
    assert items[1].text == "2026-10-01\nQ two\nA two"
    assert all(i.cite_key is None and i.title == "" for i in items)
    assert "similarity" not in render_evidence(items)


def test_evidence_caps_each_rendered_item():
    payload = {"found": True, "results": [
        {"snippet": "z" * 5000, "session_id": "s", "session_title": "t",
         "date": "2026-10-05", "similarity": 0.9}]}
    (item,) = evidence_items([_tm(payload)])
    assert len(item.text) == 1500


def test_evidence_leaves_the_summary_shape_as_it_was():
    payload = {"found": True, "results": [{"summary_text": "talked about squats"}]}
    (item,) = evidence_items([_tm(payload)])
    assert item.text == json.dumps(payload)
    assert item.header == "[From your earlier conversations with this user]"
