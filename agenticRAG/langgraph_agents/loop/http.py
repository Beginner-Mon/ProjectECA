"""SSRF-safe HTTP for plugins (stage 3).

`client_factory(allowed_hosts, policy=..., transport=None)` returns an
`httpx.AsyncClient` whose every request, redirect hops included, passes the
same checks:

  1. HTTPS only (a dev `http://localhost` is the one exception, see below);
  2. no credentials in the URL;
  3. the host is in `allowed_hosts` (exact match, case-insensitive);
  4. the host is resolved through `policy.resolve`, and EVERY address it
     returns must be public: private, loopback, link-local (169.254.0.0/16,
     which holds the metadata address), CGNAT, multicast, reserved,
     unspecified and unique-local IPv6 are all refused. IPv4-mapped and NAT64
     IPv6 forms are unwrapped and judged as the IPv4 address inside;
  5. with the real transport, the connection goes to the address that was just
     checked (the URL host is replaced by the IP, TLS keeps the real name via
     `sni_hostname`), so a second DNS answer cannot undo the check;
  6. the response body is capped (`max_bytes`) and timeouts are set.

The checks sit in a transport wrapper, not in an event hook, because httpx
calls the transport once per redirect hop: a redirect to a host outside the
list, or to a name that now resolves to a private address, is stopped exactly
like a first request.

Tests never touch the network: pass `transport=` (an `httpx.MockTransport` or
an ASGI transport) and `policy=AddressPolicy.for_tests({...})`, which resolves
from a dict.

Dev flag: `http://localhost` and `http://127.0.0.1` are accepted only if the
policy was built with `dev_localhost=True`, the host is in `allowed_hosts`, and
`AWS_LAMBDA_FUNCTION_NAME` is unset. That is read per request, so Lambda can
never reach loopback even if the flag leaks into its config.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Iterable, Mapping

import httpx

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=10.0, pool=3.0)

_DEV_HOSTS = frozenset({"localhost", "127.0.0.1"})

Resolver = Callable[[str], Awaitable[list[str]]]


class EgressBlocked(httpx.RequestError):
    """The request was refused by the egress policy. `reason` is a short code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"egress blocked: {reason}" + (f" ({detail})" if detail else ""))
        self.reason = reason


class ResponseTooLarge(httpx.TransportError):
    """The body went over `max_bytes`."""


# ── Addresses ─────────────────────────────────────────────────────────────

_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _unwrap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address):
    """The IPv4 address hidden inside an IPv6 form, else the address itself."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def is_public_address(value: str) -> bool:
    """True only for a globally routable unicast address."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    ip = _unwrap(ip)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    return ip.is_global


async def _system_resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.append(addr)
    return seen


@dataclass(frozen=True)
class AddressPolicy:
    """How a host name becomes addresses, and whether loopback may be reached.

    `resolve` is the seam tests replace; it returns every address of a host.
    """

    resolve: Resolver
    dev_localhost: bool = False

    @classmethod
    def default(cls, *, dev_localhost: bool = False) -> "AddressPolicy":
        return cls(resolve=_system_resolve, dev_localhost=dev_localhost)

    @classmethod
    def for_tests(cls, hosts: Mapping[str, Iterable[str]] | None = None, *,
                  dev_localhost: bool = False,
                  default_address: str = "93.184.216.34") -> "AddressPolicy":
        """Resolve from a dict; a host not in it gets one public address."""
        table = {k.lower(): list(v) for k, v in (hosts or {}).items()}

        async def resolve(host: str) -> list[str]:
            return list(table.get(host.lower(), [default_address]))

        return cls(resolve=resolve, dev_localhost=dev_localhost)

    @staticmethod
    def on_lambda() -> bool:
        return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


# ── Transport ─────────────────────────────────────────────────────────────


def _host_key(host: str) -> str:
    return host.lower().rstrip(".")


class _CappedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, limit: int) -> None:
        self._inner = inner
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        total = 0
        async for chunk in self._inner:
            total += len(chunk)
            if total > self._limit:
                raise ResponseTooLarge(f"response over {self._limit} bytes")
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class GuardedTransport(httpx.AsyncBaseTransport):
    """Applies the egress checks to every request, then hands it to `inner`.

    `pin=True` (the real network) rewrites the URL host to the checked IP.
    With a test transport leave it False: the URL stays as written.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, allowed_hosts: Iterable[str],
                 policy: AddressPolicy, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 pin: bool = False) -> None:
        self._inner = inner
        self._allowed = frozenset(_host_key(h) for h in allowed_hosts)
        self._policy = policy
        self._max_bytes = max_bytes
        self._pin = pin

    async def _check(self, url: httpx.URL) -> str | None:
        """Raise `EgressBlocked`, or return the address to connect to (or None)."""
        host = _host_key(url.host or "")
        if not host:
            raise EgressBlocked("no_host")
        if url.userinfo:
            raise EgressBlocked("credentials_in_url")
        if host not in self._allowed:
            raise EgressBlocked("host_not_allowed", host)

        dev = (self._policy.dev_localhost and host in _DEV_HOSTS
               and not self._policy.on_lambda())
        if url.scheme == "http":
            if not dev:
                raise EgressBlocked("scheme_not_allowed", "http")
        elif url.scheme != "https":
            raise EgressBlocked("scheme_not_allowed", url.scheme)
        if dev:
            return None  # loopback is the point; nothing to pin

        try:
            ipaddress.ip_address(host)
            addresses = [host]  # a literal address: no DNS, same judgement
        except ValueError:
            try:
                addresses = list(await self._policy.resolve(host))
            except OSError as exc:
                raise EgressBlocked("dns_failed", type(exc).__name__) from exc
        if not addresses:
            raise EgressBlocked("dns_empty", host)
        for addr in addresses:
            if not is_public_address(addr):
                raise EgressBlocked("address_not_public", f"{host} -> {addr}")
        return addresses[0]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        address = await self._check(request.url)
        if self._pin and address is not None:
            # A copy, not a mutation: httpx resolves a relative redirect against
            # the ORIGINAL request's URL, which must keep the host name.
            original_host = request.url.host
            headers = httpx.Headers(request.headers)
            headers["Host"] = (original_host if request.url.port is None
                               else f"{original_host}:{request.url.port}")
            pinned = httpx.Request(
                request.method, request.url.copy_with(host=address),
                headers=headers, stream=request.stream,
                extensions={**request.extensions, "sni_hostname": original_host})
            response = await self._inner.handle_async_request(pinned)
        else:
            response = await self._inner.handle_async_request(request)
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self._max_bytes:
            await response.aclose()
            raise ResponseTooLarge(f"content-length {declared} over {self._max_bytes}")
        response.stream = _CappedStream(response.stream, self._max_bytes)  # type: ignore[arg-type]
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def client_factory(
    allowed_hosts: Iterable[str],
    policy: AddressPolicy | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    timeout: httpx.Timeout | float | None = None,
    **client_kwargs,
) -> httpx.AsyncClient:
    """An `httpx.AsyncClient` that can only reach `allowed_hosts` over public IPs.

    `transport` is for tests (`httpx.MockTransport`, `httpx.ASGITransport`); it
    is wrapped by the same checks. Without it the client uses the real network
    and pins each connection to the address that was checked.
    """
    policy = policy or AddressPolicy.default()
    guarded = GuardedTransport(
        transport if transport is not None else httpx.AsyncHTTPTransport(),
        allowed_hosts, policy, max_bytes=max_bytes, pin=transport is None)
    client_kwargs.pop("follow_redirects", None)
    return httpx.AsyncClient(
        transport=guarded,
        follow_redirects=True,
        max_redirects=max_redirects,
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        **client_kwargs,
    )
