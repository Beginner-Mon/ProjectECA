"""Unit tests for the smoke-runner plumbing of the reasoning matrix (fix round 2).

Item 1 of the fix-2 card: `RetryingClient` retries a TPM 429 (wait = the
"try again in Xs" time + 1 s, same messages resent, waits stay outside the
round latencies) and the runner stops cleanly on "tokens per day" when no key
is left. Everything runs against fakes — no network, no Groq key value ever
read, the sleep is monkeypatched.

Item 3 unit: `rescore_rows()` recomputes `pass`/`failed_check` from the saved
`call_log`/`final_answer` with the current checks (offline file math).
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from langgraph_agents.local_tests.agent_loop import run_reasoning_matrix as rm
from langgraph_agents.local_tests.agent_loop.loop import ANSWER
from langgraph_agents.local_tests.agent_loop.tasks import task_by_id

pytestmark = pytest.mark.unit

TPM_TEXT = ("Error code: 429 - Rate limit reached for tokens per minute (TPM): "
            "Limit 6000, Used 6007. Please try again in 2.4s")
TPD_TEXT = ("Error code: 429 - Rate limit reached for tokens per day (TPD): "
            "Limit 20000. Please try again in 5m2s")


class FakeInner:
    """Inner probe-client double: scripted complete() results, no network."""

    mode = "drop"

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], schemas: list[dict]) -> Any:
        self.calls.append([dict(m) for m in messages])
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    def append_assistant(self, messages: list[dict], rnd: Any) -> None:
        messages.append({"role": "assistant", "content": "round log"})


def loop_result(**over: Any) -> SimpleNamespace:
    """A loop-result shape `run_one` can read, defaults = one clean answer."""
    base: dict[str, Any] = dict(
        stop_reason=ANSWER, rounds=1, call_log=[], final_content="no workout today",
        per_round_latency=[2.0], per_round_ttft_any=[0.5], per_round_ttft_content=[0.4],
        per_round_prompt_tokens=[100], per_round_completion_tokens=[10],
        per_round_reasoning_tokens=[None], ttft_final_s=0.4,
        prompt_tokens=100, completion_tokens=10, reasoning_tokens=None,
        reasoning_chars_received=0, reasoning_chars_sent=0,
        reasoning_passthrough_supported=None, error=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_row(task: str, mode: str = "drop", effort: str = "low", **over: Any) -> dict:
    """A saved-row shape, defaults = one clean answer run."""
    base: dict[str, Any] = {
        "task": task, "mode": mode, "effort": effort, "repeat": 1,
        "pass": True, "failed_check": None, "stop_reason": ANSWER, "rounds": 1,
        "tool_calls": 0, "final_answer": "no workout today",
        "per_round_latency": [2.0], "per_round_ttft_any": [0.5],
        "per_round_ttft_content": [0.4], "per_round_prompt_tokens": [100],
        "per_round_completion_tokens": [10], "per_round_reasoning_tokens": [None],
        "ttft_final_s": 0.4, "time_to_answer_s": 0.4,
        "prompt_tokens": 100, "completion_tokens": 10, "reasoning_tokens": None,
        "reasoning_chars_received": 0, "reasoning_chars_sent": 0,
        "reasoning_passthrough_supported": None,
        "rate_limit_waits": 0, "rate_limit_wait_s": 0.0, "error": None,
        "call_log": [], "reminders": [], "elapsed_s": 2.5,
    }
    base.update(over)
    return base


class TestRetryingClient:
    def test_single_tpm_429_waits_then_resends_the_same_messages(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception(TPM_TEXT), {"answer": "ok"}])
        retry = rm.RetryingClient().wrap(inner)
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        out = retry.complete(messages, [])
        assert out == {"answer": "ok"}
        assert retry.waits == 1
        assert len(inner.calls) == 2                  # original + 1 resend
        assert inner.calls[0] == inner.calls[1]       # the SAME messages are resent
        assert len(sleeps) == 1
        assert abs(sleeps[0] - 3.4) < 0.01            # "try again in 2.4s" + 1 s
        assert abs(retry.wait_seconds - 3.4) < 0.01

    def test_four_tpm_429s_propagate_after_three_resends(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception(TPM_TEXT), Exception(TPM_TEXT),
                           Exception(TPM_TEXT), Exception(TPM_TEXT)])
        retry = rm.RetryingClient().wrap(inner)
        with pytest.raises(Exception, match="429"):
            retry.complete([{"role": "user", "content": "u"}], [])
        assert len(inner.calls) == 4                  # original + 3 resends
        assert len(sleeps) == 3
        assert retry.waits == 3

    def test_tpm_429_without_a_parseable_wait_falls_back_to_ten_seconds(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception("Error code: 429 - tokens per minute limit hit"),
                           {"ok": 1}])
        retry = rm.RetryingClient().wrap(inner)
        out = retry.complete([{"role": "user", "content": "u"}], [])
        assert out == {"ok": 1}
        assert sleeps == [10.0]

    def test_other_errors_propagate_immediately(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception("Error code: 401 - invalid api key")])
        retry = rm.RetryingClient().wrap(inner)
        with pytest.raises(Exception, match="401"):
            retry.complete([{"role": "user", "content": "u"}], [])
        assert inner.calls and len(inner.calls) == 1  # left as the first (only) call
        assert sleeps == []


class TestRunOneWithRetries:
    def test_wait_is_recorded_but_stays_outside_the_round_latencies(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        made: list[tuple[str, str, str]] = []
        inner = FakeInner([Exception(TPM_TEXT), {"answer": "ok"}])

        def fake_make(mode: str, key: str, effort: str):
            made.append((mode, key, effort))
            return inner
        monkeypatch.setattr(rm, "make_client", fake_make)

        def fake_run_turn(client, tools, schemas, prompt):
            messages = [{"role": "system", "content": prompt}]
            out = client.complete(messages, schemas)   # through RetryingClient: 429→wait
            assert out == {"answer": "ok"}
            return loop_result(per_round_latency=[2.0], ttft_final_s=0.4)
        monkeypatch.setattr("langgraph_agents.local_tests.agent_loop.loop.run_turn",
                            fake_run_turn)

        row = rm.run_one(task_by_id("knee"), "drop", "low", 1, ["k-test"])
        assert made == [("drop", "k-test", "low")]
        assert len(sleeps) == 1 and abs(sleeps[0] - 3.4) < 0.01
        assert row["rate_limit_waits"] == 1
        assert abs(row["rate_limit_wait_s"] - 3.4) < 0.01
        # the wait is NOT part of any round latency (per-attempt bookkeeping):
        assert row["per_round_latency"] == [2.0]
        assert all(abs(x - 3.4) > 0.01 for x in row["per_round_latency"])
        assert row["stop_reason"] == ANSWER
        # item 4 formula: sum of the rounds before the answer + its content TTFT
        assert row["time_to_answer_s"] == pytest.approx(0.4, abs=1e-6)

    def test_daily_error_with_the_last_key_sets_daily_stop(self, monkeypatch):
        monkeypatch.setattr(rm, "make_client",
                            lambda mode, key, effort: FakeInner([]))
        monkeypatch.setattr(
            "langgraph_agents.local_tests.agent_loop.loop.run_turn",
            lambda *a: loop_result(stop_reason="error", error=TPD_TEXT,
                                   per_round_latency=[1.0], per_round_ttft_any=[None],
                                   per_round_ttft_content=[None],
                                   per_round_prompt_tokens=[100],
                                   per_round_completion_tokens=[0],
                                   per_round_reasoning_tokens=[0],
                                   ttft_final_s=None))
        row = rm.run_one(task_by_id("knee"), "drop", "low", 1, ["only-key"])
        assert row["daily_stop"] is True
        assert "tokens per day" in row["error"].lower()

    def test_daily_error_rotates_to_the_next_key(self, monkeypatch):
        keys_seen: list[str] = []

        def fake_make(mode: str, key: str, effort: str):
            keys_seen.append(key)
            return FakeInner([])
        monkeypatch.setattr(rm, "make_client", fake_make)

        turns = {"n": 0}

        def fake_run_turn(client, tools, schemas, prompt):
            turns["n"] += 1
            if turns["n"] == 1:
                return loop_result(stop_reason="error", error=TPD_TEXT,
                                   per_round_latency=[1.0], per_round_ttft_any=[None],
                                   per_round_ttft_content=[None],
                                   per_round_prompt_tokens=[100],
                                   per_round_completion_tokens=[0],
                                   per_round_reasoning_tokens=[0],
                                   ttft_final_s=None)
            return loop_result()
        monkeypatch.setattr("langgraph_agents.local_tests.agent_loop.loop.run_turn",
                            fake_run_turn)
        row = rm.run_one(task_by_id("knee"), "drop", "low", 1, ["k1", "k2"])
        assert keys_seen == ["k1", "k2"]
        assert "daily_stop" not in row      # the second key recovered
        assert row["stop_reason"] == ANSWER
        assert row["error"] is None


class TestMainDailyStop:
    ARGS = ["prog", "--label", "dailytest", "--repeats", "1",
            "--modes", "drop", "--efforts", "low", "--tasks", "knee"]

    def test_daily_row_is_not_written_and_the_exit_is_0(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        monkeypatch.setattr(sys, "argv", list(self.ARGS))

        def fake_run_one(task, mode, effort, repeat, keys):
            row = make_row(task["id"], mode, effort, pass_=False,
                           failed_check="expected_calls", stop_reason="error",
                           error=TPD_TEXT)
            row["daily_stop"] = True
            return row
        monkeypatch.setattr(rm, "run_one", fake_run_one)

        assert rm.main() == 0
        out = tmp_path / "dailytest.jsonl"
        assert out.exists()               # open("a") created it ...
        assert out.read_text(encoding="utf-8") == ""   # ... but wrote no row

    def test_rerun_after_the_daily_stop_resumes_and_writes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        monkeypatch.setattr(sys, "argv", list(self.ARGS))
        out = tmp_path / "dailytest.jsonl"
        out.write_text("", encoding="utf-8")     # the state left by the daily stop
        monkeypatch.setattr(
            rm, "run_one",
            lambda task, mode, effort, repeat, keys: make_row(task["id"], mode, effort))

        assert rm.main() == 0
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["pass"] is True


class TestRescore:
    def test_flips_the_superseded_facts_any_wrist_rows_to_pass(self):
        wrist_row = make_row(
            "wrist", pass_=False, failed_check="facts_any",
            final_answer=("I'm sorry, but I couldn't locate a specific physical-"
                          "therapy exercise for a wrist sprain in my database, "
                          "so I'm unable to set a reminder for it."),
            call_log=[{"round": 1, "tool": "find_exercise",
                       "args": {"condition": "wrist sprain"}}],
            per_round_latency=[1.0, 2.0], ttft_final_s=0.3, time_to_answer_s=None)
        out = rm.rescore_rows([wrist_row])
        assert out[0]["pass"] is True
        assert out[0]["failed_check"] is None
        # backfilled: rounds before the answer + its content TTFT
        assert out[0]["time_to_answer_s"] == pytest.approx(1.3, abs=1e-6)

    def test_recomputes_failures_with_the_current_checks(self):
        knee_row = make_row("knee", call_log=[])          # no find_exercise call
        wrist_row = make_row(
            "wrist", final_answer="Try the Wall Slide; reminder set.",
            call_log=[{"round": 1, "tool": "find_exercise",
                       "args": {"condition": "wrist"}},
                      {"round": 2, "tool": "create_reminder",
                       "args": {"title": "Wrist stretch", "date": "2026-11-02",
                                "time": "07:00"}}])
        rows = rm.rescore_rows([knee_row, wrist_row])
        assert rows[0]["failed_check"] == "expected_calls"
        assert rows[0]["pass"] is False
        assert rows[1]["failed_check"] == "forbid_calls"
        assert rows[1]["pass"] is False

    def test_non_answer_rows_keep_time_to_answer_none(self):
        row = make_row("knee", stop_reason="error", error=TPD_TEXT, ttft_final_s=None,
                       per_round_latency=[1.0], time_to_answer_s=None)
        out = rm.rescore_rows([row])
        assert out[0]["time_to_answer_s"] is None

    def test_main_rescore_with_a_missing_label_returns_2(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["prog", "--label", "missing", "--rescore",
                             "--tasks", "knee"])
        assert rm.main() == 2

    def test_main_rescore_rewrites_the_file_and_returns_0(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        row = make_row(
            "wrist", pass_=False, failed_check="facts_any",
            final_answer=("I'm sorry, but I couldn't locate a specific physical-"
                          "therapy exercise for a wrist sprain in my database, "
                          "so I'm unable to set a reminder for it."),
            call_log=[{"round": 1, "tool": "find_exercise",
                       "args": {"condition": "wrist sprain"}}],
            per_round_latency=[1.0, 2.0], ttft_final_s=0.3, time_to_answer_s=None)
        out = tmp_path / "rs.jsonl"
        out.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setattr(sys, "argv",
                            ["prog", "--label", "rs", "--rescore", "--tasks", "wrist"])

        assert rm.main() == 0
        rescored = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert rescored["pass"] is True
        assert rescored["failed_check"] is None
        assert abs(rescored["time_to_answer_s"] - 1.3) < 1e-6


TRANSIENT_TEXT = "APIConnectionError: Connection error."
BAD_REQUEST_TEXT = "Error code: 400 - Unrecognized request argument supplied: reasoning"


class TestTransientRetry:
    """Fix round 3 item 2: connection/timeout errors retry the SAME request
    once after 5 s; a second failure propagates (and then the run is not
    written). No network anywhere; sleeps are captured."""

    def test_connection_error_retries_once_after_five_seconds(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception(TRANSIENT_TEXT), {"answer": "ok"}])
        retry = rm.RetryingClient().wrap(inner)
        out = retry.complete([{"role": "user", "content": "u"}], [])
        assert out == {"answer": "ok"}
        assert len(inner.calls) == 2                   # original + 1 resend
        assert inner.calls[0] == inner.calls[1]        # the SAME request resent
        assert sleeps == [5.0]
        assert retry.waits == 0                        # not a rate-limit wait

    def test_connection_error_failing_twice_propagates(self, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(rm.time, "sleep", sleeps.append)
        inner = FakeInner([Exception(TRANSIENT_TEXT),
                           Exception("APITimeoutError: Request timed out.")])
        retry = rm.RetryingClient().wrap(inner)
        with pytest.raises(Exception, match="APITimeoutError"):
            retry.complete([{"role": "user", "content": "u"}], [])
        assert len(inner.calls) == 2                   # no second retry
        assert sleeps == [5.0]                         # and no resend of it
        assert retry.waits == 0

    def test_transient_variants_are_recognized_and_cannot_confuse_a_400(self):
        assert rm.is_transient_error(TRANSIENT_TEXT)
        assert rm.is_transient_error("APITimeoutError: Request timed out.")
        assert rm.is_transient_error("httpx.ConnectError: Connection refused")
        assert rm.is_transient_error("httpx.ReadTimeout: read failed")
        assert rm.is_transient_error("ConnectError: no 'httpx.' prefix")  # str() form
        assert not rm.is_transient_error(BAD_REQUEST_TEXT)   # HTTP 400: not transient
        assert not rm.is_transient_error("Error code: 401 - invalid api key")
        assert not rm.is_transient_error("")


class TestRunOneTransient:
    def test_transient_error_marks_the_row_for_a_rerun(self, monkeypatch):
        monkeypatch.setattr(rm, "make_client",
                            lambda mode, key, effort: FakeInner([]))
        monkeypatch.setattr(
            "langgraph_agents.local_tests.agent_loop.loop.run_turn",
            lambda *a: loop_result(stop_reason="error", error=TRANSIENT_TEXT,
                                   per_round_latency=[0.4, 0.4],
                                   per_round_ttft_any=[0.2, 0.2],
                                   per_round_ttft_content=[0.2, 0.2],
                                   per_round_prompt_tokens=[100, 110],
                                   per_round_completion_tokens=[0, 0],
                                   per_round_reasoning_tokens=[None, None],
                                   ttft_final_s=None))
        row = rm.run_one(task_by_id("wrist"), "keep", "medium", 1, ["k1"])
        assert row["transient_stop"] is True           # like daily_stop
        assert "APIConnectionError" in row["error"]

    def test_main_does_not_write_a_transient_row_and_exits_0(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        monkeypatch.setattr(
            sys, "argv",
            ["prog", "--label", "transient", "--repeats", "1", "--modes", "keep",
             "--efforts", "medium", "--tasks", "wrist"])

        def fake_run_one(task, mode, effort, repeat, keys):  # matrix-v1 row 28 replay
            row = make_row(task["id"], mode, effort, **{
                "pass": False, "failed_check": None, "stop_reason": "error",
                "error": TRANSIENT_TEXT})
            row["transient_stop"] = True
            return row
        monkeypatch.setattr(rm, "run_one", fake_run_one)

        assert rm.main() == 0
        out = tmp_path / "transient.jsonl"
        assert out.exists()
        assert out.read_text(encoding="utf-8") == ""   # NOT written -> rerun picks it up


class TestRedoErrors:
    def test_redo_errors_drops_only_transient_rows_and_reruns_them(self, monkeypatch,
                                                                   tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        out = tmp_path / "redo.jsonl"
        stale = make_row("wrist", "keep", "medium", **{
            "pass": False, "failed_check": None, "stop_reason": "error",
            "error": TRANSIENT_TEXT})
        bad400 = make_row("knee", **{
            "pass": False, "failed_check": "expected_calls",
            "stop_reason": "error", "error": BAD_REQUEST_TEXT})
        good = make_row("back", stop_reason=ANSWER)
        out.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n"
                    for r in [stale, bad400, good]), encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["prog", "--label", "redo", "--repeats", "1", "--tasks", "wrist",
             "--modes", "keep", "--efforts", "medium", "--redo-errors"])

        def fake_run_one(task, mode, effort, repeat, keys):
            return make_row(task["id"], mode, effort)
        monkeypatch.setattr(rm, "run_one", fake_run_one)

        assert rm.main() == 0
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        # kept: the 400 knee row and the passing back row; re-run: the wrist row
        assert len([r for r in rows if r["task"] == "knee"
                    and r.get("error") == BAD_REQUEST_TEXT]) == 1
        assert len([r for r in rows if r["task"] == "back" and r["pass"] is True]) == 1
        wrists = [r for r in rows if r["task"] == "wrist"]
        assert len(wrists) == 1                                    # stale row replaced
        assert wrists[0]["error"] is None and wrists[0]["pass"] is True
        assert wrists[0].get("transient_stop") is None

    def test_redo_errors_without_transients_rewrites_nothing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(rm, "groq_keys", lambda: ["k1"])
        monkeypatch.setattr(rm, "RESULTS", tmp_path)
        out = tmp_path / "clean.jsonl"
        out.write_text(json.dumps(make_row("back", stop_reason=ANSWER),
                                  ensure_ascii=False) + "\n", encoding="utf-8")
        monkeypatch.setattr(
            sys, "argv",
            ["prog", "--label", "clean", "--repeats", "1", "--tasks", "back",
             "--modes", "drop", "--efforts", "low", "--redo-errors"])
        assert rm.main() == 0
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1                          # untouched, still 1 row
        assert json.loads(lines[0])["error"] is None    # (back, drop, low, 1) skipped
