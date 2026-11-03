"""Thin MCP client for plugins (stage 3), on `mcp` 1.29 streamable HTTP.

Why not `langchain-mcp-adapters`: see PLAN.md §8. This layer:

  * sends every byte through `loop/http.py` (`client_factory`): HTTPS only,
    allow-listed hosts, public addresses only, capped bodies;
  * attaches the token and `X-Request-Id` per call (an `httpx.Auth` object that
    `call_tool` updates), so the caller never builds headers;
  * does NOT support elicitation, sampling or roots. The SDK's `ClientSession`
    answers those server requests with an error when no callback is given
    (`mcp/client/session.py`: `_default_sampling_callback`,
    `_default_elicitation_callback`, `_default_list_roots_callback`, dispatched
    in `_received_request`) and it advertises the capability only when a
    callback is given (`ClientSession.initialize`). So this module passes NO
    callbacks: the server is told the client cannot do these things, and if it
    asks anyway the answer is a JSON-RPC error;
  * hashes the tool definitions (`definitions_sha256`) for comparison with the
    manifest.

Task ownership. The SDK's transport and session are anyio task groups that must
be entered and left by the same task, but the loop's tools node runs calls in
`asyncio.gather` (one task per call). So each client runs its session inside ONE
owner task; calls from other tasks only send on the session's streams, which is
safe. `close()` signals the owner and joins it. A TTL (`session_ttl_s`) closes a
session a crashed turn forgot.

`max_bytes` is 4 MiB per HTTP response. Tool results are cut to 8k characters
anyway, so a larger answer is useless; but a JSON-RPC result for a wiki page can
pass the 2 MiB default of `http.client_factory` once it is wrapped in SSE. The
cap also bounds the long-lived GET stream, and a session lives seconds (turn
cap 60 s, TTL 90 s), so a keep-alive trickle cannot reach it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from langgraph_agents.loop.http import (
    AddressPolicy, DEFAULT_MAX_REDIRECTS, client_factory,
)

MCP_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_SESSION_TTL_S = 90.0
DEFAULT_CALL_TIMEOUT_S = 10.0
OPEN_TIMEOUT_S = 5.0
MCP_HTTP_TIMEOUT = httpx.Timeout(connect=3.0, read=20.0, write=10.0, pool=3.0)
CLIENT_NAME = "eca-agent-loop"


class ToolReturnedError(Exception):
    """The server answered a tool call with `isError: true`. The call did
    return; `args[0]` is the `{"error": "tool_error", ...}` JSON for the agent."""


@dataclass(frozen=True)
class ToolDef:
    """One tool as the server declares it."""

    name: str
    description: str
    input_schema: dict


def _field(tool: Any, *names: str) -> Any:
    for n in names:
        if isinstance(tool, Mapping):
            if n in tool:
                return tool[n]
        elif hasattr(tool, n):
            return getattr(tool, n)
    return None


def definitions_sha256(tools: Iterable[Any]) -> str:
    """sha256 of the canonical JSON of the tool definitions.

    Canonical form: a list of `{"name", "description", "inputSchema"}` objects,
    sorted by name, serialised with sorted keys, compact separators and
    `ensure_ascii=False`, encoded as UTF-8. A missing description counts as "".
    Accepts `ToolDef`, SDK `Tool` objects or plain dicts.
    """
    items = []
    for t in tools:
        schema = _field(t, "input_schema", "inputSchema")
        items.append({
            "name": _field(t, "name") or "",
            "description": _field(t, "description") or "",
            "inputSchema": schema if schema is not None else {},
        })
    items.sort(key=lambda d: d["name"])
    canonical = json.dumps(items, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _unwrap(exc: BaseException) -> BaseException:
    """The one real error inside an anyio task-group `ExceptionGroup`, so that
    `errors.classify` sees `EgressBlocked` and friends, not a wrapper."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


class McpUnauthorized(Exception):
    """The server answered 401 to a request of this session. Fixed text: the
    token that was sent is never put in an error."""

    def __init__(self) -> None:
        super().__init__("the MCP server rejected the connection's credentials")


class _CallAuth(httpx.Auth):
    """Adds the per-call headers to every request of the session.

    The bearer token (stage 5, F6) lives here only while a call that was given
    one is running: `hold` sets it, `release` clears it when the last running
    call ends (`_holds` counts them, because the tools node runs calls in
    parallel on one session). Nothing else keeps it.
    """

    def __init__(self) -> None:
        self.token: str | None = None
        self.request_id: str | None = None
        self.unauthorized = False
        self._holds = 0

    def hold(self, token: str | None) -> None:
        self._holds += 1
        if token:
            self.token = token

    def release(self) -> None:
        self._holds = max(0, self._holds - 1)
        if self._holds == 0:
            self.token = None

    def auth_flow(self, request: httpx.Request):
        if self.request_id:
            request.headers["X-Request-Id"] = self.request_id
        if self.token:
            request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code == 401:
            self.unauthorized = True


def mcp_http_client_factory(
    allowed_hosts: Iterable[str],
    policy: AddressPolicy | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    max_bytes: int = MCP_MAX_BYTES,
):
    """Adapter for the SDK's `httpx_client_factory` slot.

    The SDK calls the factory as `factory(headers=None, timeout=None,
    auth=None)` (`mcp/shared/_httpx_utils.py: McpHttpClientFactory`); this
    returns a callable with that signature that builds the guarded client.
    """
    hosts = list(allowed_hosts)

    def factory(headers: dict[str, str] | None = None,
                timeout: httpx.Timeout | float | None = None,
                auth: httpx.Auth | None = None) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {}
        if headers:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return client_factory(
            hosts, policy=policy, transport=transport, max_bytes=max_bytes,
            max_redirects=DEFAULT_MAX_REDIRECTS,
            timeout=MCP_HTTP_TIMEOUT if timeout is None else timeout, **kwargs)

    return factory


def _result_text(result: Any) -> str:
    """Flatten a `CallToolResult` into text. Non-text blocks become markers."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(getattr(block, "text", "") or "")
        elif kind == "resource":
            res = getattr(block, "resource", None)
            text = getattr(res, "text", None)
            parts.append(text if isinstance(text, str) else "[resource]")
        else:
            parts.append(f"[{kind or 'content'}]")
    text = "\n".join(p for p in parts if p)
    if not text:
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            text = json.dumps(structured, ensure_ascii=False)
    return text


class McpClient:
    """One MCP session to one server, owned by one asyncio task."""

    def __init__(
        self,
        url: str,
        allowed_hosts: Iterable[str],
        *,
        policy: AddressPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int = MCP_MAX_BYTES,
        session_ttl_s: float = DEFAULT_SESSION_TTL_S,
    ) -> None:
        self._url = url
        self._factory = mcp_http_client_factory(
            allowed_hosts, policy, transport, max_bytes=max_bytes)
        self._ttl = session_ttl_s
        self._auth = _CallAuth()
        self._session: Any = None
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error: BaseException | None = None
        self._open_lock = asyncio.Lock()

    # ── lifecycle ─────────────────────────────────────────────────

    @property
    def is_open(self) -> bool:
        return self._session is not None and self._task is not None \
            and not self._task.done()

    async def _run(self) -> None:
        try:
            http_client = self._factory(auth=self._auth)
            async with http_client:
                async with streamable_http_client(
                        self._url, http_client=http_client) as (read, write, _sid):
                    async with ClientSession(
                            read, write,
                            client_info=Implementation(name=CLIENT_NAME, version="1"),
                    ) as session:
                        await session.initialize()
                        self._session = session
                        self._ready.set()
                        try:
                            await asyncio.wait_for(self._stop.wait(), self._ttl)
                        except asyncio.TimeoutError:
                            pass
        except Exception as exc:  # noqa: BLE001 - reported to open()
            self._error = _unwrap(exc)
        finally:
            self._session = None
            self._ready.set()

    async def open(self, timeout: float | None = None,
                   token: str | None = None) -> None:
        """Start the session (idempotent). Raises on failure or timeout.
        `token` is sent on the requests of the handshake and then dropped; a
        server that answers 401 raises `McpUnauthorized`."""
        async with self._open_lock:
            if self.is_open:
                return
            self._error = None
            self._auth.unauthorized = False
            self._ready = asyncio.Event()
            self._stop = asyncio.Event()
            self._auth.hold(token)
            try:
                self._task = asyncio.create_task(self._run())
                try:
                    await asyncio.wait_for(self._ready.wait(), timeout)
                except BaseException:
                    await self._abort()
                    if self._auth.unauthorized:
                        raise McpUnauthorized() from None
                    raise
                if self._session is None:
                    err = self._error
                    await self._abort()
                    if self._auth.unauthorized:
                        raise McpUnauthorized() from None
                    raise err or RuntimeError("mcp session did not start")
            finally:
                self._auth.release()

    async def _abort(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass
        self._session = None

    async def close(self, timeout: float = 2.0) -> None:
        """Close the session from the outside and join the owner task."""
        task, self._task = self._task, None
        self._stop.set()
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        self._session = None

    # ── operations ────────────────────────────────────────────────

    async def list_tools(self, token: str | None = None) -> list[ToolDef]:
        """All tools the server declares (follows pagination, at most 5 pages).
        `token` is the user's bearer for a connector; it is held for this call
        only."""
        self._auth.hold(token)
        try:
            return await self._list_tools(token)
        finally:
            self._auth.release()

    async def _list_tools(self, token: str | None) -> list[ToolDef]:
        if not self.is_open:
            await self.open(token=token)
        self._auth.unauthorized = False
        tools: list[ToolDef] = []
        cursor: str | None = None
        for _ in range(5):
            try:
                page = await self._session.list_tools(cursor=cursor)
            except Exception:
                if self._auth.unauthorized:
                    raise McpUnauthorized() from None
                raise
            for t in page.tools:
                tools.append(ToolDef(
                    name=t.name, description=t.description or "",
                    input_schema=dict(t.inputSchema or {})))
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                break
        return tools

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        token: str | None = None,
        request_id: str | None = None,
        *,
        timeout: float = DEFAULT_CALL_TIMEOUT_S,
        raise_tool_error: bool = False,
    ) -> str:
        """Call a tool; return its text. A tool-level error is returned as
        `{"error": "tool_error", ...}` JSON (an answer, not a transport fault);
        transport faults raise. With `raise_tool_error` the same answer raises
        `ToolReturnedError` instead, so a caller that must tell "the server said
        isError" from "the text contains the word error" (a write) can."""
        self._auth.request_id = request_id
        self._auth.hold(token)
        try:
            if not self.is_open:
                await self.open(timeout=OPEN_TIMEOUT_S, token=token)
            self._auth.unauthorized = False
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(name, args or {}), timeout)
            except Exception:
                if self._auth.unauthorized:
                    raise McpUnauthorized() from None
                raise
        finally:
            self._auth.release()
        text = _result_text(result)
        if getattr(result, "isError", False):
            answer = json.dumps({"error": "tool_error", "message": text[:500]},
                                ensure_ascii=False)
            if raise_tool_error:
                raise ToolReturnedError(answer)
            return answer
        return text
