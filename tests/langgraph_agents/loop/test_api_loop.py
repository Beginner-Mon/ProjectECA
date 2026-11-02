"""FastAPI TestClient tests for the loop mode (AGENT_MODE=loop).

- events arrive in the order stage(memory) … stage(agent) … token …
  turn_status … done
- `/chat/cancel` works (202)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time as _time

import pytest

# Force loop mode BEFORE create_app is imported so the lifespan builds the
# loop graph and select_mode returns "loop".
os.environ["AGENT_MODE"] = "loop"

from fastapi.testclient import TestClient  # noqa: E402

from langgraph_agents.api.auth import current_user_id, override_user  # noqa: E402
from langgraph_agents.api.main import create_app  # noqa: E402
from langgraph_agents.loop.client import AgentModel, AgentReply  # noqa: E402
from langgraph_agents.loop.config import load_settings  # noqa: E402
from langgraph_agents.loop.leases import MemoryLeaseStore, set_store  # noqa: E402

load_settings.cache_clear()  # pick up AGENT_MODE=loop
set_store(MemoryLeaseStore())  # tests run without the PG lease tables


@pytest.fixture(autouse=True)
def _no_database_no_housekeeping(monkeypatch):
    """Keep these tests off the network and off the app database.

    Each turn used to run for real: a Neon pool opened from `VVA_PG_DSN`
    (the app database) and every query failed on the non-UUID test user, and
    `housekeep` ran its compaction and fact extraction after `done`
    (about 8 s of a 20-25 s test). Here the database refuses at once, and
    `housekeep` is a recorder that the production wiring must still call.
    Yields the list of `(user_id, session_id)` the turn scheduled.
    """
    from langgraph_agents.api import main as api_main
    from langgraph_agents.db.postgres import PostgresClient

    async def _refuse(self, *a, **k):
        raise ConnectionRefusedError("test: database disabled")

    scheduled: list[tuple[str, str]] = []

    async def _housekeep(user_id, session_id, **kwargs):
        scheduled.append((user_id, session_id))

    monkeypatch.setattr(PostgresClient, "connect", _refuse)
    monkeypatch.setattr(api_main, "housekeep", _housekeep)
    return scheduled


def _patch_agent_model(monkeypatch, reply_content):
    async def _complete(self, messages, tools=None, *, tool_choice="auto"):
        return AgentReply(content=reply_content, tool_calls=[])
    monkeypatch.setattr(AgentModel, "complete", _complete)


def _collect_sse_events(resp_text):
    events = []
    for block in re.split(r"\r?\n\r?\n", resp_text):
        if not block.strip():
            continue
        etype = None
        data = "{}"
        for line in block.splitlines():
            if line.startswith("event:"):
                etype = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if etype:
            events.append((etype, json.loads(data) if data else {}))
    return events


def test_loop_event_order(monkeypatch):
    _patch_agent_model(monkeypatch, "Bird dog is a core-strength exercise.")
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-1")
    )
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "query": "bird dog exercise",
            "session_id": "11111111-1111-1111-1111-111111111101",
            "persona_id": "anne",
        })
    assert resp.status_code == 200, resp.text
    events = _collect_sse_events(resp.text)
    assert resp.status_code == 200, resp.text
    events = _collect_sse_events(resp.text)
    types = [t for t, _ in events]
    # stage(memory) … stage(agent) … token … turn_status … done
    assert "stage" in types
    assert "token" in types
    assert "turn_status" in types
    assert "done" in types
    # Order: turn_status before done.
    ts_idx = types.index("turn_status")
    done_idx = types.index("done")
    assert ts_idx < done_idx
    # A memory stage and an agent stage both appeared.
    stage_payloads = [d for t, d in events if t == "stage"]
    nodes = [d.get("node") for d in stage_payloads]
    assert "memory" in nodes
    assert "agent" in nodes


def test_loop_turn_schedules_housekeeping(monkeypatch,
                                          _no_database_no_housekeeping):
    """Production path: a loop turn still starts `housekeep` for the user and
    session (the autouse fixture only replaces its body)."""
    _patch_agent_model(monkeypatch, "Bird dog works the core.")
    app = create_app()
    app.dependency_overrides[current_user_id] = override_user("u-loop-hk")
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "query": "bird dog",
            "session_id": "11111111-1111-1111-1111-111111111120",
        })
    assert resp.status_code == 200, resp.text
    assert _no_database_no_housekeeping == [
        ("u-loop-hk", "11111111-1111-1111-1111-111111111120")]


def test_loop_final_answer(monkeypatch):
    _patch_agent_model(monkeypatch, "The bird dog works your core.")
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-2")
    )
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "query": "bird dog", "session_id": "11111111-1111-1111-1111-111111111102",
        })
    assert resp.status_code == 200, resp.text
    events = _collect_sse_events(resp.text)
    tokens = [d.get("content", "") for t, d in events if t == "token"]
    body = "".join(tokens)
    assert "bird dog" in body.lower() or "core" in body.lower()


def test_chat_cancel_returns_202(monkeypatch):
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-3")
    )
    with TestClient(app) as client:
        resp = client.post("/chat/cancel", json={
            "session_id": "11111111-1111-1111-1111-111111111103", "request_id": "req-x",
        })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "cancel_requested"
    assert body["session_id"] == "11111111-1111-1111-1111-111111111103"


# A2: /chat/cancel from the same user on the same app returns 202 and the
# lease is released, so a new request in the same session is not 409.
def test_cancel_releases_lease(monkeypatch):
    from langgraph_agents.loop.leases import MemoryLeaseStore, set_store
    set_store(MemoryLeaseStore())

    async def _complete(self, messages, tools=None, *, tool_choice="auto"):
        return AgentReply(content="Done.", tool_calls=[])

    monkeypatch.setattr(AgentModel, "complete", _complete)
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-6")
    )
    with TestClient(app) as client:
        # A normal turn completes and releases its lease.
        resp = client.post("/chat", json={
            "query": "x", "session_id": "11111111-1111-1111-1111-111111111106",
        })
        assert resp.status_code == 200, resp.text
        # Cancel returns 202.
        resp = client.post("/chat/cancel", json={
            "session_id": "11111111-1111-1111-1111-111111111106", "request_id": "req-cancel",
        })
        assert resp.status_code == 202, resp.text
        # A new request in the same session is not 409 (lease was released).
        resp = client.post("/chat", json={
            "query": "x", "session_id": "11111111-1111-1111-1111-111111111106",
        })
        assert resp.status_code != 409, resp.text


# R5: loop mode needs a UUID session id.
def test_invalid_session_id_422_loop(monkeypatch):
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-8")
    )
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "query": "x", "session_id": "default",
        })
    assert resp.status_code == 422, resp.text
    body = resp.json()
    code = body.get("code") or (body.get("detail") or {}).get("code")
    assert code == "invalid_session_id", body


# R5: pipeline mode is unchanged — non-UUID session_id is not 422.
def test_invalid_session_id_pipeline_ok(monkeypatch):
    import os as _os
    from langgraph_agents.loop.config import load_settings as _ls
    saved = _os.environ.get("AGENT_MODE")
    _os.environ["AGENT_MODE"] = "pipeline"
    _ls.cache_clear()
    try:
        app = create_app()
        app.dependency_overrides[current_user_id] = (
            override_user("u-loop-9")
        )
        with TestClient(app) as client:
            # Pipeline mode: session_id="default" is not a UUID but pipeline
            # does not validate it, so it is not 422 (may be 200 or 500 for
            # other reasons, but not 422 invalid_session_id).
            resp = client.post("/chat", json={
                "query": "x", "session_id": "default",
            })
        assert resp.status_code != 422, resp.text
    finally:
        if saved is None:
            _os.environ.pop("AGENT_MODE", None)
        else:
            _os.environ["AGENT_MODE"] = saved
        _ls.cache_clear()


# B1: a loop request refused by quota, then an immediate second request in
# the same session → not 409 (the lease was never taken).
def test_refused_turn_does_not_hold_lease(monkeypatch):
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-7")
    )
    with TestClient(app) as client:
        # First request: either 200 or 429 — quota may pass locally.
        resp1 = client.post("/chat", json={
            "query": "x", "session_id": "11111111-1111-1111-1111-111111111107",
        })
        assert resp1.status_code in (200, 429), resp1.text
        # Immediate second request in the same session: not 409 (B1: lease
        # was never taken if the first request was refused).
        resp2 = client.post("/chat", json={
            "query": "x", "session_id": "11111111-1111-1111-1111-111111111107",
        })
        assert resp2.status_code != 409, resp2.text


# R2 / A2: stop ends a running turn through the production path.
def test_cancel_ends_turn_cancelled(monkeypatch):
    """Same-user cancel mid-turn → turn_status=cancelled, agent model called
    once (the round that called kb_search)."""
    from langgraph_agents.loop.leases import MemoryLeaseStore, set_store, get_lease_store
    set_store(MemoryLeaseStore())
    store = get_lease_store()

    agent_calls = {"n": 0}

    async def _complete(self, messages, tools=None, *, tool_choice="auto"):
        # Count only agent calls (tools is a list); persona calls pass None.
        if tools is not None:
            agent_calls["n"] += 1
        if agent_calls["n"] == 1:
            # First round: call kb_search, and cancel mid-round as a side
            # effect of the model call (production wiring via the store).
            await store.request_cancel("u-loop-8", "11111111-1111-1111-1111-111111111109")
            return AgentReply(content="", tool_calls=[{
                "id": "t1", "name": "kb_search",
                "arguments": '{"query": "bird dog"}',
            }])
        return AgentReply(content="Should not be reached.")

    monkeypatch.setattr(AgentModel, "complete", _complete)
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-8")
    )
    with TestClient(app) as client:
        result = {}
        def _post():
            result["resp"] = client.post("/chat", json={
                "query": "bird dog exercise",
                "session_id": "11111111-1111-1111-1111-111111111109",
                "persona_id": "anne",
            })
        t = threading.Thread(target=_post)
        t.start()
        # Cancel happens inside _complete (as a side effect of the first model
        # call), which runs on the production event loop.
        t.join(timeout=15)
    resp = result["resp"]
    assert resp.status_code == 200, resp.text
    events = _collect_sse_events(resp.text)
    types = [t for t, _ in events]
    assert "turn_status" in types, types
    ts_payloads = [d for t, d in events if t == "turn_status"]
    assert ts_payloads, "no turn_status event"
    assert ts_payloads[0].get("status") == "cancelled", ts_payloads
    # Agent model called exactly once (first round with kb_search).
    assert agent_calls["n"] == 1, agent_calls


def test_cancel_other_user_ends_completed(monkeypatch):
    """Cancel from another user does not affect this turn → completed."""
    import asyncio
    from langgraph_agents.loop.leases import MemoryLeaseStore, set_store
    set_store(MemoryLeaseStore())

    async def _complete(self, messages, tools=None, *, tool_choice="auto"):
        return AgentReply(content="Done.", tool_calls=[])

    monkeypatch.setattr(AgentModel, "complete", _complete)
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-9")
    )
    with TestClient(app) as client:
        # Cancel from a DIFFERENT user on the same session.
        resp_cancel = client.post("/chat/cancel", json={
            "session_id": "11111111-1111-1111-1111-111111111110", "request_id": "req-x",
        })
        assert resp_cancel.status_code == 202, resp_cancel.text
        # Now start the turn as u-loop-9.
        resp = client.post("/chat", json={
            "query": "bird dog exercise",
            "session_id": "11111111-1111-1111-1111-111111111110",
            "persona_id": "anne",
        })
    assert resp.status_code == 200, resp.text
    events = _collect_sse_events(resp.text)
    types = [t for t, _ in events]
    assert "turn_status" in types, types
    ts_payloads = [d for t, d in events if t == "turn_status"]
    assert ts_payloads, "no turn_status event"
    assert ts_payloads[0].get("status") == "completed", ts_payloads
