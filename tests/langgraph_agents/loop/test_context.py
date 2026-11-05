"""Tests for `loop.context.load_context_node`: no external text in system
messages; the synthetic load_context pair is present; _recent_loop_traces
fetches and orders the last two loop traces correctly."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest

from langgraph_agents.loop.context import load_context_node
from langgraph_agents.loop.limits import new_turn
from langgraph_agents.loop.prompts import AGENT_SYSTEM


def _config(session_id="s1", user_id="u1", query="what is bird dog?"):
    turn = new_turn()
    return {
        "configurable": {
            "turn_ctx": turn,
            "user_id": user_id,
            "session_id": session_id,
            "query": query,
            "request_id": "r1",
        }
    }


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, *args, **kwargs):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FakePG:
    def __init__(self, rows):
        self._rows = rows

    def transaction(self):
        return _FakeConn(self._rows)


class _FakePGError:
    def __init__(self, exc):
        self._exc = exc

    def transaction(self):
        exc = self._exc
        class _ErrConn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def fetch(self, *a, **k):
                raise exc

        return _ErrConn()


def test_no_external_text_in_system_messages():
    state = {
        "transcript": [
            {"role": "user", "content": "external private text that must not be system"},
        ],
        "user_instructions": ["stand on one leg"],
        "user_facts": ["likes swimming"],
        "session_summary": [],
    }
    out = asyncio.run(load_context_node(state, _config()))
    transcript = out["transcript"]
    systems = [m for m in transcript if m.get("role") == "system"]
    system_text = "\n".join(m.get("content", "") for m in systems)
    # AGENT_SYSTEM present.
    assert AGENT_SYSTEM in system_text
    # The user's external message text must not appear in a system message.
    assert "external private text that must not be system" not in system_text
    # Instructions/facts ARE in system (they are the user's own standing text).
    assert "stand on one leg" in system_text


def test_load_context_pair_present():
    state = {
        "transcript": [],
        "user_instructions": [],
        "user_facts": [],
        "session_summary": [],
    }
    out = asyncio.run(load_context_node(state, _config()))
    transcript = out["transcript"]
    # Last two entries: synthetic assistant tool_call + tool result.
    assert transcript[-2]["role"] == "assistant"
    calls = transcript[-2].get("tool_calls") or []
    assert len(calls) == 1
    fn = calls[0].get("function") or calls[0]
    assert fn.get("name") == "load_context"
    assert fn.get("arguments") == "{}"
    assert transcript[-1]["role"] == "tool"
    assert transcript[-1]["tool_call_id"] == "load_context"
    # No `name` key on the tool message.
    assert "name" not in transcript[-1]


def test_current_message_is_last_user_before_pair():
    state = {"transcript": [], "user_instructions": [], "user_facts": [], "session_summary": []}
    out = asyncio.run(load_context_node(state, _config(query="how are you?")))
    transcript = out["transcript"]
    users = [m for m in transcript if m.get("role") == "user"]
    assert users[-1]["content"] == "how are you?"


# ── _recent_loop_traces tests (B2) ─────────────────────────────────────────


def test_recent_loop_traces_returns_newest_two_entries(monkeypatch):
    """Two rows returned: newest row has [r3, r4], older row has [r1, r2].
    The tool result should name r4 and r3 (newest first), not r1 or r2.
    Also r4 should appear before r3 in the tool message (newest first overall).
    """
    import langgraph_agents.shared as shared_module

    # Newest row first (created_at DESC): r3, r4 are newer than r1, r2
    # Each row's meta.loop is in time order (oldest first in the list)
    # So we need to walk each list in reverse to get newest first overall.
    rows = [
        {
            "extras": json.dumps({
                "meta": {
                    "loop": [
                        {"request_id": "r3", "rounds": 1, "turn_status": "completed"},
                        {"request_id": "r4", "rounds": 2, "turn_status": "completed"},
                    ]
                }
            })
        },
        {
            "extras": json.dumps({
                "meta": {
                    "loop": [
                        {"request_id": "r1", "rounds": 1, "turn_status": "completed"},
                        {"request_id": "r2", "rounds": 2, "turn_status": "completed"},
                    ]
                }
            })
        },
    ]

    monkeypatch.setattr(shared_module, "get_pg_client", lambda: _FakePG(rows))

    state = {
        "transcript": [],
        "user_instructions": [],
        "user_facts": [],
        "session_summary": [],
    }
    out = asyncio.run(load_context_node(state, _config(session_id="550e8400-e29b-41d4-a716-446655440000")))
    transcript = out["transcript"]

    # The tool result is the last message
    tool_msg = transcript[-1]
    content = tool_msg["content"]

    # Should contain r4 and r3 (newest first from the newest row)
    assert "r4" in content
    assert "r3" in content
    # Should NOT contain r1 or r2 (older row, only first two entries kept)
    assert "r1" not in content
    assert "r2" not in content

    # Check order: r4 should appear before r3 in the tool message (newest first)
    r4_pos = content.index("r4")
    r3_pos = content.index("r3")
    assert r4_pos < r3_pos, f"r4 should appear before r3, but r4 at {r4_pos}, r3 at {r3_pos}"


def test_recent_loop_traces_skips_non_dict_entries(monkeypatch):
    """Entries that are not dicts are skipped (renderer calls tr.get(...))."""
    import langgraph_agents.shared as shared_module

    rows = [
        {
            "extras": json.dumps({
                "meta": {
                    "loop": [
                        "not-a-dict",
                        {"request_id": "r4", "rounds": 1, "turn_status": "completed"},
                        None,
                        {"request_id": "r3", "rounds": 2, "turn_status": "completed"},
                    ]
                }
            })
        },
    ]

    monkeypatch.setattr(shared_module, "get_pg_client", lambda: _FakePG(rows))

    state = {
        "transcript": [],
        "user_instructions": [],
        "user_facts": [],
        "session_summary": [],
    }
    out = asyncio.run(load_context_node(state, _config(session_id="550e8400-e29b-41d4-a716-446655440001")))
    transcript = out["transcript"]

    tool_msg = transcript[-1]
    content = tool_msg["content"]

    # Only the dict entries should appear
    assert "r4" in content
    assert "r3" in content


def test_recent_loop_traces_fetch_raises_returns_empty_and_logs_warning(monkeypatch, caplog):
    """When fetch raises, the node still returns, and one WARNING is logged
    with error field = exception class.
    """
    import langgraph_agents.shared as shared_module

    class Boom(Exception):
        pass

    monkeypatch.setattr(shared_module, "get_pg_client", lambda: _FakePGError(Boom("db down")))

    state = {
        "transcript": [],
        "user_instructions": [],
        "user_facts": [],
        "session_summary": [],
    }
    with caplog.at_level(logging.WARNING):
        out = asyncio.run(load_context_node(state, _config(session_id="550e8400-e29b-41d4-a716-446655440002")))

    # Node still returns successfully
    assert "transcript" in out
    assert out["transcript"][-1]["role"] == "tool"

    # One WARNING logged with error field = exception class
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    loop_warnings = [r for r in warnings if "loop_traces_read_failed" in r.getMessage()]
    assert len(loop_warnings) == 1
    # Check the extra fields - error is the exception class name
    assert loop_warnings[0].error == "Boom"
