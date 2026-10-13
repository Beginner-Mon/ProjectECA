"""A7 backend — prompt budgets follow the user's model window (unit)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from langgraph_agents.llm_byok import RequestLLM, bind_request_llm
from langgraph_agents.nodes import summarizer as summ
from langgraph_agents.shared import context as ctx_mod
from langgraph_agents.shared.context import budget_chars, context_budget

from test_byok_chat import _llm_body, _make_client

pytestmark = pytest.mark.unit

_BASE = {
    "history": 1000, "evidence": 1200, "session_summary": 500,
    "history_compact_at": 2000, "user_facts": 300, "persona": 600,
    "about_you": 250,
}
_SCALED = ("history", "evidence", "session_summary", "history_compact_at")
_UNSCALED = ("user_facts", "persona", "about_you")


@pytest.fixture(autouse=True)
def fixed_budgets(monkeypatch):
    monkeypatch.setattr(ctx_mod, "_load_context_budgets", lambda: dict(_BASE))
    yield
    bind_request_llm(None)


def _bind(window=None, failure=None):
    bind_request_llm(RequestLLM(
        base_url="https://x.example/v1", model="m", api_key="k",
        context_window=window, failure=failure,
    ))


@pytest.mark.parametrize("window,scale", [
    (128_000, 4.0), (20_000, 2.0), (10_000, 1.0), (5_000, 0.5), (2_000, 0.3),
])
def test_scaled_keys_follow_the_window(window, scale):
    _bind(window)
    for key in _SCALED:
        assert context_budget(key) == int(round(_BASE[key] * scale))
    for key in _UNSCALED:
        assert context_budget(key) == _BASE[key]
    assert budget_chars("history") == context_budget("history") * 4


def test_no_request_llm_is_unchanged():
    for key, value in _BASE.items():
        assert context_budget(key) == value


def test_request_llm_without_window_is_unchanged():
    _bind(None)
    for key, value in _BASE.items():
        assert context_budget(key) == value


def test_failed_request_llm_is_unchanged():
    _bind(128_000, failure={"kind": "auth", "status": 401})
    for key, value in _BASE.items():
        assert context_budget(key) == value


# ── /chat ──────────────────────────────────────────────────────────────

def _chat_window(monkeypatch, body_extra, with_llm=True):
    seen = {}

    async def fake_stream(state, config, stream_mode=None):
        from langgraph_agents.llm_byok import current_request_llm
        seen["req"] = current_request_llm()
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)
    body = {"query": "hi", **body_extra}
    if with_llm:
        body["llm"] = _llm_body()
    try:
        resp = client.post("/chat", json=body)
        assert resp.status_code == 200
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()
        bind_request_llm(None)
    return seen["req"]


@pytest.mark.parametrize("sent,expected", [
    (128_000, 128_000), (500, 1_000), (50_000_000, 2_000_000),
    (0, None), (-5, None), (None, None),
])
def test_chat_builds_request_llm_with_clamped_window(monkeypatch, sent, expected):
    req = _chat_window(monkeypatch, {"token_limit": sent})
    assert req.context_window == expected


def test_chat_token_limit_without_llm_binds_nothing(monkeypatch):
    assert _chat_window(monkeypatch, {"token_limit": 128_000}, with_llm=False) is None


# ── summariser ─────────────────────────────────────────────────────────

def _rows(*turns):
    rows, seq = [], 0
    for q, a in turns:
        rows.append({"seq_id": seq + 1, "role": "user", "content": "q" * (q * 4)})
        rows.append({"seq_id": seq + 2, "role": "assistant", "content": "a" * (a * 4)})
        seq += 2
    return rows


def test_compaction_cut_uses_given_budgets():
    rows = _rows((300, 300), (300, 300), (300, 300))      # 1800 tokens
    assert summ._compaction_cut(rows) is None              # <= 2000, defaults
    assert summ._compaction_cut(rows, 600, 600) == 4       # small window: earlier
    assert summ._compaction_cut(rows, 4000, 8000) is None  # large window: later
    big = _rows((300, 300)) * 1
    assert summ._compaction_cut(big, 600, 600) is None     # one turn: nothing older


@pytest.mark.asyncio
async def test_maybe_summarize_passes_budgets_to_cut():
    pg = AsyncMock()
    pg.fetchval.return_value = 0
    pg.fetch.return_value = _rows((300, 300), (300, 300), (300, 300))
    with patch.object(summ, "get_pg_client", return_value=pg), \
         patch.object(summ, "_run_summarize", new=AsyncMock()) as run:
        await summ.maybe_summarize("s-1", 600, 600)
    run.assert_awaited_once_with("s-1", 0, 4)


@pytest.mark.asyncio
async def test_housekeep_forwards_budgets_and_still_unbinds():
    from langgraph_agents.llm_byok import current_request_llm
    seen = {}

    async def record(*args):
        seen["args"] = args
        seen["llm"] = current_request_llm()

    _bind(5_000)
    with patch.object(summ, "summarize_finished_sessions", new=AsyncMock(return_value=0)), \
         patch.object(summ, "maybe_summarize", side_effect=record):
        await asyncio.create_task(summ.housekeep("u", "s", history_budget=500, compact_at=1000))
    assert seen["args"] == ("s", 500, 1000)
    assert seen["llm"] is None


@pytest.mark.asyncio
async def test_stream_chat_hands_scaled_budgets_to_housekeep(monkeypatch):
    got = {}

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"synthesizer": {"final_answer": "Hi.", "total_tokens": 1}})

    client, api_module, llm = _make_client(monkeypatch, fake_stream)

    async def record(user_id, session_id, **budgets):
        got.update(budgets)

    monkeypatch.setattr(api_module, "housekeep", record)
    try:
        resp = await asyncio.to_thread(
            client.post, "/chat",
            json={"query": "hi", "llm": _llm_body(), "token_limit": 5_000},
        )
        assert resp.status_code == 200
    finally:
        api_module._graph = None
        llm.get_chat_model.cache_clear()
        bind_request_llm(None)
    assert got == {"history_budget": 500, "compact_at": 1000}


# ── YAML cache ─────────────────────────────────────────────────────────

def test_yaml_is_parsed_once_and_cache_can_be_cleared(monkeypatch):
    monkeypatch.undo()                                    # real loader
    import yaml
    ctx_mod.clear_context_budget_cache()
    calls = []
    real = yaml.safe_load

    def counting(f):
        calls.append(1)
        return real(f)

    try:
        with patch.object(yaml, "safe_load", counting):
            for _ in range(20):
                context_budget("history")
                context_budget("evidence")
            assert len(calls) == 1
            ctx_mod.clear_context_budget_cache()
            context_budget("history")
            assert len(calls) == 2
    finally:
        ctx_mod.clear_context_budget_cache()
