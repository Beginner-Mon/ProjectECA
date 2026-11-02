"""In-process MCP servers for the plugin tests. No socket, no real network.

`build_server()` returns a FastMCP app (streamable HTTP) with tools:

  echo(text)      returns the text, with a hidden zero-width character appended
  count()         how many calls the server saw (shared counter)
  fail()          raises, so the client sees a tool-level error
  boom()          never returns until cancelled (for timeout tests)
  caps()          the capabilities the client declared at initialize
  ask()           asks the client to elicit; reports what happened
  sample()        asks the client for a sampling completion; reports what happened

`httpx.ASGITransport` serves every test that only lists and calls tools. A
server that sends a request to the client in the middle of a tool call
(elicitation, sampling) never gets the answer through it (measured: the call
hangs, with the SDK's own client too). Those tests use `Fake.serving()`, a
uvicorn server on 127.0.0.1 with an ephemeral port: loopback only, no outside
network, and it exercises the `http://localhost` dev flag of `loop/http.py`.
"""

import asyncio
from contextlib import asynccontextmanager

SERVER_URL = "https://mcp.test/mcp"
SERVER_HOST = "mcp.test"
ZERO_WIDTH = "​"


class Fake:
    """A FastMCP server plus its ASGI app. Use `async with fake.running():`."""

    def __init__(self, *, description: str = "Echo the text back.",
                 json_response: bool = False) -> None:
        from mcp.server.fastmcp import Context, FastMCP
        from mcp.server.transport_security import TransportSecuritySettings
        from mcp.types import SamplingMessage, TextContent
        from pydantic import BaseModel

        self.calls = 0
        self.headers_seen: list[dict] = []
        server = FastMCP(
            "fake",
            json_response=json_response,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=False))

        class Name(BaseModel):
            name: str

        @server.tool(description=description)
        async def echo(text: str) -> str:
            self.calls += 1
            return text + ZERO_WIDTH

        @server.tool(description="Always fails.")
        async def fail() -> str:
            raise ValueError("nope")

        @server.tool(description="Never answers.")
        async def boom() -> str:
            await asyncio.sleep(3600)
            return "late"

        @server.tool(description="Asks the client a question.")
        async def ask(ctx: Context) -> str:
            try:
                result = await ctx.elicit("What is your name?", Name)
                return "elicit-answered:" + result.action
            except Exception as exc:  # noqa: BLE001
                return "elicit-refused:" + str(exc)

        @server.tool(description="Asks the client to run a model.")
        async def sample(ctx: Context) -> str:
            try:
                result = await ctx.session.create_message(
                    messages=[SamplingMessage(
                        role="user", content=TextContent(type="text", text="hi"))],
                    max_tokens=5)
                return "sample-answered:" + str(result.content)
            except Exception as exc:  # noqa: BLE001
                return "sample-refused:" + str(exc)

        @server.tool(description="Reports what the client declared it can do.")
        async def caps(ctx: Context) -> str:
            params = ctx.session.client_params
            return params.capabilities.model_dump_json(exclude_none=True)

        self.server = server
        inner = server.streamable_http_app()
        seen = self.headers_seen

        async def app(scope, receive, send):
            if scope["type"] == "http":
                seen.append({k.decode(): v.decode() for k, v in scope["headers"]})
            await inner(scope, receive, send)

        self.app = app

    @asynccontextmanager
    async def running(self):
        async with self.server.session_manager.run():
            yield self

    @asynccontextmanager
    async def serving(self):
        """Serve on 127.0.0.1:<free port>; yields the base URL of the endpoint."""
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=0, log_level="warning"))
        task = asyncio.create_task(server.serve())
        try:
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.02)
            port = server.servers[0].sockets[0].getsockname()[1]
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            server.should_exit = True
            await task
