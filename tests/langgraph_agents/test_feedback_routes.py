"""Unit tests for per-message feedback — POST/DELETE /me/feedback/messages/{id}.

Pattern follows test_preferences.py: FastAPI app with only this router mounted,
current_user_id overridden via dependency_overrides, and a mocked pg client.

Unlike routes_preferences.py, routes_feedback.py calls `pg.fetchrow` /
`pg.execute` directly (not `pg.transaction()` + `conn.fetchrow`) — the plan
requires exactly one statement per route, and PostgresClient.fetchrow/execute
already open their own scoped transaction. So the mock here patches the pg
client's own methods, not a connection yielded by a transaction context
manager.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MESSAGE_ID = "00000000-0000-4000-a000-0000000000aa"
UID_A = "00000000-0000-4000-a000-000000000001"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    import langgraph_agents.api.auth as auth_mod
    monkeypatch.setenv("AUTH_PROVIDER", "cognito")
    monkeypatch.setenv("COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_testpool")
    monkeypatch.setenv("COGNITO_APP_CLIENT_ID", "test-client-id")
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()
    yield auth_mod
    auth_mod.get_auth_config.cache_clear()
    auth_mod.get_jwks_client.cache_clear()


def _app(uid: str | None):
    """FastAPI with the feedback router mounted.

    uid=None leaves current_user_id un-overridden, for the "no token" checks.
    """
    from langgraph_agents.api.routes_feedback import router as feedback_router
    from langgraph_agents.api.auth import current_user_id, override_user
    app = FastAPI()
    app.include_router(feedback_router)
    if uid is not None:
        app.dependency_overrides[current_user_id] = override_user(uid)
    return app


def _mock_pg(*, fetchrow_return=None, execute_return="DELETE 0"):
    """A pg client whose fetchrow/execute we can assert on directly.

    routes_feedback.py calls these on the client itself (each opens its own
    RLS-scoped transaction internally) — see the module docstring above.
    """
    pg = MagicMock()
    pg.fetchrow = AsyncMock(return_value=fetchrow_return)
    pg.execute = AsyncMock(return_value=execute_return)
    return pg


def _row(rating: int, reasons: list[str], comment: str | None):
    return {
        "message_id": MESSAGE_ID,
        "rating": rating,
        "reasons": reasons,
        "comment": comment,
        "updated_at": datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
    }


def _post(client: TestClient, body: dict, message_id: str = MESSAGE_ID):
    return client.post(f"/me/feedback/messages/{message_id}", json=body)


# ── POST: happy paths ───────────────────────────────────────────────────


@pytest.mark.unit
def test_thumbs_up_upserts_with_empty_reasons_and_no_comment():
    pg = _mock_pg(fetchrow_return=_row(1, [], None))
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": 1})

    assert r.status_code == 200
    body = r.json()
    assert body == {
        "message_id": MESSAGE_ID,
        "rating": 1,
        "reasons": [],
        "comment": None,
        "updated_at": "2026-09-29T12:00:00+00:00",
    }
    # A 👍 always writes reasons=[] and comment=None — that is what clears an
    # earlier 👎's reasons/comment on the next upsert.
    # call_args.args = (query, message_id, rating, reasons, comment, uid)
    args = pg.fetchrow.call_args.args
    assert args[2:] == (1, [], None, UID_A)


@pytest.mark.unit
def test_thumbs_down_with_reasons_and_stripped_comment():
    pg = _mock_pg(fetchrow_return=_row(-1, ["incorrect", "unsafe"], "text"))
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {
            "rating": -1,
            # duplicate "incorrect" must be deduped, preserving first-seen order
            "reasons": ["incorrect", "incorrect", "unsafe"],
            "comment": "  text  ",
        })

    assert r.status_code == 200
    # call_args.args = (query, message_id, rating, reasons, comment, uid)
    args = pg.fetchrow.call_args.args
    assert args[2] == -1
    assert args[3] == ["incorrect", "unsafe"]  # deduped
    assert args[4] == "text"  # stripped
    assert args[5] == UID_A


@pytest.mark.unit
def test_delete_returns_204_and_calls_execute_once():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = TestClient(_app(UID_A)).delete(f"/me/feedback/messages/{MESSAGE_ID}")

    assert r.status_code == 204
    assert r.content == b""
    pg.execute.assert_called_once()
    # call_args.args = (query, message_id, uid)
    assert pg.execute.call_args.args[1] == MESSAGE_ID
    assert pg.execute.call_args.args[2] == UID_A


# ── Defense in depth: explicit owner predicate, not just RLS ────────────


@pytest.mark.unit
def test_upsert_sql_has_explicit_owner_predicate():
    """RLS is the primary guard; c.user_id = $5 is the second layer that still
    holds if a DSN ever connects as the table owner or a BYPASSRLS role."""
    from langgraph_agents.api.routes_feedback import _UPSERT
    assert "JOIN conversations c ON c.session_id = m.session_id" in _UPSERT
    assert "c.user_id = $5::uuid" in _UPSERT


@pytest.mark.unit
def test_upsert_binds_uid_as_fifth_param():
    pg = _mock_pg(fetchrow_return=_row(1, [], None))
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        _post(TestClient(_app(UID_A)), {"rating": 1})
    assert pg.fetchrow.call_args.args[-1] == UID_A


@pytest.mark.unit
def test_delete_sql_has_explicit_owner_predicate():
    from langgraph_agents.api.routes_feedback import _DELETE
    assert "USING messages m, conversations c" in _DELETE
    assert "c.user_id = $2::uuid" in _DELETE


@pytest.mark.unit
def test_delete_binds_uid_as_second_param():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        TestClient(_app(UID_A)).delete(f"/me/feedback/messages/{MESSAGE_ID}")
    assert pg.execute.call_args.args[-1] == UID_A


# ── DELETE: deleted count in the log ─────────────────────────────────────


@pytest.mark.unit
def test_delete_logs_zero_when_nothing_removed(caplog):
    pg = _mock_pg(execute_return="DELETE 0")
    with caplog.at_level(logging.INFO):
        with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
            r = TestClient(_app(UID_A)).delete(f"/me/feedback/messages/{MESSAGE_ID}")
    assert r.status_code == 204
    records = [rec for rec in caplog.records if rec.getMessage() == "feedback_message_cleared"]
    assert len(records) == 1
    assert records[0].deleted == 0


@pytest.mark.unit
def test_delete_logs_one_when_a_row_was_removed(caplog):
    pg = _mock_pg(execute_return="DELETE 1")
    with caplog.at_level(logging.INFO):
        with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
            r = TestClient(_app(UID_A)).delete(f"/me/feedback/messages/{MESSAGE_ID}")
    assert r.status_code == 204
    records = [rec for rec in caplog.records if rec.getMessage() == "feedback_message_cleared"]
    assert len(records) == 1
    assert records[0].deleted == 1


@pytest.mark.unit
def test_parse_deleted_count_is_defensive():
    """Never raise over a status string it doesn't recognise — it's a log field."""
    from langgraph_agents.api.routes_feedback import _parse_deleted_count
    assert _parse_deleted_count("DELETE 0") == 0
    assert _parse_deleted_count("DELETE 1") == 1
    assert _parse_deleted_count("DELETE 12") == 12
    assert _parse_deleted_count("") == 0
    assert _parse_deleted_count(None) == 0
    assert _parse_deleted_count("garbage") == 0


# ── POST: validation (422) ──────────────────────────────────────────────


@pytest.mark.unit
def test_thumbs_up_with_reasons_is_422():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": 1, "reasons": ["incorrect"]})
    assert r.status_code == 422
    pg.fetchrow.assert_not_called()


@pytest.mark.unit
def test_thumbs_up_with_comment_is_422():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": 1, "comment": "why"})
    assert r.status_code == 422
    pg.fetchrow.assert_not_called()


@pytest.mark.unit
def test_unknown_reason_code_is_422():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": -1, "reasons": ["bogus"]})
    assert r.status_code == 422


@pytest.mark.unit
def test_ten_reasons_is_422():
    """Rejected on raw length (max_length=9), before dedup runs — so a caller
    cannot dodge the cap by repeating a code."""
    pg = _mock_pg()
    reasons = [
        "incorrect", "unsafe", "not_relevant", "incomplete", "hard_to_follow",
        "wrong_language", "motion_issue", "voice_issue", "other", "incorrect",
    ]
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": -1, "reasons": reasons})
    assert r.status_code == 422


@pytest.mark.unit
def test_comment_over_1000_chars_is_422():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": -1, "comment": "x" * 1001})
    assert r.status_code == 422


@pytest.mark.unit
def test_non_uuid_path_is_422():
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": 1}, message_id="not-a-uuid")
    assert r.status_code == 422


# ── POST: 404 ────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_no_row_returned_is_404():
    """RLS on messages hides other users' rows, and user messages are excluded
    by role='assistant' — both collapse into a 0-row RETURNING, hence 404."""
    pg = _mock_pg(fetchrow_return=None)
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = _post(TestClient(_app(UID_A)), {"rating": 1})
    assert r.status_code == 404


# ── Auth ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_post_requires_auth():
    r = _post(TestClient(_app(None)), {"rating": 1})
    assert r.status_code == 401


@pytest.mark.unit
def test_delete_requires_auth():
    r = TestClient(_app(None)).delete(f"/me/feedback/messages/{MESSAGE_ID}")
    assert r.status_code == 401


# ── Privacy: comment must never be logged ───────────────────────────────


@pytest.mark.unit
def test_comment_never_appears_in_logs(caplog):
    secret = "patient reports lower back pain after the squat demo XK392"
    pg = _mock_pg(fetchrow_return=_row(-1, ["unsafe"], secret))

    with caplog.at_level(logging.INFO):
        with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
            r = _post(TestClient(_app(UID_A)), {
                "rating": -1, "reasons": ["unsafe"], "comment": secret,
            })
    assert r.status_code == 200

    for record in caplog.records:
        assert secret not in record.getMessage()
        for value in record.__dict__.values():
            assert secret not in str(value)


@pytest.mark.unit
def test_delete_does_not_log_anything_message_specific():
    """Sanity check that DELETE's log line carries no comment field at all
    (there is nothing to carry — the body has none)."""
    pg = _mock_pg()
    with patch("langgraph_agents.api.routes_feedback.get_pg_client", return_value=pg):
        r = TestClient(_app(UID_A)).delete(f"/me/feedback/messages/{MESSAGE_ID}")
    assert r.status_code == 204


# ── Integration: against a real database ────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_user_feedback_is_404_and_owner_can_read_back():
    """User B posting to user A's assistant message -> 404; user A can upsert
    and read the row back. Requires migration 012 applied — skips otherwise.

    Follows the app_dsn_or_skip pattern from test_rls_policies.py rather than
    importing that fixture across files, since pytest fixtures are file-scoped
    unless shared through conftest.py.
    """
    import os
    import uuid as uuid_mod
    from urllib.parse import urlsplit

    import asyncpg

    from langgraph_agents.shared.env import load_env

    load_env()
    dsn = os.getenv("VVA_PG_DSN")
    if not dsn:
        pytest.skip("VVA_PG_DSN not configured")

    role = urlsplit(dsn).username or ""
    if role.endswith("owner") or role == "postgres":
        pytest.fail(
            f"VVA_PG_DSN connects as {role!r}, which bypasses RLS — see "
            "test_rls_policies.py::app_dsn_or_skip for why this must fail, not skip."
        )

    conn = await asyncpg.connect(dsn)
    try:
        has_table = await conn.fetchval("SELECT to_regclass('message_feedback')")
        if has_table is None:
            pytest.skip("migration 012 not applied")

        user_a = uuid_mod.uuid4()
        user_b = uuid_mod.uuid4()
        session_a = uuid_mod.uuid4()
        assistant_msg = uuid_mod.uuid4()
        created_message_ids: list[uuid_mod.UUID] = []
        created_session_ids: list[uuid_mod.UUID] = []
        created_user_ids: list[uuid_mod.UUID] = []

        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)", str(user_a),
                )
                await conn.execute(
                    "INSERT INTO users (id) VALUES ($1::uuid)", user_a,
                )
                created_user_ids.append(user_a)
                await conn.execute(
                    "INSERT INTO conversations (session_id, user_id) VALUES ($1::uuid, $2::uuid)",
                    session_a, user_a,
                )
                created_session_ids.append(session_a)
                await conn.execute(
                    "INSERT INTO messages (id, session_id, role, content) "
                    "VALUES ($1::uuid, $2::uuid, 'assistant', 'demo reply')",
                    assistant_msg, session_a,
                )
                created_message_ids.append(assistant_msg)

            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)", str(user_b),
                )
                await conn.execute(
                    "INSERT INTO users (id) VALUES ($1::uuid)", user_b,
                )
                created_user_ids.append(user_b)

            from langgraph_agents.api.routes_feedback import _UPSERT

            # User B votes on user A's assistant message -> 404 shape (0 rows).
            # $5 is the caller's own uid (user_b), exactly as the route passes
            # its `uid` — both RLS and the explicit c.user_id predicate block
            # this, independently of each other.
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)", str(user_b),
                )
                row = await conn.fetchrow(_UPSERT, assistant_msg, 1, [], None, user_b)
                assert row is None, "user B must not be able to feedback on user A's message"

            # User A upserts and reads it back.
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.user_id', $1, true)", str(user_a),
                )
                row = await conn.fetchrow(
                    _UPSERT, assistant_msg, -1, ["unsafe"], "careful", user_a,
                )
                assert row is not None
                assert row["rating"] == -1
                assert row["reasons"] == ["unsafe"]

                readback = await conn.fetchrow(
                    "SELECT rating, reasons FROM message_feedback WHERE message_id = $1::uuid",
                    assistant_msg,
                )
                assert readback["rating"] == -1
        finally:
            # Clean up as the owning user so RLS lets the deletes through;
            # conversations/messages cascade, message_feedback cascades from
            # messages too.
            for uid, sid in ((user_a, session_a),):
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.user_id', $1, true)", str(uid),
                    )
                    await conn.execute(
                        "DELETE FROM conversations WHERE session_id = $1::uuid", sid,
                    )
            for uid in created_user_ids:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.user_id', $1, true)", str(uid),
                    )
                    await conn.execute("DELETE FROM users WHERE id = $1::uuid", uid)
    finally:
        await conn.close()
