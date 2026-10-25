"""A turn that stops part-way still leaves its question behind (memory-v2, A5).

/chat used to write a turn once, after the graph had finished — question and
answer in one statement. Anything that stopped the turn first (Stop, a dropped
connection, a Lambda timeout, a graph error) left nothing, so the next turn's
model did not know the user had asked anything.

Now the question is written as the turn starts, beside the graph, and the
answer once there is one. Driven on the generator itself: Starlette's
TestClient buffers the whole body, and these tests are about what happens
before the body is complete.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import langgraph_agents.api.main as api_module
from langgraph_agents.api.schemas import ChatRequest

pytestmark = pytest.mark.unit

_USER = "00000000-0000-0000-0000-000000000001"
_QUESTION_ID = "33333333-3333-3333-3333-333333333333"


class _Store:
    """Both session writes, recording the order things happen in."""

    def __init__(self):
        self.log: list[str] = []
        self.questions: list[dict] = []
        self.answers: list[dict] = []
        self.question_gate: asyncio.Event | None = None   # the write waits on it
        self.question_delay = 0.0
        self.question_failures = 0                        # first N calls raise
        self.answer_gate: asyncio.Event | None = None     # the answer write waits on it
        self.answer_fails = False

    async def write_turn_question(self, **kwargs):
        self.questions.append(kwargs)
        self.log.append("question_started")
        if self.question_gate is not None:
            await self.question_gate.wait()
        if self.question_delay:
            await asyncio.sleep(self.question_delay)
        if len(self.questions) <= self.question_failures:
            self.log.append("question_failed")
            raise RuntimeError("DB down")
        self.log.append("question_saved")

    async def write_turn_answer(self, **kwargs):
        self.answers.append(kwargs)
        if self.answer_gate is not None:
            await self.answer_gate.wait()
        if self.answer_fails:
            raise RuntimeError("DB down")
        self.log.append("answer_saved")
        return "a-1"


@pytest.fixture
def store(monkeypatch):
    fake = _Store()
    monkeypatch.setattr(api_module, "write_turn_question", fake.write_turn_question)
    monkeypatch.setattr(api_module, "write_turn_answer", fake.write_turn_answer)
    monkeypatch.setattr(api_module, "housekeep", AsyncMock())
    return fake


def _use_graph(monkeypatch, astream):
    graph = MagicMock()
    graph.astream = astream
    monkeypatch.setattr(api_module, "_graph", graph)


async def _answering_graph(*_a, **_k):
    yield ("updates", {"memory": {}})
    yield ("custom", {"content": "Xin "})
    yield ("custom", {"content": "chào."})
    yield ("updates", {"synthesizer": {"final_answer": "Xin chào.", "total_tokens": 3}})


def _stream(request=None, user_id=_USER):
    req = ChatRequest(query="Xin chào", session_id="s-1")
    config = {"configurable": {"user_message_id": _QUESTION_ID}}
    return api_module._stream_chat(
        req, "r-1", config, {}, None, request, resolved_user_id=user_id)


async def _events(stream) -> list[str]:
    return [event["event"] async for event in stream]


# ── The question is written whatever becomes of the turn ─────────────────


@pytest.mark.asyncio
async def test_question_is_saved_when_the_graph_raises(monkeypatch, store):
    async def exploding_graph(*_a, **_k):
        raise RuntimeError("graph exploded")
        yield  # pragma: no cover — makes this an async generator function

    _use_graph(monkeypatch, exploding_graph)

    # The stream is no longer cut: the user gets `error` then `done`.
    kinds = await _events(_stream())
    assert kinds[-2:] == ["error", "done"]

    # Already there when the stream ends: on Lambda nothing runs after it.
    assert store.log == ["question_started", "question_saved"]
    assert store.questions == [{
        "user_id": _USER, "session_id": "s-1",
        "message_id": _QUESTION_ID, "user_query": "Xin chào",
    }]
    assert store.answers == []


@pytest.mark.asyncio
async def test_question_is_saved_when_the_turn_produces_no_final_answer(monkeypatch, store):
    async def silent_graph(*_a, **_k):
        yield ("updates", {"memory": {}})

    _use_graph(monkeypatch, silent_graph)
    store.question_delay = 0.05          # slower than the graph

    kinds = await _events(_stream())

    assert store.log == ["question_started", "question_saved"]
    assert store.answers == []
    assert "session_persisted" not in kinds
    assert kinds[-1] == "done"


@pytest.mark.asyncio
async def test_cutting_the_stream_does_not_cancel_the_question_write(monkeypatch, store):
    """The framework cancels the generator when the client goes away. The
    write is a task of its own, and the module keeps the reference to it."""
    _use_graph(monkeypatch, _answering_graph)
    store.question_gate = asyncio.Event()

    consumer = asyncio.create_task(_events(_stream()))
    await asyncio.sleep(0.05)            # graph done; the stream waits on the write
    assert store.log == ["question_started"]
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    assert len(api_module._question_write_tasks) == 1

    store.question_gate.set()
    await _drain_answer_writes()
    assert store.log == ["question_started", "question_saved", "answer_saved"]
    assert not api_module._question_write_tasks


# ── It does not hold up the answer on screen ─────────────────────────────


@pytest.mark.asyncio
async def test_tokens_arrive_while_the_question_write_is_still_in_flight(monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)
    store.question_gate = asyncio.Event()
    seen_before_the_write_finished: list[str] = []

    async def drive():
        async for event in _stream():
            if "question_saved" not in store.log:
                seen_before_the_write_finished.append(event["event"])
            if event["event"] == "token":
                store.question_gate.set()

    # A stream that waited for the write would never reach the token that
    # releases it; the timeout turns that into a failure instead of a hang.
    await asyncio.wait_for(drive(), timeout=5)

    assert "token" in seen_before_the_write_finished
    assert store.log == ["question_started", "question_saved", "answer_saved"]


# ── Question before answer ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_is_written_only_after_the_question_write_has_finished(monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)
    store.question_delay = 0.1           # the graph is done long before

    kinds = await _events(_stream())

    assert store.log == ["question_started", "question_saved", "answer_saved"]
    assert store.answers[0]["assistant_answer"] == "Xin chào."
    assert store.answers[0]["user_query"] == "Xin chào"
    assert store.answers[0]["session_id"] == "s-1"
    assert kinds.index("session_persisted") < kinds.index("done")


@pytest.mark.asyncio
async def test_a_failed_question_write_is_retried_once_before_the_answer(monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)
    store.question_failures = 1

    kinds = await _events(_stream())

    assert store.log == ["question_started", "question_failed",
                         "question_started", "question_saved", "answer_saved"]
    assert store.questions[0] == store.questions[1]      # same row id both times
    assert "session_persisted" in kinds


@pytest.mark.asyncio
async def test_no_answer_is_written_when_the_question_cannot_be_saved(
        monkeypatch, store, caplog):
    """An answer with no question before it is not a turn anyone can read
    back. Two attempts, then the turn goes unsaved as a failed write always
    has: logged, no `session_persisted`, and the stream still ends in `done`."""
    _use_graph(monkeypatch, _answering_graph)
    store.question_failures = 2

    with caplog.at_level(logging.WARNING, logger="langgraph.api"):
        kinds = await _events(_stream())

    assert len(store.questions) == 2
    assert store.answers == []
    assert "session_persisted" not in kinds
    assert kinds[-1] == "done"
    assert [r for r in caplog.records if r.getMessage() == "session_persist_failed"]


# ── The server does not stop a turn because the client went away ─────────


@pytest.mark.asyncio
async def test_a_disconnected_client_does_not_end_the_turn(monkeypatch, store):
    reached_the_end = []

    async def graph(*_a, **_k):
        async for item in _answering_graph():
            yield item
        reached_the_end.append(True)

    _use_graph(monkeypatch, graph)
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=True)

    kinds = await _events(_stream(request=request))

    assert reached_the_end == [True]
    assert kinds.count("token") == 2
    assert store.log == ["question_started", "question_saved", "answer_saved"]
    assert "session_persisted" in kinds
    assert kinds[-1] == "done"


# ── Housekeeping is not left running behind a stream that was cut short ──


class _Housekeeping:
    def __init__(self):
        self.started = False
        self.cancelled = False

    async def __call__(self, user_id, session_id, **_budgets):
        self.started = True
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_housekeeping_is_cancelled_when_the_stream_is_closed_early(monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)
    housekeeping = _Housekeeping()
    monkeypatch.setattr(api_module, "housekeep", housekeeping)

    stream = _stream()
    await stream.__anext__()             # the turn is under way
    await asyncio.sleep(0)               # ... and so is housekeeping
    await stream.aclose()                # what the framework does on a disconnect
    await asyncio.sleep(0.01)

    assert housekeeping.started and housekeeping.cancelled


@pytest.mark.asyncio
async def test_housekeeping_is_cancelled_when_the_graph_raises(monkeypatch, store):
    async def failing_graph(*_a, **_k):
        yield ("updates", {"memory": {}})
        raise RuntimeError("graph exploded")

    _use_graph(monkeypatch, failing_graph)
    housekeeping = _Housekeeping()
    monkeypatch.setattr(api_module, "housekeep", housekeeping)

    kinds = await _events(_stream())
    assert kinds[-2:] == ["error", "done"]
    await asyncio.sleep(0.01)

    assert housekeeping.started and housekeeping.cancelled


# ── A cut turn keeps what it got to (memory-v2, A5b) ─────────────────────


async def _graph_stalling_after_tokens(*_a, **_k):
    yield ("updates", {"memory": {}})
    yield ("custom", {"content": "Xin "})
    yield ("custom", {"content": "chào"})
    await asyncio.Event().wait()         # the graph is still working


async def _graph_stalling_before_tokens(*_a, **_k):
    yield ("updates", {"memory": {}})
    await asyncio.Event().wait()


async def _drain_answer_writes():
    """Detached tasks outlive the generator; wait for them, don't sleep."""
    await asyncio.gather(*api_module._question_write_tasks, *api_module._answer_write_tasks)


async def _cut_after(stream, kind, count):
    """Read events until `count` of `kind` were seen, then close the stream the
    way the framework does on a disconnect."""
    seen = 0
    async for event in stream:
        seen += event["event"] == kind
        if seen == count:
            break
    await stream.aclose()


@pytest.mark.asyncio
async def test_text_streamed_before_the_cut_is_saved_as_an_interrupted_answer(
        monkeypatch, store):
    _use_graph(monkeypatch, _graph_stalling_after_tokens)
    store.question_gate = asyncio.Event()

    await _cut_after(_stream(), "token", 2)

    assert store.answers == []           # the question is not in the table yet
    store.question_gate.set()
    await _drain_answer_writes()

    assert store.log == ["question_started", "question_saved", "answer_saved"]
    assert len(store.answers) == 1
    answer = store.answers[0]
    assert answer["assistant_answer"] == "Xin chào"
    assert answer["meta"]["interrupted"] is True
    assert answer["meta"]["request_id"] == "r-1"
    assert answer["session_id"] == "s-1" and answer["user_query"] == "Xin chào"


@pytest.mark.asyncio
async def test_cancelling_the_consumer_also_saves_the_streamed_text(monkeypatch, store):
    _use_graph(monkeypatch, _graph_stalling_after_tokens)
    tokens = []

    async def drive():
        async for event in _stream():
            tokens.append(event)

    consumer = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await _drain_answer_writes()

    assert [a["assistant_answer"] for a in store.answers] == ["Xin chào"]
    assert store.answers[0]["meta"]["interrupted"] is True


@pytest.mark.asyncio
async def test_a_cut_before_any_text_writes_no_answer(monkeypatch, store):
    _use_graph(monkeypatch, _graph_stalling_before_tokens)

    stream = _stream()
    await stream.__anext__()             # memory finished; no token yet
    await stream.aclose()
    await _drain_answer_writes()

    assert store.answers == []
    assert store.log == ["question_started", "question_saved"]


@pytest.mark.asyncio
async def test_whitespace_alone_is_not_an_answer(monkeypatch, store):
    async def graph(*_a, **_k):
        yield ("custom", {"content": " \n"})
        await asyncio.Event().wait()

    _use_graph(monkeypatch, graph)

    await _cut_after(_stream(), "token", 1)
    await _drain_answer_writes()

    assert store.answers == []


@pytest.mark.asyncio
async def test_a_cut_during_the_write_of_a_complete_answer_still_saves_it_once(
        monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)
    store.answer_gate = asyncio.Event()

    stream = _stream()
    async for event in stream:
        if event["event"] == "token":
            break
    consumer = asyncio.create_task(_events(stream))
    await asyncio.sleep(0.05)            # graph done; the stream waits on the write
    assert len(store.answers) == 1
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    store.answer_gate.set()
    await _drain_answer_writes()

    assert store.log == ["question_started", "question_saved", "answer_saved"]
    assert len(store.answers) == 1
    assert store.answers[0]["assistant_answer"] == "Xin chào."
    assert "interrupted" not in store.answers[0]["meta"]


@pytest.mark.asyncio
async def test_a_completed_turn_writes_one_answer_without_the_interrupted_key(
        monkeypatch, store):
    _use_graph(monkeypatch, _answering_graph)

    kinds = await _events(_stream())

    assert len(store.answers) == 1
    assert "interrupted" not in store.answers[0]["meta"]
    assert store.answers[0]["meta"]["request_id"] == "r-1"
    assert kinds.count("session_persisted") == 1
    assert not api_module._answer_write_tasks


@pytest.mark.asyncio
async def test_a_graph_error_after_some_tokens_leaves_only_the_question(monkeypatch, store):
    async def failing_graph(*_a, **_k):
        yield ("custom", {"content": "Xin "})
        raise RuntimeError("graph exploded")

    _use_graph(monkeypatch, failing_graph)

    kinds = await _events(_stream())
    await _drain_answer_writes()

    assert kinds[-2:] == ["error", "done"]
    assert store.answers == []
    assert store.log == ["question_started", "question_saved"]


@pytest.mark.asyncio
async def test_a_failing_partial_write_does_not_raise_and_is_logged(
        monkeypatch, store, caplog):
    _use_graph(monkeypatch, _graph_stalling_after_tokens)
    store.answer_fails = True

    with caplog.at_level(logging.WARNING, logger="langgraph.api"):
        await _cut_after(_stream(), "token", 2)
        await _drain_answer_writes()     # would raise if the task let it out

    assert [r for r in caplog.records if r.getMessage() == "partial_answer_persist_failed"]
