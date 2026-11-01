"""Tests for `loop.client.AgentModel`: reasoning echo (both field names) and BYOK."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from langgraph_agents.loop.client import AgentModel, AgentReply


class _FakeMessage:
    def __init__(self, content="", tool_calls=None, reasoning_field=None,
                 reasoning_text=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.model_extra = {}
        if reasoning_field == "reasoning":
            self.model_extra["reasoning"] = reasoning_text
        elif reasoning_field == "reasoning_content":
            self.model_extra["reasoning_content"] = reasoning_text


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]
        self.usage = None


class _FakeCompletions:
    def __init__(self, response):
        self._response = response

    async def create(self, **kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.chat = type("C", (), {"completions": _FakeCompletions(response)})()


def _make_reply(content="", tool_calls=None, reasoning_field=None,
                reasoning_text=None):
    msg = _FakeMessage(content, tool_calls, reasoning_field, reasoning_text)
    return _FakeResponse(msg)


def test_reasoning_echo_reasoning_field():
    client = _FakeClient(_make_reply("hello", reasoning_field="reasoning",
                                     reasoning_text="Let me think"))
    model = AgentModel(client=client)
    reply = asyncio.run(model.complete([{"role": "user", "content": "hi"}]))
    assert reply.reasoning_field == "reasoning"
    assert reply.reasoning_text == "Let me think"
    assert reply.content == "hello"


def test_reasoning_echo_reasoning_content_field():
    client = _FakeClient(_make_reply("hello", reasoning_field="reasoning_content",
                                     reasoning_text="reasoning here"))
    model = AgentModel(client=client)
    reply = asyncio.run(model.complete([{"role": "user", "content": "hi"}]))
    assert reply.reasoning_field == "reasoning_content"
    assert reply.reasoning_text == "reasoning here"


def test_tool_calls_parsed():
    tc = type("TC", (), {"id": "tc1", "function": type("F", (), {"name": "kb_search",
        "arguments": '{"query": "x"}'})()})()
    client = _FakeClient(_make_reply("", tool_calls=[tc]))
    model = AgentModel(client=client)
    reply = asyncio.run(model.complete(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "kb_search",
                                                "parameters": {}}}],
        tool_choice="auto",
    ))
    assert reply.tool_calls == [{"id": "tc1", "name": "kb_search",
                                 "arguments": '{"query": "x"}'}]


# B3: one AsyncOpenAI client per turn, reused across rounds.
def test_client_reused_per_turn(monkeypatch):
    import openai

    created = []

    class _TrackingAsyncOpenAI(openai.AsyncOpenAI):
        def __init__(self, *a, **k):
            created.append(k)
            super().__init__(*a, **k)

    completions = _FakeCompletions(_FakeResponse(_FakeMessage("ok")))

    def fake_chat(self):
        return type("C", (), {"completions": completions})()

    with patch.object(openai, "AsyncOpenAI", _TrackingAsyncOpenAI), \
            patch.object(openai.AsyncOpenAI, "chat", property(fake_chat)):
        model = AgentModel()
        reply1 = asyncio.run(model.complete(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}],
        ))
        reply2 = asyncio.run(model.complete(
            [{"role": "user", "content": "hi again"}],
            tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}],
        ))
    # Same base_url + key → one client created and reused across rounds.
    assert len(created) == 1, created
    assert reply1.content == "ok" and reply2.content == "ok"


# A9: correct usage fields (prompt_tokens / completion_tokens / total_tokens).
def test_usage_fields_are_chat_completions_shape():
    msg = _FakeMessage("hello")
    response = _FakeResponse(msg)
    response.usage = type("U", (), {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    })()
    client = _FakeClient(response)
    model = AgentModel(client=client)
    reply = asyncio.run(model.complete([{"role": "user", "content": "hi"}]))
    assert reply.usage is not None
    assert "prompt_tokens" in reply.usage
    assert "completion_tokens" in reply.usage
    assert "total_tokens" in reply.usage
    assert "input_tokens" not in reply.usage
    assert "output_tokens" not in reply.usage
    assert reply.usage["total_tokens"] == 15


def test_byok_endpoint_used():
    """When a BYOK request with the 'tools' capability is bound, AsyncOpenAI is
    constructed with the user's base_url and api_key."""
    import openai  # noqa: F401  (patched below)
    from langgraph_agents.llm_byok import bind_request_llm, RequestLLM

    req = RequestLLM(
        base_url="https://user.example.com/v1",
        model="user-model",
        api_key="sk-user",
        capabilities=frozenset({"text", "tools"}),
        omit=frozenset(),
        context_window=None,
    )
    bind_request_llm(req)
    try:
        import langgraph_agents.loop.client as client_mod
        created = {}
        real_init = openai.AsyncOpenAI.__init__

        def tracking_init(self, *a, **k):
            created["base_url"] = k.get("base_url")
            created["api_key_set"] = bool(k.get("api_key"))
            return real_init(self, *a, **k)

        completions = _FakeCompletions(_FakeResponse(_FakeMessage("byok answer")))

        def fake_chat(self):
            return type("C", (), {"completions": completions})()

        # AgentModel._call_once does `import openai` locally, so patch the
        # real openai module's AsyncOpenAI.
        with patch.object(openai.AsyncOpenAI, "__init__", tracking_init), \
             patch.object(openai.AsyncOpenAI, "chat", property(fake_chat)):
            model = AgentModel()
            reply = asyncio.run(model.complete([{"role": "user", "content": "hi"}]))

        assert created["base_url"] == "https://user.example.com/v1", created
        assert created["api_key_set"] is True
        assert reply.content == "byok answer"
    finally:
        bind_request_llm(None)
