"""OAuth for connectors (stage 5, plan §8, decisions F1 to F9).

Two endpoints, because a Lambda cannot wait for the browser inside one request:

    start(user_id, plugin_id)            -> authorize_url      POST /connectors/{id}/start
    complete(user_id, code, state, iss)  -> ConnectionInfo     POST /connectors/callback
    access_token(user_id, plugin_id)     -> str                at tool-call time (refreshes)
    refresh(user_id, plugin_id)          -> Connection         under the per-connection lock
    revoke(user_id, plugin_id)           -> bool

The `mcp` SDK is used for discovery (the WWW-Authenticate parser, the RFC 9728
and RFC 8414 URL builders and response handlers) and for the metadata and token
models. The token requests are built here: the SDK builds them inside
`OAuthClientProvider`, whose flow waits for the callback in the same process.
Every HTTP request goes through `http.client_factory` with the manifest's
`allowed_hosts`.

What is checked, and where it fails closed (`OAuthError.code`):

* start:    plugin is an OAuth connector and not gated off; redirect URI set;
            sealer present; discovered issuer equals the one in the PRM (or the
            manifest); S256 advertised; endpoints https and on `allowed_hosts`.
* callback: state is single use, unexpired and bound to the user (`PendingStore.consume`);
            discovered issuer equals the issuer pinned at start; `iss` present when the
            server advertises it, and equal to the issuer whenever it is sent (RFC 9207);
            PKCE verifier opened with its context; token request carries `resource`.
* refresh:  under the lock; re-read after taking it; 4xx -> `auth_expired` (tokens
            dropped); 5xx or network -> `transient` (nothing changes).

Secrets: access and refresh tokens, the verifier, the code, the state and the client
secret never go to a log, an exception message or a return value meant for the client.
Errors carry a fixed code and a fixed message; pydantic and httpx errors are not
chained (`from None`) because their text can echo the input.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote, urlparse

import httpx

from .. import crypto
from ..config import LoopSettings, load_settings
from ..http import AddressPolicy, EgressBlocked, client_factory
from ..plugins.manifest import Manifest
from .store import (
    ACTIVE, AUTH_EXPIRED, Connection, ConnectionStore, Pending, PendingStore,
    connection_context, get_connection_store, get_pending_store, pending_context,
)

logger = logging.getLogger(__name__)

STATE_TTL_S = 600
REFRESH_SKEW_S = 60
DEFAULT_EXPIRES_IN_S = 3600
DISCOVERY_TTL_S = 300
_MAX_FIELD = 2048

_ERRORS = {
    "unknown_plugin": "unknown connector",
    "not_available": "this connector is not available",
    "unsupported_registration": "this connector uses an unsupported client registration",
    "not_configured": "the connector is not configured on this deployment",
    "sealer_unavailable": "token storage is not available",
    "discovery_failed": "could not discover the authorization server",
    "egress_blocked": "a request to the authorization server was blocked by policy",
    "invalid_state": "the authorization request is invalid or has expired",
    "issuer_mismatch": "the authorization response came from an unexpected issuer",
    "iss_missing": "the authorization response did not name its issuer",
    "token_exchange_failed": "the authorization server refused the request",
    "bad_token_response": "the authorization server sent an unusable token response",
    "transient": "the authorization server could not be reached, try again",
    "auth_expired": "the connection has expired, reconnect it",
    "not_connected": "not connected",
    "token_unreadable": "the stored token cannot be read, reconnect",
}


class OAuthError(Exception):
    """A flow failure. `code` is one of `_ERRORS`; the message is fixed text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(_ERRORS.get(code, code))


class NotConnected(OAuthError):
    def __init__(self) -> None:
        super().__init__("not_connected")


class AuthExpired(OAuthError):
    def __init__(self) -> None:
        super().__init__("auth_expired")


@dataclass(frozen=True)
class ConnectionInfo:
    """What may leave the service: no token, no ciphertext."""

    plugin_id: str
    status: str
    scopes: tuple[str, ...]
    expires_at: Optional[datetime]


def _info(c: Connection) -> ConnectionInfo:
    return ConnectionInfo(c.plugin_id, c.status, tuple(c.scopes), c.expires_at)


@dataclass(frozen=True)
class ServerMeta:
    """Verified authorization-server metadata for one connector."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: Optional[str]
    iss_supported: bool
    auth_methods: Optional[tuple[str, ...]]
    resource: str


def _norm_issuer(value: str) -> str:
    return value.strip().rstrip("/")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _state_sha256(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


SecretFetcher = Callable[[str], Awaitable[str]]


async def _fetch_secretsmanager(secret_id: str) -> str:
    def _get() -> str:
        import boto3  # lazy: only the deployed path
        value = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
        return value["SecretString"]
    return await asyncio.to_thread(_get)


class OAuthService:
    def __init__(
        self,
        *,
        connections: Optional[ConnectionStore] = None,
        pending: Optional[PendingStore] = None,
        settings: Optional[LoopSettings] = None,
        sealer: Optional[crypto.Sealer] = None,
        catalog: Optional[Callable[[str], Optional[Manifest]]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        policy: Optional[AddressPolicy] = None,
        secret_fetcher: Optional[SecretFetcher] = None,
        clock: Optional[Callable[[], datetime]] = None,
        discovery_ttl_s: float = DISCOVERY_TTL_S,
    ) -> None:
        self._connections = connections
        self._pending = pending
        self._settings = settings
        self._sealer = sealer
        self._catalog = catalog
        self._transport = transport
        self._policy = policy
        self._secret_fetcher = secret_fetcher or _fetch_secretsmanager
        self._clock = clock or _utcnow
        self._ttl = discovery_ttl_s
        self._meta_cache: dict[tuple, tuple[float, ServerMeta]] = {}

    # ── collaborators, resolved at call time ──────────────────────────────

    @property
    def connections(self) -> ConnectionStore:
        return self._connections or get_connection_store()

    @property
    def pending(self) -> PendingStore:
        return self._pending or get_pending_store()

    @property
    def settings(self) -> LoopSettings:
        return self._settings or load_settings()

    def _seal_with(self) -> crypto.Sealer:
        if self._sealer is not None:
            return self._sealer
        try:
            return crypto.require_sealer()
        except crypto.SealerUnavailable:
            raise OAuthError("sealer_unavailable") from None

    def _manifest(self, plugin_id: str) -> Manifest:
        if self._catalog is not None:
            manifest = self._catalog(plugin_id)
        else:
            from ..plugins.manifest import get_manifest
            manifest = get_manifest(plugin_id)
        if manifest is None:
            raise OAuthError("unknown_plugin")
        auth = manifest.auth or {}
        if auth.get("type") != "mcp_oauth":
            raise OAuthError("not_available")
        if manifest.requires_gate and not self.settings.google_enabled:
            raise OAuthError("not_available")
        if auth.get("client_registration", "pre_registered") != "pre_registered":
            raise OAuthError("unsupported_registration")
        if not auth.get("client_id") or not manifest.resolved_url():
            raise OAuthError("not_configured")
        return manifest

    def _http(self, manifest: Manifest) -> httpx.AsyncClient:
        policy = self._policy or AddressPolicy.default(
            dev_localhost=self.settings.oauth_dev_localhost)
        return client_factory(manifest.effective_hosts(), policy, self._transport)

    # ── discovery (F2) ────────────────────────────────────────────────────

    async def discover(self, manifest: Manifest) -> ServerMeta:
        auth = manifest.auth or {}
        key = (manifest.id, manifest.resolved_url(), auth.get("issuer") or "")
        cached = self._meta_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            async with self._http(manifest) as client:
                meta = await self._discover(client, manifest)
        except OAuthError:
            raise
        except EgressBlocked:
            raise OAuthError("egress_blocked") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise OAuthError("discovery_failed") from None
        self._meta_cache[key] = (time.monotonic() + self._ttl, meta)
        return meta

    async def _discover(self, client: httpx.AsyncClient, manifest: Manifest) -> ServerMeta:
        from mcp.client.auth.utils import (
            build_oauth_authorization_server_metadata_discovery_urls,
            build_protected_resource_metadata_discovery_urls,
            extract_resource_metadata_from_www_auth, handle_auth_metadata_response,
            handle_protected_resource_response,
        )
        hosts = {h.lower() for h in manifest.effective_hosts()}
        resource = manifest.resolved_url()
        issuer = ((manifest.auth or {}).get("issuer") or "").strip()

        if not issuer:  # RFC 9728: the MCP server names its authorization server
            probe = await client.get(resource, headers={"Accept": "application/json"})
            hint = (extract_resource_metadata_from_www_auth(probe)
                    if probe.status_code == 401 else None)
            prm_raw = None
            for url in build_protected_resource_metadata_discovery_urls(hint, resource):
                response = await client.get(url, headers={"Accept": "application/json"})
                if await handle_protected_resource_response(response) is not None:
                    prm_raw = json.loads(response.content)
                    break
            if prm_raw is None:
                raise OAuthError("discovery_failed")
            if _norm_issuer(str(prm_raw.get("resource", ""))) != _norm_issuer(resource):
                raise OAuthError("discovery_failed")
            servers = [s for s in prm_raw.get("authorization_servers", [])
                       if isinstance(s, str) and (urlparse(s).hostname or "").lower() in hosts]
            if not servers:
                raise OAuthError("discovery_failed")
            issuer = servers[0]

        raw = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, resource):
            response = await client.get(url, headers={"Accept": "application/json"})
            keep_going, model = await handle_auth_metadata_response(response)
            if model is not None:
                raw = json.loads(response.content)
                break
            if not keep_going:
                break
        if raw is None:
            raise OAuthError("discovery_failed")
        # RFC 8414 section 3.3: the issuer in the document must be the one we asked for.
        if _norm_issuer(str(raw.get("issuer", ""))) != _norm_issuer(issuer):
            raise OAuthError("issuer_mismatch")
        if "S256" not in (raw.get("code_challenge_methods_supported") or []):
            raise OAuthError("discovery_failed")  # no PKCE S256: do not proceed
        authorize = str(raw["authorization_endpoint"])
        token = str(raw["token_endpoint"])
        revoke = raw.get("revocation_endpoint")
        for endpoint in (authorize, token, *( [revoke] if revoke else [] )):
            self._check_endpoint(str(endpoint), hosts)
        methods = raw.get("token_endpoint_auth_methods_supported")
        return ServerMeta(
            issuer=_norm_issuer(issuer), authorization_endpoint=authorize,
            token_endpoint=token, revocation_endpoint=str(revoke) if revoke else None,
            iss_supported=raw.get("authorization_response_iss_parameter_supported") is True,
            auth_methods=tuple(methods) if isinstance(methods, list) else None,
            resource=resource)

    def _check_endpoint(self, url: str, hosts: set[str]) -> None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host not in hosts:
            raise OAuthError("egress_blocked")
        if parsed.scheme == "https":
            return
        dev = (parsed.scheme == "http" and host in ("localhost", "127.0.0.1")
               and self.settings.oauth_dev_localhost
               and not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
        if not dev:
            raise OAuthError("egress_blocked")

    # ── client credentials (F1) ───────────────────────────────────────────

    async def _client_secret(self, manifest: Manifest) -> Optional[str]:
        auth = manifest.auth or {}
        if not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            var = "LOOP_DEV_CLIENT_SECRET_" + manifest.id.upper().replace("-", "_")
            if os.environ.get(var):
                return os.environ[var]
        ref = (auth.get("client_secret_ref") or "").strip()
        if not ref:
            return None  # a public client
        if not ref.startswith("secretsmanager:"):
            raise OAuthError("not_configured")
        name = ref[len("secretsmanager:"):]
        if "<env>" in name:
            if not self.settings.env_name:
                raise OAuthError("not_configured")
            name = name.replace("<env>", self.settings.env_name)
        try:
            value = await self._secret_fetcher(name)
        except Exception:  # noqa: BLE001 - never echo what the SDK says
            raise OAuthError("not_configured") from None
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                value = parsed.get("client_secret") or ""
        except ValueError:
            pass
        if not value:
            raise OAuthError("not_configured")
        return value

    @staticmethod
    def _client_auth(meta: ServerMeta, client_id: str, secret: Optional[str],
                     data: dict[str, str]) -> dict[str, str]:
        """Add client authentication to a token request; returns extra headers."""
        if secret is None:
            data["client_id"] = client_id
            return {}
        methods = meta.auth_methods
        if methods is None or "client_secret_basic" in methods:
            raw = f"{quote(client_id, safe='')}:{quote(secret, safe='')}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        if "client_secret_post" in methods:
            data["client_id"] = client_id
            data["client_secret"] = secret
            return {}
        raise OAuthError("not_configured")

    # ── start (F3, F4, F5) ────────────────────────────────────────────────

    async def start(self, user_id: str, plugin_id: str) -> str:
        manifest = self._manifest(plugin_id)
        redirect_uri = self.settings.oauth_redirect_uri
        if not redirect_uri:
            raise OAuthError("not_configured")
        sealer = self._seal_with()
        meta = await self.discover(manifest)
        auth = manifest.auth or {}

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        digest = _state_sha256(state)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        try:
            verifier_enc = await sealer.seal(
                verifier.encode("ascii"), pending_context(user_id, plugin_id, digest))
        except crypto.SealError:
            raise OAuthError("sealer_unavailable") from None
        now = self._clock()
        await self.pending.put(Pending(
            state_sha256=digest, user_id=str(user_id), plugin_id=plugin_id,
            verifier_enc=verifier_enc, issuer=meta.issuer,
            expires_at=now + timedelta(seconds=STATE_TTL_S), created_at=now))

        params: dict[str, str] = {
            "response_type": "code", "client_id": str(auth["client_id"]),
            "redirect_uri": redirect_uri, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "resource": meta.resource,
        }
        scopes = [str(s) for s in auth.get("scopes") or []]   # the manifest only
        if scopes:
            params["scope"] = " ".join(scopes)
        for k, v in (auth.get("authorize_extra_params") or {}).items():
            if k not in params and k not in ("request", "request_uri"):
                params[str(k)] = str(v)
        logger.info("oauth_start plugin=%s", plugin_id)
        return str(httpx.URL(meta.authorization_endpoint).copy_merge_params(params))

    # ── complete (F4, F5, F6) ─────────────────────────────────────────────

    async def complete(self, user_id: str, code: str, state: str,
                       iss: Optional[str] = None) -> ConnectionInfo:
        if (not isinstance(code, str) or not code or len(code) > _MAX_FIELD
                or not isinstance(state, str) or not state or len(state) > _MAX_FIELD
                or (iss is not None and (not isinstance(iss, str) or len(iss) > _MAX_FIELD))):
            raise self._fail(None, "invalid_state")
        digest = _state_sha256(state)
        # The single-use gate: atomic, bound to the user, unexpired.
        pending = await self.pending.consume(str(user_id), digest, self._clock())
        if pending is None:
            raise self._fail(None, "invalid_state")
        plugin_id = pending.plugin_id
        try:
            manifest = self._manifest(plugin_id)
            meta = await self.discover(manifest)
            if meta.issuer != _norm_issuer(pending.issuer):
                raise OAuthError("issuer_mismatch")
            iss = (iss or "").strip()
            if meta.iss_supported and not iss:
                raise OAuthError("iss_missing")
            if iss and _norm_issuer(iss) != meta.issuer:
                raise OAuthError("issuer_mismatch")
            sealer = self._seal_with()
            try:
                if pending.verifier_enc is None:
                    raise OAuthError("invalid_state")
                verifier = (await sealer.open(
                    pending.verifier_enc,
                    pending_context(user_id, plugin_id, digest))).decode("ascii")
            except crypto.SealError:
                raise OAuthError("invalid_state") from None

            auth = manifest.auth or {}
            client_id = str(auth["client_id"])
            data = {
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.settings.oauth_redirect_uri or "",
                "code_verifier": verifier, "resource": meta.resource,
            }
            headers = self._client_auth(meta, client_id, await self._client_secret(manifest), data)
            token = await self._token_request(manifest, meta, data, headers)
            if token is None:
                raise OAuthError("token_exchange_failed")
            conn = await self._seal_token(
                sealer, user_id, plugin_id, token, meta, old=None,
                requested=[str(s) for s in auth.get("scopes") or []])
            await self.connections.upsert(conn)
        except OAuthError as exc:
            raise self._fail(plugin_id, exc.code) from None
        logger.info("oauth_connected plugin=%s", plugin_id)
        return _info(conn)

    # ── token endpoint ────────────────────────────────────────────────────

    async def _token_request(self, manifest: Manifest, meta: ServerMeta,
                             data: dict[str, str], headers: dict[str, str]
                             ) -> Optional[Any]:
        """POST to the token endpoint. Returns the parsed `OAuthToken`, or None when
        the server answered 4xx (a refusal); raises `transient` for 5xx, 429 and
        network faults, `bad_token_response` for a 200 that is not a token."""
        from mcp.shared.auth import OAuthToken
        from pydantic import ValidationError
        try:
            async with self._http(manifest) as client:
                response = await client.post(
                    meta.token_endpoint, data=data,
                    headers={"Accept": "application/json", **headers})
        except EgressBlocked:
            raise OAuthError("egress_blocked") from None
        except httpx.HTTPError:
            raise OAuthError("transient") from None
        if response.status_code >= 500 or response.status_code == 429:
            raise OAuthError("transient")
        if response.status_code != 200:
            return None
        try:
            token = OAuthToken.model_validate_json(response.content)
        except (ValidationError, ValueError):
            raise OAuthError("bad_token_response") from None
        if not token.access_token:
            raise OAuthError("bad_token_response")
        return token

    async def _seal_token(self, sealer: crypto.Sealer, user_id: str, plugin_id: str,
                          token: Any, meta: ServerMeta, *, old: Optional[Connection],
                          requested: list[str]) -> Connection:
        try:
            access_enc = await sealer.seal(
                token.access_token.encode("utf-8"),
                connection_context(user_id, plugin_id, "access"))
            if token.refresh_token:           # rotated: the new one replaces the old
                refresh_enc: Optional[bytes] = await sealer.seal(
                    token.refresh_token.encode("utf-8"),
                    connection_context(user_id, plugin_id, "refresh"))
            else:                              # not rotated: keep the one we have
                refresh_enc = old.refresh_enc if old else None
        except crypto.SealError:
            raise OAuthError("sealer_unavailable") from None
        granted = token.scope.split() if token.scope else (old.scopes if old else requested)
        expires_in = token.expires_in if token.expires_in is not None else DEFAULT_EXPIRES_IN_S
        now = self._clock()
        return Connection(
            user_id=str(user_id), plugin_id=plugin_id, access_enc=access_enc,
            refresh_enc=refresh_enc, expires_at=now + timedelta(seconds=int(expires_in)),
            scopes=list(granted), status=ACTIVE, issuer=meta.issuer,
            created_at=old.created_at if old else now, updated_at=now)

    # ── refresh (F7, F8) ──────────────────────────────────────────────────

    def _stale(self, conn: Connection) -> bool:
        return (conn.expires_at is None
                or conn.expires_at - timedelta(seconds=REFRESH_SKEW_S) < self._clock())

    async def refresh(self, user_id: str, plugin_id: str, *, force: bool = False
                      ) -> Connection:
        """Refresh under the per-connection lock. After taking the lock the row is
        read again: if another request already refreshed (and the token is no longer
        stale) that token is used, so two concurrent calls refresh once. `force`
        skips only that freshness test."""
        manifest = self._manifest(plugin_id)
        sealer = self._seal_with()
        expired = False
        # An error must not leave the block after `mark_expired`: on Postgres the
        # block is a transaction and an exception would roll the update back.
        async with self.connections.locked(str(user_id), plugin_id) as locked:
            cur = locked.current
            if cur is None:
                raise NotConnected()
            if cur.status != ACTIVE:
                raise AuthExpired()
            if not force and not self._stale(cur):
                return cur                      # someone else refreshed while we waited
            if cur.refresh_enc is None:
                expired = True
                await locked.mark_expired()
            else:
                try:
                    refresh_token = (await sealer.open(
                        cur.refresh_enc,
                        connection_context(user_id, plugin_id, "refresh"))).decode("utf-8")
                except crypto.SealError:
                    raise self._fail(plugin_id, "token_unreadable") from None
                meta = await self.discover(manifest)
                if cur.issuer and _norm_issuer(cur.issuer) != meta.issuer:
                    expired = True
                    await locked.mark_expired()
                else:
                    auth = manifest.auth or {}
                    data = {"grant_type": "refresh_token",
                            "refresh_token": refresh_token, "resource": meta.resource}
                    headers = self._client_auth(
                        meta, str(auth["client_id"]),
                        await self._client_secret(manifest), data)
                    try:
                        token = await self._token_request(manifest, meta, data, headers)
                    except OAuthError as exc:   # transient etc.: nothing was changed
                        raise self._fail(plugin_id, exc.code) from None
                    if token is None:           # F8: the provider refused the token
                        expired = True
                        await locked.mark_expired()
                    else:
                        fresh = await self._seal_token(
                            sealer, user_id, plugin_id, token, meta, old=cur,
                            requested=list(cur.scopes))
                        await locked.save(fresh)
                        result = locked.current or fresh
        if expired:
            raise self._fail(plugin_id, "auth_expired")
        logger.info("oauth_refreshed plugin=%s", plugin_id)
        return result

    async def access_token(self, user_id: str, plugin_id: str) -> str:
        """The bearer token to put on an MCP request, refreshed first when it is
        within 60 s of expiring. Raises `NotConnected` or `AuthExpired`. Call at
        request time only; never keep the value."""
        conn = await self.connections.get(str(user_id), plugin_id)
        if conn is None:
            raise NotConnected()
        if conn.status != ACTIVE:
            raise AuthExpired()
        if self._stale(conn):
            conn = await self.refresh(user_id, plugin_id)
        if conn.access_enc is None:
            raise AuthExpired()
        try:
            opened = await self._seal_with().open(
                conn.access_enc, connection_context(user_id, plugin_id, "access"))
        except crypto.SealError:
            raise self._fail(plugin_id, "token_unreadable") from None
        return opened.decode("utf-8")

    # ── revoke (F9) ───────────────────────────────────────────────────────

    async def revoke(self, user_id: str, plugin_id: str) -> bool:
        """Delete the connection (always), and ask the provider to revoke the token
        (RFC 7009) when it advertises an endpoint. A failed revocation does not stop
        the delete. Returns whether a connection existed."""
        conn = await self.connections.get(str(user_id), plugin_id)
        if conn is None:
            return False
        token: Optional[str] = None
        hint = "refresh_token"
        try:
            sealer = self._seal_with()
            if conn.refresh_enc is not None:
                token = (await sealer.open(conn.refresh_enc, connection_context(
                    user_id, plugin_id, "refresh"))).decode("utf-8")
            elif conn.access_enc is not None:
                hint = "access_token"
                token = (await sealer.open(conn.access_enc, connection_context(
                    user_id, plugin_id, "access"))).decode("utf-8")
        except (OAuthError, crypto.SealError):
            token = None
        await self.connections.delete(str(user_id), plugin_id)
        if token:
            await self._revoke_at_provider(plugin_id, token, hint)
        logger.info("oauth_revoked plugin=%s", plugin_id)
        return True

    async def _revoke_at_provider(self, plugin_id: str, token: str, hint: str) -> None:
        try:
            manifest = self._manifest(plugin_id)
            meta = await self.discover(manifest)
            if not meta.revocation_endpoint:
                return
            data = {"token": token, "token_type_hint": hint}
            headers = self._client_auth(
                meta, str((manifest.auth or {})["client_id"]),
                await self._client_secret(manifest), data)
            async with self._http(manifest) as client:
                await client.post(meta.revocation_endpoint, data=data, headers=headers)
        except Exception:  # noqa: BLE001 - best effort; the row is already gone
            logger.info("oauth_revoke_provider_failed plugin=%s", plugin_id)

    # ── listing, logging ──────────────────────────────────────────────────

    async def list_connections(self, user_id: str) -> list[ConnectionInfo]:
        return [_info(c) for c in await self.connections.list(str(user_id))]

    @staticmethod
    def _fail(plugin_id: Optional[str], code: str) -> OAuthError:
        logger.warning("oauth_failed plugin=%s code=%s", plugin_id or "-", code)
        return AuthExpired() if code == "auth_expired" else OAuthError(code)


# ── Module-level service ──────────────────────────────────────────────────

_service: Optional[OAuthService] = None


def set_service(service: Optional[OAuthService]) -> None:
    """Install a service explicitly (tests, api/main.py). None resets."""
    global _service
    _service = service


def get_oauth_service() -> OAuthService:
    global _service
    if _service is None:
        _service = OAuthService()
    return _service
