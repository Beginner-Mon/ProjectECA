"""`loop/http.py`: SSRF-safe client. No test touches the network.

DNS goes through `AddressPolicy.for_tests({...})`; the server side is an
`httpx.MockTransport`, which also records what reached it.
"""

from __future__ import annotations

import httpx
import pytest

from langgraph_agents.loop import http as lhttp
from langgraph_agents.loop.http import (
    AddressPolicy, EgressBlocked, GuardedTransport, ResponseTooLarge,
    client_factory, is_public_address,
)

pytestmark = pytest.mark.unit

HOST = "mcp.example.com"
URL = f"https://{HOST}/mcp"


def _recorder(handler=None):
    seen: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request) if handler else httpx.Response(200, text="ok")

    return httpx.MockTransport(_handle), seen


def _client(hosts=(HOST,), dns=None, handler=None, **kw):
    transport, seen = _recorder(handler)
    client = client_factory(hosts, AddressPolicy.for_tests(dns or {}),
                            transport, **kw)
    return client, seen


# ── Allowed ───────────────────────────────────────────────────────────────


async def test_allowed_https_host_works():
    client, seen = _client()
    async with client:
        r = await client.get(URL)
    assert r.status_code == 200 and r.text == "ok"
    assert len(seen) == 1


async def test_host_match_ignores_case_and_trailing_dot():
    client, _ = _client()
    async with client:
        assert (await client.get(f"https://{HOST.upper()}./mcp")).status_code == 200


async def test_redirect_within_the_list_is_followed():
    def handler(request):
        if request.url.path == "/mcp":
            return httpx.Response(302, headers={"location": "https://docs.example.com/x"})
        return httpx.Response(200, text="landed")

    client, seen = _client(hosts=(HOST, "docs.example.com"), handler=handler)
    async with client:
        r = await client.get(URL)
    assert r.text == "landed" and [q.url.host for q in seen] == [HOST, "docs.example.com"]


async def test_relative_redirect_keeps_the_host_name():
    def handler(request):
        if request.url.path == "/mcp":
            return httpx.Response(307, headers={"location": "/mcp/v2"})
        return httpx.Response(200, text="v2")

    client, seen = _client(handler=handler)
    async with client:
        assert (await client.get(URL)).text == "v2"
    assert seen[-1].url.path == "/mcp/v2"


# ── Blocked: address ──────────────────────────────────────────────────────

PRIVATE_V4 = ["127.0.0.1", "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.1",
              "169.254.169.254", "169.254.0.1", "100.64.0.1", "0.0.0.0",
              "224.0.0.1", "255.255.255.255", "192.0.0.1", "240.0.0.1"]
PRIVATE_V6 = ["::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd00:ec2::254",
              "ff02::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
              "64:ff9b::a00:1", "2002:7f00:1::1"]


@pytest.mark.parametrize("addr", PRIVATE_V4 + PRIVATE_V6)
def test_non_public_addresses_are_refused(addr):
    assert is_public_address(addr) is False


@pytest.mark.parametrize("addr", ["93.184.216.34", "8.8.8.8", "1.1.1.1",
                                  "2606:2800:220:1:248:1893:25c8:1946"])
def test_public_addresses_are_accepted(addr):
    assert is_public_address(addr) is True


def test_garbage_is_not_an_address():
    assert is_public_address("not-an-ip") is False
    assert is_public_address("") is False


@pytest.mark.parametrize("addr", PRIVATE_V4 + PRIVATE_V6)
async def test_dns_that_resolves_to_a_non_public_address_is_blocked(addr):
    client, seen = _client(dns={HOST: [addr]})
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(URL)
    assert e.value.reason == "address_not_public"
    assert seen == []  # never reached the server


async def test_one_private_address_among_public_ones_blocks():
    client, seen = _client(dns={HOST: ["93.184.216.34", "10.0.0.9"]})
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get(URL)
    assert seen == []


async def test_metadata_address_as_a_literal_host_is_blocked_even_if_listed():
    client, seen = _client(hosts=("169.254.169.254",))
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get("https://169.254.169.254/latest/meta-data/")
    assert e.value.reason == "address_not_public" and seen == []


@pytest.mark.parametrize("literal", ["127.0.0.1", "[::1]", "[::ffff:127.0.0.1]",
                                     "[fd00:ec2::254]", "10.1.2.3"])
async def test_ip_literal_hosts_are_blocked_even_if_listed(literal):
    host = literal.strip("[]")
    client, seen = _client(hosts=(host,))
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get(f"https://{literal}/x")
    assert seen == []


@pytest.mark.parametrize("odd", ["2130706433", "0x7f.0.0.1", "0177.0.0.1", "127.1"])
async def test_odd_ipv4_spellings_are_not_on_the_list(odd):
    client, seen = _client()
    async with client:
        with pytest.raises(Exception):
            await client.get(f"https://{odd}/x")
    assert seen == []


async def test_dns_failure_is_a_block_not_a_pass():
    async def boom(host):
        raise OSError("nxdomain")

    transport, seen = _recorder()
    client = client_factory((HOST,), AddressPolicy(resolve=boom), transport)
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(URL)
    assert e.value.reason == "dns_failed" and seen == []


async def test_empty_dns_answer_is_a_block():
    client, seen = _client(dns={HOST: []})
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(URL)
    assert e.value.reason == "dns_empty"


# ── Blocked: host, scheme, userinfo ───────────────────────────────────────


async def test_host_not_in_list_is_blocked():
    client, seen = _client()
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get("https://evil.example.org/")
    assert e.value.reason == "host_not_allowed" and seen == []


async def test_plain_http_is_blocked():
    client, seen = _client()
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(f"http://{HOST}/mcp")
    assert e.value.reason == "scheme_not_allowed" and seen == []


async def test_other_schemes_are_blocked():
    client, _ = _client()
    async with client:
        with pytest.raises(Exception):
            await client.get(f"ftp://{HOST}/x")


async def test_userinfo_in_url_is_blocked():
    client, seen = _client()
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(f"https://user:pw@{HOST}/mcp")
    assert e.value.reason == "credentials_in_url" and seen == []


async def test_lookalike_host_is_blocked():
    client, _ = _client()
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get(f"https://{HOST}.evil.org/")
        with pytest.raises(EgressBlocked):
            await client.get(f"https://evil-{HOST}/")


# ── Blocked: redirects ────────────────────────────────────────────────────


@pytest.mark.parametrize("target", [
    "https://evil.example.org/x",
    "http://mcp.example.com/downgrade",
    "https://169.254.169.254/latest/meta-data/",
    "https://127.0.0.1/",
])
async def test_redirect_to_a_forbidden_target_is_stopped(target):
    def handler(request):
        return httpx.Response(302, headers={"location": target})

    client, seen = _client(handler=handler)
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get(URL)
    assert len(seen) == 1  # only the first hop reached the server


async def test_redirect_to_an_allowed_name_that_now_resolves_privately_is_stopped():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://docs.example.com/x"})

    client, seen = _client(hosts=(HOST, "docs.example.com"),
                           dns={"docs.example.com": ["10.0.0.7"]}, handler=handler)
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get(URL)
    assert e.value.reason == "address_not_public" and len(seen) == 1


async def test_redirect_loop_is_capped():
    def handler(request):
        return httpx.Response(302, headers={"location": URL})

    client, seen = _client(handler=handler, max_redirects=3)
    async with client:
        with pytest.raises(httpx.TooManyRedirects):
            await client.get(URL)
    assert len(seen) == 4


# ── Body size ─────────────────────────────────────────────────────────────


async def test_declared_oversized_body_is_refused():
    client, _ = _client(handler=lambda r: httpx.Response(
        200, headers={"content-length": "5000"}, content=b"x" * 5000), max_bytes=1000)
    async with client:
        with pytest.raises(ResponseTooLarge):
            await client.get(URL)


async def test_undeclared_oversized_body_is_cut_off():
    async def chunks():
        for _ in range(10):
            yield b"x" * 500

    client, _ = _client(handler=lambda r: httpx.Response(200, content=chunks()),
                        max_bytes=1000)
    async with client:
        with pytest.raises(ResponseTooLarge):
            await client.get(URL)


async def test_body_at_the_cap_passes():
    client, _ = _client(handler=lambda r: httpx.Response(200, content=b"x" * 1000),
                        max_bytes=1000)
    async with client:
        assert len((await client.get(URL)).content) == 1000


# ── Timeouts ──────────────────────────────────────────────────────────────


def test_default_timeouts_are_set():
    client = client_factory((HOST,), AddressPolicy.for_tests(), httpx.MockTransport(
        lambda r: httpx.Response(200)))
    assert client.timeout.connect == 3.0 and client.timeout.read == 10.0


# ── Dev localhost ─────────────────────────────────────────────────────────


async def test_localhost_http_is_blocked_by_default():
    client, seen = _client(hosts=("localhost",))
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get("http://localhost:9000/mcp")
    assert seen == []


async def test_localhost_http_works_with_the_dev_flag_off_lambda(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    transport, seen = _recorder()
    client = client_factory(("localhost",), AddressPolicy.for_tests(dev_localhost=True),
                            transport)
    async with client:
        assert (await client.get("http://localhost:9000/mcp")).status_code == 200
    assert len(seen) == 1


async def test_localhost_http_is_blocked_on_lambda_even_with_the_flag(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "vva-agent")
    transport, seen = _recorder()
    client = client_factory(("localhost",), AddressPolicy.for_tests(dev_localhost=True),
                            transport)
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get("http://localhost:9000/mcp")
    assert seen == []


async def test_dev_flag_does_not_open_other_http_hosts(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    transport, _ = _recorder()
    client = client_factory((HOST,), AddressPolicy.for_tests(dev_localhost=True), transport)
    async with client:
        with pytest.raises(EgressBlocked):
            await client.get(f"http://{HOST}/mcp")


async def test_localhost_must_still_be_listed(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    transport, _ = _recorder()
    client = client_factory((HOST,), AddressPolicy.for_tests(dev_localhost=True), transport)
    async with client:
        with pytest.raises(EgressBlocked) as e:
            await client.get("http://localhost:9000/")
    assert e.value.reason == "host_not_allowed"


# ── Pinning (the real-network path, with a recording inner transport) ────


async def test_pinned_request_connects_to_the_checked_address():
    transport, seen = _recorder()
    guarded = GuardedTransport(
        transport, (HOST,), AddressPolicy.for_tests({HOST: ["93.184.216.34"]}), pin=True)
    async with httpx.AsyncClient(transport=guarded) as client:
        await client.get(URL)
    sent = seen[0]
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == HOST
    assert sent.extensions["sni_hostname"] == HOST


async def test_pinned_request_with_an_ipv6_address():
    transport, seen = _recorder()
    guarded = GuardedTransport(
        transport, (HOST,),
        AddressPolicy.for_tests({HOST: ["2606:2800:220:1:248:1893:25c8:1946"]}), pin=True)
    async with httpx.AsyncClient(transport=guarded) as client:
        await client.get(URL)
    assert seen[0].url.host == "2606:2800:220:1:248:1893:25c8:1946"


async def test_pinning_keeps_relative_redirects_on_the_host_name():
    def handler(request):
        if request.url.path == "/mcp":
            return httpx.Response(302, headers={"location": "/other"})
        return httpx.Response(200, text="ok")

    transport, seen = _recorder(handler)
    guarded = GuardedTransport(transport, (HOST,), AddressPolicy.for_tests(), pin=True)
    async with httpx.AsyncClient(transport=guarded, follow_redirects=True) as client:
        r = await client.get(URL)
    assert r.status_code == 200 and seen[-1].url.path == "/other"


async def test_each_redirect_hop_is_resolved_again():
    calls: list[str] = []

    async def resolve(host):
        calls.append(host)
        return ["93.184.216.34"]

    def handler(request):
        if request.url.path == "/mcp":
            return httpx.Response(302, headers={"location": "https://docs.example.com/x"})
        return httpx.Response(200, text="ok")

    transport, _ = _recorder(handler)
    client = client_factory((HOST, "docs.example.com"), AddressPolicy(resolve=resolve),
                            transport)
    async with client:
        await client.get(URL)
    assert calls == [HOST, "docs.example.com"]


def test_default_policy_uses_the_system_resolver():
    assert AddressPolicy.default().resolve is lhttp._system_resolve
    assert AddressPolicy.default().dev_localhost is False


async def test_default_client_without_transport_is_pinned_and_guarded():
    client = client_factory((HOST,), AddressPolicy.for_tests())
    try:
        assert isinstance(client._transport, GuardedTransport)
        assert client._transport._pin is True
    finally:
        await client.aclose()
