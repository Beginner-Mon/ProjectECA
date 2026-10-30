"""Unit tests for the agent-loop probe harness: fake LLM, no network.

Covers the loop mechanics the card requires: stop on a plain answer, the
round cap, the call caps (per round and per turn), write-tool runs at most
once per call, keep-mode reasoning passback, and each task's check passing on
a scripted correct trace and failing on a scripted wrong one.

Fix round 1 additions:
    F1  ttft_final_s is the answer round's ttft_content_s; both ttfts recorded
        per round.
    F2  usage from a chunk stream whose usage arrives the way Groq sends it
        (usage on more than one chunk) is counted once; and the real
        langchain AIMessageChunk merge is shown to sum usage_metadata (the
        smoke's double-count mechanism).
    F3  reasoning_passthrough_supported flips on the request that actually
        carried reasoning (true on success, false on rejection, null before);
        reasoning chars sent/received are accumulated.
    F5  time-token normalization in checks: a correct answer written
        "7 am" / "2 pm" passes.
"""
from __future__ import annotations

import pytest

from langgraph_agents.local_tests.agent_loop.fake_tools import TOOL_SCHEMAS, bind_tools
from langgraph_agents.local_tests.agent_loop.loop import (
    ANSWER,
    CALL_CAP,
    ROUND_CAP,
    Client,
    KeepClient,
    Round,
    reduce_chat_stream,
    run_turn,
)
from langgraph_agents.local_tests.agent_loop.tasks import (
    TASKS,
    _norm,
    check_task,
    normalize_check_text,
    normalize_time_tokens,
    task_by_id,
)

pytestmark = pytest.mark.unit


def tool_call(cid: str, tool: str, **args) -> dict:
    return {"id": cid, "name": tool, "args": dict(args)}


class ScriptedClient(Client):
    """Plays a scripted sequence of rounds; the last entry repeats when exhausted."""

    mode = "drop"

    def __init__(self, script: list):
        self.script = list(script)
        self.current = ("answer", dict(content="", ttft_any=0.04, ttft_content=0.12))
        self.sent: list[list[dict]] = []     # every outgoing message list, per round

    def complete(self, messages, schemas):
        self.sent.append([dict(m) for m in messages])
        if self.script:
            self.current = self.script.pop(0)
        kind, payload = self.current
        base = {
            "latency_s": 0.02 if kind == "answer" else 0.03,
            "ttft_any_s": payload["ttft_any"],
            "ttft_content_s": payload.get("ttft_content"),
            "reasoning": payload.get("reasoning"),
            "reasoning_chars_received": payload.get("rchars", 0),
        }
        if kind == "answer":
            return Round(content=payload["content"], tool_calls=[], prompt_tokens=45,
                         completion_tokens=30, **base)
        if kind == "calls":
            return Round(content=None, tool_calls=payload["tool_calls"], prompt_tokens=60,
                         completion_tokens=18, **base)
        raise AssertionError(f"unknown scripted kind: {kind}")

    def append_assistant(self, messages, rnd):
        super().append_assistant(messages, rnd)


def answer_round(text: str, ttft_any: float = 0.04, ttft_content: float = 0.12,
                 reasoning: str | None = None, rchars: int = 0) -> tuple:
    return ("answer", dict(content=text, ttft_any=ttft_any, ttft_content=ttft_content,
                           reasoning=reasoning, rchars=rchars))


def calls_round(*tcs: dict, ttft_any: float = 0.02,
                reasoning: str | None = None, rchars: int = 0) -> tuple:
    return ("calls", dict(tool_calls=list(tcs), ttft_any=ttft_any,
                          reasoning=reasoning, rchars=rchars))


def run_script(script: list) -> tuple:
    state: dict = {}
    result = run_turn(ScriptedClient(script), bind_tools(state), TOOL_SCHEMAS, "test task")
    return result, state


def strip_calls(call_log: list[dict]) -> list[tuple]:
    return [(c["tool"], c["args"]) for c in call_log]


class TestLoopStops:
    def test_stops_on_answer_without_tool_calls(self):
        result, state = run_script([answer_round("Do a wall slide once a day.")])
        assert result.stop_reason == ANSWER
        assert result.rounds == 1
        assert result.call_log == []
        assert result.final_content == "Do a wall slide once a day."
        assert result.prompt_tokens == 45 and result.completion_tokens == 30

    def test_round_cap_six_rounds_of_new_calls(self):
        script = [calls_round(tool_call(f"c{i}", "find_exercise", condition="knee pain"))
                  for i in range(1, 9)]
        result, state = run_script(script)
        assert result.stop_reason == ROUND_CAP
        assert result.rounds == 6
        assert len(result.call_log) == 6
        assert result.final_content is None

    def test_call_cap_more_than_five_calls_in_one_round(self):
        script = [calls_round(*[tool_call(f"c{i}", "find_exercise", condition="knee pain")
                                for i in range(1, 8)])]
        result, state = run_script(script)
        # nothing executed: the round itself exceeded the per-round cap
        assert result.stop_reason == CALL_CAP
        assert result.call_log == []
        assert result.rounds == 1
        assert state.get("calls") == []

    def test_call_cap_twelve_calls_per_turn(self):
        def batch(tag: str, n: int) -> tuple:
            return calls_round(*[tool_call(f"{tag}{i}", "find_exercise", condition="knee pain")
                                 for i in range(1, n + 1)])

        script = [batch("a", 4), batch("b", 4), batch("c", 5)]
        result, state = run_script(script)
        # 4 + 4 executed; the third batch would take the turn past 12 -> stop, run nothing
        assert result.stop_reason == CALL_CAP
        assert len(result.call_log) == 8
        assert result.rounds == 3

    def test_error_stop_records_error_text(self):
        class BoomClient(Client):
            def complete(self, messages, schemas):
                raise RuntimeError("con agua")

        result = run_turn(BoomClient(), bind_tools({}), TOOL_SCHEMAS, "test")
        assert result.stop_reason == "error"
        assert "con agua" in (result.error or "")


class TestF1Timing:
    def test_ttft_final_is_the_answer_rounds_content_ttft(self):
        script = [
            calls_round(tool_call("c1", "find_exercise", condition="knee pain")),
            answer_round("Wall Slide at 7:00 AM.", ttft_any=0.07, ttft_content=0.12),
        ]
        result, state = run_script(script)
        assert result.ttft_final_s == 0.12
        assert result.per_round_ttft_any == [0.02, 0.07]
        assert result.per_round_ttft_content == [None, 0.12]   # calls round had no content
        assert result.total_latency_s > 0

    def test_ttft_final_is_none_when_the_turn_never_answers(self):
        script = [calls_round(tool_call(f"c{i}", "find_exercise", condition="knee pain"))
                  for i in range(1, 8)]
        result, state = run_script(script)
        assert result.stop_reason == ROUND_CAP
        assert result.ttft_final_s is None
        assert len(result.per_round_ttft_any) == 6


class TestF2UsageCountedOnce:
    def test_usage_arriving_on_two_groq_style_chunks_is_counted_once(self):
        class Chunk:
            def __init__(self, content, usage):
                self.content = content
                self.usage_metadata = usage
                self.tool_calls = []

            def __add__(self, other):
                return Chunk((self.content or "") + (other.content or ""), None)

        groq_style_usage = {"input_tokens": 450, "output_tokens": 90,
                            "output_token_details": {"reasoning": 17}}
        content, _, usage, ttft_any, ttft_content = reduce_chat_stream(
            [Chunk("", groq_style_usage), Chunk("Hello", groq_style_usage)], t0=0.0)
        assert usage["input_tokens"] == 450          # NOT 900 (merge-sum would say so)
        assert usage["output_tokens"] == 90          # NOT 180

    def test_last_usage_chunk_wins(self):
        class Chunk:
            def __init__(self, content, usage):
                self.content = content
                self.usage_metadata = usage
                self.tool_calls = []

            def __add__(self, other):
                return Chunk((self.content or "") + (other.content or ""), None)

        final = {"input_tokens": 700, "output_tokens": 30, "output_token_details": {}}
        content, _, usage, ttft_any, ttft_content = reduce_chat_stream(
            [Chunk("", {"input_tokens": 100, "output_tokens": 5}), Chunk("done", final)], t0=0.0)
        assert usage["input_tokens"] == 700
        assert content == "done"
        assert usage.get("output_token_details") == {}
        assert ttft_any is not None and ttft_content is not None

    def test_real_langchain_merge_sums_usage_metadata(self):
        """The smoke's double-count mechanism, seen live: ChatOpenAI's
        `full = full + chunk` yields AIMessageChunks whose merge SUMS
        usage_metadata — two usage-bearing chunks double the tokens."""
        from langchain_core.messages import AIMessageChunk

        um = {"input_tokens": 450, "output_tokens": 90, "total_tokens": 540}
        merged = AIMessageChunk(content="", usage_metadata=dict(um)) \
            + AIMessageChunk(content="ok", usage_metadata=dict(um))
        assert merged.usage_metadata["input_tokens"] == 900          # summing, not last-wins
        assert merged.usage_metadata["total_tokens"] == 1080
        # and the fixed reader takes the LAST chunk's value instead:
        _, _, usage, _, _ = reduce_chat_stream(
            [AIMessageChunk(content="", usage_metadata=dict(um)),
             AIMessageChunk(content="ok", usage_metadata=dict(um))], t0=0.0)
        assert usage["input_tokens"] == 450


class TestToolExecution:
    def test_write_tool_runs_at_most_once_per_call(self):
        # the same tool-call id repeated inside one round dedupes to a single execution
        script = [
            calls_round(tool_call("c1", "create_reminder", title="Wall Slide",
                                  date="2026-11-02", time="07:00"),
                        tool_call("c1", "create_reminder", title="Wall Slide",
                                  date="2026-11-02", time="07:00")),
            answer_round("Reminder set."),
        ]
        client = ScriptedClient(script)
        state: dict = {}
        result = run_turn(client, bind_tools(state), TOOL_SCHEMAS, "test")
        assert result.stop_reason == ANSWER
        assert strip_calls(result.call_log)[0][0] == "create_reminder"
        assert len(result.call_log) == 1          # executed exactly once
        assert len(state["reminders"]) == 1       # written exactly once
        assert state["calls"] and state["calls"][0]["ok"] is True
        # the model received a tool result for BOTH of its repeated ids, none re-run
        round2 = client.sent[1]
        tool_msgs = [m for m in round2 if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        assert all(t["tool_call_id"] == "c1" for t in tool_msgs)

    def test_repeated_call_gets_its_earlier_result_again(self):
        script = [
            calls_round(tool_call("c1", "find_exercise", condition="knee pain")),
            calls_round(tool_call("c1", "find_exercise", condition="knee pain")),
            answer_round("Wall Slide (quadriceps)."),
        ]
        result, state = run_script(script)
        assert result.stop_reason == ANSWER
        assert len(result.call_log) == 1          # the tool itself ran once
        assert result.rounds == 3

    def test_unknown_tool_returns_error_to_model(self):
        script = [
            calls_round(tool_call("c1", "no_such_tool")),
            answer_round("I asked for a tool that does not exist; sorry."),
        ]
        result, state = run_script(script)
        assert result.stop_reason == ANSWER
        assert result.call_log[0]["result"].get("error", "").startswith("KeyError")


class ScriptedKeepClient(ScriptedClient):
    """A scripted client that ALSO attaches each round's reasoning to the
    outgoing assistant message (what KeepClient does). Optionally rejects
    requests that carry reasoning — the keep-mode failure path — or raises
    any other scripted error text."""

    mode = "keep"

    def __init__(self, script: list, reject_when_reasoning: bool = False,
                 reject_without_reasoning: bool = False, raise_text: str | None = None):
        super().__init__(script)
        self.reject_when_reasoning = reject_when_reasoning
        self.reject_without_reasoning = reject_without_reasoning
        self.raise_text = raise_text

    def append_assistant(self, messages, rnd):
        KeepClient.__new__(KeepClient).append_assistant(messages, rnd)   # no SDK needed

    def complete(self, messages, schemas):
        if self.raise_text:
            raise RuntimeError(self.raise_text)
        carried = last_assistant_reasoning(messages)
        if self.reject_when_reasoning and carried:
            raise RuntimeError("Error code: 400 - Unrecognized request argument "
                               "supplied: reasoning")
        if self.reject_without_reasoning and not carried:
            raise RuntimeError("provider rejected the request for another reason")
        return super().complete(messages, schemas)


def last_assistant_reasoning(messages: list[dict]) -> str | None:
    """The reasoning text the NEXT request will carry (last assistant message)."""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            return m.get("reasoning")
    return None


class TestKeepModePassthrough:
    def test_reasoning_text_is_sent_back_in_the_next_request(self):
        client = KeepClient(api_key="unused-in-unit-test", base_url="http://127.0.0.1:1",
                            model="openai/gpt-oss-120b", reasoning_effort="low")
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        rnd = Round(content=None, tool_calls=[{"id": "c1", "name": "find_exercise",
                                               "args": {"condition": "knee pain"}}],
                    reasoning="I need an exercise first.")
        client.append_assistant(messages, rnd)
        asst = messages[-1]
        assert asst["role"] == "assistant"
        assert asst["reasoning"] == "I need an exercise first."
        assert asst["tool_calls"][0]["function"]["arguments"] == '{"condition": "knee pain"}'

    def test_repeated_assistant_turns_keep_their_own_reasoning(self):
        client = KeepClient.__new__(KeepClient)   # avoid even constructing the SDK client
        messages = [{"role": "user", "content": "task"}]
        for i in range(1, 4):
            client.append_assistant(messages, Round(content=f"round {i}", tool_calls=[],
                                                    reasoning=f"thought {i}"))
        assert [m.get("reasoning") for m in messages] == \
            [None, "thought 1", "thought 2", "thought 3"]


class TestF3ReasoningEvidence:
    def test_passthrough_true_and_chars_sent_when_a_reasoning_request_succeeds(self):
        reason_1 = "I need the slots first."
        reason_2 = "Slot known; answering."
        script = [
            calls_round(tool_call("c1", "get_free_slots", date="2026-11-02"),
                        reasoning=reason_1, rchars=len(reason_1)),
            answer_round("07:00 works.", reasoning=reason_2, rchars=len(reason_2)),
        ]
        result = run_turn(ScriptedKeepClient(script), bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == ANSWER
        # request 2 carried the round-1 reasoning and succeeded
        assert result.reasoning_passthrough_supported is True
        assert result.reasoning_chars_sent == len(reason_1)
        assert result.reasoning_chars_received == len(reason_1) + len(reason_2)

    def test_passthrough_false_when_a_reasoning_request_is_rejected(self):
        reason_1 = "I need the slots first."
        script = [
            calls_round(tool_call("c1", "get_free_slots", date="2026-11-02"),
                        reasoning=reason_1, rchars=len(reason_1)),
            calls_round(tool_call("c2", "find_exercise", condition="knee pain")),
        ]
        result = run_turn(ScriptedKeepClient(script, reject_when_reasoning=True),
                          bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == "error"
        # fix round 3: the rejection text must be an HTTP 400 that mentions
        # reasoning; both are part of the flip condition now
        err = (result.error or "").lower()
        assert "reasoning" in err and "400" in err
        assert result.reasoning_passthrough_supported is False

    def test_passthrough_stays_null_until_reasoning_is_first_sent(self):
        # round 1's request carries no reasoning; rejection there must NOT claim
        # the passthrough was tested and failed
        result = run_turn(ScriptedKeepClient([], reject_without_reasoning=True),
                          bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == "error"
        assert result.reasoning_passthrough_supported is None
        assert result.reasoning_chars_sent == 0


class TestFix3PassthroughOnlyForReasoning400:
    """Fix round 3 item 2, loop side: `reasoning_passthrough_supported` is
    false ONLY for an HTTP 400 whose message mentions `reasoning`; any other
    error leaves the flag exactly as it was."""

    def test_connection_error_on_a_reasoning_request_is_not_a_rejection(self):
        # matrix-v1 row 28's exact failure: keep request 2 carried reasoning,
        # the connection died, and the flag was wrongly flipped to False.
        reason_1 = "I need the slots first."
        script = [
            calls_round(tool_call("c1", "get_free_slots", date="2026-11-02"),
                        reasoning=reason_1, rchars=len(reason_1)),
            calls_round(tool_call("c2", "find_exercise", condition="knee pain")),
        ]
        result = run_turn(
            ScriptedKeepClient(script, raise_text="APIConnectionError: Connection error."),
            bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == "error"
        assert result.reasoning_passthrough_supported is None   # as-was, not False

    def test_a_400_without_reasoning_in_the_message_does_not_flip(self):
        reason_1 = "I need the slots first."
        script = [
            calls_round(tool_call("c1", "get_free_slots", date="2026-11-02"),
                        reasoning=reason_1, rchars=len(reason_1)),
            calls_round(tool_call("c2", "find_exercise", condition="knee pain")),
        ]
        result = run_turn(
            ScriptedKeepClient(
                script, raise_text="Error code: 400 - Invalid parameter: max_tokens"),
            bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == "error"
        assert result.reasoning_passthrough_supported is None

    def test_a_later_transient_error_keeps_an_earlier_true(self):
        reason_1 = "I need the slots first."
        script = [
            calls_round(tool_call("c1", "get_free_slots", date="2026-11-02"),
                        reasoning=reason_1, rchars=len(reason_1)),
            calls_round(tool_call("c2", "find_exercise", condition="knee pain")),
            calls_round(tool_call("c3", "get_free_slots", date="2026-11-05")),
        ]
        client = ScriptedKeepClient(script)
        # raise only on the THIRD request: the first two run as scripted (the
        # second one carries round-1 reasoning and succeeds -> True), then the
        # connection dies. True must survive the failure.
        original_complete = ScriptedClient.complete

        def complete_then_die(self, messages, schemas):
            if len(self.sent) >= 2:
                raise RuntimeError("APIConnectionError: Connection error.")
            return original_complete(self, messages, schemas)
        client.complete = complete_then_die.__get__(client)
        result = run_turn(client, bind_tools({}), TOOL_SCHEMAS, "task")
        assert result.stop_reason == "error"
        assert result.reasoning_passthrough_supported is True  # round-2 success stands


class TestFix3UnicodeNormalization:
    """Fix round 3 item 1: NFKC + dash/space mapping before the time
    normalization. The two unit answers are the REAL matrix-v1 `back` rows
    (`back drop low r1`, `back keep high r3`), written with \\u escapes so
    the exact codepoints U+202F (narrow no-break space) and U+2011
    (non-breaking hyphen) survive every editor."""

    DROP_LOW_R1 = (
        "You should do the Bird\u202fDog exercise for your lower back pain at "
        "08:00 on 2026\u201111\u201103. A reminder has been set for that time.")
    KEEP_HIGH_R3 = (
        "The Bird\u202fDog is an effective strengthening exercise for lower back "
        "pain, helping improve spinal stability and reduce discomfort. "
        "I\u2019ve set a reminder for you to do the Bird\u202fDog at 08:00 on "
        "2026\u201111\u201103. Let\u2019s get started with it first thing in the "
        "morning!")
    BACK_LOG = [
        {"tool": "find_exercise", "args": {"condition": "lower back pain"}},
        {"tool": "get_free_slots", "args": {"date": "2026-11-03"}},
        {"tool": "create_reminder",
         "args": {"date": "2026-11-03", "time": "08:00",
                  "title": "Do Bird Dog exercise"}},
    ]

    def test_back_drop_low_r1_row_passes_with_current_checks(self):
        assert check_task(task_by_id("back"), self.BACK_LOG,
                          self.DROP_LOW_R1) is None

    def test_back_keep_high_r3_row_passes_with_current_checks(self):
        assert check_task(task_by_id("back"), self.BACK_LOG,
                          self.KEEP_HIGH_R3) is None

    def test_normalize_check_text_maps_dashes_and_spaces(self):
        assert normalize_check_text("2026\u201111\u201103") == "2026-11-03"
        assert normalize_check_text("Bird\u202fDog") == "Bird Dog"
        assert normalize_check_text("06:30\u00a0am") == "06:30 am"  # NFKC maps nbsp
        assert _norm("Bird\u202fdog\u00a007:00") == "bird dog 07:00"


class TestF5Normalization:
    def test_normalize_time_tokens(self):
        n = normalize_time_tokens
        assert n("07:00") == "07:00"
        assert n("7:00") == "07:00"
        assert n("7:00 AM") == "07:00"
        assert n("7 am") == "07:00"
        assert n("7am") == "07:00"
        assert n("2 pm") == "14:00"
        assert n("2:30 pm") == "14:30"
        assert n("14:00") == "14:00"
        assert n("12:30") == "12:30"           # no meridiem, no shift
        assert n("12 am") == "00:00"
        assert n("12 pm") == "12:00"
        assert n("2026-11-02") == "2026-11-02"  # dates untouched

    def test_correct_answer_written_as_7am_passes(self):
        calls, _ = correct_trace("knee")
        answer = ("Reminder set: Wall Slide (quadriceps) at 7am on 2026-11-02.")
        assert check_task(task_by_id("knee"), as_log(calls), answer) is None
        answer = ("Reminder set: Wall Slide (quadriceps) at 7:00 AM on 2026-11-02.")
        assert check_task(task_by_id("knee"), as_log(calls), answer) is None

    def test_slots_answer_written_as_2pm_passes(self):
        task = task_by_id("slots")
        log = [{"tool": "get_free_slots", "args": {"date": "2026-11-05"}}]
        assert check_task(task, log, "You have 9:00 am and 2 pm free on 2026-11-05.") is None


def correct_trace(task_id: str) -> tuple[list[dict], str]:
    """A scripted, correct end-to-end trace: calls in order + the final answer."""
    if task_id == "knee":
        return ([tool_call("c1", "find_exercise", condition="knee pain"),
                 tool_call("c2", "get_free_slots", date="2026-11-02"),
                 tool_call("c3", "create_reminder", title="Wall Slide (quadriceps)",
                           date="2026-11-02", time="07:00")],
                "Reminder k: do Wall Slide (quadriceps) at 07:00 on 2026-11-02.")
    if task_id == "back":
        return ([tool_call("c1", "find_exercise", condition="lower back pain"),
                 tool_call("c2", "get_free_slots", date="2026-11-03"),
                 tool_call("c3", "create_reminder", title="Bird Dog (spinal stability)",
                           date="2026-11-03", time="08:00")],
                "Do Bird Dog at 08:00 on 2026-11-03.")
    if task_id == "shoulder":
        return ([tool_call("c1", "find_exercise", condition="shoulder tendinitis"),
                 tool_call("c2", "get_free_slots", date="2026-11-04"),
                 tool_call("c3", "create_reminder", title="External Rotation with Band",
                           date="2026-11-04", time="06:30")],
                "External Rotation with Band at 06:30 on 2026-11-04.")
    raise AssertionError(task_id)


def as_log(calls: list[dict]) -> list[dict]:
    return [{"tool": tc["name"], "args": tc["args"]} for tc in calls]


class TestTaskChecks:
    @pytest.mark.parametrize("task_id", ["knee", "back", "shoulder"])
    def test_correct_trace_passes(self, task_id):
        calls, answer = correct_trace(task_id)
        assert check_task(task_by_id(task_id), as_log(calls), answer) is None

    @pytest.mark.parametrize("task_id", ["knee", "back", "shoulder"])
    def test_missing_call_fails(self, task_id):
        calls, answer = correct_trace(task_id)
        assert check_task(task_by_id(task_id), as_log(calls[:-1]),
                          answer) == "expected_calls"

    @pytest.mark.parametrize("task_id", ["knee", "back", "shoulder"])
    def test_wrong_argument_fails(self, task_id):
        calls, answer = correct_trace(task_id)
        bad = [dict(tc) for tc in calls]
        bad[-1]["args"] = {**bad[-1]["args"], "time": "23:59"}   # not the first free slot
        assert check_task(task_by_id(task_id), as_log(bad), answer) == "expected_calls"

    @pytest.mark.parametrize("task_id", ["knee", "back", "shoulder"])
    def test_answer_missing_fact_fails(self, task_id):
        calls, answer = correct_trace(task_id)
        assert check_task(task_by_id(task_id), as_log(calls),
                          "Reminder set. See you then.") == "facts"

    def test_wrist_negative_passes_without_a_reminder(self):
        task = task_by_id("wrist")
        calls = [tool_call("c1", "find_exercise", condition="wrist sprain")]
        answer = "There is no exercise for a wrist sprain in the catalog, so I set no reminder."
        assert check_task(task, as_log(calls), answer) is None

    def test_wrist_smoke2_recorded_answers_pass_the_structural_check(self):
        """Both smoke2 wrist answers (recorded in final_answer, F4) said
        'couldn't locate ... unable to set a reminder' — model behaviour is
        correct. The fix-round-2 structural check passes them WITHOUT a
        phrase list; verified offline ONLY: no Groq request involved."""
        task = task_by_id("wrist")
        log = [{"tool": "find_exercise", "args": {"condition": "wrist sprain"}},
               {"tool": "find_exercise", "args": {"condition": "wrist"}}]
        answer = ("I'm sorry, but I couldn't locate a specific physical-therapy "
                  "exercise for a wrist sprain in my database, so I'm unable to "
                  "set a reminder for it.")
        assert check_task(task, log, answer) is None
        log_one = [{"tool": "find_exercise", "args": {"condition": "wrist sprain"}}]
        assert check_task(task, log_one, answer) is None

    def test_wrist_negative_fails_when_a_reminder_is_created(self):
        task = task_by_id("wrist")
        calls = [tool_call("c1", "find_exercise", condition="wrist sprain"),
                 tool_call("c2", "create_reminder", title="Wrist stretch",
                           date="2026-11-02", time="07:00")]
        assert check_task(task, as_log(calls), "Reminder set for 07:00.") == "forbid_calls"

    def test_wrist_negative_fails_when_the_answer_names_a_catalog_exercise(self):
        # fix round 2: structural check — the fake tool has no wrist exercise,
        # so a correct turn names none of the catalog's exercises,
        # wording-independently (no phrase list involved).
        task = task_by_id("wrist")
        calls = [tool_call("c1", "find_exercise", condition="wrist sprain")]
        assert check_task(task, as_log(calls),
                          "For your wrist I could suggest the Bird Dog, but the "
                          "catalog has nothing wrist-specific.") == "forbid_facts"
        assert check_task(task, as_log(calls),
                          "The Wall Slide would help; reminder at 07:00.") == "forbid_facts"
        assert check_task(task, as_log(calls),
                          "An External Rotation with a band could help the wrist."
                          ) == "forbid_facts"

    def test_no_tool_task_passes_without_calls(self):
        task = task_by_id("noweek")
        answer = ("Two to three times per week; muscles need recovery days between "
                  "the same loads.")
        assert check_task(task, [], answer) is None

    def test_no_tool_task_fails_when_any_tool_is_called(self):
        task = task_by_id("noweek")
        calls = [tool_call("c1", "get_free_slots", date="2026-11-02")]
        assert check_task(task, as_log(calls), "Two to three times per week.") == "forbid_calls"

    def test_slots_task_requires_both_times(self):
        task = task_by_id("slots")
        calls = [tool_call("c1", "get_free_slots", date="2026-11-05")]
        assert check_task(task, as_log(calls), "Two slots: 09:00 and 14:00 on 2026-11-05.") is None
        assert check_task(task, as_log(calls), "Two slots: 09:00 and 16:00.") == "facts"

    def test_all_six_tasks_have_checks(self):
        assert [t["id"] for t in TASKS] == ["knee", "back", "shoulder", "wrist", "slots", "noweek"]
        multi = [t for t in TASKS if t["needs_rounds"]]
        assert len(multi) == 5       # knee, back, shoulder, wrist, slots: 2+ rounds each
