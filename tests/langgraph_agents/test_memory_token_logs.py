"""Token telemetry on the two background LLM logs (memory-v2 step 1).

`fact_extraction_done` and `summarizer_done` must carry `input_tokens` /
`output_tokens` read from the model answer's `usage_metadata`, so benchmark A
already measures background cost. No database and no real LLM here.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.nodes import fact_extractor as fe
from langgraph_agents.nodes import summarizer as summ

pytestmark = pytest.mark.unit

_USER = "11111111-1111-1111-1111-111111111111"
_SESSION = "22222222-2222-2222-2222-222222222222"
_AT = datetime(2026, 10, 12, 23, 30, tzinfo=timezone.utc)


def _done(records, name):
    return [r for r in records if r.getMessage() == name]


# ── fact_extractor ──────────────────────────────────────────────────────


class _FeConn:
    def __init__(self, tag="UPDATE 1"):
        self.tag = tag

    async def execute(self, sql, *args):
        return self.tag


class _FePg:
    def __init__(self, unread, facts=()):
        self.unread = list(unread)
        self.facts = list(facts)

    async def fetch(self, sql, *args):
        return self.unread if sql is fe._UNREAD_SQL else self.facts

    async def execute(self, sql, *args):
        return "UPDATE 1"

    @asynccontextmanager
    async def transaction(self):
        yield _FeConn()


def _fe_msg(seq, role, content):
    return {"session_id": _SESSION, "ltm_up_to_seq": 0, "seq_id": seq,
            "role": role, "content": content, "created_at": _AT}


def _fe_llm(raw):
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value={
        "raw": raw, "parsed": fe.Operations(operations=[]), "parsing_error": None})
    return llm


async def test_fact_extraction_done_carries_token_counts(caplog):
    pg = _FePg([_fe_msg(1, "user", "I have back pain."),
                _fe_msg(2, "assistant", "Noted.")])
    raw = SimpleNamespace(content="{}",
                          usage_metadata={"input_tokens": 100, "output_tokens": 20})
    with patch.object(fe, "get_pg_client", return_value=pg), \
         patch.object(fe, "extraction_llm", return_value=_fe_llm(raw)):
        with caplog.at_level(logging.DEBUG):
            await fe.extract_user_facts(_USER, _SESSION)
    (rec,) = _done(caplog.records, "fact_extraction_done")
    assert rec.input_tokens == 100
    assert rec.output_tokens == 20
    assert isinstance(rec.elapsed_ms, int)


async def test_fact_extraction_done_tolerates_missing_usage(caplog):
    pg = _FePg([_fe_msg(1, "user", "I have back pain."),
                _fe_msg(2, "assistant", "Noted.")])
    with patch.object(fe, "get_pg_client", return_value=pg), \
         patch.object(fe, "extraction_llm", return_value=_fe_llm(SimpleNamespace(content="{}"))):
        with caplog.at_level(logging.DEBUG):
            await fe.extract_user_facts(_USER, _SESSION)
    (rec,) = _done(caplog.records, "fact_extraction_done")
    assert rec.input_tokens is None
    assert rec.output_tokens is None


# ── summarizer ──────────────────────────────────────────────────────────


class _SumPg:
    def __init__(self, rows):
        self.rows = list(rows)

    async def connect(self):
        return None

    async def fetchrow(self, sql, *args):
        return {"stm_summary": None, "stm_up_to_seq": 0}

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        return "UPDATE 1"


async def test_stm_compacted_carries_token_counts(caplog):
    rows = [{"seq_id": 1, "role": "user", "content": "my knee hurts " * 250},
            {"seq_id": 2, "role": "assistant", "content": "try heel slides " * 250},
            {"seq_id": 3, "role": "user", "content": "stairs hurt too " * 250},
            {"seq_id": 4, "role": "assistant", "content": "try step-ups " * 250}]
    ai_msg = SimpleNamespace(content="A summary.",
                             usage_metadata={"input_tokens": 50, "output_tokens": 10})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=ai_msg)
    with patch.object(summ, "get_pg_client", return_value=_SumPg(rows)), \
         patch.object(summ, "get_chat_model", return_value=llm):
        with caplog.at_level(logging.DEBUG):
            await summ.compact_stm(_SESSION)
    (rec,) = _done(caplog.records, "stm_compacted")
    assert rec.input_tokens == 50
    assert rec.output_tokens == 10
    assert isinstance(rec.elapsed_ms, int)


async def test_stm_compacted_tolerates_missing_usage(caplog):
    rows = [{"seq_id": 1, "role": "user", "content": "my knee hurts " * 250},
            {"seq_id": 2, "role": "assistant", "content": "try heel slides " * 250},
            {"seq_id": 3, "role": "user", "content": "stairs hurt too " * 250},
            {"seq_id": 4, "role": "assistant", "content": "try step-ups " * 250}]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content="A summary."))
    with patch.object(summ, "get_pg_client", return_value=_SumPg(rows)), \
         patch.object(summ, "get_chat_model", return_value=llm):
        with caplog.at_level(logging.DEBUG):
            await summ.compact_stm(_SESSION)
    (rec,) = _done(caplog.records, "stm_compacted")
    assert rec.input_tokens is None
    assert rec.output_tokens is None
    assert str(uuid.UUID(_SESSION)) == _SESSION  # session id passes through untouched
