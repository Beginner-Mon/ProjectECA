"""The dev-only fake write server behind `catalog/dev-reminders.json` (E9).

A FastMCP server with a write tool, `create_reminder(title, when, request_id)`,
and a read tool, `list_reminders()`. It does not touch anything outside the
process: a "write" appends to `DevReminders.writes` (a read bumps
`DevReminders.reads`), so a test (or a person) can see exactly what ran.

Modes (`DevReminders.mode`, or env `LOOP_DEV_REMINDERS_MODE` for the CLI):

  ok                    the write is recorded and the call returns
  timeout_after_write   the write is recorded, then the call never returns
                        (the client times out after the side effect)

Run it for local dev:

    LOOP_DEV_FAKE_WRITE=1 python -m langgraph_agents.loop.plugins.dev_reminders

then set `LOOP_DEV_REMINDERS_MCP_URL=http://127.0.0.1:8765/mcp`. The loop only
loads the manifest when `LOOP_DEV_FAKE_WRITE=1` and off Lambda.
"""

from __future__ import annotations

import asyncio
import os


class DevReminders:
    """The fake reminders backend and its FastMCP app."""

    def __init__(self, mode: str = "ok") -> None:
        from mcp.server.fastmcp import FastMCP
        from mcp.server.transport_security import TransportSecuritySettings

        self.mode = mode
        self.writes: list[dict] = []
        self.reads = 0
        server = FastMCP(
            "dev-reminders",
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False))

        @server.tool(description="Create a reminder for the user.")
        async def create_reminder(title: str, when: str, request_id: str = "") -> str:
            self.writes.append({"title": title, "when": when, "request_id": request_id})
            if self.mode == "timeout_after_write":
                await asyncio.sleep(3600)
            return f"reminder created: {title} at {when}"

        @server.tool(description="List the user's reminders.")
        async def list_reminders() -> str:
            self.reads += 1
            return "no reminders yet"

        self.server = server
        self.app = server.streamable_http_app()


def main() -> None:  # pragma: no cover - manual dev entry point
    import uvicorn

    if os.environ.get("LOOP_DEV_FAKE_WRITE", "").strip() != "1":
        raise SystemExit("set LOOP_DEV_FAKE_WRITE=1 to run the dev reminders server")
    fake = DevReminders(os.environ.get("LOOP_DEV_REMINDERS_MODE", "ok"))
    uvicorn.run(fake.app, host="127.0.0.1", port=int(os.environ.get("PORT", "8765")))


if __name__ == "__main__":  # pragma: no cover
    main()
