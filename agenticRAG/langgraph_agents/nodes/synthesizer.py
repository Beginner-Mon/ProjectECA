"""Synthesizer node — M.3b universal responder, persona-styled.

Decisions encoded:
  D29:  Mode EMERGES from signals, NOT enum response_mode
  D30:  Persona applies to ALL modes (including refuse/clarify)
  D32:  Safety warning = FIRST in output (synthesizer writes);
        unverified disclaimer = LAST (grader appends)
  D33:  Danger detection = PLANNER only (1 place);
        synthesizer executes tags, does NOT re-evaluate danger
  D26:  Motion coherence via tag motion_descriptor;
        synthesizer does NOT receive motion flag

Mode derivation (D29 — derive, don't store):
  needs_clarification OR tool ambiguous → CLARIFY
  clinical tag + all tools empty (no-source)  → REFUSE
  Has ToolMessage non-empty                   → SYNTHESIZE
  No tools + no tags                          → CHAT
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig

from langgraph.config import get_stream_writer
from langgraph_agents.shared import reply_emotion
from langgraph_agents.shared.context import budget_chars, estimate_tokens
from langgraph_agents.evidence import (
    classify_tool_result as _classify_tool_result,
    evidence_items,
    evidence_messages as _evidence_messages,
    render_evidence,
)
from langgraph_agents.sources import source_for_tool
from langgraph_agents.state import AgentState, ErrorSeverity
from langgraph_agents.tag_contract import (
    closing_items_note,
    model_tags,
    opening_line,
)
from langgraph_agents.llm import get_chat_model, get_fallback_chat_model, extract_cache_tokens
from langgraph_agents.nodes._persona_loader import (
    PersonaError,
    get_persona,
    build_persona_prompt,
    build_voice_card,
    get_ui_string,
    persona_name,
)
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.synthesizer")


# ── Language rule (shared across all modes) ──────────────────────────────

_LANGUAGE_RULE = """## LANGUAGE (which language to answer in — not how to sound)
- Answer in the SAME language the user wrote their question in. Whatever that
  language is.
- Everything in this prompt — these instructions, the persona description, the
  retrieved evidence — is reference material. Its language is not the language
  of your reply. Obey what it MEANS, then write in the user's language.
- The whole reply is in that one language, including clinical terms, exercise
  names and safety warnings. Do NOT mix languages.
- Do NOT write preambles like "I'll answer in...". Start DIRECTLY with the
  answer content.
"""


# ── Per-tag instructions (B3: chỉ tag của lượt mới được hướng dẫn) ──────

# Tách từ _SYNTHESIZE_TASK: mỗi dòng "For <tag>" trước đây có mặt ở MỌI lượt
# (kèm ví dụ sets/reps cụ thể) nên lượt không hỏi liều lượng vẫn tự cho số.
# _build_tag_instructions chỉ trả dòng của tag có trong lượt — cùng cách
# _build_safety_rules đang làm. Không thêm luật mới.
_TAG_INSTRUCTIONS = {
    "exercise_protocol":
        "- For exercise_protocol: give the sets, reps and frequency the evidence "
        "states, and name the source. Where the evidence does not state one of "
        "them, say so. Do not supply numbers of your own.",
    "exercise_steps":
        "- For exercise_steps: provide ≥2 ordered steps",
    "contraindication":
        "- For contraindication: list conditions where the exercise "
        "should NOT be done",
    "motion_descriptor":
        "- For motion_descriptor: describe the movement + joints involved clearly",
}


def _build_tag_instructions(required_outputs: list) -> str:
    """Dòng hướng dẫn của đúng các tag trong lượt; rỗng khi không có tag nào."""
    return "\n".join(
        _TAG_INSTRUCTIONS[t] for t in _TAG_INSTRUCTIONS if t in required_outputs
    )


def _build_contract_note(opening: str, required_outputs: list) -> str:
    """Báo cho model câu đã hiện và các dòng sẽ được thêm; "" khi không có gì."""
    blocks = []
    if opening:
        blocks.append(
            "## Already on the user's screen\n"
            "This line was shown to the user just before your reply:\n"
            f'"{opening}"\n'
            "Start from there. Do not repeat it, quote it or rephrase it.")
    closing = closing_items_note(required_outputs)
    if closing:
        blocks.append(
            "## Added after your reply\n"
            f"These are added automatically when you finish: {closing}.\n"
            "Do not write any of them yourself.")
    return "\n\n".join(blocks)


# ── Mode-specific prompts ────────────────────────────────────────────────

_SYNTHESIZE_TASK = """## This turn
Answer the user's wellness question from the evidence below.

{language_rule}

## Required deliverables (tags)
{required_outputs}

## Retrieved evidence
{tool_results}

## User's question (cleaned, coreferences resolved)
{resolved_query}

Instructions:
- Cover ALL required_outputs tags in your response
- Base your answer on the retrieved evidence — cite sources when available
{tag_instructions}
- Numbers for sets, reps, hold times or frequency come only from the evidence.
  If the evidence gives none, give none.
- Length and layout are set by your own Formatting rules, not by this list.
"""

_REFUSE_TASK = """## This turn
You have no reliable source for the guidance the user asked for. Do not make
up exercise or health guidance. Say so for that part only.

{language_rule}

## Situation
The guidance the user asked for has no reliable source: the question is
OUTSIDE your wellness advisory scope and/or nothing trustworthy was found.
Speak only to that part — anything else in the turn you can still answer.

## Required deliverables (tags)
{required_outputs}

## User's question
{resolved_query}

Instructions:
- Be honest: explain WHY you cannot give that guidance (out of scope / no sources)
- If no sources were found: state this clearly, suggest the user rephrase or ask a professional
- Keep it brief
- Do NOT invent exercises, diagnoses, or medical advice
- Numbers for sets, reps, hold times or frequency come only from the evidence.
  If the evidence gives none, give none.
"""

_CLARIFY_TASK = """## This turn
The user's query needs clarification before you can give a useful answer.

{language_rule}

## User's question
{resolved_query}

## Context (tool results may contain ambiguity candidates)
{tool_results}

Instructions:
- Ask for the specific missing information
- Explain briefly WHY you need it
- Keep concise (1-3 sentences)
- If tool results contain candidates (multiple matching sessions/articles), list 2-3 briefly for the user to choose
"""

_CHAT_TASK = """## This turn
A casual conversational message — no clinical content needed.

{language_rule}

## User's question
{resolved_query}

Instructions:
- Respond naturally, the way you would speak
- Keep under 50 words for greetings, under 100 for follow-up chat
- Do NOT add clinical advice unless the user explicitly asks
"""


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_tool_results(messages: list) -> str:
    """Format ToolMessage content from retriever tool calls, newest kept first.

    Two caps, and the total is the new one. The per-message cap alone bounded
    nothing that mattered: `messages` is an `add_messages` list that is never
    pruned, so a retriever second round (MAX_RETRIEVER_ROUNDS=2) and a grader
    retry each append more ToolMessages to the same list and the dump grew with
    them — 3,000 to 12,000 characters in practice, against a persona block of
    roughly 800.

    Selecting newest-first means a retry keeps the evidence it just went and
    fetched rather than the round it was told to improve on. Output stays in
    chronological order; only the dropping is done from the far end.

    At least one tool result always survives, however long it is — a single
    oversized document should be truncated, not silently omitted.

    B5: kb_search messages are split per segment first (titles differ by
    source_type); the budget below counts split pieces, newest first.
    """
    return render_evidence(evidence_items(messages))


def _has_tool_results(messages: list) -> bool:
    """Check if any ToolMessage has non-empty, non-error results."""
    return any(
        _classify_tool_result(str(m.content)) == "hits"
        for m in _evidence_messages(messages)
    )


def _top_similarity(content: str) -> float | None:
    """Best `similarity` in a tool result, if it carries any.

    kb_search's cutoff (`kb_min_similarity`, plan T5) is optional and may be
    unset, so "hits" alone does not mean the library covered the question.
    This number does, and it is what that cutoff should be tuned against.
    """
    import json
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return None
    scores = [r["similarity"] for r in rows
              if isinstance(r, dict) and isinstance(r.get("similarity"), (int, float))]
    return max(scores) if scores else None


def _evidence_summary(messages: list) -> list[dict]:
    """One entry per tool call: why a turn refused, or what it answered from.

    Tells a library gap (hits, low similarity) from a retrieval fault (error)
    from a retriever that never searched (empty list).
    """
    summary = []
    for m in messages:
        if isinstance(m, ToolMessage):
            content = str(m.content)
            entry = {"tool": m.name, "status": _classify_tool_result(content)}
            top = _top_similarity(content)
            if top is not None:
                entry["top_similarity"] = top
            summary.append(entry)
    return summary


def _check_tool_ambiguous(messages: list) -> bool:
    """Check if any tool returned ambiguity metadata (D22: dynamic clarify)."""
    import json
    for m in _evidence_messages(messages):
        try:
            data = json.loads(str(m.content))
            if isinstance(data, dict) and data.get("ambiguous"):
                return True
        except (json.JSONDecodeError, TypeError):
            pass
    return False


def _build_avatar_switch_note(
    prev_id: str | None,
    persona_id: str,
    locale: str,
) -> str:
    """One-line context for the turn right after the user switched avatar.

    Reads from config, not from state.messages: synthesizer drops every
    SystemMessage from history (see `history` below), so ephemeral context
    must travel via config.

    Resolves the display name rather than passing the slug through. The prompt
    calls this character "Bronya", never "bronya", and a slug the model has not
    seen before is a slug it will happily read back to the user verbatim —
    "hatsune-miku" being the case that makes it obvious.

    Only the PREVIOUS character needs naming: the note says "to you", and who
    "you" is has already been established by the voice card this text is
    appended to.
    """
    if not prev_id or prev_id == persona_id:
        return ""
    try:
        prev_name = persona_name(get_persona(prev_id, locale))
    except PersonaError:
        # Character switched off in the catalog since the client last saw it.
        # A missing acknowledgement is a non-event; a 500 on the whole turn is
        # not. Same fallback-don't-raise stance as grader._safety_templates.
        return ""
    return (
        f"\n\n## Avatar switch\n"
        f"The user just switched from {prev_name} to you. Acknowledge it "
        f"briefly ONLY if it fits naturally; otherwise ignore it and answer "
        f"the question."
    )


def _build_body_state_note(messages: list) -> str:
    """What this character's own 3D body is doing this turn (plan T4).

    Reads the newest motion-source message (kimodo node today, show_movement
    tool after T9) and returns a short first-person-able block. Empty when
    there is no motion message or its payload is broken — blocks with no
    data never enter the prompt (~10K token window).

    Never names machinery: when the body cannot perform, the character says
    so as itself, with no technical reason.
    """
    import json

    motion_msg = None
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        src = source_for_tool(m.name or "")
        if src is not None and src.id == "motion":
            motion_msg = m
    if motion_msg is None:
        return ""

    try:
        data = json.loads(str(motion_msg.content))
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""

    state = data.get("state")
    if state in ("queued", "cache_hit"):
        prompt = str(data.get("prompt", "") or "").strip()
        eta = data.get("eta_seconds")
        time_clause = f", in about {eta} seconds" if eta else ""
        return (
            "\n\n## Your body this turn\n"
            f"You are about to show \"{prompt}\" with your own body{time_clause}. "
            "Speak as the one doing it."
        )
    if state in ("unavailable", "busy"):
        return (
            "\n\n## Your body this turn\n"
            "You are not able to show a movement right now. Do not promise to, "
            "and give no technical reason. You may describe it in words instead."
        )
    return ""


def _build_about_you(messages: list) -> str:
    """What the character knows about itself, from recall_self (plan T8f).

    Newest usable result wins. Capped so the block never eats the ~10K token
    window. Empty when the tool found nothing — the identity core (T8a) still
    tells the character not to invent.
    """
    import json

    for m in reversed(messages):
        if not isinstance(m, ToolMessage) or (m.name or "") != "recall_self":
            continue
        try:
            data = json.loads(str(m.content))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict) or not data.get("found"):
            continue
        parts = []
        for r in data.get("results", []) or []:
            if isinstance(r, dict) and r.get("content"):
                title = (r.get("title") or "").strip()
                parts.append(f"{title}: {r['content']}" if title else r["content"])
        content = "\n".join(parts)[:budget_chars("about_you")]
        if content.strip():
            return (
                "\n\n## About you\n"
                "This is what you know about yourself. Say it in the first person, "
                "as your own\nknowledge. Never say you looked it up.\n"
                f"{content}"
            )
    return ""


# ── Mode derivation (D29: emerge from signals, no enum) ─────────────────

def _derive_mode(state: AgentState) -> str:
    """Derive synthesizer mode from state signals.

    Returns one of: 'clarify', 'refuse', 'synthesize', 'chat'
    """
    needs_clarification = state.get("needs_clarification", False)
    required_outputs = state.get("required_outputs", [])
    messages = state.get("messages", [])

    # 1. CLARIFY: static (planner-detected) or dynamic (tool ambiguous)
    if needs_clarification or _check_tool_ambiguous(messages):
        return "clarify"

    has_results = _has_tool_results(messages)

    # 2. REFUSE: clinical/safety tags + no sources (D25 — clinical-no-source)
    safety_tags = {"red_flag_screen", "referral_advice"}
    clinical_tags = {"exercise_protocol", "exercise_steps", "contraindication",
                     "evidence_citation", "scope_disclaimer"}
    has_clinical = bool(set(required_outputs) & (safety_tags | clinical_tags))

    if has_clinical and not has_results:
        return "refuse"

    # 3. SYNTHESIZE: has tool results (regardless of tags)
    if has_results:
        return "synthesize"

    # 4. CHAT: no tools, no tags (greeting/general/empty)
    return "chat"


# ── Node ─────────────────────────────────────────────────────────────────

async def synthesizer_node(state: AgentState, config: RunnableConfig) -> dict:
    """Synthesizer node — universal responder (M.3b).

    Derives mode from state signals (D29), applies persona voice (D30),
    writes safety warning FIRST (D32).
    """
    t0 = time.perf_counter()
    request_id = config["configurable"].get("request_id", "-")
    persona_id = config["configurable"].get("persona_id", "anne")
    resolved_query = state.get("resolved_query") or config["configurable"]["query"]
    required_outputs = state.get("required_outputs", [])

    mode = _derive_mode(state)

    logger.info("node_start", extra={
        "node": "synthesizer", "request_id": request_id,
        "mode": mode, "persona_id": persona_id,
        "tags": required_outputs,
        "query_preview": resolved_query[:80],
        "evidence": _evidence_summary(state.get("messages", [])),
    })

    # ── Build prompts ─────────────────────────────────────────────────
    # Persona is loaded before the task prompt, not after it: the safety block
    # is now worded from this character's own templates.
    # The site locale, not a guess at this message's language: the voice card
    # built from it is IMITATED by the model, so it has to be a declared
    # choice rather than something detected and occasionally wrong.
    locale = config["configurable"].get("locale", "en")
    persona = get_persona(persona_id, locale)
    tool_results = _extract_tool_results(state.get("messages", []))
    opening = opening_line(required_outputs, persona_id, locale)
    prefix = f"{opening}\n\n" if opening else ""
    tags_str = ", ".join(model_tags(required_outputs)) or "(none — free response)"

    # Tag instructions: only the tags actually required this turn (B3)
    tag_instructions = _build_tag_instructions(required_outputs)

    if mode == "clarify":
        task_system = _CLARIFY_TASK.format(
            language_rule=_LANGUAGE_RULE,
            resolved_query=resolved_query,
            tool_results=tool_results or "(no tool results — static clarification)",
        )
    elif mode == "refuse":
        task_system = _REFUSE_TASK.format(
            language_rule=_LANGUAGE_RULE,
            required_outputs=tags_str,
            resolved_query=resolved_query,
        )
    elif mode == "synthesize":
        task_system = _SYNTHESIZE_TASK.format(
            language_rule=_LANGUAGE_RULE,
            required_outputs=tags_str,
            tool_results=tool_results or "(no evidence)",
            resolved_query=resolved_query,
            tag_instructions=tag_instructions,
        )
    else:  # chat
        task_system = _CHAT_TASK.format(
            language_rule=_LANGUAGE_RULE,
            resolved_query=resolved_query,
        )

    contract_note = _build_contract_note(opening, required_outputs)
    if contract_note:
        task_system = f"{task_system}\n{contract_note}\n"

    # Persona prompt (D30: applies to ALL modes)
    persona_system = build_persona_prompt(persona, mode)
    # Body state sits between persona and task (plan T4; T8 slots About-you
    # after it). Absent when there is no motion message, so chat turns keep
    # the exact prompt they had before.
    body_note = _build_body_state_note(state.get("messages", []))
    about_you = _build_about_you(state.get("messages", []))
    middle_blocks = [b for b in (body_note, about_you) if b]
    middle = ("\n\n".join(middle_blocks) + "\n\n") if middle_blocks else ""
    system = f"{persona_system}\n\n---\n\n{middle}{task_system}"

    llm = get_chat_model("synthesizer")

    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = None

    # Include prior conversation (loaded by memory node into state messages)
    # so the model has context for follow-ups ("what did I just say"). Keep
    # plain user/assistant turns only — drop the memory SystemMessage, tool-call
    # AIMessages, and ToolMessages (tool evidence is already in the system prompt).
    history = [
        m for m in state.get("messages", [])
        if isinstance(m, HumanMessage)
        or (isinstance(m, AIMessage) and not getattr(m, "tool_calls", None))
    ]
    # The voice card goes LAST — after the evidence, after the history, after the
    # question. Whatever sits closest to the generation point is what the model
    # answers in the register of, and until now that was a tag contract.
    avatar_note = _build_avatar_switch_note(
        config["configurable"].get("previous_persona_id"),
        persona_id,
        locale,
    )
    voice_card = build_voice_card(persona, mode) + avatar_note
    # Reply-driven avatar emotion: the model opens with [emotion: NAME N],
    # stripped below before anything else sees the text (shared/reply_emotion).
    # Last, beside the voice card, where instructions are followed most reliably.
    if reply_emotion.enabled():
        voice_card += reply_emotion.PROMPT_RULE
    msgs = [
        SystemMessage(content=system),
        *history,
        HumanMessage(content=resolved_query),
        SystemMessage(content=voice_card),
    ]

    ai_msg = None  # kept for prompt-cache telemetry (fix #1)
    used_fallback = False
    tag_stream = reply_emotion.EmotionTagStream()

    def send_emotion(emotion: dict | None) -> None:
        # Its own custom-stream item, ahead of the text: api/main.py turns it
        # into the `emotion` SSE event. Health rules applied here, where the
        # turn's mode and safety tags are known.
        emotion = reply_emotion.apply_emotion_policy(emotion, mode, required_outputs)
        if emotion is not None and writer is not None:
            writer({"emotion": emotion})

    try:
        if writer is not None:
            final = ""
            tokens = 0
            if prefix:
                writer({"content": prefix})
            async for chunk in llm.astream(msgs):
                raw = chunk.content if hasattr(chunk, "content") else str(chunk)
                # Hold back only while the start could still be the emotion tag;
                # everything the user sees (and `final`) is tag-free.
                emotion, content = tag_stream.feed(raw) if raw else (None, "")
                if emotion is not None:
                    send_emotion(emotion)
                if content:
                    final += content
                    writer({"content": content})
                    # LangGraph's "custom" stream mode only drains this node's
                    # writer() queue when a sibling "waiter" task gets scheduled
                    # by asyncio (see PregelRunner.atick's asyncio.wait(...,
                    # FIRST_COMPLETED) race). This loop's own awaits (network
                    # reads from llm.astream) keep resuming THIS task fast enough
                    # that the waiter never gets a turn — so every token silently
                    # queues up and only flushes to the SSE client in one burst
                    # right as the node finishes. sleep(0) forces one real event-
                    # loop tick per token, giving the waiter a chance to run and
                    # actually deliver tokens as they're generated.
                    await asyncio.sleep(0)
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    tokens = chunk.usage_metadata.get("total_tokens", 0)
                    ai_msg = chunk
                elif (getattr(chunk, "response_metadata", None) or {}).get("token_usage"):
                    ai_msg = chunk
            # A reply that ended while still possibly a tag (e.g. just "[1]"):
            # release what was held.
            emotion, content = tag_stream.flush()
            if emotion is not None:
                send_emotion(emotion)
            if content:
                final += content
                writer({"content": content})
        else:
            ai_msg = await llm.ainvoke(msgs)
            _, final = reply_emotion.parse_emotion_tag(ai_msg.content or "")
            tokens = 0
            if hasattr(ai_msg, "usage_metadata") and ai_msg.usage_metadata:
                tokens = ai_msg.usage_metadata.get("total_tokens", 0)
    except Exception as exc:
        # Primary DeepSeek call failed/timed out — try ONE-shot Gemini fallback
        # before falling through to the existing CRITICAL error handling.
        #
        # Guard: if the primary stream already emitted some tokens via writer()
        # before failing (e.g. times out mid-generation, not at first byte), those
        # tokens are ALREADY on the wire to the browser (writer() is a live SSE
        # channel, independent of this function's return value — see api/main.py).
        # Appending a full fallback answer on top would concatenate into a garbled,
        # duplicated-looking response. In that case, skip the fallback and fall
        # through to the existing error path instead of compounding the output.
        # `final` is always bound by this point when writer is not None (assigned
        # "" before the astream loop starts) — safe to reference directly.
        already_streamed = writer is not None and bool(final)
        final = None
        fallback_model = None if already_streamed else get_fallback_chat_model("synthesizer")
        if fallback_model is not None:
            try:
                fb_ai_msg = await fallback_model.ainvoke(msgs)
                emotion, final = reply_emotion.parse_emotion_tag(fb_ai_msg.content or "")
                send_emotion(emotion)
                tokens = 0
                if hasattr(fb_ai_msg, "usage_metadata") and fb_ai_msg.usage_metadata:
                    tokens = fb_ai_msg.usage_metadata.get("total_tokens", 0)
                ai_msg = fb_ai_msg
                used_fallback = True
                # Fallback does not stream — emit the whole answer as one chunk
                # so the SSE `token` event contract is preserved for the frontend.
                if writer is not None and final:
                    writer({"content": final})
            except Exception:
                final = None

        if final is None:
            elapsed_ms = round((time.perf_counter() - t0) * 1000)
            logger.error("node_failed", extra={
                "node": "synthesizer", "request_id": request_id,
                "elapsed_ms": elapsed_ms, "error": str(exc),
            }, exc_info=True)
            # The character's own wording, not a string hard-coded in this node.
            # api/main.py:582 already resolves the same key on the path where the
            # graph produces no answer at all; two places that both mean "we
            # could not answer" should not disagree about how to say it.
            #
            # It still reads Vietnamese today because personas/*.md are Vietnamese.
            # That is the persona overlay's problem to fix, and fixing it there
            # fixes both call sites at once — which is the point of routing
            # through here rather than translating this literal.
            fallback = get_ui_string(persona_id, "error_unavailable", locale)
            return {
                "final_answer": prefix + fallback,
                "errors": [{
                    "node": "synthesizer",
                    "severity": ErrorSeverity.CRITICAL,
                    "message": f"Synthesizer LLM failed ({elapsed_ms:.0f}ms): {exc}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }],
            }

        logger.info("llm_fallback_used", extra={
            "node": "synthesizer", "request_id": request_id,
            "llm_fallback_used": True,
            "primary_error": str(exc),
        })

    cache_hit_tokens, cache_miss_tokens = extract_cache_tokens(ai_msg)

    # Prompt-block sizes for budget tracking (plan T11). usage_metadata carries
    # the provider's real token counts; estimate_tokens is the local ~4
    # chars/token rule whose accuracy V4 measures.
    usage = getattr(ai_msg, "usage_metadata", None) or {}
    history_chars = sum(len(str(getattr(m, "content", "") or "")) for m in history)
    prompt_blocks = {
        "persona": len(persona_system),
        "body_state": len(body_note),
        "about_you": len(about_you),
        "task": len(task_system),
        "evidence": len(tool_results),
        "history": history_chars,
        "voice_card": len(voice_card),
    }

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    logger.info("node_complete", extra={
        "node": "synthesizer", "request_id": request_id,
        "elapsed_ms": elapsed_ms, "tokens": tokens, "mode": mode,
        "output_chars": len(final) if final else 0,
        "streamed": writer is not None,
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "llm_fallback_used": used_fallback,
        "prompt_blocks": prompt_blocks,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    })

    return {
        "final_answer": prefix + (final or ""),
        "total_tokens": tokens,
    }
