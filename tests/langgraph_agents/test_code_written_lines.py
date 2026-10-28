"""Câu an toàn và dòng nguồn — các chủ sở hữu mới sau khi grader bỏ.

- `guardrail.stream` viết câu cảnh báo (head) trước chữ đầu tiên và dòng kết
  (tail) sau thân bài; `synthesizer` chỉ viết phần thân giữa hai đầu.
- `api/main.py` nối head + body + tail thành composed text, lưu và nói chính
  văn bản đó.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

from langgraph_agents.tag_contract import get_safety_text

pytestmark = pytest.mark.unit


def _config(**kw):
    base = {"persona_id": "anne", "locale": "vi", "query": "đau ngực",
            "request_id": "t"}
    base.update(kw)
    return {"configurable": base}


def _state(required=None, messages=None, **kw):
    return {"messages": messages or [], "resolved_query": "đau ngực",
            "required_outputs": required or [], "needs_clarification": False,
            "total_tokens": 0, "errors": [], **kw}


def _config(**kw):
    base = {"persona_id": "anne", "locale": "vi", "query": "đau ngực",
            "request_id": "t"}
    base.update(kw)
    return {"configurable": base}


def _tm(name, payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return ToolMessage(content=content, tool_call_id=f"tc-{name}", name=name)


_KB = [{"content": "Cat-cow for back pain.", "similarity": 0.9,
        "source_type": "exercise_db", "document_title": "Back", "chunk_index": 0}]


def _astream(*parts):
    async def gen(_msgs):
        for p in parts:
            yield AIMessageChunk(content=p)
    return gen


def _streaming_llm(captured, *parts):
    mock_llm = MagicMock()

    async def gen(msgs):
        captured.append(msgs)
        for p in parts:
            yield AIMessageChunk(content=p)
    mock_llm.astream = gen
    return mock_llm


def _verdict(level="routine", out_of_scope=False, health_topic=False,
             source="llm", elapsed_ms=5):
    from langgraph_agents.guardrail.types import Verdict
    return Verdict(level=level, out_of_scope=out_of_scope,
                   health_topic=health_topic, source=source,
                   elapsed_ms=elapsed_ms)


OPENING = get_safety_text("red_flag_screen", "anne", "vi")


# ── Synthesizer: body only between the guardrail's fixed ends ─────────────

@pytest.mark.asyncio
async def test_synthesizer_no_longer_emits_opening_line():
    """Synthesizer writes the body only; the guardrail's head comes first
    (emitted by api/main.py before the first body token), never here."""
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_streaming_llm(captured, "Đau ngực cần khám.")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append), \
         patch.object(syn_mod, "current_verdict",
                      new=AsyncMock(return_value=_verdict("soon"))):
        # Planner no longer emits safety tags; quality tags only
        result = await syn_mod.synthesizer_node(
            _state(["scope_disclaimer"]), _config())
    contents = [p["content"] for p in sent if "content" in p]
    assert contents and contents[0] == "Đau ngực cần khám."
    assert not result["final_answer"].startswith(OPENING)
    assert result["final_answer"] == "Đau ngực cần khám."


@pytest.mark.asyncio
async def test_directive_only_on_urgent_turns():
    from langgraph_agents.nodes import synthesizer as syn_mod

    captured: list = []
    sent: list = []
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_streaming_llm(captured, "ok")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append):
        # routine → no safety block
        await syn_mod.synthesizer_node(_state(), _config())
        routine_prompt = captured[0][0].content
        assert "## Safety rule for this turn" not in routine_prompt

        captured.clear()
        # urgent → safety block present
        with patch.object(syn_mod, "current_verdict",
                          new=AsyncMock(return_value=_verdict("emergency"))):
            await syn_mod.synthesizer_node(_state(), _config())
            urgent_prompt = captured[0][0].content
            assert "## Safety rule for this turn" in urgent_prompt
            assert "emergency care" in urgent_prompt.lower()


@pytest.mark.asyncio
async def test_urgent_verdict_never_yields_clarify_mode():
    from langgraph_agents.nodes import synthesizer as syn_mod

    with patch.object(syn_mod, "current_verdict",
                      new=lambda config: _verdict("emergency")):
        mode = syn_mod._derive_mode(
            {"needs_clarification": True, "required_outputs": [], "messages": []},
            _verdict("emergency"))
    assert mode != "clarify"


@pytest.mark.asyncio
async def test_source_line_appended_once_when_evidence_citation():
    from langgraph_agents.nodes import synthesizer as syn_mod
    from langgraph_agents.evidence import EvidenceItem

    captured: list = []
    sent: list = []
    items = [EvidenceItem(header="[From ECA's exercise library: Elbow plank]",
                          text="x", cite_key="exercise_db", title="Elbow plank")]
    elbow_msg = _tm("kb_search", [{"content": "x", "similarity": 0.9,
                                   "source_type": "exercise_db",
                                   "document_title": "Elbow plank",
                                   "chunk_index": 0}])
    with patch.object(syn_mod, "get_chat_model",
                      return_value=_streaming_llm(captured,
                                                  "Giơ tay phải lên theo Elbow plank.")), \
         patch.object(syn_mod, "get_stream_writer", return_value=sent.append), \
         patch.object(syn_mod, "current_verdict",
                      new=AsyncMock(return_value=_verdict("routine"))):
        result = await syn_mod.synthesizer_node(
            _state(["evidence_citation"], [elbow_msg]), _config())
    source = "*Nguồn: thư viện bài tập của ECA — Elbow plank.*"
    # Appended exactly once, after the body.
    assert result["final_answer"].count(source) == 1
    assert result["final_answer"].endswith(source)
    # One source token event on the wire.
    source_events = [p for p in sent if source in p.get("content", "")]
    assert len(source_events) == 1


# ── api/main.py: head before first body token, tail once, composed text ───

def _make_fake_astream_with_head_tail(tokens, final_answer):
    """custom tokens, then synthesizer final_answer (no grader any more)."""

    async def fake_stream(state, config, stream_mode=None):
        yield ("updates", {"memory": {}})
        yield ("updates", {"planner": {"required_outputs": []}})
        for tok in tokens:
            yield ("custom", {"content": tok})
        yield ("updates", {"synthesizer": {
            "final_answer": final_answer,
            "total_tokens": len(final_answer),
        }})

    return fake_stream


@pytest.mark.unit
def test_head_before_first_body_token_and_tail_once(monkeypatch):
    import langgraph_agents.api.main as api_module
    from langgraph_agents.api.main import create_app
    from langgraph_agents.api.auth import current_user_id, override_user
    from fastapi.testclient import TestClient

    from langgraph_agents.guardrail.pipeline import InputResult
    from langgraph_agents.guardrail.stream import compose, head, tail
    from langgraph_agents.guardrail.types import Verdict

    mock_redis = MagicMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_graph = MagicMock()
    mock_graph.astream = _make_fake_astream_with_head_tail(
        ["Chào ", "bạn"], "Chào bạn")
    api_module._graph = mock_graph
    api_module._health_redis = mock_redis

    app = create_app()
    app.dependency_overrides[current_user_id] = override_user(
        "00000000-0000-0000-0000-000000000001")
    client = TestClient(app)

    try:
        # Force an urgent verdict so the guardrail's head is emitted before
        # the first body token. `run_input` is the verdict source main.py uses.
        async def fake_run_input(ctx):
            return InputResult(_verdict("emergency"))

        monkeypatch.setattr(api_module.guardrail, "run_input", fake_run_input)

        captured = {}

        async def fake_write(*args, **kwargs):
            captured.update(kwargs)
            return "id-1"

        monkeypatch.setattr(api_module, "write_turn_question", AsyncMock())
        monkeypatch.setattr(api_module, "write_turn_answer", fake_write)
        monkeypatch.setattr(api_module, "housekeep", AsyncMock())

        resp = client.post("/chat", json={"query": "đau ngực"})
        assert resp.status_code == 200

        # Parse SSE: head must be the first token, tail the last, and the
        # persisted/spoken text must equal compose(head, body, tail).
        events = []
        current_event = None
        for line in resp.content.decode("utf-8").splitlines():
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                try:
                    events.append((current_event,
                                   json.loads(line[len("data:"):].strip())))
                except json.JSONDecodeError:
                    events.append((current_event, line[len("data:"):].strip()))
            else:
                current_event = None
        token_events = [d for ev, d in events if ev == "token"]
        joined = "".join(d.get("content", "") for d in token_events)

        expected_head = head(_verdict("emergency"), "anne", "en")
        expected_tail = tail(["referral_advice", "scope_disclaimer"], "anne",
                             "en")
        expected = compose(expected_head, "Chào bạn", expected_tail)
        assert joined == expected, f"tokens={joined!r} != expected={expected!r}"
        assert captured.get("assistant_answer") == expected
    finally:
        api_module._graph = None
        api_module._health_redis = None
        app.dependency_overrides.clear()

