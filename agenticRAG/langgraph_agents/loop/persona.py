"""The `persona` node: the loop's universal responder (plan §6).

Builds the §6 prompt by concatenation (never `.format` — persona text
contains braces):

    build_persona_prompt(persona, "synthesize")
    + "\n---\n"
    + _build_user_instructions(...) + _build_user_facts(...)
    + "\n## This turn\n"
    + "The user said: {user_message}"
    + "What was found and done for them (internal notes):\n{agent_result}"
    + "From ECA's library:\n{internal_evidence}"
    + "Say this to the user..." clause
    + _LANGUAGE_RULE
    + _build_contract_note(head_text, safety_keys(verdict, []))
    + directive(verdict)

Streams tokens through the LangGraph custom writer with the same payload
keys as `nodes/synthesizer.py` (`{"stage": "synthesizer_started"}`,
`{"emotion": …}`, `{"content": …}`), so `_stream_chat` needs no new token
path. Sets `final_answer`. Emotion via `safety_keys(verdict, [])`.

After the body: outcome lines (only when the turn is remarkable), then the
contract note lives in the system prompt. Evidence comes from internal
tools only. On exception → `get_ui_string(persona_id, "error_unavailable",
locale)` + errors entry (no gemini fallback in stage 1).

The model is injected via `config["configurable"]["persona_model"]` (tests
use `FakeAgentModel`); production uses `AgentModel`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from langgraph_agents.loop.agent import AgentModel
from langgraph_agents.loop.client import AgentReply
from langgraph_agents.loop.limits import (
    ensure_turn, _msg_content, _msg_role, _msg_tool_call_id, _msg_tool_calls,
)
from langgraph_agents.loop.prompts import _SAY_CLAUSE
from langgraph_agents.loop.outcome import outcome_lines, stop_note, EMPTY_ANSWER
from langgraph_agents.loop.state import LoopState
from langgraph_agents.loop.trace import log_turn
from langgraph_agents.shared.context import budget_chars
from langgraph_agents.shared.logging import get_logger
from langgraph_agents.shared import reply_emotion

from langgraph_agents.guardrail.directive import directive
from langgraph_agents.guardrail.handoff import current_verdict
from langgraph_agents.guardrail.stream import head, safety_keys
from langgraph_agents.evidence import EVIDENCE_PER_MESSAGE_CAP
from langgraph_agents.nodes._errors import error_entry, ErrorSeverity
from langgraph_agents.nodes._persona_loader import (
    build_persona_prompt, build_voice_card, get_persona, get_ui_string,
)
from langgraph_agents.nodes.synthesizer import (
    _build_user_facts, _build_user_instructions,
)

logger = get_logger("langgraph.loop.persona")


_INTERNAL_TOOLS = ("kb_search", "recall_self")
_PARTIAL_WRITE = frozenset({"failed", "unknown", "declined", "expired", "invalidated"})


def _internal_evidence(transcript: list[dict], limit_chars: int,
                       call_registry: dict[str, str]) -> str:
    """Render evidence from internal tools only (kb_search, recall_self).

    Capped via budget_chars("evidence") plus a per-message cap (~1500).
    Tool names are looked up via the turn's call registry (tool messages
    carry no `name` key by design).
    """
    pieces: list[str] = []
    used = 0
    for m in transcript:
        if _msg_role(m) != "tool":
            continue
        tcid = _msg_tool_call_id(m)
        name = call_registry.get(tcid) if tcid else None
        if name not in _INTERNAL_TOOLS:
            continue
        content = _msg_content(m)
        # Skip refused/empty results.
        if content in ("", "[]", "{}", '{"found": false}') or '"error"' in content:
            continue
        text = content[:EVIDENCE_PER_MESSAGE_CAP]
        if used + len(text) > limit_chars:
            break
        pieces.append(f"[From {name}]\n{text}")
        used += len(text)
    return "\n\n".join(pieces)


async def persona_node(state: LoopState, config: RunnableConfig) -> dict:
    """The persona node. Every path ends here, so the plugin sessions of the
    turn are closed here (D2), after the answer has been produced."""
    try:
        return await _persona_body(state, config)
    finally:
        plugins = getattr(ensure_turn(config), "plugins", None)
        if plugins is not None:
            await plugins.close()


async def _persona_body(state: LoopState, config: RunnableConfig) -> dict:
    t0 = time.perf_counter()
    turn = ensure_turn(config)
    configurable = config.get("configurable") or {}
    request_id = configurable.get("request_id", "-")
    persona_id = configurable.get("persona_id", "anne")
    locale = configurable.get("locale", "en")
    user_message = configurable.get("query") or ""
    agent_result = state.get("agent_result") or ""
    turn_status = state.get("turn_status", "completed")
    actions = state.get("actions") or []
    # A write action that did not succeed leaves the turn `partial` (plan §3).
    if turn_status == "completed" and any(
            a.get("risk") == "write" and a.get("status") in _PARTIAL_WRITE
            for a in actions):
        turn_status = "partial"

    # Verdict for this turn (fetched by the agent node; read here for the
    # safety keys and the contract note).
    try:
        verdict = await current_verdict(config)
    except Exception:  # noqa: BLE001
        from langgraph_agents.guardrail.types import fallback_verdict
        verdict = fallback_verdict()

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None
    if writer is not None:
        writer({"stage": "synthesizer_started"})
        await asyncio.sleep(0)

    # ── Persona + prompt ─────────────────────────────────────────
    persona = get_persona(persona_id, locale)
    user_instructions = _build_user_instructions(state.get("user_instructions") or [])
    user_facts = _build_user_facts(state.get("user_facts") or [])
    internal_evidence = _internal_evidence(
        state.get("transcript") or [], budget_chars("evidence"),
        turn.call_registry,
    )
    head_text = head(verdict, persona_id, locale).strip()
    safety = safety_keys(verdict, [])
    from langgraph_agents.nodes.synthesizer import _build_contract_note
    contract_note = _build_contract_note(head_text, safety)
    directive_text = directive(verdict)

    # §6 template via concatenation (persona text contains braces).
    say_clause = _SAY_CLAUSE
    language_rule = (
        "## LANGUAGE (which language to answer in — not how to sound)\n"
        "- Answer in the SAME language the user wrote their question in. "
        "Whatever that language is.\n"
        "- Everything in this prompt is reference material; write in the "
        "user's language. Do NOT mix languages.\n"
        "- Start DIRECTLY with the answer content.\n"
    )
    system = (
        build_persona_prompt(persona, "synthesize")
        + "\n---\n"
        + user_instructions
        + user_facts
        + "\n## This turn\n"
        + f"The user said: {user_message}"
        + "\nWhat was found and done for them (internal notes):\n"
        + agent_result
        + "\nFrom ECA's library:\n"
        + internal_evidence
        + say_clause
        + language_rule
        + contract_note
        + directive_text
    )

    # ── Message assembly (mirrors synthesizer) ────────────────────
    history: list[dict[str, Any]] = []
    for m in state.get("transcript") or []:
        role = _msg_role(m)
        if role == "assistant" and not _msg_tool_calls(m):
            history.append({"role": "assistant", "content": _msg_content(m)})
        elif role == "user" and _msg_content(m) != user_message:
            history.append({"role": "user", "content": _msg_content(m)})
    msgs: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": user_message},
    ]
    voice_card = build_voice_card(persona, "synthesize")
    if reply_emotion.enabled():
        voice_card += reply_emotion.PROMPT_RULE
    msgs.append({"role": "system", "content": voice_card})

    # ── Model call (stream or single shot) ────────────────────────
    persona_model = configurable.get("persona_model") or AgentModel()
    final = ""
    emotion_sent = False
    persona_tokens = 0  # A9: real usage from the single-shot path; 0 for streaming

    def send_emotion(emotion):
        nonlocal emotion_sent
        final_emotion = reply_emotion.apply_emotion_policy(
            emotion, "synthesize", list(safety))
        emotion_sent = True
        if final_emotion is not None and writer is not None:
            writer({"emotion": final_emotion})

    try:
        if hasattr(persona_model, "astream"):
            # Streaming path (production).
            async for chunk in persona_model.astream(msgs, tools=None):  # type: ignore[attr-defined]
                raw = getattr(chunk, "content", "") or ""
                if raw and writer is not None:
                    writer({"content": raw})
                    final += raw
                    await asyncio.sleep(0)
        else:
            # Single-shot path (tests: FakeAgentModel has only `complete`).
            reply: AgentReply = await persona_model.complete(  # type: ignore[attr-defined]
                msgs, tools=None, tool_choice="none",
            )
            final = reply.content or ""
            persona_tokens = int((reply.usage or {}).get("total_tokens") or 0)
            if writer is not None and final:
                writer({"content": final})
    except Exception as exc:  # noqa: BLE001
        logger.warning("persona_model_failed", extra={
            "request_id": request_id, "error": type(exc).__name__})
        final = get_ui_string(persona_id, "error_unavailable", locale)
        return {
            "final_answer": final,
            "errors": list(state.get("errors") or []) + [error_entry(
                "persona", exc, ErrorSeverity.CRITICAL,
                f"Persona model failed: {type(exc).__name__}")],
            "turn_status": turn_status,
        }

    # Emotion (one shot, after the body).
    if not emotion_sent:
        send_emotion(None)

    # ── Outcome lines + final answer ──────────────────────────────
    outcome = outcome_lines(actions, locale) if (turn_status != "completed" or actions) else ""
    final_answer = final
    if outcome:
        final_answer = f"{final}\n\n{outcome}".strip()
    # E7: the result of a write action reaches the user as code wrote it, right
    # after the body. (The lines for read calls stay in the saved answer only.)
    write_lines = outcome_lines(
        [a for a in actions if a.get("risk") == "write"], locale)
    if write_lines and writer is not None:
        writer({"content": f"\n\n{write_lines}"})

    # ── Turn trace for meta["loop"] ───────────────────────────────
    trace = {
        "rounds": int(state.get("rounds", 0)),
        "actions": len(actions),
        "turn_status": turn_status,
        "motion_jobs": state.get("motion_jobs") or [],
    }
    plugins = getattr(turn, "plugins", None)
    if plugins is not None and (plugins.unavailable or plugins.changed):
        trace["plugins_unavailable"] = list(plugins.unavailable)
        trace["plugins_definition_changed"] = list(plugins.changed)
    if plugins is not None and getattr(plugins, "reconnect", None):
        trace["plugins_reconnect"] = list(plugins.reconnect)
    loop_trace = list(state.get("loop_trace") or [])
    loop_trace.append({**trace, "request_id": request_id})

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("node_complete", extra={
        "node": "persona", "request_id": request_id,
        "elapsed_ms": elapsed_ms, "turn_status": turn_status,
        "output_chars": len(final_answer),
    })

    return {
        "final_answer": final_answer,
        "turn_status": turn_status,
        # Re-emitted whole on purpose: api/main.py builds `final_state` from the
        # persona node's update alone (stream_mode="updates"), and the turn_status
        # event reads its actions from there. The reducer drops the repeats.
        "actions": actions,
        "loop_trace": loop_trace,
        "motion_jobs": state.get("motion_jobs") or [],
        "total_tokens": persona_tokens,
    }
