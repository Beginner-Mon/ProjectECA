"""Agent model calls for the read-only loop.

`AgentModel.complete(messages, tools, *, tool_choice="auto") -> AgentReply`.
Calls go through `AsyncOpenAI`. The endpoint comes from
`llm.endpoint_for_role("agent")` (new), or from the BYOK
`current_request_llm()` when that covers `tools`. The provider's reasoning
field (`reasoning` or `reasoning_content`) is echoed on the assistant
message for the next round, as the keep mode of
`local_tests/agent_loop/loop.py` does.

Wrapped in `CircuitBreaker` (`core/circuit_breaker.py`); one retry on a
transient error.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import openai  # declared dependency (openai>=2.0,<3)

from langgraph_agents.core.circuit_breaker import CircuitBreaker
from langgraph_agents.llm import (
    classify_provider_error, current_request_llm, endpoint_for_role,
)
from langgraph_agents.loop.config import load_settings
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.client")


@dataclass
class AgentReply:
    """What the agent model returned in one round."""
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    reasoning_field: str | None = None
    reasoning_text: str | None = None
    usage: dict[str, Any] | None = None


def to_wire_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Render a transcript as OpenAI wire dicts.

    The graph's `transcript` key goes through LangGraph's `add_messages`
    reducer, so by the time a node reads it the dicts are LangChain message
    objects (`AIMessage.tool_calls` as `{name, args, id}`, the echoed
    reasoning in `additional_kwargs`). The provider needs plain dicts, so this
    puts them back: tool calls as `{id, type, function: {name, arguments}}`
    and the reasoning field under its original key. Dicts pass through.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append(m)
            continue
        kind = getattr(m, "type", None)
        role = {"human": "user", "ai": "assistant"}.get(kind, kind)
        wire: dict[str, Any] = {"role": role, "content": getattr(m, "content", "") or ""}
        if role == "tool":
            wire["tool_call_id"] = getattr(m, "tool_call_id", None)
        if role == "assistant":
            calls = []
            for tc in getattr(m, "tool_calls", None) or []:
                args = tc.get("args", {})
                calls.append({
                    "id": tc.get("id"), "type": "function",
                    "function": {"name": tc.get("name"),
                                 "arguments": args if isinstance(args, str)
                                 else json.dumps(args, ensure_ascii=False)},
                })
            for tc in getattr(m, "invalid_tool_calls", None) or []:
                calls.append({
                    "id": tc.get("id"), "type": "function",
                    "function": {"name": tc.get("name"),
                                 "arguments": tc.get("args") or "{}"},
                })
            if calls:
                wire["tool_calls"] = calls
            extra = getattr(m, "additional_kwargs", None) or {}
            for key in ("reasoning", "reasoning_content"):
                if extra.get(key) is not None:
                    wire[key] = extra[key]
        out.append(wire)
    return out


class _TransientError(RuntimeError):
    """A retryable provider error (unreachable / server / 5xx)."""


class ModelToolCallInvalid(RuntimeError):
    """The provider rejected the model's own tool call twice (400
    `tool_use_failed`, e.g. Groq's "Failed to parse tool call arguments as
    JSON"). Not a cap: the agent node ends the turn with `turn_status:
    failed`, `error_code: model_tool_call_invalid`."""


def is_tool_use_failed(exc: BaseException) -> bool:
    """A 400 whose error code is `tool_use_failed` (openai SDK shapes: the
    `code` attribute, or the error object / envelope in `body`)."""
    if getattr(exc, "status_code", None) != 400:
        return False
    if getattr(exc, "code", None) == "tool_use_failed":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        return err.get("code") == "tool_use_failed"
    return False


class AgentModel:
    """Scripted in tests (FakeAgentModel), real on AsyncOpenAI in production.

    One AsyncOpenAI client per turn (keyed by base URL + key) so every round
    reuses the same TCP/TLS connection instead of paying a fresh handshake.
    """

    def __init__(
        self,
        *,
        role: str = "agent",
        client: Any = None,
        breaker: CircuitBreaker | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.role = role
        self._client = client
        # Probe hook: an explicit endpoint replaces `endpoint_for_role` and
        # BYOK. Production callers pass none of these.
        self._explicit_endpoint: dict | None = None
        if base_url is not None:
            self._explicit_endpoint = {
                "base_url": base_url, "key_env": None, "key_param_env": None,
                "model": model, "extra_body": dict(extra_body or {}),
                "byok": False, "api_key": api_key,
            }
        self._breaker = breaker or CircuitBreaker(
            name=f"loop:{role}", failure_threshold=3, cool_down_seconds=30.0,
        )
        self._clients: dict[str, Any] = {}

    async def aclose(self) -> None:
        """Close the cached `AsyncOpenAI` clients. Production (one event loop
        per process) never calls this; a runner that uses one `asyncio.run`
        per run does, so no client outlives its loop."""
        clients, self._clients = list(self._clients.values()), {}
        for c in clients:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                logger.debug("agent_client_close_failed")

    # ── endpoint resolution ──────────────────────────────────────────

    def _resolve_endpoint(self) -> dict:
        """BYOK when bound and capable of tools, else ECA endpoint_for_role."""
        if self._explicit_endpoint is not None:
            return dict(self._explicit_endpoint)
        req = current_request_llm()
        if (req is not None and req.failure is None
                and "tools" in getattr(req, "capabilities", frozenset())):
            return {
                "base_url": req.base_url,
                "key_env": None,  # BYOK key comes from req.api_key
                "key_param_env": None,
                "model": req.model,
                "extra_body": {},
                "byok": True,
                "api_key": req.api_key,
            }
        from langgraph_agents.loop.config import load_settings
        ep = endpoint_for_role(
            self.role,
            reasoning_effort=load_settings().reasoning_effort,
        )
        ep["byok"] = False
        return ep

    # ── completion ───────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | None = "auto",
    ) -> AgentReply:
        ep = self._resolve_endpoint()
        last_exc: BaseException | None = None
        for attempt in range(1 + 1):  # first try + one retry on transient
            try:
                if not self._breaker.allow():
                    raise _TransientError(
                        f"circuit breaker open for loop:{self.role}")
                reply = await self._call_once(ep, messages, tools, tool_choice)
                self._breaker.record_success()
                return reply
            except _TransientError as exc:
                last_exc = exc
                self._breaker.record_failure()
                if attempt == 0:
                    continue
                raise
            except Exception as exc:  # noqa: BLE001
                if is_tool_use_failed(exc):
                    # The model produced a malformed call: transient, one retry.
                    last_exc = exc
                    self._breaker.record_failure()
                    if attempt == 0:
                        continue
                    raise ModelToolCallInvalid(
                        "provider rejected the tool call twice "
                        "(tool_use_failed)") from exc
                classified = classify_provider_error(exc)
                if classified["kind"] in ("unreachable", "server"):
                    last_exc = exc
                    self._breaker.record_failure()
                    if attempt == 0:
                        continue
                self._breaker.record_failure()
                raise
        # Unreachable: the loop above always returns or raises.
        raise last_exc or RuntimeError("AgentModel.complete failed without error")

    async def _call_once(
        self,
        ep: dict,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        tool_choice: str | None,
    ) -> AgentReply:
        api_key = ep.get("api_key")
        if api_key is None:
            key_env = ep.get("key_env")
            if key_env:
                api_key = os.getenv(key_env)
            if not api_key:
                raise RuntimeError(
                    f"loop: no API key for role {self.role!r} — set {key_env} "
                    "or bind a BYOK key with the 'tools' capability")

        client_key = f"{ep['base_url']}|{api_key or ''}"
        client = self._client
        if client is None:
            client = self._clients.get(client_key)
            if client is None:
                client = openai.AsyncOpenAI(
                    api_key=api_key,
                    base_url=ep["base_url"],
                    timeout=20.0,
                    max_retries=0,
                )
                self._clients[client_key] = client

        create_kwargs: dict[str, Any] = {
            "model": ep["model"],
            "messages": to_wire_messages(messages),
        }
        if tools:
            create_kwargs["tools"] = tools
            create_kwargs["tool_choice"] = tool_choice or "none"
        extra_body = dict(ep.get("extra_body") or {})
        if extra_body:
            create_kwargs["extra_body"] = extra_body

        response = await client.chat.completions.create(**create_kwargs)
        return self._parse(response)

    def _parse(self, response: Any) -> AgentReply:
        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""

        # Reasoning: OpenAI puts it in model_extra["reasoning"], DeepSeek in
        # reasoning_content. Record which field was present.
        reasoning_field: str | None = None
        reasoning_text: str | None = None
        extra = getattr(msg, "model_extra", None) or {}
        if isinstance(extra, dict):
            if extra.get("reasoning"):
                reasoning_field = "reasoning"
                reasoning_text = extra["reasoning"]
            elif extra.get("reasoning_content"):
                reasoning_field = "reasoning_content"
                reasoning_text = extra["reasoning_content"]

        tool_calls: list[dict[str, Any]] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments or "{}",
            })

        usage = None
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
            details = getattr(response.usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", None)
            if isinstance(reasoning_tokens, int):
                usage["reasoning_tokens"] = reasoning_tokens

        return AgentReply(
            content=content,
            tool_calls=tool_calls,
            reasoning_field=reasoning_field,
            reasoning_text=reasoning_text,
            usage=usage,
        )
