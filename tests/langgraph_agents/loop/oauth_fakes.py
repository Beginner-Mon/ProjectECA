"""An in-process OAuth authorization server for the connector tests (stage 5).

One ASGI app serves two hosts, because `httpx.ASGITransport` ignores the host:

* `mcp.test`:  `/mcp` answers 401 with `WWW-Authenticate: Bearer resource_metadata=...`
               and `/.well-known/oauth-protected-resource/mcp` (RFC 9728);
* `auth.test`: `/.well-known/oauth-authorization-server` (RFC 8414), `/authorize`,
               `/token`, `/revoke`.

No socket, no real network, no Google. Use `MockAuthServer(...).transport()` as the
`transport=` of the service and `MockAuthServer.browser(url)` for the user's browser
step (the authorize URL is followed here, not through the guarded client).

Behaviour:

* `/authorize` checks client_id, the registered redirect URI, `response_type=code`,
  `code_challenge_method=S256` and `resource`, auto-approves and answers 302 to the
  redirect URI with `code`, `state` and (unless `send_iss=False`) `iss`;
* `/token` handles `authorization_code` (single-use code, redirect URI, PKCE S256,
  `resource`) and `refresh_token` (refresh tokens ROTATE: the old one is dead once a
  new one is issued, like Google with rotation on);
* `expire_refresh_tokens()` makes every refresh token invalid (-> `invalid_grant`);
* `fail_token_endpoint_with = 503` makes the token endpoint answer 5xx;
* `/revoke` records the token and kills it.

Every request body is recorded in `.token_requests` / `.authorize_requests` so a test
can assert `resource` was sent. Tokens it issues contain the marker `MOCK-` so a test
can grep for leaks.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

MCP_HOST = "mcp.test"
AUTH_HOST = "auth.test"
MCP_URL = f"https://{MCP_HOST}/mcp"
ISSUER = f"https://{AUTH_HOST}"
REDIRECT_URI = "https://app.test/connectors/callback"
CLIENT_ID = "eca-client"
CLIENT_SECRET = "MOCK-client-secret-value"


class MockAuthServer:
    def __init__(self, *, issuer: str = ISSUER, advertise_iss: bool = True,
                 send_iss: bool = True, iss_value: Optional[str] = None,
                 client_id: str = CLIENT_ID, client_secret: Optional[str] = CLIENT_SECRET,
                 auth_methods: Optional[list[str]] = None, expires_in: int = 3600,
                 refresh_delay_s: float = 0.0, with_revocation: bool = True,
                 rotate: bool = True, metadata_issuer: Optional[str] = None,
                 pkce_methods: Optional[list[str]] = None) -> None:
        self.issuer = issuer
        self.advertise_iss = advertise_iss
        self.send_iss = send_iss
        self.iss_value = iss_value          # what /authorize puts in `iss`
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_methods = auth_methods if auth_methods is not None else [
            "client_secret_basic", "client_secret_post"]
        self.expires_in = expires_in
        self.refresh_delay_s = refresh_delay_s
        self.with_revocation = with_revocation
        self.rotate = rotate
        self.metadata_issuer = metadata_issuer      # a lying metadata document
        self.pkce_methods = pkce_methods if pkce_methods is not None else ["S256"]
        self.fail_token_endpoint_with: Optional[int] = None

        self.codes: dict[str, dict] = {}
        self.access_tokens: dict[str, str] = {}      # token -> refresh token it came with
        self.refresh_tokens: dict[str, bool] = {}    # token -> alive
        self.revoked: list[str] = []
        self.token_requests: list[dict] = []
        self.authorize_requests: list[dict] = []
        self.refresh_calls = 0
        self._n = 0
        self.app = self._build()

    # ── helpers for tests ─────────────────────────────────────────────────

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)

    async def browser(self, authorize_url: str) -> dict[str, str]:
        """Play the user: open the authorize URL, return the callback query params."""
        # The user's browser is outside our process: its request log (httpx logs the
        # full URL, state included) must not land in the caplog the leak tests read.
        import logging
        httpx_log = logging.getLogger("httpx")
        was_disabled, httpx_log.disabled = httpx_log.disabled, True
        try:
            async with httpx.AsyncClient(transport=self.transport()) as client:
                response = await client.get(authorize_url, follow_redirects=False)
        finally:
            httpx_log.disabled = was_disabled
        assert response.status_code == 302, response.text
        query = parse_qs(urlparse(response.headers["location"]).query)
        return {k: v[0] for k, v in query.items()}

    def expire_refresh_tokens(self) -> None:
        for token in self.refresh_tokens:
            self.refresh_tokens[token] = False

    def last_refresh_token(self) -> Optional[str]:
        alive = [t for t, ok in self.refresh_tokens.items() if ok]
        return alive[-1] if alive else None

    def all_issued_secrets(self) -> list[str]:
        out = list(self.access_tokens) + list(self.refresh_tokens) + list(self.codes)
        if self.client_secret:
            out.append(self.client_secret)
        return out

    def _next(self, kind: str) -> str:
        self._n += 1
        return f"MOCK-{kind}-{self._n}-{'x' * 12}"

    # ── the app ───────────────────────────────────────────────────────────

    def _build(self):
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        server = self

        async def mcp_endpoint(request: Request) -> Response:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": (
                    'Bearer resource_metadata="https://%s/.well-known/'
                    'oauth-protected-resource/mcp"' % MCP_HOST)})

        async def prm(request: Request) -> Response:
            return JSONResponse({"resource": MCP_URL,
                                 "authorization_servers": [server.issuer]})

        async def metadata(request: Request) -> Response:
            doc = {
                "issuer": server.metadata_issuer or server.issuer,
                "authorization_endpoint": f"{server.issuer}/authorize",
                "token_endpoint": f"{server.issuer}/token",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": server.pkce_methods,
                "token_endpoint_auth_methods_supported": server.auth_methods,
                "authorization_response_iss_parameter_supported": server.advertise_iss,
            }
            if server.with_revocation:
                doc["revocation_endpoint"] = f"{server.issuer}/revoke"
            return JSONResponse(doc)

        async def authorize(request: Request) -> Response:
            q = dict(request.query_params)
            server.authorize_requests.append(q)
            if q.get("client_id") != server.client_id:
                return JSONResponse({"error": "unauthorized_client"}, status_code=400)
            if q.get("redirect_uri") != REDIRECT_URI:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if q.get("response_type") != "code" or q.get("code_challenge_method") != "S256":
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if not q.get("code_challenge") or not q.get("state"):
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            if q.get("resource") != MCP_URL:
                return JSONResponse({"error": "invalid_target"}, status_code=400)
            code = server._next("code")
            server.codes[code] = {"challenge": q["code_challenge"], "used": False,
                                  "scope": q.get("scope", ""), "resource": q["resource"]}
            params = {"code": code, "state": q["state"]}
            if server.send_iss:
                params["iss"] = server.iss_value or server.issuer
            return Response(status_code=302, headers={
                "location": f"{REDIRECT_URI}?{urlencode(params)}"})

        def client_ok(form: dict, headers) -> bool:
            if server.client_secret is None:
                return form.get("client_id") == server.client_id
            basic = headers.get("authorization", "")
            if basic.lower().startswith("basic "):
                try:
                    raw = base64.b64decode(basic[6:]).decode()
                except Exception:  # noqa: BLE001
                    return False
                cid, _, sec = raw.partition(":")
                from urllib.parse import unquote
                return unquote(cid) == server.client_id and unquote(sec) == server.client_secret
            return (form.get("client_id") == server.client_id
                    and form.get("client_secret") == server.client_secret)

        async def token(request: Request) -> Response:
            form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
            server.token_requests.append(form)
            if server.fail_token_endpoint_with:
                return JSONResponse({"error": "temporarily_unavailable"},
                                    status_code=server.fail_token_endpoint_with)
            if not client_ok(form, request.headers):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
            grant = form.get("grant_type")
            if grant == "authorization_code":
                rec = server.codes.get(form.get("code", ""))
                if rec is None or rec["used"]:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                rec["used"] = True
                if form.get("redirect_uri") != REDIRECT_URI:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                verifier = form.get("code_verifier", "")
                digest = base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if digest != rec["challenge"]:
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if form.get("resource") != rec["resource"]:
                    return JSONResponse({"error": "invalid_target"}, status_code=400)
                scope = rec["scope"]
            elif grant == "refresh_token":
                server.refresh_calls += 1
                if server.refresh_delay_s:
                    await asyncio.sleep(server.refresh_delay_s)
                old = form.get("refresh_token", "")
                if not server.refresh_tokens.get(old):
                    return JSONResponse({"error": "invalid_grant"}, status_code=400)
                if form.get("resource") != MCP_URL:
                    return JSONResponse({"error": "invalid_target"}, status_code=400)
                scope = ""
                if server.rotate:
                    server.refresh_tokens[old] = False
            else:
                return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            access = server._next("access")
            body = {"access_token": access, "token_type": "Bearer",
                    "expires_in": server.expires_in}
            if scope:
                body["scope"] = scope
            if grant == "authorization_code" or server.rotate:
                refresh = server._next("refresh")
                server.refresh_tokens[refresh] = True
                body["refresh_token"] = refresh
            server.access_tokens[access] = body.get("refresh_token", "")
            return JSONResponse(body)

        async def revoke(request: Request) -> Response:
            form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
            tok = form.get("token", "")
            server.revoked.append(tok)
            if tok in server.refresh_tokens:
                server.refresh_tokens[tok] = False
            return Response(status_code=200)

        return Starlette(routes=[
            Route("/mcp", mcp_endpoint, methods=["GET", "POST"]),
            Route("/.well-known/oauth-protected-resource/mcp", prm),
            Route("/.well-known/oauth-authorization-server", metadata),
            Route("/authorize", authorize),
            Route("/token", token, methods=["POST"]),
            Route("/revoke", revoke, methods=["POST"]),
        ])


def manifest_dict(**over) -> dict:
    """A connector manifest for the mock server (plan §8 shape)."""
    base = {
        "id": "mock-calendar", "version": "1.0.0", "publisher": "Test Inc.",
        "name": "Mock calendar",
        "mcp_server": {"url": MCP_URL, "transport": "streamable_http",
                       "allowed_hosts": [MCP_HOST, AUTH_HOST]},
        "auth": {"type": "mcp_oauth", "client_registration": "pre_registered",
                 "client_id": CLIENT_ID,
                 "client_secret_ref": "secretsmanager:vva/<env>/plugins/mock-calendar",
                 "scopes": ["calendar.read", "calendar.events"],
                 "authorize_extra_params": {"access_type": "offline", "prompt": "consent"}},
        "tools": {"list_events": {"egress": True, "risk": "read"}},
        "data_class": "personal",
    }
    base.update(over)
    return base


async def fake_secret_fetcher(name: str) -> str:
    assert name == "vva/test/plugins/mock-calendar", name
    return json.dumps({"client_secret": CLIENT_SECRET})


# ── An OAuth-protected MCP server beside the mock authorization server (5B) ─────────────


class ConnectorWorld:
    """`MockAuthServer` plus a bearer-checking MCP server on one ASGI app.

    `world.transport()` is the `transport=` of both the OAuth service and the plugin
    runtime (`set_client_overrides`). `/mcp` is a real FastMCP streamable-HTTP app. It
    answers 401 with the RFC 9728 `WWW-Authenticate` header unless the request carries
    `Authorization: Bearer <a token the auth server issued and has not revoked>`.
    Everything else is the mock authorization server.

    Tools (the owner of a call is the label the test gave the token with
    `label_new_tokens`; the server never echoes a token):

        list_events(day)   -> that owner's events for the day
        read_inbox()       -> `inbox_text` (default: the P9 email of GD0 with its injected line)

    What it records (no token text, only counts and hashes):

        list_bearers   sha256 of the bearer of every `tools/list`
        call_bearers   sha256 of the bearer of every `tools/call`
        unauthorised   how many requests were answered 401
        calls          (owner label, tool name) per executed tool call
    """

    def __init__(self, auth: Optional[MockAuthServer] = None, *,
                 inbox_text: Optional[str] = None) -> None:
        from mcp.server.fastmcp import Context, FastMCP
        from mcp.server.transport_security import TransportSecuritySettings

        if inbox_text is None:
            from langgraph_agents.local_tests.agent_loop.probes import P9_INBOX_TEXT
            inbox_text = P9_INBOX_TEXT
        self.auth = auth or MockAuthServer()
        self.inbox_text = inbox_text
        self.owner: dict[str, str] = {}
        self.events: dict[str, str] = {}
        self.list_bearers: list[str] = []
        self.call_bearers: list[str] = []
        self.unauthorised = 0
        self.calls: list[tuple[str, str]] = []
        self.rejected: set[str] = set()
        self._labelled: set[str] = set()

        server = FastMCP("connector-world", transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False))
        world = self

        def owner_of(ctx: Context) -> str:
            request = ctx.request_context.request
            raw = request.headers.get("authorization", "") if request is not None else ""
            return world.owner.get(raw[7:], "?")

        async def list_events(day: str, ctx: Context) -> str:
            who = owner_of(ctx)
            world.calls.append((who, "list_events"))
            return world.events.get(who, f"no events on {day}")

        async def read_inbox(ctx: Context) -> str:
            world.calls.append((owner_of(ctx), "read_inbox"))
            return world.inbox_text

        # `from __future__ import annotations` froze these annotations as strings,
        # and both FastMCP passes (`find_context_parameter` -> get_type_hints and
        # `func_metadata` -> inspect.signature(eval_str=True)) re-evaluate strings
        # against the module's globals only — the closure variables of __init__ are
        # invisible there, so a bare `Context` raises NameError at registration.
        # Bind the real objects (non-string annotations are not re-evaluated) first.
        list_events.__annotations__ = {"day": str, "ctx": Context, "return": str}
        read_inbox.__annotations__ = {"ctx": Context, "return": str}

        server.add_tool(
            list_events, description="List the user's calendar events for a day (YYYY-MM-DD).")
        server.add_tool(read_inbox, description="Read the user's inbox.")

        self.server = server
        # Dispatched to by `app` below; replaced on every `running()` entry.
        self._mcp_asgi = server.streamable_http_app()
        mock_app = self.auth.app

        def www_authenticate() -> bytes:
            return ('Bearer resource_metadata="https://%s/.well-known/'
                    'oauth-protected-resource/mcp"' % MCP_HOST).encode()

        async def app(scope, receive, send):
            if scope["type"] != "http":
                return await mock_app(scope, receive, send)
            path = scope["path"]
            if path != "/mcp" and not path.startswith("/mcp/"):
                return await mock_app(scope, receive, send)
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            auth_header = headers.get("authorization", "")
            token = auth_header[7:] if auth_header.lower().startswith("bearer ") else ""
            if not token or token not in world.auth.access_tokens or token in world.rejected:
                world.unauthorised += 1
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"www-authenticate", www_authenticate()),
                    (b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
            digest = hashlib.sha256(token.encode()).hexdigest()
            if scope["method"] == "POST":
                body = b""
                while True:
                    message = await receive()
                    body += message.get("body", b"")
                    if not message.get("more_body"):
                        break
                try:
                    method = json.loads(body or b"{}").get("method")
                except ValueError:
                    method = None
                if method == "tools/list":
                    world.list_bearers.append(digest)
                elif method == "tools/call":
                    world.call_bearers.append(digest)
                sent = False

                async def replay():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await receive()

                return await world._mcp_asgi(scope, replay, send)
            return await world._mcp_asgi(scope, receive, send)

        self.app = app

    # ── helpers for tests ─────────────────────────────────────────────────

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)

    def running(self):
        """`async with world.running():` starts the MCP session manager.

        FastMCP memoizes one session manager per server, and a manager can be
        run only once, but tests enter and exit this block many times on one
        world. So every entry builds a fresh manager with FastMCP's own
        construction and points `/mcp` at a bare ASGI handler around it. The
        app built in `__init__` stays as the dispatch target until the first
        entry (requests before `running()` hit an un-started manager).
        """
        from mcp.server.fastmcp.server import StreamableHTTPASGIApp
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        server = self.server
        manager = StreamableHTTPSessionManager(
            app=server._mcp_server,
            event_store=getattr(server, "_event_store", None),
            retry_interval=getattr(server, "_retry_interval", None),
            json_response=server.settings.json_response,
            stateless=server.settings.stateless_http,
            security_settings=server.settings.transport_security,
            max_request_body_size=server.settings.max_request_body_size,
        )
        self._mcp_asgi = StreamableHTTPASGIApp(manager)
        return manager.run()

    def label_new_tokens(self, label: str) -> None:
        """Name the owner of every access token issued since the last call."""
        for token in self.auth.access_tokens:
            if token not in self._labelled:
                self._labelled.add(token)
                self.owner[token] = label

    def reject(self, token: str) -> None:
        """Make the MCP server refuse this access token (revoked on the server side)."""
        self.rejected.add(token)

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
