"""Tests for `loop.context.load_context_node`: no external text in system
messages; the synthetic load_context pair is present."""

from __future__ import annotations

import asyncio

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
