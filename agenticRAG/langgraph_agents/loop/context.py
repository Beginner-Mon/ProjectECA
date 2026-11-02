"""The `load_context` node for the read-only loop.

Builds, in order, all as transcript dicts (OpenAI format, no LangChain
message objects):

  1. [system] AGENT_SYSTEM
  2. [system] instructions and facts (_build_user_instructions, _build_user_facts
     from nodes/synthesizer.py)
  3. chat history as plain user/assistant text
  4. [user] the current message
  5. a synthetic [assistant] tool_call `load_context` + a [tool] result

That tool result holds a locally-built session summary and the traces of the
last two loop turns (from meta.loop), each labelled with its source.

External text never goes into a system message.

Also refreshes the turn's taint snapshot (banned_terms / allowed_terms) from
the instructions, facts and current message.
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.runnables import RunnableConfig

from langgraph_agents.loop.limits import ensure_turn, _msg_content, _msg_role, _msg_tool_calls
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.taint import build_taint
from langgraph_agents.loop.trace import log_turn
from langgraph_agents.loop.prompts import AGENT_SYSTEM
from langgraph_agents.nodes.synthesizer import (
    _build_user_facts, _build_user_instructions,
)
from langgraph_agents.shared.context import budget_chars
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.context")


def _local_session_summary(chunks: list[str]) -> str:
    """Session-summary text built locally (no header).

    Same newest-first keep/cap logic as _build_session_summary, but without
    the wrapper header, because the summary is delivered inside a tool
    result rather than as a system block.
    """
    limit = budget_chars("session_summary")
    kept: list[str] = []
    used = 0
    for chunk in reversed(chunks or []):
        cost = len(chunk) + (2 if kept else 0)
        if kept and used + cost > limit:
            break
        kept.insert(0, chunk)
        used += cost
    content = "\n\n".join(kept)[:limit]
    return content.strip()


def _recent_loop_traces(config: dict) -> list[dict]:
    """Read the last two loop-turn traces for this session.

    Local read via `get_pg_client()` wrapped in `pg.transaction()` (RLS needs
    the bound user). Fails soft → [] when the DB is unavailable (tests).
    Never raises.
    """
    try:
        from langgraph_agents.shared import get_pg_client
        pg = get_pg_client()
    except Exception:  # noqa: BLE001
        return []
    try:
        # Best-effort: table may not exist until migration 021 is applied.
        import asyncio

        async def _read() -> list[dict]:
            async with pg.transaction():
                rows = await pg.fetch(
                    "SELECT loop_trace FROM loop_turns "
                    "WHERE session_id = $1::uuid "
                    "ORDER BY updated_at DESC LIMIT 2",
                    (config.get("configurable") or {}).get("session_id"),
                )
            return [dict(r["loop_trace"]) for r in rows if r["loop_trace"]]

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # We're inside an event loop already; schedule a fresh one is not
            # possible. Return [] (the traces are a nice-to-have).
            return []
        return asyncio.run(_read())
    except Exception:  # noqa: BLE001
        return []


async def _prepare_plugins(turn, configurable: dict, request_id: str) -> None:
    """Stage 3: decide the plugin tools of this turn (D1, D3). One store query
    and cached definitions; never raises, never fails the turn. Skipped when a
    test or probe injected `turn_tools` (that list replaces every tool)."""
    if configurable.get("turn_tools") is not None:
        return
    try:
        from langgraph_agents.loop.config import load_settings
        from langgraph_agents.loop.plugins import runtime

        settings = configurable.get("loop_settings") or load_settings()
        turn.plugins = await runtime.prepare_turn(
            user_id=str(configurable.get("user_id", "-")),
            request_id=request_id,
            settings=settings,
            catalog=configurable.get("plugin_catalog"),
            web_search=bool(configurable.get("web_search", False)),
        )
    except Exception as exc:  # noqa: BLE001 - plugins are optional
        logger.warning("plugins_prepare_failed", extra={
            "request_id": request_id, "error": type(exc).__name__})


async def load_context_node(state: LoopState, config: RunnableConfig) -> dict:
    t0 = time.perf_counter()
    turn = ensure_turn(config)
    configurable = config.get("configurable") or {}
    request_id = configurable.get("request_id", "-")
    user_id = configurable.get("user_id", "-")
    session_id = configurable.get("session_id", "-")
    query = configurable.get("query", "")

    await _prepare_plugins(turn, configurable, request_id)

    # Memory-node outputs (reused verbatim).
    user_instructions = state.get("user_instructions") or []
    user_facts = state.get("user_facts") or []
    session_summary_chunks = state.get("session_summary") or []

    # Refresh the taint snapshot each round (agent node re-does it too).
    taint = build_taint(user_instructions, user_facts, query)
    turn.banned_terms = list(taint.banned_terms)
    turn.allowed_terms = list(taint.allowed_terms)

    # Chat history as plain user/assistant dicts (external text, not system).
    history: list[dict[str, Any]] = []
    for m in state.get("transcript") or []:
        role = _msg_role(m)
        if role in ("user", "assistant") and not _msg_tool_calls(m):
            history.append({"role": role, "content": _msg_content(m)})

    transcript: list[dict[str, Any]] = []

    # 1. [system] AGENT_SYSTEM
    transcript.append({"role": "system", "content": AGENT_SYSTEM})

    # 2. [system] instructions + facts
    mem_block = _build_user_instructions(user_instructions) + _build_user_facts(user_facts)
    if mem_block.strip():
        transcript.append({"role": "system", "content": mem_block.strip()})

    # 3. chat history as plain user/assistant text
    transcript.extend(history)

    # 4. [user] the current message
    transcript.append({"role": "user", "content": query})

    # 5. synthetic [assistant] tool_call load_context + [tool] result
    summary_text = _local_session_summary(session_summary_chunks)
    traces = _recent_loop_traces(config)
    trace_parts = []
    if summary_text:
        trace_parts.append(f"[source:session_summary]\n{summary_text}")
    for tr in traces:
        trace_parts.append(f"[source:loop_trace:{tr.get('request_id','?')}]\n"
                           f"rounds={tr.get('rounds')} status={tr.get('turn_status')}")
    tool_result_content = "\n\n".join(trace_parts) if trace_parts else '{"found": false}'

    transcript.append({
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "load_context", "type": "function",
                        "function": {"name": "load_context", "arguments": "{}"}}],
    })
    transcript.append({
        "role": "tool",
        "content": tool_result_content,
        "tool_call_id": "load_context",
    })

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    plugins = getattr(turn, "plugins", None)
    fields: dict = {"node": "load_context", "elapsed_ms": elapsed_ms}
    if plugins is not None and (plugins.unavailable or plugins.changed):
        fields["plugins_unavailable"] = list(plugins.unavailable)
        fields["plugins_definition_changed"] = list(plugins.changed)
    log_turn(request_id, fields)

    return {
        "transcript": transcript,
        "turn_status": state.get("turn_status", "completed"),
    }
