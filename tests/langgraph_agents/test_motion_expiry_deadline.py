"""A restored `motion_job_id` must say whether it still points at anything.

The three clocks do not agree, and only one of them is authoritative:

    messages.motion_job_id   forever      — lives as long as the message
    DynamoDB job row         24h TTL      — best-effort, AWS promises ~48h
    S3 motions/*.bvh         1 day        — lifecycle rule, reliable

So a day after the turn, every stored job id points at nothing. Asking
`GET /motion/{job_id}` cannot tell you that: a swept row, an expired row and a
job that never existed all answer 404 identically — the evidence is gone.

Postgres has what DynamoDB does not: `created_at`. The age of the message
decides it, locally, with no round trip, and it is the only way to separate
"this expired" from "this failed" — which matters because the user-facing
sentence is different and the wrong one reads as a broken system.

The S3 rule is the binding clock, not the DynamoDB TTL: the file is what the
browser fetches, and lifecycle deletes it on time while TTL only promises to
get around to it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from langgraph_agents.db.session_store import (
    MOTION_TTL_SECONDS, _shape_message, motion_expires_at,
)
from vva_motion.jobs import TTL_SECONDS as QUEUE_TTL_SECONDS


@pytest.mark.unit
def test_the_two_ttl_constants_have_not_drifted():
    """session_store carries its own copy of the queue's TTL, because it is
    served by two deployments and only one of them ships vva_motion: the CRUD
    Lambda is a zip of agenticRAG/langgraph_agents alone, where importing it is
    a cold-start crash that takes /sessions and /me/memory down.

    Duplication is the cost of that boundary; silent drift is not. If the queue
    changes its TTL and this copy does not, every restored motion is labelled
    with the wrong lifetime and nothing else complains.
    """
    assert MOTION_TTL_SECONDS == QUEUE_TTL_SECONDS


TTL_SECONDS = MOTION_TTL_SECONDS


def _ago(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


class _Row(dict):
    """asyncpg Record stand-in: `_shape_message` probes it with `.keys()`."""
    def __init__(self, **kw):
        super().__init__(**kw)


def _deadline(created_at):
    """Parse what the API would send, back into a datetime."""
    return datetime.fromisoformat(motion_expires_at(created_at))


@pytest.mark.unit
def test_the_deadline_is_the_turn_plus_the_ttl():
    made = _ago(minutes=5)
    assert _deadline(made) == made + timedelta(seconds=MOTION_TTL_SECONDS)


@pytest.mark.unit
def test_a_fresh_turn_has_not_reached_its_deadline():
    assert _deadline(_ago(minutes=5)) > datetime.now(timezone.utc)


@pytest.mark.unit
def test_an_old_turn_is_already_past_its_deadline():
    assert _deadline(_ago(days=3)) < datetime.now(timezone.utc)


@pytest.mark.unit
def test_the_deadline_does_not_go_stale_the_way_a_boolean_would():
    """The reason this is an instant and not `motion_expired: true|false`.

    A boolean is computed once, at request time, and answers a question whose
    answer changes: a payload built at 10:00 says `false` and a tab left open
    until the next morning is still holding that `false`. The instant is the
    same value whenever it is read, so the client compares it against its own
    clock at the moment it actually needs to decide.
    """
    made = _ago(hours=23)
    first = _deadline(made)
    later = _deadline(made)
    assert first == later
    # Still live now, definitively gone two hours from now — one value, both
    # answers, no refetch.
    now = datetime.now(timezone.utc)
    assert first > now
    assert first < now + timedelta(hours=2)


@pytest.mark.unit
def test_naive_timestamp_is_read_as_utc_not_local():
    """asyncpg returns tz-aware values, but a hand-built row or a different
    driver may not. Treating a naive UTC timestamp as local time shifts the
    deadline by the machine's offset — seven hours here."""
    naive = datetime.utcnow() - timedelta(hours=1)
    assert _deadline(naive) == naive.replace(tzinfo=timezone.utc) + timedelta(
        seconds=MOTION_TTL_SECONDS
    )


@pytest.mark.unit
def test_the_flag_is_absent_when_there_is_no_motion():
    """Most messages have no motion — it is an occasional extra, never part of
    a chat turn. Stamping every one of them with `motion_expired` says
    something false about a thing that does not exist, and puts a key on every
    row of every history payload to describe nothing.

    The flag belongs to the job id: present together, absent together.
    """
    rows = [
        _Row(role="user", content="cho tôi xem squat", token_count=None, extras=None),
        _Row(role="assistant", content="đây", token_count=12,
             extras='{"motion": {"job_id": "a72fb4b3"}}'),
        # Extras present but for another subsystem — the whole point of the
        # JSONB column. Still no motion, so still no motion keys.
        _Row(role="assistant", content="chỉ là chữ", token_count=8,
             extras='{"tts": {"lang": "vi"}}'),
    ]
    shaped = [_shape_message(r, _ago(minutes=5)) for r in rows]

    assert "motion_job_id" not in shaped[0] and "motion_expires_at" not in shaped[0]
    assert shaped[1]["motion_job_id"] == "a72fb4b3"
    assert shaped[1]["motion_expires_at"] is not None
    assert "motion_prompt" not in shaped[1], "old row: job_id only, no prompt stored"
    assert "motion_job_id" not in shaped[2] and "motion_expires_at" not in shaped[2]


@pytest.mark.unit
def test_motion_prompt_is_carried_when_stored_but_absent_on_old_rows():
    """A row written after this feature shipped has both job_id and prompt in
    its extras — the restored motion should be labelled by what Kimodo
    actually rendered, not the raw user message. A row written before it
    (job_id only) must not fabricate a prompt key."""
    with_prompt = _Row(role="assistant", content="đây", token_count=12,
                        extras='{"motion": {"job_id": "a72fb4b3", "prompt": "squat movement"}}')
    job_id_only = _Row(role="assistant", content="đây", token_count=12,
                        extras='{"motion": {"job_id": "a72fb4b3"}}')

    shaped_with_prompt = _shape_message(with_prompt, _ago(minutes=5))
    shaped_job_id_only = _shape_message(job_id_only, _ago(minutes=5))

    assert shaped_with_prompt["motion_prompt"] == "squat movement"
    assert "motion_prompt" not in shaped_job_id_only


@pytest.mark.unit
def test_no_timestamp_yields_no_deadline():
    """Unknown age cannot be assumed fresh. None means "assume gone": promising
    a motion that is not there costs a poll and a wrong message, while treating
    a live one as gone costs a replay the user can trigger again."""
    assert motion_expires_at(None) is None


# ── id + feedback (task T2, message-feedback plan §2.3/§4.A) ────────────────


@pytest.mark.unit
def test_id_present_when_row_carries_one():
    """`id` appears on the wire whenever the row has one — the frontend needs
    it to attach a 👍/👎 vote to the right message."""
    row = _Row(role="assistant", content="đây", token_count=12, extras=None,
               id="a1b2c3d4-0000-0000-0000-000000000001")
    shaped = _shape_message(row, _ago(minutes=5))
    assert shaped["id"] == "a1b2c3d4-0000-0000-0000-000000000001"


@pytest.mark.unit
def test_id_absent_when_row_has_none():
    """Callers that hand in a row without `id` (this file's other rows) must
    not blow up — same guard style as `_extras`."""
    row = _Row(role="assistant", content="đây", token_count=12, extras=None)
    shaped = _shape_message(row, _ago(minutes=5))
    assert "id" not in shaped


@pytest.mark.unit
def test_feedback_present_only_when_rating_is_not_none():
    """`feedback` mirrors the motion-keys rule: present only when a
    message_feedback row was actually joined in (rating IS NOT NULL from the
    LEFT JOIN) — most messages have no vote at all."""
    voted = _Row(role="assistant", content="đây", token_count=12, extras=None,
                 feedback_rating=-1, feedback_reasons=["incorrect", "unsafe"],
                 feedback_comment="sai roi")
    unvoted = _Row(role="assistant", content="đây", token_count=12, extras=None,
                   feedback_rating=None, feedback_reasons=None, feedback_comment=None)
    no_join_columns = _Row(role="assistant", content="đây", token_count=12, extras=None)

    shaped_voted = _shape_message(voted, _ago(minutes=5))
    shaped_unvoted = _shape_message(unvoted, _ago(minutes=5))
    shaped_no_join = _shape_message(no_join_columns, _ago(minutes=5))

    assert shaped_voted["feedback"] == {
        "rating": -1, "reasons": ["incorrect", "unsafe"], "comment": "sai roi",
    }
    assert "feedback" not in shaped_unvoted
    assert "feedback" not in shaped_no_join


@pytest.mark.unit
def test_meta_never_exposed_on_the_wire():
    """`extras.meta` (turn context snapshot) is for ops SQL only — it must
    never appear in the shaped message, unlike `extras.motion`."""
    row = _Row(role="assistant", content="đây", token_count=12,
               extras='{"motion": {"job_id": "a72fb4b3"}, "meta": {"request_id": "req-1", "persona_id": "anne"}}')
    shaped = _shape_message(row, _ago(minutes=5))
    assert shaped["motion_job_id"] == "a72fb4b3"
    assert "meta" not in shaped
    assert "request_id" not in shaped
