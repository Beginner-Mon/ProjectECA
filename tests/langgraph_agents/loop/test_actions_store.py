"""Contract suite for `ActionStore` and `TurnStore` (stage 4A).

Every case runs on the memory stores and, marked `integration`, on Postgres as
`eca_user` under RLS (`user_scope()`), through `test_pg_client`. The PG runs
skip here and run in the CI job `Loop PG contract`, which applies every
migration (so 021 must apply on a bare PG 18 after 020).

The concurrency tests use `asyncio.gather` and require exactly one winner.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from langgraph_agents.loop import actions as A
from langgraph_agents.loop import turns as T
from langgraph_agents.loop.actions import (
    ACTION_TTL_S, APPROVED, DECLINED, EXECUTING, EXPIRED, FAILED, FINAL_STATUSES,
    INVALIDATED, PROPOSED, STATUSES, SUCCEEDED, TRANSITIONS, UNKNOWN,
    MemoryActionStore, PgActionStore, is_legal, make_action_id, new_action,
    payload_sha256,
)
from langgraph_agents.loop.turns import MemoryTurnStore, PgTurnStore


def _id() -> str:
    return str(uuid.uuid4())


# ── fixture: memory or Postgres ───────────────────────────────────────────

class Harness:
    def __init__(self, actions, turns, age_action, cleanup_users):
        self.actions = actions
        self.turns = turns
        self._age_action = age_action
        self.users: list[str] = []

    def user(self) -> str:
        u = _id()
        self.users.append(u)
        return u

    async def age(self, user_id: str, action_id: str, seconds: float) -> None:
        """Make an action's `updated_at` older by `seconds`."""
        await self._age_action(user_id, action_id, seconds)


@pytest.fixture(params=[
    pytest.param("memory", id="memory"),
    pytest.param("pg", id="pg", marks=pytest.mark.integration),
])
def h(request):
    if request.param == "memory":
        actions = MemoryActionStore()
        turns = MemoryTurnStore()

        async def age(user_id, action_id, seconds):
            row = actions._rows[action_id]
            row.updated_at = row.updated_at - timedelta(seconds=seconds)

        yield Harness(actions, turns, age, None)
        return

    pg = request.getfixturevalue("test_pg_client")

    async def _has_tables():
        async with pg.get_client().transaction() as conn:
            return bool(await conn.fetchval("SELECT to_regclass('agent_actions')")
                        and await conn.fetchval("SELECT to_regclass('loop_turns')"))

    if not asyncio.run(_has_tables()):
        pytest.skip("migration 021 not applied")

    actions = PgActionStore(get_pg_client=pg.get_client)
    turns = PgTurnStore(get_pg_client=pg.get_client)

    async def age(user_id, action_id, seconds):
        async with pg.get_client().user_scope(user_id) as conn:
            await conn.execute(
                "UPDATE agent_actions SET updated_at = updated_at - ($2 * interval '1 second') "
                "WHERE action_id = $1::uuid", action_id, float(seconds))

    harness = Harness(actions, turns, age, None)
    yield harness

    async def _cleanup():
        for uid in harness.users:
            async with pg.get_client().user_scope(uid) as conn:
                await conn.execute("DELETE FROM agent_actions")
                await conn.execute("DELETE FROM loop_turns")

    try:
        asyncio.run(_cleanup())
    except Exception:  # noqa: BLE001 - cleanup must not mask the result
        pass


def _action(user: str, session: str, thread: str | None = None,
            call: str | None = None, **extra) -> A.ActionRecord:
    thread = thread or _id()
    return new_action(
        user_id=user, session_id=session, thread_id=thread,
        tool_call_id=call or _id(), plugin_id="dev-reminders", tool="create_reminder",
        arguments={"title": "uống thuốc", "at": "08:00"},
        payload_display=[{"label": "title", "value": "uống thuốc"}],
        recipients=["a@x.org", "b@y.org"],
        provenance=[{"tool": "kb_search", "source_label": "kb"}],
        payload_enc=b"\x01sealed-bytes", plugin_version="1", tool_def_sha256="d" * 64,
        **extra)


async def _walk(store, rec: A.ActionRecord, to: str) -> None:
    """Propose `rec` and move it, step by legal step, to status `to`."""
    path = {
        PROPOSED: [], APPROVED: [APPROVED], EXECUTING: [APPROVED, EXECUTING],
        SUCCEEDED: [APPROVED, EXECUTING, SUCCEEDED],
        FAILED: [APPROVED, EXECUTING, FAILED],
        UNKNOWN: [APPROVED, EXECUTING, UNKNOWN],
        DECLINED: [DECLINED], EXPIRED: [EXPIRED], INVALIDATED: [INVALIDATED],
    }[to]
    assert await store.propose(rec) is True
    cur = PROPOSED
    for nxt in path:
        assert await store.transition(rec.user_id, rec.action_id, cur, nxt) is True
        cur = nxt


# ── ids and helpers (pure) ────────────────────────────────────────────────

def test_action_id_is_uuid5_of_thread_and_tool_call():
    t = _id()
    assert make_action_id(t, "call_1") == str(uuid.uuid5(uuid.UUID(t), "call_1"))
    assert make_action_id(t, "call_1") == make_action_id(t, "call_1")
    assert make_action_id(t, "call_1") != make_action_id(t, "call_2")
    assert make_action_id(t, "call_1") != make_action_id(_id(), "call_1")


def test_payload_sha256_is_canonical():
    assert payload_sha256({"a": 1, "b": [1, 2]}) == payload_sha256({"b": [1, 2], "a": 1})
    assert payload_sha256({"a": 1}) != payload_sha256({"a": 2})


def test_new_action_expires_after_thirty_minutes():
    now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    r = new_action(user_id=_id(), session_id=_id(), thread_id=_id(), tool_call_id="c",
                   plugin_id="p", tool="t", arguments={}, now=now)
    assert ACTION_TTL_S == 1800
    assert r.expires_at - r.created_at == timedelta(minutes=30)
    assert r.status == PROPOSED


def test_transition_table_matches_the_plan():
    assert TRANSITIONS[PROPOSED] == {APPROVED, DECLINED, EXPIRED, INVALIDATED}
    assert TRANSITIONS[APPROVED] == {EXECUTING, INVALIDATED, EXPIRED}
    assert TRANSITIONS[EXECUTING] == {SUCCEEDED, FAILED, UNKNOWN}
    for final in FINAL_STATUSES:
        assert final not in TRANSITIONS          # nothing leaves a final status


# ── actions: propose ──────────────────────────────────────────────────────

def test_propose_inserts_and_get_returns_the_record(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        ok = await h.actions.propose(rec)
        return ok, await h.actions.get(u, rec.action_id)

    ok, got = asyncio.run(go())
    assert ok is True
    assert got.status == PROPOSED
    assert (got.action_id, got.user_id, got.session_id, got.thread_id) == (
        rec.action_id, u, s, rec.thread_id)
    assert (got.plugin_id, got.tool, got.plugin_version) == ("dev-reminders", "create_reminder", "1")
    assert got.payload_sha256 == rec.payload_sha256 and got.tool_def_sha256 == "d" * 64
    assert got.payload_display == [{"label": "title", "value": "uống thuốc"}]
    assert got.recipients == ["a@x.org", "b@y.org"]
    assert got.provenance == [{"tool": "kb_search", "source_label": "kb"}]
    assert got.payload_enc == b"\x01sealed-bytes"
    assert got.risk == "write" and got.result_ref is None
    assert abs((got.expires_at - got.created_at).total_seconds() - ACTION_TTL_S) < 1


def test_propose_twice_inserts_once(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        first = await h.actions.propose(rec)
        second = await h.actions.propose(dataclasses.replace(rec, payload_enc=b"other"))
        return first, second, await h.actions.get(u, rec.action_id)

    first, second, got = asyncio.run(go())
    assert (first, second) == (True, False)
    assert got.payload_enc == b"\x01sealed-bytes"        # the first insert stands


def test_propose_twice_in_parallel_inserts_once(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        return await asyncio.gather(*[h.actions.propose(dataclasses.replace(rec))
                                      for _ in range(6)])

    results = asyncio.run(go())
    assert sorted(results) == [False] * 5 + [True]


def test_propose_does_not_reset_a_moved_action(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, APPROVED)
        again = await h.actions.propose(dataclasses.replace(rec))
        return again, (await h.actions.get(u, rec.action_id)).status

    assert asyncio.run(go()) == (False, APPROVED)


# ── actions: transitions ──────────────────────────────────────────────────

LEGAL = [(f, t) for f, ts in TRANSITIONS.items() for t in sorted(ts)]


@pytest.mark.parametrize("frm,to", LEGAL)
def test_every_legal_transition(h, frm, to):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, frm)
        ok = await h.actions.transition(u, rec.action_id, frm, to)
        return ok, await h.actions.get(u, rec.action_id)

    ok, got = asyncio.run(go())
    assert ok is True
    assert got.status == to


ILLEGAL = [(f, t) for f, t in itertools.product(sorted(STATUSES), repeat=2)
           if not is_legal(f, t)]


def test_there_are_illegal_pairs_to_check():
    assert len(LEGAL) == 10 and len(ILLEGAL) == 81 - 10


def test_every_illegal_transition_is_refused_and_changes_nothing(h):
    u, s = h.user(), _id()

    async def go():
        bad: list = []
        for frm in sorted(STATUSES):
            rec = _action(u, s)
            await _walk(h.actions, rec, frm)
            before = await h.actions.get(u, rec.action_id)
            for to in sorted(STATUSES):
                if is_legal(frm, to):
                    continue
                if await h.actions.transition(u, rec.action_id, frm, to):
                    bad.append((frm, to))
            after = await h.actions.get(u, rec.action_id)
            if (after.status, after.payload_enc) != (before.status, before.payload_enc):
                bad.append(("changed", frm))
        return bad

    assert asyncio.run(go()) == []


def test_a_stale_from_status_is_refused(h):
    """The compare-and-set: the row is `approved`, the caller thinks `proposed`."""
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, APPROVED)
        stale = await h.actions.transition(u, rec.action_id, PROPOSED, DECLINED)
        return stale, (await h.actions.get(u, rec.action_id)).status

    assert asyncio.run(go()) == (False, APPROVED)


def test_transition_of_an_unknown_action_is_false(h):
    u = h.user()
    assert asyncio.run(h.actions.transition(u, _id(), PROPOSED, APPROVED)) is False


def test_unknown_status_and_unknown_field_raise(h):
    u = h.user()
    with pytest.raises(ValueError):
        asyncio.run(h.actions.transition(u, _id(), "bogus", APPROVED))
    with pytest.raises(ValueError):
        asyncio.run(h.actions.transition(u, _id(), PROPOSED, "bogus"))
    with pytest.raises(ValueError):
        asyncio.run(h.actions.transition(u, _id(), PROPOSED, APPROVED, payload_enc=b"x"))


def test_transition_sets_result_ref(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, EXECUTING)
        ok = await h.actions.transition(u, rec.action_id, EXECUTING, SUCCEEDED,
                                        result_ref="reminder:42")
        return ok, await h.actions.get(u, rec.action_id)

    ok, got = asyncio.run(go())
    assert ok and got.status == SUCCEEDED and got.result_ref == "reminder:42"


def test_the_sealed_payload_is_dropped_at_a_final_status_only(h):
    u, s = h.user(), _id()

    async def go():
        kept, dropped = {}, {}
        for to in sorted(STATUSES):
            rec = _action(u, s)
            await _walk(h.actions, rec, to)
            got = await h.actions.get(u, rec.action_id)
            (dropped if to in FINAL_STATUSES else kept)[to] = got.payload_enc
        return kept, dropped

    kept, dropped = asyncio.run(go())
    assert set(kept) == {PROPOSED, APPROVED, EXECUTING}
    assert all(v == b"\x01sealed-bytes" for v in kept.values())
    assert set(dropped) == set(FINAL_STATUSES)
    assert all(v is None for v in dropped.values())


# ── actions: concurrency (one winner) ─────────────────────────────────────

def test_concurrent_approved_to_executing_exactly_one_wins(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, APPROVED)
        results = await asyncio.gather(*[
            h.actions.transition(u, rec.action_id, APPROVED, EXECUTING)
            for _ in range(2)])
        return results, (await h.actions.get(u, rec.action_id)).status

    results, status = asyncio.run(go())
    assert sorted(results) == [False, True]
    assert status == EXECUTING


def test_many_concurrent_approved_to_executing_exactly_one_wins(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, APPROVED)
        return await asyncio.gather(*[
            h.actions.transition(u, rec.action_id, APPROVED, EXECUTING)
            for _ in range(8)])

    results = asyncio.run(go())
    assert results.count(True) == 1 and results.count(False) == 7


def test_double_click_proposed_to_approved_exactly_one_wins(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await h.actions.propose(rec)
        return await asyncio.gather(*[
            h.actions.transition(u, rec.action_id, PROPOSED, APPROVED)
            for _ in range(2)])

    assert sorted(asyncio.run(go())) == [False, True]


def test_approve_and_decline_race_exactly_one_wins(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await h.actions.propose(rec)
        results = await asyncio.gather(
            h.actions.transition(u, rec.action_id, PROPOSED, APPROVED),
            h.actions.transition(u, rec.action_id, PROPOSED, DECLINED))
        return results, (await h.actions.get(u, rec.action_id)).status

    results, status = asyncio.run(go())
    assert sorted(results) == [False, True]
    assert status in (APPROVED, DECLINED)
    assert (status == APPROVED) == results[0]


def test_replaying_a_finished_transition_loses(h):
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, EXECUTING)
        first = await h.actions.transition(u, rec.action_id, EXECUTING, SUCCEEDED)
        replay = await h.actions.transition(u, rec.action_id, EXECUTING, SUCCEEDED)
        return first, replay

    assert asyncio.run(go()) == (True, False)


# ── actions: session queries ──────────────────────────────────────────────

def test_pending_for_session_lists_proposed_only_without_the_payload(h):
    u, s, other = h.user(), _id(), _id()

    async def go():
        a, b, c, d = (_action(u, s), _action(u, s), _action(u, s), _action(u, other))
        for r in (a, b, c, d):
            await h.actions.propose(r)
        await h.actions.transition(u, c.action_id, PROPOSED, APPROVED)
        got = await h.actions.pending_for_session(u, s)
        return {a.action_id, b.action_id}, got

    want, got = asyncio.run(go())
    assert {r.action_id for r in got} == want
    assert all(r.status == PROPOSED and r.payload_enc is None for r in got)
    assert [r.created_at for r in got] == sorted(r.created_at for r in got)


def test_invalidate_for_session_hits_proposed_in_that_session_only(h):
    u, s, other = h.user(), _id(), _id()

    async def go():
        p1, p2 = _action(u, s), _action(u, s)
        approved, elsewhere = _action(u, s), _action(u, other)
        for r in (p1, p2, approved, elsewhere):
            await h.actions.propose(r)
        await h.actions.transition(u, approved.action_id, PROPOSED, APPROVED)
        ids = await h.actions.invalidate_for_session(u, s)
        again = await h.actions.invalidate_for_session(u, s)
        st = {r.action_id: (await h.actions.get(u, r.action_id)) for r in
              (p1, p2, approved, elsewhere)}
        return {p1.action_id, p2.action_id}, ids, again, st, approved, elsewhere

    want, ids, again, st, approved, elsewhere = asyncio.run(go())
    assert set(ids) == want and len(ids) == 2
    assert again == []
    assert {k: v.status for k, v in st.items() if k in want} == {k: INVALIDATED for k in want}
    assert all(st[k].payload_enc is None for k in want)
    assert st[approved.action_id].status == APPROVED
    assert st[elsewhere.action_id].status == PROPOSED


def test_sweep_turns_stale_executing_into_unknown_and_never_runs_them_again(h):
    u, s, other = h.user(), _id(), _id()

    async def go():
        stale, fresh = _action(u, s), _action(u, s)
        approved, elsewhere = _action(u, s), _action(u, other)
        await _walk(h.actions, stale, EXECUTING)
        await _walk(h.actions, fresh, EXECUTING)
        await _walk(h.actions, approved, APPROVED)
        await _walk(h.actions, elsewhere, EXECUTING)
        for r in (stale, elsewhere, approved):
            await h.age(u, r.action_id, 300)
        swept = await h.actions.sweep_stale_executing(u, s, older_than_s=150)
        again = await h.actions.sweep_stale_executing(u, s, older_than_s=150)
        st = {n: (await h.actions.get(u, r.action_id)) for n, r in
              dict(stale=stale, fresh=fresh, approved=approved, elsewhere=elsewhere).items()}
        # `unknown` is final: it cannot be driven to executing again.
        rerun = await h.actions.transition(u, stale.action_id, APPROVED, EXECUTING)
        rerun2 = await h.actions.transition(u, stale.action_id, EXECUTING, SUCCEEDED)
        return stale, swept, again, st, rerun, rerun2

    stale, swept, again, st, rerun, rerun2 = asyncio.run(go())
    assert swept == [stale.action_id] and again == []
    assert st["stale"].status == UNKNOWN and st["stale"].payload_enc is None
    assert st["fresh"].status == EXECUTING
    assert st["approved"].status == APPROVED          # only `executing` is swept
    assert st["elsewhere"].status == EXECUTING        # another session
    assert (rerun, rerun2) == (False, False)


def test_a_zero_age_sweep_after_a_late_finish_loses_to_the_finish(h):
    """The sweep and a slow `executing -> succeeded` race: one outcome only."""
    u, s = h.user(), _id()
    rec = _action(u, s)

    async def go():
        await _walk(h.actions, rec, EXECUTING)
        await h.age(u, rec.action_id, 300)
        done, swept = await asyncio.gather(
            h.actions.transition(u, rec.action_id, EXECUTING, SUCCEEDED),
            h.actions.sweep_stale_executing(u, s, older_than_s=100))
        return done, swept, (await h.actions.get(u, rec.action_id)).status

    done, swept, status = asyncio.run(go())
    assert done != bool(swept)                       # exactly one of them moved it
    assert status == (SUCCEEDED if done else UNKNOWN)


# ── actions: users are separate ───────────────────────────────────────────

def test_user_b_cannot_see_or_move_user_a_actions(h):
    a, b, s = h.user(), h.user(), _id()
    rec = _action(a, s)

    async def go():
        await h.actions.propose(rec)
        seen = await h.actions.get(b, rec.action_id)
        moved = await h.actions.transition(b, rec.action_id, PROPOSED, APPROVED)
        pending = await h.actions.pending_for_session(b, s)
        inval = await h.actions.invalidate_for_session(b, s)
        swept = await h.actions.sweep_stale_executing(b, s, 0)
        mine = await h.actions.get(a, rec.action_id)
        return seen, moved, pending, inval, swept, mine

    seen, moved, pending, inval, swept, mine = asyncio.run(go())
    assert seen is None and moved is False
    assert pending == [] and inval == [] and swept == []
    assert mine.status == PROPOSED and mine.payload_enc is not None


# ── turns ─────────────────────────────────────────────────────────────────

def _exp(minutes: int = 30) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


async def _pause(h, u, s, t=None, **kw):
    t = t or _id()
    ok = await h.turns.pause(u, s, t, request_id=kw.get("request_id", _id()),
                             transcript_enc=kw.get("transcript_enc", b"\x01sealed"),
                             state_json=kw.get("state_json", {"rounds": 2, "actions": ["a"]}),
                             expires_at=kw.get("expires_at", _exp()))
    return t, ok


def test_pause_then_get(h):
    u, s = h.user(), _id()

    async def go():
        t, ok = await _pause(h, u, s, state_json={"rounds": 2, "note": "xin chào"})
        return t, ok, await h.turns.get(u, t)

    t, ok, got = asyncio.run(go())
    assert ok is True
    assert (got.thread_id, got.user_id, got.session_id, got.status) == (t, u, s, T.PAUSED)
    assert got.transcript_enc == b"\x01sealed"
    assert got.state_json == {"rounds": 2, "note": "xin chào"}


def test_pausing_a_paused_thread_again_is_refused(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        _, again = await _pause(h, u, s, t=t, transcript_enc=b"other")
        return again, (await h.turns.get(u, t)).transcript_enc

    assert asyncio.run(go()) == (False, b"\x01sealed")


def test_claim_returns_the_turn_and_a_second_claim_loses(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        first = await h.turns.claim(u, t)
        second = await h.turns.claim(u, t)
        return first, second, (await h.turns.get(u, t)).status

    first, second, status = asyncio.run(go())
    assert first is not None and first.transcript_enc == b"\x01sealed"
    assert first.status == T.RESUMED
    assert second is None and status == T.RESUMED


def test_claim_in_parallel_wins_once(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        return await asyncio.gather(*[h.turns.claim(u, t) for _ in range(6)])

    results = asyncio.run(go())
    assert sum(r is not None for r in results) == 1


def test_claim_of_an_unknown_thread_is_none(h):
    assert asyncio.run(h.turns.claim(h.user(), _id())) is None


def test_finish_only_after_claim_and_drops_the_transcript(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        early = await h.turns.finish(u, t)               # still paused
        await h.turns.claim(u, t)
        done = await h.turns.finish(u, t)
        twice = await h.turns.finish(u, t)
        return early, done, twice, await h.turns.get(u, t)

    early, done, twice, got = asyncio.run(go())
    assert (early, done, twice) == (False, True, False)
    assert got.status == T.FINISHED and got.transcript_enc is None and got.state_json == {}


def test_a_resumed_turn_can_pause_again(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        await h.turns.claim(u, t)
        _, again = await _pause(h, u, s, t=t, transcript_enc=b"\x01second",
                                state_json={"rounds": 5})
        got = await h.turns.get(u, t)
        reclaim = await h.turns.claim(u, t)
        return again, got, reclaim

    again, got, reclaim = asyncio.run(go())
    assert again is True
    assert got.status == T.PAUSED and got.transcript_enc == b"\x01second"
    assert got.state_json == {"rounds": 5}
    assert reclaim is not None and reclaim.transcript_enc == b"\x01second"


def test_a_finished_turn_cannot_pause_or_claim_again(h):
    u, s = h.user(), _id()

    async def go():
        t, _ = await _pause(h, u, s)
        await h.turns.claim(u, t)
        await h.turns.finish(u, t)
        _, again = await _pause(h, u, s, t=t)
        return again, await h.turns.claim(u, t)

    assert asyncio.run(go()) == (False, None)


def test_turns_invalidate_for_session(h):
    u, s, other = h.user(), _id(), _id()

    async def go():
        t1, _ = await _pause(h, u, s)
        t2, _ = await _pause(h, u, s)
        t3, _ = await _pause(h, u, other)
        await h.turns.claim(u, t2)                       # resumed: not invalidated
        ids = await h.turns.invalidate_for_session(u, s)
        again = await h.turns.invalidate_for_session(u, s)
        return (t1, t2, t3, ids, again, await h.turns.get(u, t1),
                await h.turns.get(u, t2), await h.turns.get(u, t3),
                await h.turns.claim(u, t1))

    t1, t2, t3, ids, again, g1, g2, g3, claim1 = asyncio.run(go())
    assert ids == [t1] and again == []
    assert g1.status == T.INVALIDATED and g1.transcript_enc is None
    assert g2.status == T.RESUMED
    assert g3.status == T.PAUSED
    assert claim1 is None                                # an invalidated card cannot resume


def test_user_b_cannot_claim_or_see_user_a_turns(h):
    a, b, s = h.user(), h.user(), _id()

    async def go():
        t, _ = await _pause(h, a, s)
        return (await h.turns.get(b, t), await h.turns.claim(b, t),
                await h.turns.finish(b, t), await h.turns.invalidate_for_session(b, s),
                await h.turns.get(a, t))

    seen, claimed, finished, inval, mine = asyncio.run(go())
    assert seen is None and claimed is None and finished is False and inval == []
    assert mine.status == T.PAUSED                       # untouched; A can still resume


# ── resolution (memory only) ──────────────────────────────────────────────

def test_default_stores_are_in_memory_under_the_guard():
    assert isinstance(A.get_action_store(), MemoryActionStore)
    assert isinstance(T.get_turn_store(), MemoryTurnStore)


# ── Postgres only: RLS and the migration ──────────────────────────────────

@pytest.mark.integration
def test_pg_rls_hides_other_users_rows_even_without_a_user_filter(test_pg_client):
    pg = test_pg_client

    async def go():
        async with pg.get_client().transaction() as conn:
            if not await conn.fetchval("SELECT to_regclass('agent_actions')"):
                return None
        a, b = _id(), _id()
        store = PgActionStore(get_pg_client=pg.get_client)
        rec = _action(a, _id())
        try:
            await store.propose(rec)
            async with pg.get_client().user_scope(b) as conn:
                seen = await conn.fetchval(
                    "SELECT count(*) FROM agent_actions WHERE action_id = $1::uuid",
                    rec.action_id)
                updated = await conn.execute(
                    "UPDATE agent_actions SET status = 'declined' "
                    "WHERE action_id = $1::uuid", rec.action_id)
                try:
                    await conn.execute(
                        "INSERT INTO agent_actions (action_id, user_id, session_id, "
                        "thread_id, plugin_id, tool, payload_sha256, expires_at) "
                        "VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, 'p', 't', 'x', now())",
                        _id(), a, _id(), _id())
                    forged = False
                except Exception:  # noqa: BLE001 - WITH CHECK violation
                    forged = True
            async with pg.get_client().user_scope(a) as conn:
                status = await conn.fetchval(
                    "SELECT status FROM agent_actions WHERE action_id = $1::uuid",
                    rec.action_id)
            return seen, updated, forged, status
        finally:
            async with pg.get_client().user_scope(a) as conn:
                await conn.execute("DELETE FROM agent_actions")

    out = asyncio.run(go())
    if out is None:
        pytest.skip("migration 021 not applied")
    seen, updated, forged, status = out
    assert seen == 0
    assert updated == "UPDATE 0"
    assert forged is True
    assert status == PROPOSED


@pytest.mark.integration
def test_pg_a_query_outside_user_scope_fails_loudly(test_pg_client):
    pg = test_pg_client

    async def go():
        async with pg.get_client()._raw_transaction() as conn:
            if not await conn.fetchval("SELECT to_regclass('loop_turns')"):
                return None
            try:
                await conn.fetch("SELECT * FROM loop_turns")
            except Exception:  # noqa: BLE001
                return True
            return False

    out = asyncio.run(go())
    if out is None:
        pytest.skip("migration 021 not applied")
    assert out is True
