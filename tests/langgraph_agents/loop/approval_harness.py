"""Shared harness for the stage-4 approval tests (`test_actions_p0.py`).

Everything here is the production wiring except the things a test must fake:

  * the model: `FakeAgentModel` behind `AgentModel.complete` (no LLM call);
  * the guardrail triage: a stub for `guardrail.run_input` (no LLM call);
  * the dev reminders server: the real `DevReminders` FastMCP app served on a
    loopback port by uvicorn in a thread, reached through the real plugin
    runtime and MCP client (the `http://127.0.0.1` dev endpoint of
    `loop/http.py`);
  * the stores: memory action, turn, lease, rate and plugin stores;
  * the sealer: the real `LocalSealer`, built by `crypto.build_sealer` from
    `LOOP_DEV_SEAL_KEY`, as in local dev.

Requests go through FastAPI's TestClient to `/chat` and `/chat/resume`.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from types import SimpleNamespace

from tests.langgraph_agents.loop.fakes import AgentReply, FakeAgentModel

USER = "11111111-1111-1111-1111-0000000000aa"
OTHER = "11111111-1111-1111-1111-0000000000bb"
SESSION = "22222222-2222-2222-2222-000000000001"
SEAL_KEY = base64.b64encode(bytes(range(32))).decode()
CREATE = "dev-reminders__create_reminder"
LIST = "dev-reminders__list_reminders"


def call(call_id: str, name: str, **args) -> dict:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


def reply(content: str = "", *calls: dict) -> AgentReply:
    return AgentReply(content=content, tool_calls=list(calls))


def sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        if not block.strip():
            continue
        etype, data = None, "{}"
        for line in block.splitlines():
            if line.startswith("event:"):
                etype = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if etype:
            events.append((etype, json.loads(data) if data else {}))
    return events


def types(events) -> list[str]:
    return [t for t, _ in events]


def first(events, etype):
    return next((d for t, d in events if t == etype), None)


def body_text(events) -> str:
    return "".join(d.get("content", "") for t, d in events if t == "token")


def tool_contents(messages) -> list[str]:
    """The text of every tool message in what the agent was sent (the graph
    hands the model LangChain messages, tests may pass dicts)."""
    out = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", None)
        if role == "tool":
            out.append((m.get("content") if isinstance(m, dict) else m.content) or "")
    return out


def serve_reminders():
    """Start the dev reminders server on 127.0.0.1:<free port> in a thread.
    Returns `(fake, url, stop)`."""
    import uvicorn
    from langgraph_agents.loop.plugins.dev_reminders import DevReminders

    fake = DevReminders()
    server = uvicorn.Server(uvicorn.Config(
        fake.app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("dev reminders server did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    def stop():
        server.should_exit = True
        thread.join(5)

    return fake, f"http://127.0.0.1:{port}/mcp", stop


class Model:
    """`FakeAgentModel` installed behind `AgentModel.complete`; the agent and
    the persona both call it, in the order the turn runs."""

    def __init__(self, monkeypatch) -> None:
        from langgraph_agents.loop.client import AgentModel

        self.fake = FakeAgentModel([])
        fake = self.fake

        async def _complete(self_, messages, tools=None, *, tool_choice="auto"):
            return await fake.complete(messages, tools, tool_choice=tool_choice)

        monkeypatch.setattr(AgentModel, "complete", _complete)

    def script(self, *replies: AgentReply) -> None:
        self.fake.script.extend(replies)

    @property
    def calls(self) -> list[dict]:
        return self.fake.calls

    @property
    def left(self) -> int:
        return len(self.fake.script)


class Recorder:
    """What the turn handed to the session store (the real writers are off)."""

    def __init__(self) -> None:
        self.questions: list[dict] = []
        self.answers: list[dict] = []
        self.updates: list[dict] = []
        self.housekeeping: list[tuple] = []


def verdict_box(monkeypatch):
    """Stub `guardrail.run_input`; `box.result` is what every turn gets."""
    import langgraph_agents.api.main as api_main
    from langgraph_agents.guardrail.types import InputResult, fallback_verdict

    box = SimpleNamespace(result=InputResult(fallback_verdict()), calls=0)

    async def fake_run_input(ctx):
        box.calls += 1
        return box.result

    monkeypatch.setattr(api_main.guardrail, "run_input", fake_run_input)
    return box
