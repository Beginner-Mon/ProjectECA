"""Tests for `loop.limits`: every cap stops the loop; rounds cap ends with
`tool_choice="none"`."""

from __future__ import annotations

from langgraph_agents.loop.limits import Caps, Budget, TurnContext, new_turn


def test_caps_defaults():
    c = Caps()
    assert c.max_rounds == 6
    assert c.max_calls_per_round == 5
    assert c.max_calls_per_turn == 12
    assert c.max_writes_per_round == 1
    assert c.loop_time_s == 20.0
    assert c.request_time_s == 60.0
    assert c.max_input_tokens == 60_000
    assert c.rate_limit == 30
    assert c.rate_window_s == 60


def test_budget_elapsed_uses_time_fn():
    now = [0.0]
    b = Budget(time_fn=lambda: now[0])
    assert b.elapsed() == 0.0
    now[0] = 10.0
    assert b.elapsed() == 10.0


def test_turn_context_defaults():
    t = new_turn()
    assert t.cancelled is False
    assert t.repeat_empty_signatures == set()
    assert t.force_no_tools is False
    assert t.used_result_chars == 0


def test_plan_round_resets_round_counters():
    t = new_turn(Caps(max_calls_per_round=5))
    t.round_calls = 4
    t.round_writes = 1
    t.plan_round()
    assert t.round_calls == 0
    assert t.round_writes == 0


def test_call_position_turn_wide_and_round():
    t = new_turn()
    p1 = t.call_position("id1", "kb_search")
    p2 = t.call_position("id2", "web_search")
    assert p1 == 1
    assert p2 == 2
    assert t.call_registry == {"id1": "kb_search", "id2": "web_search"}
    assert t.turn_call_positions == {"id1": 1, "id2": 2}
    assert t.round_positions == {"id1": 1, "id2": 2}
    assert t.turn_calls == 2
    assert t.round_calls == 2


def test_round_writes_tracking():
    t = new_turn(Caps(max_writes_per_round=1))
    t.record_write()
    assert t.round_writes == 1


def test_result_chars_accounting():
    t = new_turn()
    t.result_chars(1000)
    t.result_chars(500)
    assert t.used_result_chars == 1500


def test_rounds_cap_forces_no_tools():
    t = new_turn(Caps(max_calls_per_round=5))
    # simulate filling the round
    for i in range(5):
        t.call_position(f"id{i}", "kb_search")
    assert t.round_calls == 5
    # next call would hit the cap -> force tool_choice=none
    assert t.round_calls >= t.caps.max_calls_per_round
    t.force_no_tools = True
    assert t.force_no_tools is True


def test_turn_cap_forces_no_tools():
    t = new_turn(Caps(max_calls_per_turn=3))
    for i in range(3):
        t.call_position(f"id{i}", "kb_search")
    assert t.turn_calls == 3
    assert t.turn_calls >= t.caps.max_calls_per_turn
    t.force_no_tools = True
    assert t.force_no_tools is True
