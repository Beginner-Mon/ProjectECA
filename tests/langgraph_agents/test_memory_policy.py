"""Storage rules of the memory store: `enforce` and `apply_policy`, direct.

Both functions are pure: no database, no model. `enforce` holds the limits the
memory prompt states; `apply_policy` decides the tier, kind and status one kept
operation writes. These tests call them with plain dicts only.
"""

from __future__ import annotations

import pytest

from langgraph_agents.nodes import fact_extractor as fe

pytestmark = pytest.mark.unit

TYPES = fe.MEMORY_TYPES
NON_INSTRUCTION_TYPES = tuple(t for t in TYPES if t != "instruction")
CTX = {"session_id": "s-1", "area_topics": ["Knee Rehab"]}
_MISSING = object()


def _add(**over):
    op = {"op": "ADD", "type": "profile", "topic": "routine",
          "fact": "Plays pickleball.", "safety": False}
    op.update(over)
    return op


def _update(**over):
    op = {"op": "UPDATE", "id": "f1", "type": "profile", "topic": "routine",
          "fact": "Plays pickleball twice a week.", "safety": False}
    op.update(over)
    return op


def _stored(**over):
    row = {"id": "f1", "type": "profile", "topic": "routine",
           "safety": False, "fact": "Plays pickleball.", "locked": False}
    row.update(over)
    return row


def _row(**over):
    row = {"id": "f1", "fact": "Plays pickleball.", "type": "profile",
           "topic": "routine", "safety": False, "kind": "fact", "tier": 2,
           "status": "active", "seen_count": 1, "seen_projects": [""],
           "last_seen_session_id": "s-old", "source": "extracted"}
    row.update(over)
    return row


def _single(kept, dropped):
    assert dropped == []
    assert len(kept) == 1
    return kept[0]


def _reasons(dropped):
    return [d["reason"] for d in dropped]


# ── enforce: types ────────────────────────────────────────────────────────

@pytest.mark.parametrize("typ", TYPES)
def test_add_each_memory_type_is_kept(typ):
    kept, dropped = fe.enforce([_add(type=typ)], [])
    assert _single(kept, dropped)["type"] == typ


@pytest.mark.parametrize("typ", ["mood", "identifier", ""])
def test_add_unknown_type_is_dropped(typ):
    kept, dropped = fe.enforce([_add(type=typ)], [])
    assert kept == []
    assert _reasons(dropped) == ["bad_type"]


# ── enforce: topic ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "topic, reason",
    [
        pytest.param(_MISSING, "bad_topic", id="missing"),
        pytest.param("", "bad_topic", id="empty"),
        pytest.param("   ", "bad_topic", id="blank"),
        pytest.param("one two three four five", "bad_topic", id="five-words"),
        pytest.param("x" * 61, "bad_topic", id="61-chars"),
        pytest.param("one two three four", None, id="four-words"),
        pytest.param("x" * 60, None, id="60-chars"),
    ],
)
def test_topic_limits(topic, reason):
    op = _add()
    if topic is _MISSING:
        op.pop("topic")
    else:
        op["topic"] = topic
    kept, dropped = fe.enforce([op], [])
    if reason is None:
        assert _single(kept, dropped)["topic"] == topic
    else:
        assert kept == []
        assert _reasons(dropped) == [reason]


# ── enforce: safety ──────────────────────────────────────────────────────

@pytest.mark.parametrize("safety", [1, 0, "true", "false", None, _MISSING])
def test_add_non_bool_safety_is_dropped(safety):
    op = _add()
    if safety is _MISSING:
        op.pop("safety")
    else:
        op["safety"] = safety
    kept, dropped = fe.enforce([op], [])
    assert kept == []
    assert _reasons(dropped) == ["bad_safety"]


# ── enforce: topic reuse ─────────────────────────────────────────────────

@pytest.mark.parametrize("kind", ["ADD", "UPDATE"])
def test_topic_reuses_stored_spelling_of_same_type(kind):
    stored = [_stored(type="area", topic="Knee  Rehab")]
    op = (_add(type="area", topic="  knee   REHAB ") if kind == "ADD"
          else _update(type="area", topic="  knee   REHAB "))
    kept, dropped = fe.enforce([op], stored)
    assert _single(kept, dropped)["topic"] == "Knee Rehab"


@pytest.mark.parametrize("kind", ["ADD", "UPDATE"])
def test_topic_is_not_reused_across_types(kind):
    stored = [_stored(type="area", topic="Knee Rehab")]
    op = (_add(type="preference", topic="knee rehab") if kind == "ADD"
          else _update(type="preference", topic="knee rehab"))
    kept, dropped = fe.enforce([op], stored)
    assert _single(kept, dropped)["topic"] == "knee rehab"


# ── enforce: ids ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "op, reason",
    [
        pytest.param(_update(id="f1"), "locked_id", id="update-locked"),
        pytest.param({"op": "REPEAT", "id": "f1"}, "locked_id",
                     id="repeat-locked"),
        pytest.param({"op": "DELETE", "id": "f1"}, None,
                     id="delete-locked-kept"),
        pytest.param(_update(id="f-unknown"), "unknown_id",
                     id="update-unknown"),
        pytest.param({"op": "REPEAT", "id": "f-unknown"}, "unknown_id",
                     id="repeat-unknown"),
        pytest.param({"op": "DELETE", "id": "f-unknown"}, "unknown_id",
                     id="delete-unknown"),
    ],
)
def test_id_rules(op, reason):
    kept, dropped = fe.enforce([op], [_stored(locked=True)])
    if reason is None:
        assert _single(kept, dropped) == {"op": "DELETE", "id": "f1"}
    else:
        assert kept == []
        assert _reasons(dropped) == [reason]


# ── enforce: operation limit ─────────────────────────────────────────────

def test_operations_after_the_eighth_are_dropped():
    ops = [_add(topic=f"topic {i}") for i in range(fe.MAX_OPS + 1)]
    kept, dropped = fe.enforce(ops, [])
    assert len(kept) == fe.MAX_OPS == 8
    assert _reasons(dropped) == ["over_limit"]
    assert dropped[0]["op"] is ops[fe.MAX_OPS]


# ── apply_policy: ADD tier and status ────────────────────────────────────

@pytest.mark.parametrize("typ", TYPES)
def test_add_safety_true_is_tier1_active_for_every_type(typ):
    out = fe.apply_policy(None, _add(type=typ, safety=True), CTX)
    assert out["action"] == "add"
    assert (out["fields"]["tier"], out["fields"]["status"]) == (1, "active")


@pytest.mark.parametrize("topic", ["knee rehab", "KNEE  REHAB", " Knee Rehab "])
def test_add_instruction_in_area_topic_is_tier2(topic):
    out = fe.apply_policy(None, _add(type="instruction", topic=topic), CTX)
    assert (out["fields"]["tier"], out["fields"]["status"]) == (2, "active")


def test_add_instruction_outside_area_topics_is_tier1():
    out = fe.apply_policy(None, _add(type="instruction", topic="answer style"),
                          CTX)
    assert (out["fields"]["tier"], out["fields"]["status"]) == (1, "active")


@pytest.mark.parametrize("typ", NON_INSTRUCTION_TYPES)
def test_add_other_non_safety_type_is_tier2(typ):
    out = fe.apply_policy(None, _add(type=typ, safety=False), CTX)
    assert (out["fields"]["tier"], out["fields"]["status"]) == (2, "active")


# ── apply_policy: status, kind, category ─────────────────────────────────

@pytest.mark.parametrize("kind", ["ADD", "UPDATE"])
@pytest.mark.parametrize("typ", TYPES)
def test_status_is_active_in_every_add_and_update(kind, typ):
    if kind == "ADD":
        out = fe.apply_policy(None, _add(type=typ), CTX)
    else:
        out = fe.apply_policy(_row(type=typ), _update(type=typ), CTX)
    assert out["fields"]["status"] == "active"


@pytest.mark.parametrize("kind", ["ADD", "UPDATE"])
@pytest.mark.parametrize("typ", TYPES)
def test_kind_follows_type_and_category_is_none(kind, typ):
    if kind == "ADD":
        out = fe.apply_policy(None, _add(type=typ), CTX)
    else:
        out = fe.apply_policy(_row(type=typ), _update(type=typ), CTX)
    fields = out["fields"]
    assert fields["memory_type"] == typ
    assert fields["kind"] == ("instruction" if typ == "instruction" else "fact")
    assert fields["category"] is None


# ── apply_policy: REPEAT ─────────────────────────────────────────────────

@pytest.mark.parametrize("row_type, tier", [("area", 1), ("profile", 2)])
def test_repeat_in_the_same_session_is_noop(row_type, tier):
    row = _row(type=row_type, tier=tier, last_seen_session_id="s-1")
    out = fe.apply_policy(row, {"op": "REPEAT", "id": "f1"}, CTX)
    assert out == {"action": "noop", "fields": {}}


@pytest.mark.parametrize("seen_count", [0, 1, 4])
@pytest.mark.parametrize("row_type, tier", [("area", 1), ("profile", 2)])
def test_repeat_in_another_session_refreshes(seen_count, row_type, tier):
    row = _row(type=row_type, tier=tier, seen_count=seen_count,
               last_seen_session_id="s-old")
    out = fe.apply_policy(row, {"op": "REPEAT", "id": "f1"}, CTX)
    assert out["action"] == "refresh"
    fields = out["fields"]
    assert fields["seen_count"] == seen_count + 1
    assert fields["last_seen_session_id"] == "s-1"
    assert "tier" not in fields
    assert "status" not in fields
    assert "type" not in fields


# ── apply_policy: UPDATE recomputes the tier ─────────────────────────────

@pytest.mark.parametrize(
    "row_type, row_tier, op_type, topic, safety, tier",
    [
        ("profile", 2, "profile", "routine", True, 1),
        ("profile", 1, "profile", "routine", False, 2),
        ("event", 2, "instruction", "knee rehab", False, 2),
        ("event", 2, "instruction", "answer style", False, 1),
        ("instruction", 1, "instruction", "knee rehab", True, 1),
        ("preference", 1, "reference", "app", False, 2),
    ],
)
def test_update_recomputes_tier_from_new_type_topic_and_safety(
        row_type, row_tier, op_type, topic, safety, tier):
    row = _row(type=row_type, tier=row_tier)
    out = fe.apply_policy(
        row, _update(type=op_type, topic=topic, safety=safety), CTX)
    assert out["action"] == "rewrite"
    assert out["fields"]["memory_type"] == op_type
    assert out["fields"]["tier"] == tier
    assert out["fields"]["status"] == "active"
