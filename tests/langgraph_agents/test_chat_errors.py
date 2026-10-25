"""Chat error contract, backend half: classification, error_code, history wire.

The SSE half lives in test_phase5_sse.py; the contract is
docs/architecture/sse-contract.md.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph_agents.llm import CircuitBreakerOpenError
from langgraph_agents.nodes._errors import error_code_for, error_entry
from langgraph_agents.state import ErrorSeverity

pytestmark = pytest.mark.unit


class APIStatusError(Exception):
    """Named like the openai SDK class: classification goes by class name."""

    def __init__(self, status_code):
        super().__init__("provider said no")
        self.status_code = status_code


class AuthenticationError(APIStatusError):
    pass


class RateLimitError(APIStatusError):
    pass


# ── classification ───────────────────────────────────────────────────────


@pytest.mark.parametrize("exc,kind,status", [
    (APIStatusError(402), "quota", 402),
    (AuthenticationError(401), "auth", 401),
    (RateLimitError(429), "quota", 429),
    (APIStatusError(503), "server", 503),
    (asyncio.TimeoutError(), "unreachable", None),
    (CircuitBreakerOpenError("open"), "unreachable", None),
    (ValueError("x"), "other", None),
])
def test_error_entry_classifies(exc, kind, status):
    entry = error_entry("synthesizer", exc, ErrorSeverity.CRITICAL)
    assert (entry["kind"], entry["status"]) == (kind, status)
    assert entry["node"] == "synthesizer"
    assert entry["severity"] == ErrorSeverity.CRITICAL
    assert entry["message"] and entry["timestamp"]


def test_error_entry_without_exception_is_unreachable():
    entry = error_entry("planner", None, ErrorSeverity.RECOVERABLE, "no model")
    assert entry["kind"] == "unreachable" and entry["message"] == "no model"


@pytest.mark.parametrize("entry,code", [
    ({"kind": "quota", "status": 402}, "provider_billing"),
    ({"kind": "quota", "status": 429}, "provider_unavailable"),
    ({"kind": "auth", "status": 401}, "provider_auth"),
    ({"kind": "unreachable", "status": None}, "provider_unavailable"),
    ({"kind": "server", "status": 500}, "provider_unavailable"),
    ({"kind": "model_not_found", "status": 404}, "provider_unavailable"),
    ({"kind": "other", "status": None}, "provider_unavailable"),
    ({"message": "no kind"}, "internal"),
    (None, "internal"),
])
def test_error_code_mapping(entry, code):
    assert error_code_for(entry) == code


# ── error_handler ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_error_handler_sets_error_code_and_keeps_persona_line():
    from langgraph_agents.nodes._persona_loader import get_ui_string
    from langgraph_agents.nodes.error_handler import error_handler_node

    state = {"messages": [], "errors": [
        error_entry("retriever_agent", APIStatusError(402), ErrorSeverity.CRITICAL),
    ]}
    result = await error_handler_node(state, {"configurable": {"persona_id": "anne"}})
    assert result["error_code"] == "provider_billing"
    assert result["final_answer"] == get_ui_string("anne", "error_system")


@pytest.mark.asyncio
async def test_error_handler_picks_the_most_severe_critical_error():
    from langgraph_agents.nodes.error_handler import error_handler_node

    state = {"messages": [], "errors": [
        error_entry("synthesizer", asyncio.TimeoutError(), ErrorSeverity.CRITICAL),
        error_entry("retriever_agent", AuthenticationError(401), ErrorSeverity.CRITICAL),
        error_entry("planner", APIStatusError(402), ErrorSeverity.RECOVERABLE),
    ]}
    assert (await error_handler_node(state))["error_code"] == "provider_auth"


@pytest.mark.asyncio
async def test_error_handler_no_kind_is_internal_and_recoverable_has_no_code():
    from langgraph_agents.nodes.error_handler import error_handler_node

    crit = {"messages": [], "errors": [{"node": "x", "severity": ErrorSeverity.CRITICAL}]}
    assert (await error_handler_node(crit))["error_code"] == "internal"
    soft = {"messages": [], "errors": [{"node": "x", "severity": ErrorSeverity.RECOVERABLE}]}
    assert "error_code" not in await error_handler_node(soft)


@pytest.mark.asyncio
async def test_error_handler_logs_codes_not_text(caplog):
    import logging
    from langgraph_agents.nodes.error_handler import error_handler_node

    state = {"messages": [], "errors": [
        error_entry("synthesizer", APIStatusError(402), ErrorSeverity.CRITICAL,
                    "private message text")]}
    with caplog.at_level(logging.ERROR):
        await error_handler_node(state)
    lines = [r for r in caplog.records if r.getMessage() == "chat_error"]
    assert len(lines) == 1
    assert (lines[0].code, lines[0].kind, lines[0].status, lines[0].node) == (
        "provider_billing", "quota", 402, "synthesizer")
    assert "private message text" not in str(vars(lines[0]))


# ── session wire ─────────────────────────────────────────────────────────


class _Row(dict):
    pass


def _row(extras=None):
    return _Row(id="m1", role="assistant", content="hi", token_count=1, extras=extras)


def test_session_wire_carries_error_code_and_interrupted():
    from langgraph_agents.db.session_store import _shape_message

    now = datetime.now(timezone.utc)
    failed = _shape_message(_row({"meta": {"error_code": "provider_billing"}}), now)
    assert failed["error_code"] == "provider_billing" and failed["interrupted"] is None
    cut = _shape_message(_row({"meta": {"interrupted": True}}), now)
    assert cut["interrupted"] is True and cut["error_code"] is None
    plain = _shape_message(_row(None), now)
    assert plain["error_code"] is None and plain["interrupted"] is None
    assert "meta" not in failed


# ── memory skips error rows ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_history_skips_assistant_rows_with_error_code():
    from langgraph_agents.nodes import memory as mem

    # Newest first, as the SQL returns them.
    rows = [
        {"stm_summary": None, "role": "assistant", "content": "apology", "error_code": "internal"},
        {"stm_summary": None, "role": "user", "content": "q2", "error_code": None},
        {"stm_summary": None, "role": "assistant", "content": "a1", "error_code": None},
        {"stm_summary": None, "role": "user", "content": "q1", "error_code": None},
    ]
    pg = AsyncMock()
    pg.fetch.return_value = rows
    with patch.object(mem, "get_pg_client", return_value=pg):
        _, history = await mem._load_stm("s-1")
    assert [h["content"] for h in history] == ["q1", "a1", "q2"]

    pg.fetch.return_value = [
        {"role": "assistant", "content": "apology", "error_code": "provider_billing"},
        {"role": "user", "content": "q", "error_code": None},
    ]
    with patch.object(mem, "get_pg_client", return_value=pg):
        latest = await mem._load_latest_turns("s-1")
    assert [h["content"] for h in latest] == ["q"]


# ── STM append skips error turns ─────────────────────────────────────────


class _Conn:
    async def fetchval(self, *a, **k):
        return "00000000-0000-0000-0000-000000000009"


class _Tx:
    async def __aenter__(self):
        return _Conn()

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
@pytest.mark.parametrize("meta,appended", [
    ({"error_code": "internal"}, False),
    ({"request_id": "r"}, True),
    (None, True),
])
async def test_stm_append_skips_error_turns(meta, appended):
    from langgraph_agents.db import session_store as ss

    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.transaction = lambda: _Tx()
    with patch.object(ss, "get_pg_client", return_value=pg), \
         patch.object(ss, "_append_stm", AsyncMock()) as append:
        await ss.write_turn_answer("s", "q", "a", meta=meta)
    assert append.await_count == (1 if appended else 0)
