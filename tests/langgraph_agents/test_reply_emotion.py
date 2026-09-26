"""Reply-driven emotion: the synthesizer's leading [emotion: …] tag.

The tag is for the avatar's face only. It must never reach the text the user
reads, the voice, or the saved history — so most of these tests are about the
tag disappearing cleanly, however the stream happens to split it.
"""
from __future__ import annotations

import pytest

from langgraph_agents.shared.reply_emotion import (
    EmotionTagStream,
    apply_emotion_policy,
    parse_emotion_tag,
)


def stream(chunks):
    """Feed chunks the way the LLM delivers them; collect what reaches the user."""
    s = EmotionTagStream()
    emotions, text = [], ""
    for c in chunks:
        emo, out = s.feed(c)
        if emo:
            emotions.append(emo)
        text += out
    emo, out = s.flush()
    if emo:
        emotions.append(emo)
    return emotions, text + out


@pytest.mark.unit
class TestParseEmotionTag:
    def test_reads_name_and_intensity_and_strips_the_tag(self):
        emo, rest = parse_emotion_tag("[emotion: happy 0.7] Chào bạn!")
        assert emo == {"name": "happy", "intensity": 0.7}
        assert rest == "Chào bạn!"

    def test_tolerates_the_formats_models_actually_write(self):
        for tag in ("[emotion:sad,0.4]", "[Emotion: Sad 0.4]", "  [emotion = sad 0.4]\n"):
            emo, _ = parse_emotion_tag(tag + "text")
            assert emo == {"name": "sad", "intensity": 0.4}, tag

    def test_missing_intensity_defaults_to_moderate(self):
        emo, _ = parse_emotion_tag("[emotion: relaxed] ok")
        assert emo == {"name": "relaxed", "intensity": 0.6}

    def test_no_tag_means_no_emotion_and_untouched_text(self):
        assert parse_emotion_tag("Hello there") == (None, "Hello there")

    def test_unknown_emotion_is_dropped_but_the_tag_still_removed(self):
        emo, rest = parse_emotion_tag("[emotion: ecstatic 0.9] Hi")
        assert emo is None
        assert rest == "Hi"

    def test_a_bracket_that_is_not_our_tag_is_left_alone(self):
        assert parse_emotion_tag("[1] Stretch first") == (None, "[1] Stretch first")


@pytest.mark.unit
class TestEmotionTagStream:
    def test_tag_in_one_chunk(self):
        emotions, text = stream(["[emotion: happy 0.6] Xin chào", " bạn!"])
        assert emotions == [{"name": "happy", "intensity": 0.6}]
        assert text == "Xin chào bạn!"

    def test_tag_split_across_many_chunks_never_leaks(self):
        emotions, text = stream(["[", "emo", "tion: s", "ad 0.", "4]", " Tôi rất tiếc."])
        assert emotions == [{"name": "sad", "intensity": 0.4}]
        assert text == "Tôi rất tiếc."

    def test_reply_without_a_tag_streams_through_unchanged(self):
        emotions, text = stream(["Hello", " world"])
        assert emotions == []
        assert text == "Hello world"

    def test_numbered_list_starting_with_a_bracket_is_not_swallowed(self):
        emotions, text = stream(["[1", "] Warm up", " first"])
        assert emotions == []
        assert text == "[1] Warm up first"

    def test_reply_that_is_only_an_unfinished_tag_is_flushed_as_text(self):
        emotions, text = stream(["[emo"])
        assert emotions == []
        assert text == "[emo"

    def test_only_the_leading_tag_counts(self):
        emotions, text = stream(["Hi. ", "[emotion: happy] later"])
        assert emotions == []
        assert text == "Hi. [emotion: happy] later"


@pytest.mark.unit
class TestEmotionPolicy:
    def test_passes_ordinary_emotions_within_caps(self):
        assert apply_emotion_policy({"name": "happy", "intensity": 0.5}, "chat", []) == {"name": "happy", "intensity": 0.5}

    def test_sadness_is_capped_to_read_as_sympathy_not_distress(self):
        assert apply_emotion_policy({"name": "sad", "intensity": 1.0}, "synthesize", [])["intensity"] == 0.5

    def test_never_angry_at_a_patient(self):
        assert apply_emotion_policy({"name": "angry", "intensity": 0.9}, "chat", [])["name"] == "neutral"

    def test_safety_warnings_and_refusals_are_neutral(self):
        happy = {"name": "happy", "intensity": 0.8}
        assert apply_emotion_policy(happy, "synthesize", ["red_flag_screen"])["name"] == "neutral"
        assert apply_emotion_policy(happy, "synthesize", ["referral_advice"])["name"] == "neutral"
        assert apply_emotion_policy(happy, "refuse", [])["name"] == "neutral"

    def test_no_emotion_stays_none(self):
        assert apply_emotion_policy(None, "chat", []) is None


# ── Synthesizer integration ────────────────────────────────────────────────
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from langchain_core.messages import AIMessage, AIMessageChunk  # noqa: E402


def _state(required=None):
    return {
        "messages": [], "resolved_query": "hello",
        "required_outputs": required or [], "needs_clarification": False,
        "total_tokens": 0,
    }


def _config():
    return {"configurable": {"request_id": "re1", "persona_id": "anne", "query": "hello"}}


def _astream(*parts):
    async def gen(_msgs):
        for p in parts:
            yield AIMessageChunk(content=p)
    return gen


@pytest.mark.unit
class TestSynthesizerEmotion:
    @pytest.mark.asyncio
    async def test_stream_sends_emotion_first_then_only_clean_text(self):
        from langgraph_agents.nodes import synthesizer as syn_mod

        sent = []
        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
            mock_llm.return_value.astream = _astream("[emo", "tion: happy 0.7]", " Xin chào", " bạn!")
            result = await syn_mod.synthesizer_node(_state(), _config())

        assert sent[0] == {"emotion": {"name": "happy", "intensity": 0.7}}
        assert "".join(p["content"] for p in sent[1:]) == "Xin chào bạn!"
        assert result["final_answer"] == "Xin chào bạn!"  # history, TTS, grader see no tag

    @pytest.mark.asyncio
    async def test_safety_turn_forces_neutral_face(self):
        from langgraph_agents.nodes import synthesizer as syn_mod

        sent = []
        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
            mock_llm.return_value.astream = _astream("[emotion: happy 0.9] See a doctor.")
            await syn_mod.synthesizer_node(_state(["red_flag_screen"]), _config())

        assert sent[0] == {"emotion": {"name": "neutral", "intensity": 1.0}}

    @pytest.mark.asyncio
    async def test_no_tag_no_emotion_event(self):
        from langgraph_agents.nodes import synthesizer as syn_mod

        sent = []
        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
            mock_llm.return_value.astream = _astream("Hello", " there")
            result = await syn_mod.synthesizer_node(_state(), _config())

        assert all("emotion" not in p for p in sent)
        assert result["final_answer"] == "Hello there"

    @pytest.mark.asyncio
    async def test_fallback_answer_is_cleaned_too(self):
        from langgraph_agents.nodes import synthesizer as syn_mod

        sent = []
        fallback = MagicMock()
        fallback.ainvoke = AsyncMock(return_value=AIMessage(content="[emotion: relaxed 0.5] Thở đều nhé."))
        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_fallback_chat_model", return_value=fallback), \
             patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
            async def boom(_m):
                raise TimeoutError("primary down")
                yield  # pragma: no cover  (makes this an async generator)
            mock_llm.return_value.astream = boom
            result = await syn_mod.synthesizer_node(_state(), _config())

        assert result["final_answer"] == "Thở đều nhé."
        assert {"emotion": {"name": "relaxed", "intensity": 0.5}} in sent
        assert {"content": "Thở đều nhé."} in sent

    @pytest.mark.asyncio
    async def test_non_streaming_path_strips_the_tag(self):
        from langgraph_agents.nodes import synthesizer as syn_mod

        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_stream_writer", side_effect=RuntimeError("no stream")):
            mock_llm.return_value.ainvoke = AsyncMock(return_value=AIMessage(content="[emotion: sad 0.4] Tôi rất tiếc."))
            result = await syn_mod.synthesizer_node(_state(), _config())

        assert result["final_answer"] == "Tôi rất tiếc."

    @pytest.mark.asyncio
    async def test_prompt_asks_for_the_tag_last_and_can_be_switched_off(self, monkeypatch):
        from langgraph_agents.nodes import synthesizer as syn_mod

        captured = {}

        async def ainvoke(msgs):
            captured["last"] = msgs[-1].content
            return AIMessage(content="ok")

        with patch.object(syn_mod, "get_chat_model") as mock_llm, \
             patch.object(syn_mod, "get_stream_writer", side_effect=RuntimeError("no stream")):
            mock_llm.return_value.ainvoke = ainvoke
            await syn_mod.synthesizer_node(_state(), _config())
            assert "[emotion: NAME INTENSITY]" in captured["last"]

            monkeypatch.setenv("VVA_REPLY_EMOTION", "0")
            await syn_mod.synthesizer_node(_state(), _config())
            assert "[emotion:" not in captured["last"]
