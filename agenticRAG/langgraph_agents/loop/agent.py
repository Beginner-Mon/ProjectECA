"""The `agent` node: one model call per round, then route.

Round start: cancel check → stop_reason (time/tokens) stop-note path →
tool_choice auto/none → model call → assistant append with reasoning echo
→ plan_round → route.

Routing is decided by `route_after_agent` in graph.py: this node only
produces state updates. When the model answers without a tool call, write
`agent_result`. An empty answer gets a code-written fallback (§5 rule: the
stop message goes to `agent_result`, not to messages). If the model returns
calls despite `tool_choice="none"`, write `agent_result` and set
`unanswered_tool_call_ids` so graph.py routes to persona WITHOUT answering
those ids (GĐ0-style stop).

The model is injected via `config["configurable"]["agent_model"]` (tests
use `FakeAgentModel`); production uses `AgentModel`. The verdict is fetched
ONCE per round via the injectable `verdict_getter`
(default `guardrail.handoff.current_verdict`); tests inject a routine
verdict.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph_agents.loop.client import AgentModel, AgentReply
from langgraph_agents.loop.limits import (
    ensure_turn, _msg_content, _msg_role, _msg_tool_calls,
)
from langgraph_agents.loop.outcome import stop_note
from langgraph_agents.loop.prompts import budget_line
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.tools.registry import build_turn_tools, to_openai_tools
from langgraph_agents.loop.trace import log_turn
from langgraph_agents.shared.context import estimate_tokens
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.agent")


async def agent_node(state: LoopState, config: RunnableConfig) -> dict:
    turn = ensure_turn(config)
    request_id = (config.get("configurable") or {}).get("request_id", "-")
    turn.plan_round()

    # ── 1. cancel check ───────────────────────────────────────────
    cancel_getter = (config.get("configurable") or {}).get("cancel_checker")
    cancelled = False
    if cancel_getter is not None:
        try:
            cancelled = bool(await cancel_getter(config))
        except Exception:
            cancelled = False
    turn.cancelled = cancelled
    if cancelled:
        agent_result = stop_note("cancelled", _locale(config))
        return {
            "agent_result": agent_result,
            "turn_status": "cancelled",
            "rounds": int(state.get("rounds", 0)) + 1,
            "total_tokens": 0,
        }

    # ── 2. stop_reason: time / token caps ─────────────────────────
    caps = turn.caps
    if turn.budget.elapsed() > caps.loop_time_s:
        agent_result = stop_note("time", _locale(config))
        return {
            "agent_result": agent_result,
            "turn_status": "budget_exceeded",
            "rounds": int(state.get("rounds", 0)) + 1,
            "total_tokens": 0,
        }
    transcript_text = " ".join(
        _msg_content(m) for m in (state.get("transcript") or [])
    )
    input_tokens = estimate_tokens(transcript_text)
    if input_tokens >= caps.max_input_tokens:
        agent_result = stop_note("tokens", _locale(config))
        return {
            "agent_result": agent_result,
            "turn_status": "budget_exceeded",
            "rounds": int(state.get("rounds", 0)) + 1,
            "total_tokens": 0,
        }

    # ── 3. tool_choice ────────────────────────────────────────────
    tool_choice = "none" if turn.force_no_tools else "auto"

    # ── 4. model call ─────────────────────────────────────────────
    agent_model = (config.get("configurable") or {}).get("agent_model") or AgentModel()
    specs = build_turn_tools(config.get("configurable") or {})
    openai_tools = to_openai_tools(specs)

    transcript = list(state.get("transcript") or [])
    try:
        reply: AgentReply = await agent_model.complete(
            transcript, openai_tools, tool_choice=tool_choice,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_model_failed", extra={"request_id": request_id, "error": str(exc)})
        agent_result = stop_note("empty", _locale(config))
        return {
            "agent_result": agent_result,
            "turn_status": "budget_exceeded",
            "errors": list(state.get("errors") or []) + [{
                "node": "agent", "severity": "critical", "message": str(exc),
            }],
            "rounds": int(state.get("rounds", 0)) + 1,
            "total_tokens": 0,
        }

    # A9: the LLM usage for this round, charged to the quota.
    round_tokens = int((reply.usage or {}).get("total_tokens") or 0)

    # ── 5. assistant append + reasoning echo ──────────────────────
    # A8: reasoning goes into its own field (the provider's keep-mode field),
    # never into `content`. content is the model's own text only. Reasoning
    # never enters loop_trace, logs or meta.
    content = reply.content or ""
    assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
    if reply.reasoning_field and reply.reasoning_text is not None:
        assistant_msg[reply.reasoning_field] = reply.reasoning_text
    if reply.tool_calls:
        # LangChain's add_messages reducer expects tool_calls in the nested
        # format {id, type, function: {name, arguments}}; AgentReply carries
        # them flat, so convert on the way into the transcript.
        assistant_msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc.get("name"), "arguments": tc.get("arguments", "{}")}}
            for tc in reply.tool_calls
        ]

    new_transcript = [*transcript, assistant_msg]
    rounds = int(state.get("rounds", 0)) + 1

    # ── 6. plan_round: register each call's position ──────────────
    tool_calls = reply.tool_calls or []
    for tc in tool_calls:
        turn.call_position(tc["id"], tc.get("name", "?"))

    # ── 7. produce state; graph.py routes via the explicit _route field ─────
    if not tool_calls:
        agent_result = content or stop_note("empty", _locale(config))
        return {
            "transcript": new_transcript,
            "agent_result": agent_result,
            "rounds": rounds,
            "turn_status": "completed",
            "_route": "persona",
            "total_tokens": round_tokens,
        }

    # tool_choice="none" but the model called tools anyway: GĐ0-style stop —
    # write agent_result, route to persona, do NOT answer those ids.
    if tool_choice == "none":
        agent_result = content or stop_note("empty", _locale(config))
        return {
            "transcript": new_transcript,
            "agent_result": agent_result,
            "rounds": rounds,
            "turn_status": "completed",
            "unanswered_tool_call_ids": [tc["id"] for tc in tool_calls],
            "_route": "persona",
            "total_tokens": round_tokens,
        }

    # Read calls → tools_read.
    return {
        "transcript": new_transcript,
        "rounds": rounds,
        "_route": "tools_read",
        "total_tokens": round_tokens,
    }


def _locale(config: dict) -> str:
    return (config.get("configurable") or {}).get("locale", "en")
