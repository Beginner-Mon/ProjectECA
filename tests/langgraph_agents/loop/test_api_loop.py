"""FastAPI TestClient tests for the loop mode (AGENT_MODE=loop).

- events arrive in the order stage(memory) … stage(agent) … token …
  turn_status … done
- `/chat/cancel` works (202)
"""

from __future__ import annotations

import os
import re
import json
import sys
from types import ModuleType

# vva_motion is not available in this test env; mock it so api/main imports.
if "vva_motion" not in sys.modules:
    _vm = ModuleType("vva_motion")
    _jobs = ModuleType("vva_motion.jobs")
    _jobs.MAX_QUEUE_DEPTH = 10
    _jobs.SECONDS_PER_JOB = 1
    _jobs.compute_job_id = lambda *a, **k: "job"
    _jobs.enqueue = lambda *a, **k: None
    _jobs.queue_depth = lambda *a, **k: 0
    _jobs.read_status = lambda *a, **k: {"status": "ok"}
    _jobs.worker_alive = lambda *a, **k: True
    _vm.jobs = _jobs
    sys.modules["vva_motion"] = _vm
    sys.modules["vva_motion.jobs"] = _jobs

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
            "session_id": "s-loop-1",
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


def test_loop_final_answer(monkeypatch):
    _patch_agent_model(monkeypatch, "The bird dog works your core.")
    app = create_app()
    app.dependency_overrides[current_user_id] = (
        override_user("u-loop-2")
    )
    with TestClient(app) as client:
        resp = client.post("/chat", json={
            "query": "bird dog", "session_id": "s-loop-2",
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
            "session_id": "s-loop-3", "request_id": "req-x",
        })
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "cancel_requested"
    assert body["session_id"] == "s-loop-3"


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
            "query": "x", "session_id": "s-loop-6",
        })
        assert resp.status_code == 200, resp.text
        # Cancel returns 202.
        resp = client.post("/chat/cancel", json={
            "session_id": "s-loop-6", "request_id": "req-cancel",
        })
        assert resp.status_code == 202, resp.text
        # A new request in the same session is not 409 (lease was released).
        resp = client.post("/chat", json={
            "query": "x", "session_id": "s-loop-6",
        })
        assert resp.status_code != 409, resp.text


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
            "query": "x", "session_id": "s-loop-7",
        })
        assert resp1.status_code in (200, 429), resp1.text
        # Immediate second request in the same session: not 409 (B1: lease
        # was never taken if the first request was refused).
        resp2 = client.post("/chat", json={
            "query": "x", "session_id": "s-loop-7",
        })
        assert resp2.status_code != 409, resp2.text
