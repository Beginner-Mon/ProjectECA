"""T1 — BYOK binding, endpoint guard, constants (unit, no network)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_AGN_ROOT = Path(__file__).resolve().parents[2] / "agenticRAG"

pytestmark = pytest.mark.unit


def _public_addr(monkeypatch, ip="203.0.113.10"):
    """getaddrinfo answering one public address. 203.0.113.0/24 is TEST-NET-3
    (documentation): globally routable-looking but not private/loopback, so it
    passes the blocklist while never touching the network in tests."""
    # NOTE: 192.0.2.0/24 and 203.0.113.0/24 are marked is_reserved in some
    # Python versions — use a plain public IP instead.
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [
            (2, 1, 6, "", (ip, 0)),
        ],
    )


def test_rejects_http_scheme(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("http://api.example.com/v1")


def test_rejects_loopback_ip_literal(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://127.0.0.1/v1")


def test_rejects_private_ip_literal(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://10.0.0.5/v1")


def test_rejects_hostname_resolving_to_link_local(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("169.254.1.1", 0))],
    )
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://provider.example/v1")


@pytest.mark.parametrize("addr", ["100.64.0.1", "224.0.1.1", "fc00::1"])
def test_rejects_non_global_resolved_addresses(monkeypatch, addr):
    """Only globally reachable addresses may be called: shared CGNAT space,
    multicast and unique-local IPv6 are all non-global and must fail."""
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (addr, 0))],
    )
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://provider.example/v1")


@pytest.mark.parametrize("addr", ["8.8.8.8", "2606:4700:4700::1111"])
def test_allows_global_v4_and_v6_addresses(monkeypatch, addr):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", (addr, 0))],
    )
    out = llm_byok.validate_endpoint_url("https://provider.example/v1")
    assert out == "https://provider.example/v1"


def test_rejects_userinfo(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://user:pass@api.example.com/v1")


def test_rejects_empty_string(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("")


def test_rejects_unresolvable_host(monkeypatch):
    import socket as _socket

    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)

    def _fail(host, port, *a, **k):
        raise _socket.gaierror("no such host")

    monkeypatch.setattr("langgraph_agents.llm_byok.socket.getaddrinfo", _fail)
    with pytest.raises(llm_byok.EndpointNotAllowed):
        llm_byok.validate_endpoint_url("https://does-not-exist.invalid/v1")


@pytest.mark.unit
def test_allows_public_hostname(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.delenv("BYOK_ALLOW_LOCAL", raising=False)
    _public_addr(monkeypatch, "8.8.8.8")
    out = llm_byok.validate_endpoint_url("https://api.example.com/v1")
    assert out == "https://api.example.com/v1"


@pytest.mark.unit
def test_allows_local_when_enabled(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.setenv("BYOK_ALLOW_LOCAL", "true")
    monkeypatch.setattr(
        "langgraph_agents.llm_byok.socket.getaddrinfo",
        lambda host, port, *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))],
    )
    out = llm_byok.validate_endpoint_url("http://localhost:11434/v1/")
    assert out == "http://localhost:11434/v1/"


@pytest.mark.unit
def test_local_flag_case_insensitive(monkeypatch):
    from langgraph_agents import llm_byok

    monkeypatch.setenv("BYOK_ALLOW_LOCAL", "True")
    assert llm_byok.local_endpoints_allowed() is True
    monkeypatch.setenv("BYOK_ALLOW_LOCAL", "TRUE")
    assert llm_byok.local_endpoints_allowed() is True
    monkeypatch.setenv("BYOK_ALLOW_LOCAL", "false")
    assert llm_byok.local_endpoints_allowed() is False


@pytest.mark.unit
def test_constants():
    from langgraph_agents import llm_byok

    assert llm_byok.CAPABILITIES == ("text", "answer", "tools", "image")
    assert llm_byok.OMITTABLE == ("temperature", "max_tokens")
    assert llm_byok.ECA_CAPABILITIES == ("text", "answer", "tools")


@pytest.mark.unit
def test_contextvar_isolation_between_concurrent_tasks():
    """Two asyncio tasks binding different RequestLLM must not see each other."""
    from pydantic import SecretStr

    from langgraph_agents import llm_byok

    async def _run():
        from pydantic import SecretStr

        async def _task(name: str, seen: dict):
            req = llm_byok.RequestLLM(
                base_url="https://api.example.com/v1",
                model=name,
                api_key=SecretStr("k-" + name),
                capabilities=frozenset({"text"}),
            )
            llm_byok.bind_request_llm(req)
            await asyncio.sleep(0.01)
            seen[name] = llm_byok.current_request_llm()
            llm_byok.bind_request_llm(None)

        seen: dict = {}
        await asyncio.gather(_task("a", seen), _task("b", seen))
        return seen

    seen = asyncio.run(_run())
    assert seen["a"].model == "a"
    assert seen["b"].model == "b"


@pytest.mark.unit
def test_current_secret_none_when_unbound():
    from langgraph_agents import llm_byok

    llm_byok.bind_request_llm(None)
    assert llm_byok.current_secret() is None


@pytest.mark.unit
def test_byok_module_does_not_import_heavy_stack():
    """Copy of test_crud_app.py's probe: llm_byok must stay stdlib+pydantic."""
    probe = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_AGN_ROOT)!r})

        import langgraph_agents.llm_byok  # noqa: F401

        forbidden = [
            m for m in (
                "langchain_openai",
                "openai",
                "httpx",
                "torch",
                "langchain_core",
            )
            if m in sys.modules
        ]
        print("LEAKED:" + ",".join(forbidden))
    """)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    leaked = [
        line[len("LEAKED:"):]
        for line in result.stdout.splitlines()
        if line.startswith("LEAKED:")
    ]
    assert leaked, f"probe produced no verdict:\n{result.stdout}\n{result.stderr}"
    assert leaked[0] == "", f"llm_byok pulled in {leaked[0]}"
