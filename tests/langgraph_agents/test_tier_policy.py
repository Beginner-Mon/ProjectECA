"""Tier policy (memory-v2 tiers): every row of the promotion table, pure.

`apply_policy(row_or_none, op, ctx)` decides what one extractor operation
writes, with no database and no clock. Thresholds ride in `ctx`
(promote_fact_at / promote_instruction_at); the caller stamps
said_at/updated_at.
"""

from __future__ import annotations

import pytest

from langgraph_agents.nodes.fact_extractor import apply_policy

pytestmark = pytest.mark.unit

CTX = {"session_id": "s-new", "promote_fact_at": 2, "promote_instruction_at": 3}


def _row(**over):
    row = {"id": "f1", "fact": "old", "category": "preference", "kind": "fact",
           "tier": 2, "status": "active", "project": None, "seen_count": 1,
           "seen_projects": [""], "last_seen_session_id": "s-old",
           "source": "extracted"}
    row.update(over)
    return row


# ── ADD ─────────────────────────────────────────────────────────────────


def test_add_safe_category_is_tier1_active_at_once():
    for category in ("restriction", "condition"):
        for kind in ("fact", "instruction"):
            write = apply_policy(None, {
                "op": "ADD", "kind": kind, "fact": "x", "category": category,
                "project": None, "explicit": False}, CTX)
            assert write["action"] == "add"
            assert (write["fields"]["tier"], write["fields"]["status"]) == (1, "active")


def test_add_fact_explicit_is_active_implicit_is_candidate():
    active = apply_policy(None, {
        "op": "ADD", "kind": "fact", "fact": "x", "category": "goal",
        "project": "A", "explicit": True}, CTX)
    assert (active["fields"]["tier"], active["fields"]["status"],
            active["fields"]["project"]) == (2, "active", "A")
    candidate = apply_policy(None, {
        "op": "ADD", "kind": "fact", "fact": "x", "category": "goal",
        "project": None, "explicit": False}, CTX)
    assert (candidate["fields"]["tier"], candidate["fields"]["status"]) == (2, "candidate")


def test_add_instruction_never_enters_tier1_directly():
    explicit = apply_policy(None, {
        "op": "ADD", "kind": "instruction", "fact": "x", "category": "style",
        "project": None, "explicit": True}, CTX)
    assert (explicit["fields"]["tier"], explicit["fields"]["status"]) == (2, "active")
    implicit = apply_policy(None, {
        "op": "ADD", "kind": "instruction", "fact": "x", "category": "style",
        "project": None, "explicit": False}, CTX)
    assert (implicit["fields"]["tier"], implicit["fields"]["status"]) == (2, "candidate")


def test_add_starts_its_sighting_count():
    write = apply_policy(None, {
        "op": "ADD", "kind": "fact", "fact": "x", "category": "goal",
        "project": "A", "explicit": True}, CTX)
    assert write["fields"]["seen_count"] == 1
    assert write["fields"]["seen_projects"] == ["A"]
    assert write["fields"]["last_seen_session_id"] == "s-new"
    assert apply_policy(None, {
        "op": "ADD", "kind": "fact", "fact": "x", "category": "goal",
        "project": None, "explicit": True}, CTX)["fields"]["seen_projects"] == [""]


# ── REPEAT ──────────────────────────────────────────────────────────────


def test_repeat_in_the_same_conversation_is_a_noop():
    write = apply_policy(
        _row(last_seen_session_id="s-new"),
        {"op": "REPEAT", "id": "f1", "project": None}, CTX)
    assert write == {"action": "noop", "fields": {}}


def test_repeat_elsewhere_counts_and_restamps():
    write = apply_policy(
        _row(seen_count=1, seen_projects=[""], last_seen_session_id="s-old"),
        {"op": "REPEAT", "id": "f1", "project": "A"}, CTX)
    assert write["action"] == "refresh"
    assert write["fields"]["seen_count"] == 2
    assert write["fields"]["seen_projects"] == ["", "A"]
    assert write["fields"]["last_seen_session_id"] == "s-new"


def test_repeat_promotes_a_candidate_fact_at_two_sightings():
    write = apply_policy(
        _row(status="candidate", seen_count=1, seen_projects=[""],
             last_seen_session_id="s-old"),
        {"op": "REPEAT", "id": "f1", "project": None}, CTX)
    assert write["fields"]["status"] == "active"
    assert write["fields"]["tier"] == 2


def test_repeat_promotes_a_general_instruction_at_three_sightings():
    write = apply_policy(
        _row(kind="instruction", status="active", seen_count=2,
             seen_projects=["A", ""], last_seen_session_id="s-old"),
        {"op": "REPEAT", "id": "f1", "project": None}, CTX)
    assert write["fields"]["seen_count"] == 3
    assert write["fields"]["tier"] == 1
    assert write["fields"]["project"] is None
    assert write["fields"]["status"] == "active"


def test_repeat_keeps_a_single_project_instruction_in_tier2():
    write = apply_policy(
        _row(kind="instruction", status="candidate", seen_count=2,
             seen_projects=["A", "A"], last_seen_session_id="s-old"),
        {"op": "REPEAT", "id": "f1", "project": "A"}, CTX)
    assert write["fields"]["seen_count"] == 3
    assert write["fields"]["tier"] == 2
    assert write["fields"]["project"] == "A"
    assert write["fields"]["status"] == "active"


# ── UPDATE / DELETE / FORGET_ALL ────────────────────────────────────────


def test_update_rewrites_text_and_restamps():
    write = apply_policy(
        _row(fact="old", category="goal", project="A"),
        {"op": "UPDATE", "id": "f1", "fact": "new", "category": "goal",
         "project": "A"}, CTX)
    assert write["action"] == "rewrite"
    assert write["fields"]["fact"] == "new"
    assert write["fields"]["last_seen_session_id"] == "s-new"


def test_update_demotes_a_changed_tier1_instruction():
    write = apply_policy(
        _row(tier=1, status="active", category="style", kind="instruction",
             seen_count=5, seen_projects=["A", "", "B"]),
        {"op": "UPDATE", "id": "f1", "fact": "changed", "category": "style",
         "project": "C"}, CTX)
    assert write["action"] == "demote"
    assert write["fields"]["tier"] == 2
    assert write["fields"]["status"] == "candidate"
    assert write["fields"]["seen_count"] == 1
    assert write["fields"]["seen_projects"] == ["C"]


def test_update_keeps_a_safe_tier1_row():
    write = apply_policy(
        _row(tier=1, status="active", category="restriction", kind="fact"),
        {"op": "UPDATE", "id": "f1", "fact": "new", "category": "restriction",
         "project": None}, CTX)
    assert write["action"] == "rewrite"
    assert "tier" not in write["fields"]


def test_delete_hides():
    assert apply_policy(_row(), {"op": "DELETE", "id": "f1"}, CTX) == {
        "action": "hide", "fields": {}}


def test_forget_all_hides_everything_extracted():
    assert apply_policy(None, {"op": "FORGET_ALL"}, CTX) == {
        "action": "forget_all", "fields": {}}


# ── The four worked examples ────────────────────────────────────────────


def _ctx(session):
    return {"session_id": session, "promote_fact_at": 2, "promote_instruction_at": 3}


def test_name_churn_never_reaches_tier1():
    """"Gọi tôi là Tri", rồi "Du", rồi "Long": UPDATEs on one candidate."""
    row = None
    for session, name in (("s1", "Tri"), ("s2", "Du"), ("s3", "Long")):
        if row is None:
            write = apply_policy(None, {
                "op": "ADD", "kind": "fact", "fact": f"Call me {name}",
                "category": "style", "project": None, "explicit": False},
                _ctx(session))
            assert write["fields"]["status"] == "candidate"
            row = _row(fact=f"Call me {name}", category="style",
                       status="candidate", seen_count=1, seen_projects=[""],
                       last_seen_session_id=session)
        else:
            write = apply_policy(row, {
                "op": "UPDATE", "id": "f1", "fact": f"Call me {name}",
                "category": "style", "project": None}, _ctx(session))
            assert write["fields"].get("tier", 2) == 2
            row = _row(fact=f"Call me {name}", category="style",
                       status="candidate", seen_count=1, seen_projects=[""],
                       last_seen_session_id=session)


def test_serverless_for_project_a_stays_tier2_with_that_label():
    row = None
    for session in ("s1", "s2", "s3"):
        if row is None:
            write = apply_policy(None, {
                "op": "ADD", "kind": "instruction", "fact": "Prefer serverless",
                "category": "project", "project": "A", "explicit": True},
                _ctx(session))
            assert (write["fields"]["tier"], write["fields"]["project"]) == (2, "A")
            row = _row(kind="instruction", category="project", project="A",
                       status="active", seen_count=1, seen_projects=["A"],
                       last_seen_session_id=session)
        else:
            write = apply_policy(row, {"op": "REPEAT", "id": "f1", "project": "A"},
                                 _ctx(session))
            row = _row(kind="instruction", category="project", project="A",
                       status="active", seen_count=write["fields"]["seen_count"],
                       seen_projects=write["fields"]["seen_projects"],
                       last_seen_session_id=session)
    assert write["fields"]["tier"] == 2
    assert write["fields"]["project"] == "A"


def test_short_answers_across_projects_reaches_tier1():
    row = None
    for session, project in (("s1", "A"), ("s2", None), ("s3", None)):
        if row is None:
            write = apply_policy(None, {
                "op": "ADD", "kind": "instruction", "fact": "Answer briefly",
                "category": "style", "project": project, "explicit": True},
                _ctx(session))
            row = _row(kind="instruction", category="style", project=project,
                       status="active", seen_count=1,
                       seen_projects=[project or ""],
                       last_seen_session_id=session)
        else:
            write = apply_policy(row, {"op": "REPEAT", "id": "f1", "project": project},
                                 _ctx(session))
            row = _row(kind="instruction", category="style",
                       project=write["fields"]["project"],
                       status=write["fields"]["status"],
                       seen_count=write["fields"]["seen_count"],
                       seen_projects=write["fields"]["seen_projects"],
                       last_seen_session_id=session)
    assert write["fields"]["tier"] == 1
    assert write["fields"]["project"] is None


def test_back_restriction_reaches_tier1_at_once():
    write = apply_policy(None, {
        "op": "ADD", "kind": "fact", "fact": "No exercise for the back",
        "category": "restriction", "project": None, "explicit": True},
        _ctx("s1"))
    assert (write["fields"]["tier"], write["fields"]["status"]) == (1, "active")
