"""The avatar-switch note has to survive the trip to the model.

This file exists because the obvious test for this feature passes while the
feature is dead. The first design put the note in `state["messages"]` from
`memory_node`, and a test asserting it landed there would have been green —
but `synthesizer_node` filters that list down to plain user/assistant turns
before it builds the prompt (see `history` in synthesizer.py, and
`test_memory_regression.test_synthesizer_includes_conversation_history`, which
pins that filtering deliberately). Every SystemMessage is dropped.

So the assertion that matters is not "the note is in state" but "the note is in
the message list handed to the LLM". `test_note_reaches_the_llm` is the only
test here that would fail if someone moved the note back onto state.messages,
or tidied the voice card into the cached system prefix.

The note deliberately does NOT persist: there is no checkpointer
(`graph.compile()` takes none), `write_session_turn` takes strings rather than
graph state, and the STM row shape `{q, a, ts}` has no slot for a system turn.
`test_note_is_not_in_the_returned_state` pins the one of those three that lives
in this node.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import SystemMessage, HumanMessage

from langgraph_agents.nodes import synthesizer as syn
from langgraph_agents.nodes._persona_loader import PersonaError


# ── The helper in isolation ────────────────────────────────────────────────

@pytest.mark.unit
def test_note_names_the_previous_character_not_its_slug():
    """The prompt says "Bronya"; the wire says "bronya". The note must agree
    with the prompt, or the model reads a slug back to the user."""
    note = syn._build_avatar_switch_note("bronya", "anne", "en")
    assert "Bronya" in note
    assert "bronya" not in note, "raw slug leaked into the prompt"


@pytest.mark.unit
def test_note_uses_the_authored_name_even_when_it_differs_from_the_slug():
    """`hatsune-miku` is the case that makes slug-vs-name obvious: the persona
    file authors this character as plain "Miku", so the note must say Miku and
    must not fall back to title-casing the slug into "Hatsune Miku" — a name
    the model has never been given."""
    note = syn._build_avatar_switch_note("hatsune-miku", "anne", "en")
    assert "Miku" in note
    assert "hatsune" not in note.lower(), "raw slug leaked into the prompt"


@pytest.mark.unit
@pytest.mark.parametrize("prev", [None, "", "anne"])
def test_note_is_empty_when_nothing_changed(prev):
    """None = field absent, "" = a client that lost its selection, "anne" =
    switched away and back before sending. None of the three is a change."""
    assert syn._build_avatar_switch_note(prev, "anne", "en") == ""


@pytest.mark.unit
def test_unknown_previous_character_is_silent_not_fatal():
    """A character switched off in the catalog since the client last saw it.
    `get_persona` raises for that; a missing acknowledgement is a non-event,
    a 500 on the whole turn is not."""
    def boom(_pid, _lang="en"):
        raise PersonaError("gone")

    with patch.object(syn, "get_persona", boom):
        assert syn._build_avatar_switch_note("ghost", "anne", "en") == ""


# ── The note's trip through the node — the test that actually matters ──────

class _FakeAI:
    content = "ok"
    usage_metadata = None


def _capturing_llm(captured: dict) -> MagicMock:
    async def fake_ainvoke(msgs):
        captured["msgs"] = msgs
        return _FakeAI()

    llm = MagicMock()
    llm.ainvoke = fake_ainvoke
    return llm


def _state() -> dict:
    return {
        "messages": [],
        "resolved_query": "bài tập lưng?",
        "required_outputs": [],
        "needs_clarification": False,
        "total_tokens": 0,
    }


def _config(**extra) -> dict:
    base = {
        "request_id": "r",
        "persona_id": "anne",
        "query": "bài tập lưng?",
        "locale": "en",
    }
    base.update(extra)
    return {"configurable": base}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_note_reaches_the_llm():
    """The one that would have caught the original design being a no-op."""
    captured: dict = {}

    with patch.object(syn, "get_chat_model", return_value=_capturing_llm(captured)):
        await syn.synthesizer_node(_state(), _config(previous_persona_id="bronya"))

    msgs = captured.get("msgs")
    assert msgs is not None, "synthesizer never called the LLM"

    prompt = "\n".join(getattr(m, "content", "") for m in msgs)
    assert "Bronya" in prompt, "the switch note never reached the model"
    assert "switched" in prompt

    # It rides on the voice card, which is last on purpose: whatever sits
    # closest to the generation point is what the model answers in the register
    # of. A note buried above the evidence would be read and then forgotten.
    assert isinstance(msgs[-1], SystemMessage)
    assert "Who is speaking" in msgs[-1].content
    assert "Bronya" in msgs[-1].content

    # And it must not have displaced the question.
    humans = [m for m in msgs if isinstance(m, HumanMessage)]
    assert humans[-1].content == "bài tập lưng?"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_note_on_an_ordinary_turn():
    """~99% of requests carry no previous_persona_id and must pay nothing."""
    captured: dict = {}

    with patch.object(syn, "get_chat_model", return_value=_capturing_llm(captured)):
        await syn.synthesizer_node(_state(), _config())

    prompt = "\n".join(getattr(m, "content", "") for m in captured["msgs"])
    assert "Avatar switch" not in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_note_is_not_in_the_returned_state():
    """Ephemeral means one turn. The node must not hand the note back as state
    for the graph to carry — persistence reads elsewhere, but this is the hop
    that would leak it."""
    captured: dict = {}

    with patch.object(syn, "get_chat_model", return_value=_capturing_llm(captured)):
        result = await syn.synthesizer_node(
            _state(), _config(previous_persona_id="bronya")
        )

    blob = repr(result)
    assert "Avatar switch" not in blob
    assert "just switched from" not in blob
