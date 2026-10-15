"""The models that answer and that pick tools read what the user wrote, not a rewrite.

The planner's `resolved_query` is one sentence by design and drops statements,
second questions and tone. The synthesizer reads `config["configurable"]["query"]`
instead; the retriever, which has no history, reads both. Memory-tool results
reach the answering model as plain text: no ids, no key names, and nothing that
calls a past conversation a "session".
"""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from langgraph_agents.evidence import EVIDENCE_PER_MESSAGE_CAP, evidence_items, render_evidence
from langgraph_agents.shared.context import budget_chars
from test_synthesizer_blocks import _KB, _capturing_llm, _tm

pytestmark = pytest.mark.unit

_USER = ("then lets talk about food, what is your favorite food or drinks, "
         "mine is noodles, but the nutrients are kinda bad")
_RESOLVED = "RESOLVED-REWRITE what is your favorite food or drinks"

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_SID_1 = "3f2b8c1e-9d4a-4e6b-8a57-1c2d3e4f5a6b"
_SID_2 = "7a1d9e02-5b3c-4f8d-9e21-0a1b2c3d4e5f"


# ── Synthesizer ──────────────────────────────────────────────────────────

_MODES = {
    "chat": {"messages": [], "required_outputs": [], "needs_clarification": False},
    "synthesize": {"messages": [_tm("kb_search", _KB)], "required_outputs": [],
                   "needs_clarification": False},
    "refuse": {"messages": [], "required_outputs": ["exercise_protocol"],
               "needs_clarification": False},
    "clarify": {"messages": [], "required_outputs": [], "needs_clarification": True},
}


async def _run_synthesizer(state_extra: dict, configurable: dict) -> list:
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {"resolved_query": _RESOLVED, "total_tokens": 0, **state_extra}
    config = {"configurable": {"request_id": "r", "persona_id": "anne",
                               "locale": "en", **configurable}}
    captured: dict = {}
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_capturing_llm(captured)):
        await syn_mod.synthesizer_node(state, config)
    assert captured.get("msgs"), "synthesizer never called the LLM"
    return captured["msgs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(_MODES))
async def test_synthesizer_reads_the_users_own_message(mode):
    msgs = await _run_synthesizer(_MODES[mode], {"query": _USER})
    human = [m for m in msgs if isinstance(m, HumanMessage)]
    assert human[-1].content == _USER
    system = msgs[0].content
    assert isinstance(msgs[0], SystemMessage)
    assert _USER in system
    assert all("RESOLVED-REWRITE" not in str(m.content) for m in msgs)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", list(_MODES))
async def test_synthesizer_falls_back_to_resolved_query_without_query(mode):
    msgs = await _run_synthesizer(_MODES[mode], {})
    human = [m for m in msgs if isinstance(m, HumanMessage)]
    assert human[-1].content == _RESOLVED
    assert _RESOLVED in msgs[0].content


@pytest.mark.asyncio
async def test_synthesizer_addition_pass_replays_the_users_message():
    from langchain_core.messages import AIMessage
    from langgraph_agents.nodes import synthesizer as syn_mod

    state = {"resolved_query": _RESOLVED, "total_tokens": 0, "messages": [],
             "required_outputs": [], "retry_count": 1,
             "grader_feedback": "no greeting", "final_answer": "Hello there."}
    config = {"configurable": {"request_id": "r", "persona_id": "anne",
                               "locale": "en", "query": _USER}}
    captured: dict = {}

    async def fake_run_addition(state, config, llm, writer, msgs, *a, **k):
        captured["msgs"] = msgs
        return {}

    with patch.object(syn_mod, "get_chat_model", return_value=MagicMock()), \
            patch.object(syn_mod, "_run_addition", fake_run_addition):
        await syn_mod.synthesizer_node(state, config)

    human = [m for m in captured["msgs"] if isinstance(m, HumanMessage)]
    assert human[-1].content == _USER
    assert isinstance(captured["msgs"][-2], AIMessage)
    assert all("RESOLVED-REWRITE" not in str(m.content) for m in captured["msgs"])


# ── Retriever ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retriever_sees_both_the_message_and_the_resolved_request():
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableConfig
    from langgraph_agents.nodes.retriever_agent import retriever_agent_node

    config = RunnableConfig(configurable={
        "user_id": "u", "session_id": "s", "query": _USER, "persona_id": "anne",
        "request_id": "r", "web_search": False})
    state = {"messages": [], "errors": [], "retry_count": 0, "total_tokens": 0,
             "required_outputs": [], "resolved_query": _RESOLVED}
    with patch("langgraph_agents.nodes.retriever_agent.get_chat_model") as mock_llm:
        invoke = AsyncMock(return_value=AIMessage(content=""))
        mock_llm.return_value.bind_tools.return_value.ainvoke = invoke
        await retriever_agent_node(state, config)
    msgs = invoke.call_args[0][0]
    system, human = msgs[0].content, msgs[1].content
    assert f"User's message: {_USER}" in system
    assert f"Same request, references resolved: {_RESOLVED}" in system
    assert _USER in human and _RESOLVED in human


def test_retriever_prompt_without_user_message_still_shows_the_request():
    from langgraph_agents.nodes.retriever_agent import _build_retriever_system_prompt

    prompt = _build_retriever_system_prompt(
        web_search_enabled=False, allow_web_fallback=False, retry_note="",
        required_outputs="(none)", resolved_query=_RESOLVED)
    assert _RESOLVED in prompt


# ── Memory evidence ──────────────────────────────────────────────────────

_RESUME = {
    "found": True, "session_id": _SID_1,
    "summary_chunks": ["They planned a trip to Da Nang and talked about knee pain."],
    "recent_messages": [
        {"role": "user", "content": "my knee hurts after stairs"},
        {"role": "assistant", "content": "Try slow step-downs."},
    ],
}
_RESUME_NO_SUMMARY = {**_RESUME, "summary_chunks": []}
_RESUME_AMBIGUOUS = {
    **_RESUME, "ambiguous": True,
    "candidates": [
        {"session_id": _SID_1, "preview": "my knee hurts after stairs...",
         "updated_at": "2026-10-12T09:30:00+00:00"},
        {"session_id": _SID_2, "preview": "what should I eat before a run...",
         "updated_at": "2026-10-12T20:00:00+00:00"},
    ],
}
_SHAPES = {
    "resume": ("resume_last_session", _RESUME,
               ["Da Nang", "my knee hurts after stairs", "slow step-downs"]),
    "resume_no_summary": ("resume_last_session", _RESUME_NO_SUMMARY,
                          ["my knee hurts after stairs", "slow step-downs"]),
    "resume_ambiguous": ("resume_last_session", _RESUME_AMBIGUOUS,
                         ["my knee hurts after stairs", "what should I eat before a run"]),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
def test_memory_evidence_is_plain_text(shape):
    name, payload, expected = _SHAPES[shape]
    items = evidence_items([_tm(name, payload)])
    assert items
    text = render_evidence(items)
    assert "session" not in text.lower()
    assert not _UUID.search(text)
    assert "{" not in text and "}" not in text
    for key in ("summary_chunks", "recent_messages", "candidates", "summary_text",
                "session_id", "similarity", "ambiguous", "created_at", "found"):
        assert key not in text
    for fragment in expected:
        assert fragment in text


def test_resume_renders_date_summaries_and_chat_lines():
    payload = {**_RESUME, "title": "Knee talk", "updated_at": "2026-10-12T09:30:00+00:00"}
    text = render_evidence(evidence_items([_tm("resume_last_session", payload)]))
    assert "2026-10-12" in text and "Knee talk" in text
    assert "User: my knee hurts after stairs" in text
    assert "You: Try slow step-downs." in text


def test_ambiguous_resume_lists_each_candidate_with_its_date():
    text = render_evidence(evidence_items([_tm("resume_last_session", _RESUME_AMBIGUOUS)]))
    assert "2026-10-12" in text
    assert "what should I eat before a run" in text


def test_memory_items_keep_the_source_label():
    items = evidence_items([_tm("resume_last_session", _RESUME)])
    assert all(i.header == "[From your earlier conversations with this user]" for i in items)


def test_per_item_cap_and_total_budget_still_apply():
    long_chat = {**_RESUME, "summary_chunks": [],
                 "recent_messages": [{"role": "user", "content": "x" * 5000}]}
    items = evidence_items([_tm("resume_last_session", long_chat)])
    assert all(len(i.text) <= EVIDENCE_PER_MESSAGE_CAP for i in items)

    many = [{"content": f"topic {n} " + "y" * 1400, "similarity": 0.5,
             "source_type": "exercise_db", "document_title": f"Doc {n}",
             "chunk_index": n} for n in range(40)]
    items = evidence_items([_tm("kb_search", many)])
    assert 1 < len(items) < 40
    assert sum(len(i.text) for i in items) <= budget_chars("evidence") + EVIDENCE_PER_MESSAGE_CAP


def test_memory_error_and_empty_results_are_not_rendered():
    assert evidence_items([_tm("resume_last_session", {"found": False})]) == []
    assert evidence_items([_tm("resume_last_session", {"error": "db down"})]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", list(_SHAPES))
async def test_nothing_the_synthesizer_sends_says_session(shape):
    name, payload, _ = _SHAPES[shape]
    msgs = await _run_synthesizer(
        {"messages": [_tm(name, payload)], "required_outputs": [],
         "needs_clarification": False},
        {"query": "yo what have we talked before you remember ?"})
    sent = "\n".join(str(m.content) for m in msgs)
    assert "session" not in sent.lower()
    assert not _UUID.search(sent)
