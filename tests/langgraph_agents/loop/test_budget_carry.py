"""Tests for budget carry (R5): restoring loop_elapsed_s and turn_calls on resume."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from langgraph_agents.api.main import create_app
from langgraph_agents.loop.crypto import LocalSealer, set_sealer
from langgraph_agents.loop.limits import Budget, Caps, TurnContext
from langgraph_agents.loop.pause import open_turn, restore_budget, seal_turn
from langgraph_agents.api.auth import current_user_id, override_user
from tests.langgraph_agents.loop.approval_harness import Model, reply, call, CREATE, SEAL_KEY
from tests.langgraph_agents.loop.test_actions_p0 import TITLE, WHEN, reminders_server


# --- Unit tests for restore_budget ---


def test_restore_budget_elapsed_time_from_carry():
    """A Budget with a fake clock sealed at 12s elapsed. Clock moves 300s.
    After restore_budget, elapsed() is 12, time left under 20s cap is 8s.
    """
    clock = {"now": 1000.0}

    def time_fn() -> float:
        return clock["now"]

    caps = Caps(loop_time_s=20.0)
    turn = TurnContext(caps=caps, budget=Budget(time_fn=time_fn))
    # Simulate 12s elapsed before pause
    turn.budget.started = clock["now"] - 12.0
    assert turn.budget.elapsed() == 12.0

    # Clock moves forward 300s (simulating user thinking time)
    clock["now"] += 300.0

    # New turn, restore from carry
    new_turn_ctx = TurnContext(caps=caps, budget=Budget(time_fn=time_fn))
    carry = {"loop_elapsed_s": 12.0, "turn_calls": 0}
    restore_budget(new_turn_ctx, carry)

    # Elapsed should be 12s (the sealed value), not 312s
    assert new_turn_ctx.budget.elapsed() == 12.0
    # Time left under 20s cap = 8s
    assert new_turn_ctx.caps.loop_time_s - new_turn_ctx.budget.elapsed() == 8.0


def test_restore_budget_turn_calls_from_carry():
    """turn_calls 7 is sealed. After restore, gate allows 5 more calls,
    refuses 6th with budget.
    """
    clock = {"now": 1000.0}

    def time_fn() -> float:
        return clock["now"]

    # Set max_calls_per_round high so round cap doesn't interfere with turn cap test
    caps = Caps(max_calls_per_turn=12, max_calls_per_round=20, loop_time_s=20.0)

    # New turn, restore from carry (simulates resume)
    new_turn_ctx = TurnContext(caps=caps, budget=Budget(time_fn=time_fn))
    carry = {"loop_elapsed_s": 0.0, "turn_calls": 7}
    restore_budget(new_turn_ctx, carry)

    # turn_calls should be restored to 7
    assert new_turn_ctx.turn_calls == 7

    # Now call the gate directly to verify it allows 5 more and refuses 6th
    from langgraph_agents.loop.tools.registry import ToolSpec
    from langgraph_agents.loop import gate

    # Create an internal tool spec (no verdict wait)
    spec = ToolSpec(
        name='kb_search',
        description='test',
        parameters={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
        risk='internal',
        source='builtin',
        call=lambda args, config: 'test',
    )

    call = {'id': 'c1', 'name': 'kb_search', 'arguments': {'query': 'test'}}

    import asyncio

    # First 5 calls (calls 8-12) should be allowed
    # Gate is checked BEFORE call_position (correct order in tools_node)
    for i in range(5):
        result = asyncio.run(gate.check(call, spec, new_turn_ctx))
        assert result.kind == "allow", f"Call {i+8} should be allowed, got {result.kind}: {result.message}"
        # Then register the call (what tools_node does after gate allows)
        new_turn_ctx.call_position(f'c{i}', 'kb_search')

    # 6th call (call 13) should be refused with budget kind
    result = asyncio.run(gate.check(call, spec, new_turn_ctx))
    assert result.kind == "budget", f"Call 13 should be refused with budget, got {result.kind}: {result.message}"
    assert "turn call cap" in result.message


def test_restore_budget_ignores_invalid_values():
    """restore_budget ignores non-numeric or negative values."""
    clock = {"now": 1000.0}

    def time_fn() -> float:
        return clock["now"]

    caps = Caps(loop_time_s=20.0)
    turn = TurnContext(caps=caps, budget=Budget(time_fn=time_fn))
    turn.budget.started = clock["now"]  # 0 elapsed
    turn.turn_calls = 0

    # Invalid elapsed (string) - should not change budget
    carry = {"loop_elapsed_s": "not-a-number", "turn_calls": 5}
    restore_budget(turn, carry)
    assert turn.budget.elapsed() == 0.0
    assert turn.turn_calls == 5

    # Negative elapsed - should not change budget
    carry = {"loop_elapsed_s": -1.0, "turn_calls": 5}
    restore_budget(turn, carry)
    assert turn.budget.elapsed() == 0.0

    # Negative turn_calls - should not change turn_calls
    carry = {"loop_elapsed_s": 5.0, "turn_calls": -1}
    restore_budget(turn, carry)
    assert turn.turn_calls == 5

    # Missing keys - should not change anything
    carry = {}
    restore_budget(turn, carry)
    assert turn.budget.elapsed() == 5.0
    assert turn.turn_calls == 5


# --- Integration test for /chat/resume ---


@pytest.fixture()
def app_client(monkeypatch, reminders_server):
    """Create a test app with loop mode, fake sealer, no DB."""
    fake, url = reminders_server
    fake.writes.clear()
    fake.reads = 0
    fake.mode = "ok"

    monkeypatch.setenv("AGENT_MODE", "loop")
    monkeypatch.setenv("AGENT_PLUGINS_DISABLED", "false")
    monkeypatch.setenv("LOOP_PLUGINS_OFF", "deepwiki,web-search")
    monkeypatch.setenv("LOOP_DEV_FAKE_WRITE", "1")
    monkeypatch.setenv("LOOP_DEV_REMINDERS_MCP_URL", url)
    monkeypatch.setenv("LOOP_DEV_SEAL_KEY", SEAL_KEY)
    monkeypatch.delenv("LOOP_KMS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)

    import langgraph_agents.api.main as api_main
    from langgraph_agents.db.postgres import PostgresClient

    async def _refuse(self, *a, **k):
        raise ConnectionRefusedError("test: database disabled")

    async def _question(**row):
        pass

    async def _answer(**row):
        return "msg-1"

    async def _update(**row):
        return "msg-updated"

    async def _housekeep(user_id, session_id, **kwargs):
        pass

    monkeypatch.setattr(PostgresClient, "connect", _refuse)
    monkeypatch.setattr(api_main, "write_turn_question", _question)
    monkeypatch.setattr(api_main, "write_turn_answer", _answer)
    monkeypatch.setattr(api_main, "update_turn_answer", _update)
    monkeypatch.setattr(api_main, "housekeep", _housekeep)

    from tests.langgraph_agents.loop.approval_harness import Model
    from langgraph_agents.api.auth import override_user

    model = Model(monkeypatch)
    app = create_app()
    app.dependency_overrides[current_user_id] = override_user("test-user")

    import langgraph_agents.loop.plugins.runtime as runtime
    import langgraph_agents.loop.plugins.store as plugin_store
    from langgraph_agents.loop.plugins.store import MemoryPluginStore
    runtime.reset_runtime()
    plugin_store.set_store(MemoryPluginStore())
    from langgraph_agents.loop.config import load_settings
    load_settings.cache_clear()

    import base64
    seal_key_bytes = base64.b64decode(SEAL_KEY)
    set_sealer(LocalSealer(seal_key_bytes))

    with TestClient(app) as client:
        yield client, model

    set_sealer(None)
    runtime.reset_runtime()
    plugin_store.set_store(None)
    load_settings.cache_clear()


def test_resume_restores_turn_calls(app_client, monkeypatch):
    """A /chat/resume test proves the route calls restore_budget
    (the resumed turn's turn_calls equals the sealed value).
    """
    client, model = app_client
    USER = "test-user"
    SESSION = "550e8400-e29b-41d4-a716-446655440000"

    from tests.langgraph_agents.loop.approval_harness import reply, call, CREATE, sse, first
    from tests.langgraph_agents.loop.test_actions_p0 import TITLE, WHEN

    # First turn: propose a write (this seals the turn with turn_calls=1)
    model.script(reply("", call("c1", CREATE, title=TITLE, when=WHEN)))
    resp = client.post("/chat", json={
        "query": "remind me to stretch",
        "session_id": SESSION,
        "persona_id": "anne"
    })
    assert resp.status_code == 200, resp.text
    events = sse(resp.text)
    card = first(events, "approval_required")
    assert card is not None

    action_id = card["action_id"]
    thread_id = card["thread_id"]

    # Monkeypatch restore_budget to record the turn_calls value
    import langgraph_agents.loop.pause as pause_module
    original_restore_budget = pause_module.restore_budget
    recorded_turn_calls = {}

    def wrapped_restore_budget(turn, carry):
        result = original_restore_budget(turn, carry)
        recorded_turn_calls['value'] = turn.turn_calls
        return result

    monkeypatch.setattr(pause_module, "restore_budget", wrapped_restore_budget)

    # Resume: the resumed turn should have turn_calls restored from the sealed turn
    # The sealed turn had 1 call (the write proposal), so turn_calls should be 1
    model.script(reply("ok"), reply("Done."))
    resp = client.post("/chat/resume", json={
        "thread_id": thread_id,
        "action_id": action_id,
        "decision": "approve"
    })
    assert resp.status_code == 200, resp.text
    events = sse(resp.text)

    # Verify the turn completed
    ts = first(events, "turn_status")
    assert ts is not None
    assert ts["status"] in ("completed", "partial")

    # Verify restore_budget was called and restored the correct turn_calls
    assert recorded_turn_calls.get('value') == 1, f"Expected turn_calls=1, got {recorded_turn_calls.get('value')}"


# --- Seal/Open round-trip test ---


@pytest.mark.asyncio
async def test_seal_open_roundtrip_carries_budget_and_calls():
    """Sealing and opening a turn preserves loop_elapsed_s and turn_calls."""
    from langgraph_agents.loop.turns import MemoryTurnStore
    from langgraph_agents.loop.crypto import LocalSealer, set_sealer

    seal_key = bytes(range(32))
    set_sealer(LocalSealer(seal_key))

    try:
        store = MemoryTurnStore()
        user_id = "test-user"
        thread_id = "550e8400-e29b-41d4-a716-446655440001"
        session_id = "550e8400-e29b-41d4-a716-446655440002"

        # Build a transcript and carry with budget/calls
        transcript = [{"role": "user", "content": "test"}]
        carry = {
            "user_instructions": [],
            "user_facts": [],
            "actions": [],
            "motion_jobs": [],
            "loop_trace": [],
            "personal_sources": [],
            "loop_elapsed_s": 12.5,
            "turn_calls": 7,
            "query": "test query"
        }
        state = {
            "transcript": transcript,
            **carry
        }

        # Seal
        blob = await seal_turn(
            transcript=transcript,
            state=state,
            query="test query",
            user_id=user_id,
            thread_id=thread_id
        )

        # Save to turn store (simulating pause)
        await store.pause(
            user_id=user_id,
            session_id=session_id,
            thread_id=thread_id,
            transcript_enc=blob,
            state_json={"action_id": "test-action-id"},
            expires_at=datetime.now(timezone.utc),
            request_id="test-request-id"
        )

        # Open (simulating resume)
        rec = await store.get(user_id, thread_id)
        messages, opened_carry = await open_turn(
            rec.transcript_enc,
            user_id=user_id,
            thread_id=thread_id
        )

        # Verify carry values preserved
        assert opened_carry["loop_elapsed_s"] == 12.5
        assert opened_carry["turn_calls"] == 7
        assert messages == transcript
    finally:
        set_sealer(None)