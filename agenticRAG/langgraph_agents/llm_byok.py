"""Per-request BYOK binding + SSRF guard for user-supplied LLM endpoints.

The browser keeps the user's key (localStorage) and sends it with each
``POST /chat`` as ``body.llm``. The server binds it here in memory for the
duration of that request only — it is never written to Postgres, Redis, a
file, a cache, or a module global that outlives the request, and it is never
logged (see ``shared/logging.py::JsonFormatter`` redaction).

Transport is a :class:`contextvars.ContextVar`, the same shape as
``db/postgres.py::bind_request_character``: concurrent requests share the
process, so a module global would hand one user another user's key, and
``asyncio.create_task`` copies the context so nodes running in tasks still
see the binding.

This module is imported by the CRUD Lambda path (``api/routes_llm.py`` via
``api/crud_app.py``), so it may only use stdlib + pydantic at module scope.
Do NOT import langchain, openai, or httpx here — there is a test pinning
that (``tests/langgraph_agents/test_crud_app.py``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import urllib.parse
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import SecretStr

CAPABILITIES = ("text", "answer", "tools", "image")
OMITTABLE = ("temperature", "max_tokens")
ECA_CAPABILITIES = ("text", "answer", "tools")


def local_endpoints_allowed() -> bool:
    """Whether loopback/private targets are accepted.

    Off on the web deployment; enabled locally (``BYOK_ALLOW_LOCAL=true``)
    so Ollama / LM Studio on localhost can be used.
    """
    return os.getenv("BYOK_ALLOW_LOCAL", "").strip().lower() == "true"


@dataclass
class RequestLLM:
    """The user's LLM connection for one request. Deliberately mutable.

    Task nodes receive copies of the context but point at the same object,
    so a ``failure`` recorded by one node is visible to the next — that is
    how the first provider error wins and later roles fall back to ECA.
    """

    base_url: str
    model: str
    api_key: SecretStr
    capabilities: frozenset[str] = field(default_factory=frozenset)
    omit: frozenset[str] = field(default_factory=frozenset)
    failure: dict | None = None
    http_client: Any = None


_request_llm: ContextVar[Optional[RequestLLM]] = ContextVar(
    "eca_request_llm", default=None,
)


def bind_request_llm(req: Optional[RequestLLM]) -> None:
    """Bind (or, with ``None``, unbind) this request's LLM connection."""
    _request_llm.set(req)


def current_request_llm() -> Optional[RequestLLM]:
    """The bound connection, or ``None`` outside a BYOK request."""
    return _request_llm.get()


def current_secret() -> str | None:
    """The bound key's value, or ``None`` when nothing is bound.

    Used only for log redaction (``shared/logging.py``) — never logged.
    """
    req = _request_llm.get()
    if req is None or req.api_key is None:
        return None
    try:
        return req.api_key.get_secret_value()
    except Exception:
        return None


class EndpointNotAllowed(ValueError):
    """Raised when a user-supplied endpoint URL must not be called."""


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _addr_is_blocked(addr: str) -> bool:
    """True when this resolved address must never be called from the server."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Not parseable as an IP — treat as blocked (fail closed).
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_endpoint_url(url: str) -> str:
    """Return the normalized URL, or raise :class:`EndpointNotAllowed`.

    The server is about to call a user-supplied address, so internal
    targets are blocked: no userinfo, https only (http additionally when
    :func:`local_endpoints_allowed`), no IP-literal hosts, and the hostname
    must resolve to at least one address with every address public —
    anything private/loopback/link-local/reserved/multicast/unspecified
    fails, as does an unresolvable name.
    """
    if not isinstance(url, str):
        raise EndpointNotAllowed("endpoint must be a string")
    normalized = url.strip()
    if not normalized:
        raise EndpointNotAllowed("endpoint is empty")

    try:
        parts = urllib.parse.urlsplit(normalized)
    except ValueError as exc:
        raise EndpointNotAllowed(f"endpoint is not a valid URL: {exc}") from None

    host = parts.hostname
    if not host:
        raise EndpointNotAllowed("endpoint has no host")
    # Userinfo (user:pass@host) must never reach the server call.
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        # urlsplit strips userinfo from hostname, so check netloc too: a URL
        # like "https://user@example.com/v1" parses hostname "example.com"
        # with username "user" (caught above), while a malformed one may keep
        # the "@" in netloc.
        raise EndpointNotAllowed("endpoint must not contain userinfo")

    allow_local = local_endpoints_allowed()
    scheme = (parts.scheme or "").lower()
    if allow_local:
        if scheme not in ("http", "https"):
            raise EndpointNotAllowed("endpoint scheme must be http or https")
    else:
        if scheme != "https":
            raise EndpointNotAllowed("endpoint scheme must be https")

    if _is_ip_literal(host) and not allow_local:
        raise EndpointNotAllowed("endpoint must be a hostname, not an IP address")

    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise EndpointNotAllowed(f"endpoint host does not resolve: {exc}") from None
    if not infos:
        raise EndpointNotAllowed("endpoint host does not resolve")

    addrs: list[str] = []
    for info in infos:
        try:
            sockaddr = info[4]
            addrs.append(sockaddr[0])
        except (IndexError, TypeError):
            raise EndpointNotAllowed("endpoint host does not resolve") from None

    if not allow_local:
        for addr in addrs:
            if _addr_is_blocked(addr):
                raise EndpointNotAllowed("endpoint resolves to an internal address")
    return normalized


async def validate_endpoint_url_async(url: str) -> str:
    """Async wrapper — DNS resolution blocks, so it runs in a thread."""
    return await asyncio.to_thread(validate_endpoint_url, url)
