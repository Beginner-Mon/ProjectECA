"""T2 — get_chat_model uses the user's key when bound (unit, no network)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

pytestmark = pytest.mark.unit


USER_BASE = "https://user.example/v1"
USER_MODEL = "user-model-x"


def _bind(monkeypatch, capabilities=("text", "answer", "tools"), omit=(), failure=None):
    import langgraph_agents.llm_byok as byok

    req = byok.RequestLLM(
        base_url=USER_BASE,
        model=USER_MODEL,
        api_key=SecretStr("user-key-123"),
        capabilities=frozenset(capabilities),
        omit=frozenset(omit),
        failure=failure,
    )
    byok.bind_request_llm(req)
    return req


def _setup(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    import langgraph_agents.llm as llm

    llm.get_chat_model.cache_clear()
    llm.bind_request_llm if hasattr(llm, "bind_request_llm") else None
    # llm imports current_request_llm; unbind via llm_byok directly.
    import langgraph_agents.llm_byok as byok

    byok.bind_request_llm(None)
    return llm, byok


def _teardown(llm, byok):
    byok.bind_request_llm(None)
    llm.get_chat_model.cache_clear()


def test_no_binding_returns_cached_eca_model(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        a = llm.get_chat_model("planner")
        b = llm.get_chat_model("planner")
        assert a is b
        assert getattr(a, "openai_api_base", "") != USER_BASE
    finally:
        _teardown(llm, byok)


def test_binding_routes_main_roles_to_user(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        _bind(monkeypatch)
        for role in ("planner", "retriever", "synthesizer"):
            m = llm.get_chat_model(role)
            assert getattr(m, "openai_api_base", None) == USER_BASE
            assert getattr(m, "model_name", None) == USER_MODEL
    finally:
        _teardown(llm, byok)


def test_grader_stays_on_eca(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        _bind(monkeypatch)
        m = llm.get_chat_model("grader")
        assert getattr(m, "openai_api_base", None) != USER_BASE
    finally:
        _teardown(llm, byok)


def test_missing_tools_falls_back_per_role(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        _bind(monkeypatch, capabilities=("text", "answer"))
        planner = llm.get_chat_model("planner")
        retriever = llm.get_chat_model("retriever")
        synth = llm.get_chat_model("synthesizer")
        assert getattr(planner, "openai_api_base", None) != USER_BASE
        assert getattr(retriever, "openai_api_base", None) != USER_BASE
        assert getattr(synth, "openai_api_base", None) == USER_BASE
    finally:
        _teardown(llm, byok)


def test_omit_temperature_and_max_tokens(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        _bind(monkeypatch, omit=("temperature",))
        m = llm.get_chat_model("planner")
        assert m.temperature is None
    finally:
        _teardown(llm, byok)
    try:
        _bind(monkeypatch, omit=("max_tokens",))
        m = llm.get_chat_model("planner")
        assert getattr(m, "max_tokens", None) is None
    finally:
        _teardown(llm, byok)


def test_existing_failure_returns_eca(monkeypatch):
    llm, byok = _setup(monkeypatch)
    try:
        _bind(monkeypatch, failure={"kind": "auth", "status": 401})
        for role in ("planner", "retriever", "synthesizer"):
            m = llm.get_chat_model(role)
            assert getattr(m, "openai_api_base", None) != USER_BASE
    finally:
        _teardown(llm, byok)


def test_guard_ainvoke_falls_back_and_records_auth(monkeypatch):
    import openai

    llm, byok = _setup(monkeypatch)
    try:
        req = _bind(monkeypatch)

        class _FakeByok:
            async def ainvoke(self, *a, **k):
                raise openai.AuthenticationError(
                    "bad key",
                    response=httpx.Response(
                        401, request=httpx.Request("POST", "https://x")
                    ),
                    body=None,
                )

            async def astream(self, *a, **k):  # pragma: no cover
                yield None

        class _FakeEca:
            async def ainvoke(self, *a, **k):
                return "eca-answer"

            async def astream(self, *a, **k):  # pragma: no cover
                yield None

        monkeypatch.setattr(llm, "_eca_chat_model", lambda role, temperature=None: _FakeEca())
        model = llm._guard_byok(_FakeByok(), req, "planner", 0.0)
        out = asyncio.run(model.ainvoke("hi"))
        assert out == "eca-answer"
        assert req.failure == {"kind": "auth", "status": 401}
    finally:
        _teardown(llm, byok)


def test_guard_astream_before_first_chunk_uses_eca(monkeypatch):
    import openai

    llm, byok = _setup(monkeypatch)
    try:
        req = _bind(monkeypatch)

        class _FakeByok:
            async def ainvoke(self, *a, **k):  # pragma: no cover
                return None

            async def astream(self, *a, **k):
                raise openai.RateLimitError(
                    "slow down",
                    response=httpx.Response(
                        429, request=httpx.Request("POST", "https://x")
                    ),
                    body=None,
                )
                yield  # pragma: no cover

        from langchain_core.messages import AIMessageChunk

        class _FakeEca:
            async def ainvoke(self, *a, **k):  # pragma: no cover
                return None

            async def astream(self, *a, **k):
                yield AIMessageChunk(content="eca-1")
                yield AIMessageChunk(content="eca-2")

        monkeypatch.setattr(llm, "_eca_chat_model", lambda role, temperature=None: _FakeEca())
        model = llm._guard_byok(_FakeByok(), req, "synthesizer", 0.7)

        async def _collect():
            return [c async for c in model.astream("hi")]

        chunks = asyncio.run(_collect())
        assert "".join(getattr(c, "content", "") for c in chunks) == "eca-1eca-2"
        assert req.failure == {"kind": "quota", "status": 429}
    finally:
        _teardown(llm, byok)


def test_guard_astream_after_first_chunk_raises(monkeypatch):
    import openai

    llm, byok = _setup(monkeypatch)
    try:
        req = _bind(monkeypatch)

        from langchain_core.messages import AIMessageChunk

        class _FakeByok:
            async def ainvoke(self, *a, **k):  # pragma: no cover
                return None

            async def astream(self, *a, **k):
                yield AIMessageChunk(content="user-1")
                raise openai.AuthenticationError(
                    "bad",
                    response=httpx.Response(
                        401, request=httpx.Request("POST", "https://x")
                    ),
                    body=None,
                )

        class _FakeEca:
            async def ainvoke(self, *a, **k):  # pragma: no cover
                return None

            async def astream(self, *a, **k):  # pragma: no cover
                yield AIMessageChunk(content="eca")

        monkeypatch.setattr(llm, "_eca_chat_model", lambda role, temperature=None: _FakeEca())
        model = llm._guard_byok(_FakeByok(), req, "synthesizer", 0.7)

        async def _collect():
            out = []
            async for c in model.astream("hi"):
                out.append(c)
            return out

        with pytest.raises(llm.ProviderCallError):
            asyncio.run(_collect())
    finally:
        _teardown(llm, byok)


def test_breaker_untouched_by_byok_failure(monkeypatch):
    import openai

    llm, byok = _setup(monkeypatch)
    try:
        # Create the ECA breaker entry first so the snapshot exists.
        _ = llm.get_chat_model("planner")
        before = llm.get_breaker_snapshot("planner")
        req = _bind(monkeypatch)

        class _FakeByok:
            async def ainvoke(self, *a, **k):
                raise openai.AuthenticationError(
                    "bad",
                    response=httpx.Response(
                        401, request=httpx.Request("POST", "https://x")
                    ),
                    body=None,
                )

            async def astream(self, *a, **k):  # pragma: no cover
                yield None

        class _FakeEca:
            async def ainvoke(self, *a, **k):
                return "eca"

            async def astream(self, *a, **k):  # pragma: no cover
                yield None

        monkeypatch.setattr(llm, "_eca_chat_model", lambda role, temperature=None: _FakeEca())
        model = llm._guard_byok(_FakeByok(), req, "planner", 0.0)
        asyncio.run(model.ainvoke("hi"))
        after = llm.get_breaker_snapshot("planner")
        assert before == after
    finally:
        _teardown(llm, byok)
