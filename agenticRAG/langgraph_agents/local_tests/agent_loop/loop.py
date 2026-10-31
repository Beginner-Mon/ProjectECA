"""Minimal ReAct loop: probe harness for a small multi-round tool loop (probe only).

The loop sends conversation + tool schemas, runs every tool call the model
makes (deduped by tool-call id — a tool runs at most once per call), appends
results, and repeats until the model answers without tool calls or a cap
bites. Hard caps: 6 rounds, 5 tool calls per round, 12 per turn. The stop
reason is one of:

    answer     the model replied with no tool calls
    round_cap  MAX_ROUNDS exhausted
    call_cap   a round asked for more than MAX_CALLS_PER_ROUND calls, or the
               turn went past MAX_CALLS_PER_TURN

Two client modes (both satisfy the same Client interface below so unit tests
can fake them):

    drop   ChatOpenAI + bind_tools — the model's reasoning text is not sent
           back between rounds (what ECA does today: serialized LangChain
           messages carry no model-specific reasoning).
    keep   the OpenAI SDK directly; each assistant message keeps its
           "reasoning" text and the text is sent back in the next request.
           If the provider rejects that, the exact error is recorded and the
           mode is treated as failed (no workaround).

Fix round 1 (fix-1.md):
    F1  per round the loop records ttft_any_s (first chunk of any kind) and
        ttft_content_s (first non-empty content delta); ttft_final_s is the
        answer round's ttft_content_s.
    F2  per round usage is counted exactly once: totals come from the stream's
        last usage-bearing chunk (last write wins), never from chunk merging
        (merging summed every usage-bearing chunk — smoke found drop ≈ 2× keep
        for identical work). Reasoning-token counts are kept when the provider
        returns them.
    F3  rows carry the reasoning evidence: chars received per round, chars sent
        in the next request, and reasoning_passthrough_supported — true only
        when a request that carried non-empty reasoning succeeded, false when
        one was rejected, null when no request ever carried it.

g0-probes additions (default off, so the reasoning matrix is unchanged):
    extra_system   a second system message right after `system`
    budget_line    "… about N s left" appended to the last tool message of
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

MAX_ROUNDS = 6
MAX_CALLS_PER_ROUND = 5
MAX_CALLS_PER_TURN = 12

ANSWER = "answer"
ROUND_CAP = "round_cap"
CALL_CAP = "call_cap"
ERROR = "error"

SYSTEM_PROMPT = (
    "You are a physical-therapy assistant with three tools: find_exercise(condition), "
    "get_free_slots(date), create_reminder(title, date, time). Dates are YYYY-MM-DD, "
    "times are HH:MM. Work step by step: call a tool when you need a fact, act on the "
    "results of earlier tool calls, and never invent data you can get from a tool. "
    "An identical call returns the identical result: change the call or stop. "
    "An empty result is an answer: there is nothing there. "
    "When everything is known, answer in 2-4 sentences in English, mention the exercise "
    "by name and the exact time if a reminder is involved, and make no further tool calls."
)


@dataclass
class Round:
    """One model round: what the model produced plus timing and usage.

    ttft_any_s     time to the first chunk of any kind (incl. usage-only chunks)
    ttft_content_s time to the first non-empty `content` delta (the first word
                   the user would see); None when the round made tool calls and
                   produced no content
    reasoning_chars_received / _sent   keep-mode evidence (0 in drop mode)
    """

    content: str | None
    tool_calls: list[dict[str, Any]]          # [{"id", "name", "args"}]
    reasoning: str | None = None              # only surfaced in `keep` mode
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None       # provider-provided when available
    latency_s: float = 0.0
    ttft_any_s: float | None = None
    ttft_content_s: float | None = None
    reasoning_chars_received: int = 0
    reasoning_chars_sent: int = 0


@dataclass
class TurnResult:
    """The outcome of one user turn through the loop."""

    stop_reason: str
    final_content: str | None = None
    rounds: int = 0
    call_log: list[dict[str, Any]] = field(default_factory=list)   # executed calls
    per_round_latency: list[float] = field(default_factory=list)
    per_round_ttft_any: list[float | None] = field(default_factory=list)
    per_round_ttft_content: list[float | None] = field(default_factory=list)
    per_round_prompt_tokens: list[int] = field(default_factory=list)
    per_round_completion_tokens: list[int] = field(default_factory=list)
    per_round_reasoning_tokens: list[int | None] = field(default_factory=list)
    ttft_final_s: float | None = None         # the answer round's ttft_content_s
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    reasoning_chars_received: int = 0
    reasoning_chars_sent: int = 0
    reasoning_passthrough_supported: bool | None = None
    error: str | None = None

    @property
    def total_latency_s(self) -> float:
        return round(sum(x for x in self.per_round_latency if x is not None), 3)

    def mode_failed(self) -> bool:
        """`keep`-mode failure per the card: the provider rejected the reasoning passback."""
        return bool(self.error) and "reasoning" in self.error.lower()


class Client:
    """Interface: `complete(messages, schemas) -> Round`, plus assistant assembly."""

    mode: str = "unknown"

    def complete(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> Round:
        raise NotImplementedError

    def append_assistant(
        self, messages: list[dict[str, Any]], rnd: Round
    ) -> None:
        tool_calls = [
            {"type": "function", "id": tc["id"],
             "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
            for tc in rnd.tool_calls
        ]
        msg: dict[str, Any] = {"role": "assistant",
                               "content": rnd.content if rnd.content else None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)


def run_turn(
    client: Client,
    tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
    schemas: list[dict[str, Any]],
    task: str,
    *,
    system: str = SYSTEM_PROMPT,
    max_rounds: int = MAX_ROUNDS,
    extra_system: str | None = None,
    budget_line: bool = False,
    budget_seconds: int = 20,
) -> TurnResult:
    """Run one user task to completion (or a cap) and return how it went.

    extra_system: an optional second system message, placed AFTER `system`
    (probes: AGENT_PROMPT followed by the fixed user-facts block).

    budget_line (probe option, default off — the matrix is unchanged): append
    to the LAST tool message of every round that ran tools a line
    `\n[Round <n> of <max_rounds> · about <s> s left]`, where <s> is
    `budget_seconds` minus the elapsed loop time, rounded down, never below 0.
    """
    result = TurnResult(stop_reason=ERROR)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    if extra_system:
        messages.insert(1, {"role": "system", "content": extra_system})
    t_budget = time.perf_counter() if budget_line else 0.0
    executed_ids: set[str] = set()
    results_by_id: dict[str, Any] = {}
    rounds = 0
    total_calls = 0

    def finish(stop: str, content: str | None = None, error: str | None = None) -> TurnResult:
        result.stop_reason = stop
        result.final_content = content
        result.rounds = rounds
        result.error = error
        return result

    while rounds < max_rounds:
        # F3: does the NEXT request carry non-empty reasoning? KeepClient attaches
        # it to the outgoing assistant message, so the evidence is the last
        # assistant message's "reasoning" — NOT messages[-1], which after a
        # calls-round is a tool result (that was why the first scripted test said null).
        req_reasoning = ""
        for m in reversed(messages):
            if m.get("role") == "assistant":
                req_reasoning = m.get("reasoning") or ""
                break
        try:
            rnd = client.complete(messages, schemas)
        except Exception as exc:  # noqa: BLE001 - the error text is part of the evidence
            err_text = str(exc).lower()
            if req_reasoning and "400" in err_text and "reasoning" in err_text:
                # fix round 3: ONLY an HTTP 400 whose message mentions
                # `reasoning` is a passthrough rejection. Any other error
                # (connection, timeout, 429, ...) leaves the flag as it was:
                # a connection error must not be recorded as a rejection
                # (matrix-v1 row 28), and a later transient failure must not
                # undo an earlier success (True stays True).
                result.reasoning_passthrough_supported = False
            return finish(ERROR, error=f"{type(exc).__name__}: {exc}"[:500])
        if req_reasoning and result.reasoning_passthrough_supported is not False:
            result.reasoning_passthrough_supported = True   # such a request succeeded
        rounds += 1
        result.per_round_latency.append(rnd.latency_s)
        result.per_round_ttft_any.append(rnd.ttft_any_s)
        result.per_round_ttft_content.append(rnd.ttft_content_s)
        result.per_round_prompt_tokens.append(rnd.prompt_tokens or 0)
        result.per_round_completion_tokens.append(rnd.completion_tokens or 0)
        result.per_round_reasoning_tokens.append(rnd.reasoning_tokens)
        result.prompt_tokens += rnd.prompt_tokens or 0
        result.completion_tokens += rnd.completion_tokens or 0
        if rnd.reasoning_tokens is not None:
            result.reasoning_tokens = (result.reasoning_tokens or 0) + rnd.reasoning_tokens
        result.reasoning_chars_received += rnd.reasoning_chars_received
        result.reasoning_chars_sent += len(req_reasoning)

        calls = rnd.tool_calls or []
        if not calls:                       # plain answer, no tool calls
            result.ttft_final_s = rnd.ttft_content_s   # F1: the answer round's content TTFT
            return finish(ANSWER, content=rnd.content or "", error=None)

        if len(calls) > MAX_CALLS_PER_ROUND:
            return finish(CALL_CAP, content=rnd.content or "")
        if total_calls + len(calls) > MAX_CALLS_PER_TURN:
            return finish(CALL_CAP, content=rnd.content or "")

        round_results: list[dict[str, Any]] = []
        executes_something = False
        for tc in calls:
            cid, name, args = tc.get("id"), tc["name"], tc.get("args", {})
            if cid in executed_ids:         # same tool call again: do not re-run the tool
                round_results.append({"id": cid, "result": results_by_id[cid]})
                continue
            executes_something = True
            try:
                out = tools[name](args)
            except Exception as exc:  # noqa: BLE001 - tool errors go back to the model
                out = {"error": f"{type(exc).__name__}: {exc}"[:300]}
            if cid is None:                 # cannot be addressed again; just run it
                cid = f"anon-{len(executed_ids)}"
            executed_ids.add(cid)
            results_by_id[cid] = out
            round_results.append({"id": cid, "result": out})
            total_calls += 1
            result.call_log.append(
                {"round": rounds, "tool": name, "args": args, "result": out}
            )

        # Hand the round back to the model before the tool results.
        try:
            client.append_assistant(messages, rnd)
        except Exception as exc:  # noqa: BLE001 - client-interface drift is an ERROR row
            return finish(ERROR, error=f"append_assistant: "
                                       f"{type(exc).__name__}: {exc}"[:500])
        for item in round_results:
            messages.append({
                "role": "tool",
                "tool_call_id": item["id"],
                "content": json.dumps(item["result"], ensure_ascii=False),
            })
        if budget_line:
            # the last tool message of the round carries the remaining budget
            elapsed = time.perf_counter() - t_budget
            left = max(0, int(budget_seconds - elapsed))
            messages[-1]["content"] = (
                messages[-1]["content"]
                + f"\n[Round {rounds} of {max_rounds} · about {left} s left]"
            )
        if not executes_something:
            # Every call was a repeat of an already-executed one and produced no new
            # information; the model still did not answer, so keep looping (bounded
            # by the round cap) rather than stopping early.
            continue

    return finish(ROUND_CAP, content=None)


# --------------------------------------------------------------------------- #
# mode: drop — ChatOpenAI + bind_tools (ECA today)                             #
# --------------------------------------------------------------------------- #

def _to_lc_messages(messages: list[dict[str, Any]]) -> list[Any]:
    from langchain_core.messages import (  # local import: keeps loop importable without langchain
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )
    out: list[Any] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            out.append(SystemMessage(content=m.get("content") or ""))
        elif role == "user":
            out.append(HumanMessage(content=m.get("content") or ""))
        elif role == "assistant":
            calls = [{"name": tc["function"]["name"],
                      "args": json.loads(tc["function"]["arguments"]),
                      "id": tc["id"],
                      "type": "tool_call"}
                     for tc in m.get("tool_calls", [])]
            out.append(AIMessage(content=m.get("content") or "", tool_calls=calls))
        elif role == "tool":
            out.append(ToolMessage(content=m.get("content") or "", tool_call_id=m["tool_call_id"]))
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown message role: {role}")
    return out


def _reasoning_tokens_from_usage_meta(usage: dict[str, Any] | None) -> int | None:
    if not usage:
        return None
    details = (usage or {}).get("output_token_details") or {}
    n = details.get("reasoning")
    return n if n is not None else None


def reduce_chat_stream(
    chunks: Iterable[Any], t0: float
) -> tuple[str | None, list[dict[str, Any]], dict[str, Any], float | None, float | None]:
    """Reduce a ChatOpenAI chunk stream to (content, tool_calls, usage, ttft_any, ttft_content).

    F2: usage comes from the stream's LAST usage-bearing chunk — last write
    wins, never summed. (The smoke found `full = full + chunk` sums
    `usage_metadata` of every usage-bearing chunk: drop reported ~1.9x keep's
    tokens for identical work.)
    """
    full: Any = None
    last_usage: dict[str, Any] = {}
    ttft_any: float | None = None
    ttft_content: float | None = None
    for chunk in chunks:
        now = time.perf_counter()
        if ttft_any is None:
            ttft_any = now - t0
        piece = getattr(chunk, "content", None)
        if isinstance(piece, str) and piece and ttft_content is None:
            ttft_content = now - t0
        um = getattr(chunk, "usage_metadata", None)
        if um:
            last_usage = um          # do NOT let the merge sum it
        full = chunk if full is None else full + chunk
    tool_calls = [
        {"id": tc.get("id") or "", "name": tc["name"], "args": dict(tc.get("args") or {})}
        for tc in (getattr(full, "tool_calls", None) or [])
    ]
    content = full.content if isinstance(full.content, str) else None
    return content, tool_calls, last_usage, ttft_any, ttft_content


class DropClient(Client):
    """ChatOpenAI + bind_tools: reasoning exists inside the request, but is not
    carried back in any message (serialized LangChain messages have no reasoning).

    F2: usage is taken from the stream's LAST usage-bearing chunk (via
    `reduce_chat_stream`), exactly once per round.
    """

    mode = "drop"

    def __init__(self, api_key: str, base_url: str, model: str, reasoning_effort: str,
                 max_tokens: int = 1500, timeout: float = 120.0) -> None:
        from langchain_openai import ChatOpenAI

        self.chat = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            streaming=True,
            timeout=timeout,
            max_retries=0,
            max_tokens=max_tokens,
            stream_usage=True,
            reasoning_effort=reasoning_effort,
        )

    def complete(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> Round:
        bound = self.chat.bind_tools(schemas) if schemas else self.chat
        lc = _to_lc_messages(messages)
        t0 = time.perf_counter()
        content, tool_calls, usage, ttft_any, ttft_content = reduce_chat_stream(
            bound.stream(lc), t0)
        latency = time.perf_counter() - t0
        return Round(
            content=content,
            tool_calls=tool_calls,
            reasoning=None,      # ChatOpenAI (ECA today): nowhere to carry reasoning
            prompt_tokens=usage.get("input_tokens") or 0,
            completion_tokens=usage.get("output_tokens") or 0,
            reasoning_tokens=_reasoning_tokens_from_usage_meta(usage),
            latency_s=latency,
            ttft_any_s=ttft_any,
            ttft_content_s=ttft_content,
            reasoning_chars_received=0,
            reasoning_chars_sent=0,
        )

    def append_assistant(self, messages: list[dict[str, Any]], rnd: Round) -> None:
        # ChatOpenAI drops reasoning: serialize the assistant turn plainly.
        super().append_assistant(messages, rnd)


# --------------------------------------------------------------------------- #
# mode: keep — OpenAI SDK directly, reasoning sent back between rounds        #
# --------------------------------------------------------------------------- #

class KeepClient(Client):
    """OpenAI SDK called directly: each assistant message keeps its "reasoning"
    text and it is sent back in the next request. If the provider rejects a
    request because of that field, the raised error text is the evidence."""

    mode = "keep"

    def __init__(self, api_key: str, base_url: str, model: str, reasoning_effort: str,
                 max_tokens: int = 1500, timeout: float = 120.0) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens

    def complete(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> Round:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": self.max_tokens,
            "reasoning_effort": self.reasoning_effort,
            "stream_options": {"include_usage": True},
        }
        if schemas:
            kwargs["tools"] = schemas
        t0 = time.perf_counter()
        ttft_any: float | None = None
        ttft_content: float | None = None
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage: Any = None
        stream = self.client.chat.completions.create(**kwargs)
        # the count of what the round's RESPONSE carried (F3)
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage            # last write wins (final chunk, Groq style)
            if not getattr(chunk, "choices", None):
                continue
            delta = chunk.choices[0].delta
            now = time.perf_counter()
            if ttft_any is None:
                ttft_any = now - t0
            piece = getattr(delta, "content", None)
            if piece:
                if ttft_content is None:
                    ttft_content = now - t0
                content.append(piece)
            extra = getattr(delta, "model_extra", None) or {}
            text = extra.get("reasoning") or getattr(delta, "reasoning_content", None) \
                or getattr(delta, "reasoning", None)
            if text:
                reasoning.append(text)
            for tc in (getattr(delta, "tool_calls", None) or []):
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "args": []})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"].append(tc.function.arguments)
        latency = time.perf_counter() - t0
        tool_calls = [
            {"id": slot["id"], "name": slot["name"], "args": json.loads("".join(slot["args"]))}
            for _, slot in sorted(calls.items())
        ]
        details = getattr(usage, "completion_tokens_details", None)
        return Round(
            content="".join(content) or None,
            tool_calls=tool_calls,
            reasoning="".join(reasoning) or None,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            reasoning_tokens=(getattr(details, "reasoning_tokens", None)
                              if details is not None else None),
            latency_s=latency,
            ttft_any_s=ttft_any,
            ttft_content_s=ttft_content,
            reasoning_chars_received=sum(len(chunk) for chunk in reasoning),
            reasoning_chars_sent=0,     # a request's attached reasoning is loop-counted
        )

    def append_assistant(self, messages: list[dict[str, Any]], rnd: Round) -> None:
        super().append_assistant(messages, rnd)
        if rnd.reasoning:
            # pass the same reasoning text back so the model knows what it thought
            messages[-1]["reasoning"] = rnd.reasoning
