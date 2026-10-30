"""Smoke runner for the agent-loop probe on Groq openai/gpt-oss-120b (probe only).

One JSON line per run into results/<label>.jsonl. Row fields (fix round 1):

    {task, mode, effort, repeat, pass, failed_check, stop_reason, rounds,
     tool_calls, final_answer (<=2000 chars), per_round_latency,
     per_round_ttft_any, per_round_ttft_content, per_round_prompt_tokens,
     per_round_completion_tokens, per_round_reasoning_tokens, ttft_final_s,
     time_to_answer_s, prompt_tokens, completion_tokens, reasoning_tokens,
     reasoning_chars_received, reasoning_chars_sent,
     reasoning_passthrough_supported (loop-based), rate_limit_waits,
     rate_limit_wait_s, error, call_log, reminders, elapsed_s}

F6 pacing, in this file only:
    - a 429 whose message says "tokens per minute" waits the "try again in Xs"
      time + 1 s and resends the same request, at most 3 times per request
      (`RetryingClient`); `rate_limit_waits`/`rate_limit_wait_s` are recorded
      per row and the wait is NOT part of any round latency (round latency is
      measured inside the wrapped client, per attempt);
    - on "tokens per day" with no key left the whole run stops cleanly:
      prints `daily token limit reached; rerun the same command to resume`,
      does NOT append a failure row, exits 0 (resumable);
    - fix round 3: transient connection/timeout errors get ONE resend of the
      same request after 5 s inside `RetryingClient`; still failing marks the
      row `transient_stop` — main() does NOT append a failure row, exits 0,
      a rerun picks it up. `reasoning_passthrough_supported` flips false ONLY
      on an HTTP 400 whose message mentions `reasoning` (loop-side change).
    - `--redo-errors` (fix round 3 item 3): drop rows whose error is
      transient from results/<label>.jsonl, then re-run their (task, mode,
      effort, repeat) rows via the resumable skip logic.
    - `--pace-sec N` (default 0): sleep N seconds between runs.
    - `--rescore`: recompute pass/failed_check of every recorded row from its
      saved call_log/final_answer with the current checks, rewrite the file,
      print the table — no network (fix round 2, item 3). Also backfills
      `time_to_answer_s` (item 4) from the saved per-round numbers.

Resumable: (task, mode, effort, repeat) combinations already in the file are
skipped. Ends with a small table: pass rate per (mode, effort) — a task counts
as passed only when every of its repeats passed — plus p50 `ttft_final_s`,
p50 total latency, mean prompt tokens per round, tool calls and tokens.

Key handling mirrors run_memory_design.py (lines 88, 550-561): GROQ_API_KEY
holds comma-separated keys; a run whose error contains "tokens per day" is
retried on the next key. Never calls DeepSeek; key values are never printed.

Usage from this folder:
    python run_reasoning_matrix.py --efforts low,medium --modes drop,keep \
        --repeats 1 --tasks knee,back --label smoke2 --pace-sec 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
AGENTIC_ROOT = HERE.parents[2]          # agenticRAG/
ENV_FILE = AGENTIC_ROOT / ".env"

# ./run_reasoning_matrix.py already puts this folder on sys.path[0]; as siblings do,
# agenticRAG has to be there too for `langgraph_agents.*` imports.
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

GROQ = {
    "base_url": "https://api.groq.com/openai/v1",
    "env": "GROQ_API_KEY",
    "model": "openai/gpt-oss-120b",
    "max_tokens": 1500,
}

WAIT_RE = re.compile(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", re.IGNORECASE)

# fix round 3 item 2: connection errors and timeouts are transient. The
# str(exc) text may carry only the message ("Connection error.") or the
# class name too ("APIConnectionError: Connection error."), and httpx errors
# surface as ConnectError/ConnectTimeout/ReadTimeout/ReadError.
TRANSIENT_RE = re.compile(
    r"(APIConnectionError|APITimeoutError|ConnectError|ConnectTimeout"
    r"|ReadTimeout|ReadError|connection error)", re.IGNORECASE)


def is_transient_error(text: str) -> bool:
    """True for connection/timeout text (matrix-v1 row 28's `APIConnectionError:
    Connection error.` counts); false for 400/401/429 and everything else."""
    return bool(text and TRANSIENT_RE.search(text))


def read_env_file() -> dict[str, str]:
    """Same convention as safety_eval/_common.py: agenticRAG/.env, values never printed."""
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def groq_keys() -> list[str]:
    raw = os.getenv(GROQ["env"]) or read_env_file().get(GROQ["env"], "")
    return [k.strip() for k in raw.split(",") if k.strip()]


class RetryingClient:
    """F6: wraps a probe client for one run.

    A 429 whose message says "tokens per minute" waits the `try again in Xs`
    time + 1 s (10 s when the text is not parseable) and resends the same
    request, at most 3 times per request. Transient connection/timeout
    errors (fix round 3) get ONE resend of the same request after 5 s; if it
    fails again the error propagates so `run_one` marks the row
    `transient_stop` and the run is not written (like the daily stop) — a
    rerun picks it up. The waits are counter bookkeeping only — the round
    latency recorded inside the wrapped client is per attempt, so sleeps
    never land in `per_round_latency`.
    """

    TRANSIENT_DELAY = 5.0

    def __init__(self) -> None:
        self.waits = 0
        self.wait_seconds = 0.0

    def wrap(self, inner: Any) -> "RetryingClient":
        self.inner = inner
        return self

    def append_assistant(self, messages: list[dict[str, Any]], rnd: Any) -> None:
        self.inner.append_assistant(messages, rnd)

    @property
    def mode(self) -> str:
        return getattr(self.inner, "mode", "unknown")

    @staticmethod
    def _delay(text: str) -> float:
        m = WAIT_RE.search(text)
        if not m:
            return 10.0
        return (float(m.group(1) or 0) * 60) + float(m.group(2)) + 1.0

    def _once(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> Any:
        """One attempt with the fix-round-3 transient retry: one resend of
        the SAME request after `TRANSIENT_DELAY` s; a second transient
        failure propagates."""
        try:
            return self.inner.complete(messages, schemas)
        except Exception as exc:  # noqa: BLE001 - the rate-limit text decides
            if not is_transient_error(str(exc)):
                raise
            print(f"  ~ transient connection error; resending the same request "
                  f"once in {self.TRANSIENT_DELAY:.0f}s", flush=True)
            time.sleep(self.TRANSIENT_DELAY)
            return self.inner.complete(messages, schemas)

    def complete(self, messages: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> Any:
        for attempt in range(4):            # 1 original + up to 3 resends
            try:
                return self._once(messages, schemas)
            except Exception as exc:  # noqa: BLE001 - the rate-limit text decides
                text = str(exc)
                if attempt < 3 and "429" in text and "tokens per minute" in text.lower():
                    delay = self._delay(text)
                    print(f"  ~ 429 tokens-per-minute; waiting {delay:.1f}s "
                          f"(resend {attempt + 1}/3)", flush=True)
                    self.waits += 1
                    self.wait_seconds += delay
                    time.sleep(delay)
                    continue
                raise
        raise AssertionError("unreachable: retry loop fell through")


def make_client(mode: str, api_key: str, effort: str) -> Any:
    """Build the probe client for one (mode, effort) combination."""
    from langgraph_agents.local_tests.agent_loop.loop import DropClient, KeepClient

    if mode == "drop":
        return DropClient(api_key=api_key, base_url=GROQ["base_url"], model=GROQ["model"],
                          reasoning_effort=effort, max_tokens=GROQ["max_tokens"])
    if mode == "keep":
        return KeepClient(api_key=api_key, base_url=GROQ["base_url"], model=GROQ["model"],
                          reasoning_effort=effort, max_tokens=GROQ["max_tokens"])
    raise ValueError(f"unknown mode: {mode}")


def run_one(task: dict[str, Any], mode: str, effort: str, repeat: int, keys: list[str],
            ) -> dict[str, Any]:
    """One row: run the loop, score it, rotate Groq keys on daily-token errors."""
    from langgraph_agents.local_tests.agent_loop.fake_tools import TOOL_SCHEMAS, bind_tools
    from langgraph_agents.local_tests.agent_loop.loop import ANSWER, run_turn
    from langgraph_agents.local_tests.agent_loop.tasks import check_task

    row: dict[str, Any] = {
        "task": task["id"], "mode": mode, "effort": effort, "repeat": repeat,
        "pass": False, "failed_check": None, "stop_reason": None, "rounds": 0,
        "tool_calls": 0, "final_answer": "", "per_round_latency": [],
        "per_round_ttft_any": [], "per_round_ttft_content": [],
        "per_round_prompt_tokens": [], "per_round_completion_tokens": [],
        "per_round_reasoning_tokens": [], "ttft_final_s": None,
        "time_to_answer_s": None,
        "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": None,
        "reasoning_chars_received": 0, "reasoning_chars_sent": 0,
        "reasoning_passthrough_supported": None,
        "rate_limit_waits": 0, "rate_limit_wait_s": 0.0,
        "error": None, "call_log": [], "reminders": [],
    }
    t0 = time.perf_counter()
    for k_i in range(len(keys)):
        retry = RetryingClient().wrap(make_client(mode, keys[k_i], effort))
        state: dict[str, Any] = {}
        tools = bind_tools(state)
        result = run_turn(retry, tools, TOOL_SCHEMAS, task["prompt"])
        # item 4: time from the start of the turn = all rounds before the answer
        # round + the answer round's content TTFT (None when it never answered).
        lat = result.per_round_latency
        tta = None
        if result.stop_reason == ANSWER and result.ttft_final_s is not None:
            tta = round(sum(lat[:-1]) + result.ttft_final_s, 4)
        row.update({
            "stop_reason": result.stop_reason,
            "rounds": result.rounds,
            "tool_calls": len(result.call_log),
            "final_answer": (result.final_content or "")[:2000],
            "per_round_latency": [round(x, 4) for x in result.per_round_latency],
            "per_round_ttft_any":
                [None if x is None else round(x, 4) for x in result.per_round_ttft_any],
            "per_round_ttft_content":
                [None if x is None else round(x, 4) for x in result.per_round_ttft_content],
            "per_round_prompt_tokens": result.per_round_prompt_tokens,
            "per_round_completion_tokens": result.per_round_completion_tokens,
            "per_round_reasoning_tokens": result.per_round_reasoning_tokens,
            "ttft_final_s": (round(result.ttft_final_s, 4)
                             if result.ttft_final_s is not None else None),
            "time_to_answer_s": tta,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "reasoning_tokens": result.reasoning_tokens,
            "reasoning_chars_received": result.reasoning_chars_received,
            "reasoning_chars_sent": result.reasoning_chars_sent,
            "reasoning_passthrough_supported": result.reasoning_passthrough_supported,
            "rate_limit_waits": retry.waits,
            "rate_limit_wait_s": round(retry.wait_seconds, 3),
            "error": result.error,
            "call_log": [
                {"round": c["round"], "tool": c["tool"], "args": c["args"]}
                for c in result.call_log
            ],
            "reminders": list(state.get("reminders", [])),
        })
        err = result.error or ""
        if "tokens per day" in err.lower() and k_i + 1 < len(keys):
            print(f"  -- {GROQ['env']} key #{k_i + 1} hit its daily token limit: next key",
                  flush=True)
            continue
        answer = result.final_content if result.stop_reason == ANSWER else None
        row["failed_check"] = check_task(task, result.call_log, answer)
        row["pass"] = result.stop_reason == ANSWER and row["failed_check"] is None \
            and not bool(result.error)
        # F3: reasoning_passthrough_supported comes straight from the loop
        # (true only if a request carrying non-empty reasoning succeeded,
        #  false if one was rejected, null otherwise incl. every drop row).
        break
    if "tokens per day" in (row["error"] or "").lower():
        row["daily_stop"] = True          # keys exhausted; main() stops cleanly
    elif is_transient_error(row["error"] or ""):
        # fix round 3 item 2: the transient retry inside RetryingClient has
        # already resent the same request once; still failing means the
        # connection is down — the run is NOT written, a rerun picks it up.
        row["transient_stop"] = True
    row["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return row


def pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return round(s[idx], 2)


def summarize(rows: list[dict[str, Any]], tasks: list[str]) -> list[str]:
    """Table lines: pass rate per (mode, effort); p50/p95 of `time_to_answer_s`
    and of the total loop latency; mean rounds and mean prompt tokens per run."""
    lines = ["", "mode   effort  passed   runs  p50_tta p95_tta p50_lat p95_lat "
                 "mean_rnd  mean_ptok"]
    modes = sorted({r["mode"] for r in rows})
    efforts = sorted({r["effort"] for r in rows})
    for mode in modes:
        for effort in efforts:
            sel = [r for r in rows if r["mode"] == mode and r["effort"] == effort]
            if not sel:
                continue
            ok_tasks = 0
            for tid in tasks:
                per_task = [r for r in sel if r["task"] == tid]
                if per_task and all(r["pass"] for r in per_task):
                    ok_tasks += 1
            tta = [r["time_to_answer_s"] for r in sel
                   if r.get("time_to_answer_s") is not None]
            lats = [round(sum(r["per_round_latency"]), 3) for r in sel]
            mean_rnd = round(sum(r["rounds"] for r in sel) / len(sel), 2)
            mean_ptok = round(sum(r["prompt_tokens"] for r in sel) / len(sel))
            lines.append(
                f"{mode:6} {effort:7} {ok_tasks:>4}/{len(tasks):<4} {len(sel):>4} "
                f"{pctl(tta, 0.5):>7} {pctl(tta, 0.95):>7} "
                f"{pctl(lats, 0.5):>7} {pctl(lats, 0.95):>7} "
                f"{mean_rnd:>7} {mean_ptok:>9}"
            )
    return lines


def rescore_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`--rescore` (fix round 2 item 3): recompute every row's `failed_check`
    and `pass` from its saved `call_log` and `final_answer` with the CURRENT
    checks, and backfill `time_to_answer_s` from the saved per-round numbers.
    Pure file math — no network, so a check change never needs a Groq rerun."""
    from langgraph_agents.local_tests.agent_loop.loop import ANSWER
    from langgraph_agents.local_tests.agent_loop.tasks import check_task, task_by_id

    for row in rows:
        task = task_by_id(row["task"])
        answer = (row.get("final_answer") or None) \
            if row.get("stop_reason") == ANSWER else None
        row["failed_check"] = check_task(task, row.get("call_log") or [], answer)
        row["pass"] = (row.get("stop_reason") == ANSWER
                       and row["failed_check"] is None
                       and not (row.get("error") or ""))
        lat = row.get("per_round_latency") or []
        if row.get("stop_reason") == ANSWER and row.get("ttft_final_s") is not None:
            row["time_to_answer_s"] = round(sum(lat[:-1]) + row["ttft_final_s"], 4)
    return rows


def main() -> int:
    from langgraph_agents.local_tests.agent_loop.tasks import TASKS, task_by_id

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--efforts", default="low,medium,high")
    ap.add_argument("--modes", default="drop,keep")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--tasks", default="all")
    ap.add_argument("--label", default="matrix")
    ap.add_argument("--pace-sec", dest="pace_sec", type=float, default=0.0,
                    help="sleep N seconds between runs (F6)")
    ap.add_argument("--rescore", action="store_true",
                    help="recompute pass/failed_check for every row of "
                         "results/<label>.jsonl from its saved call_log and "
                         "final_answer with the current checks, rewrite the "
                         "file, print the table; no network (fix round 2)")
    ap.add_argument("--redo-errors", dest="redo_errors", action="store_true",
                    help="drop rows whose error is transient (connection or "
                         "timeout, fix round 3 item 2) from "
                         "results/<label>.jsonl and run them again; their "
                         "(task, mode, effort, repeat) rows are re-planned by "
                         "the resumable skip logic")
    args = ap.parse_args()

    keys = groq_keys()
    if not keys:
        print(f"{GROQ['env']} is missing in the environment and agenticRAG/.env",
              file=sys.stderr)
        return 3
    print(f"{len(keys)} Groq key(s) loaded from {GROQ['env']} (values not printed)", flush=True)

    efforts = [e.strip().lower() for e in args.efforts.split(",") if e.strip()]
    modes = [m.strip().lower() for m in args.modes.split(",") if m.strip()]
    task_ids = [t["id"] for t in TASKS] if args.tasks == "all" else \
        [t.strip() for t in args.tasks.split(",") if t.strip()]
    tasks = [task_by_id(t) for t in task_ids]

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"{args.label}.jsonl"

    if args.redo_errors:
        # fix round 3 item 3: before the resumable skip logic, remove the
        # transient-error rows so their (task, mode, effort, repeat) rows
        # re-enter the plan and run again. Non-transient error rows stay
        # recorded and skipped — --redo-errors is not a general rerun.
        dropped = 0
        if out_path.exists():
            kept: list[dict[str, Any]] = []
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if is_transient_error(r.get("error") or ""):
                    dropped += 1
                else:
                    kept.append(r)
            if dropped:
                out_path.write_text(
                    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept),
                    encoding="utf-8")
        print(f"--redo-errors: dropped {dropped} transient-error row(s); "
              f"re-running them now", flush=True)

    if args.rescore:
        # item 3: recompute with the current checks; rewrite; print the table.
        if not out_path.exists():
            print(f"nothing to rescore: {out_path} does not exist", file=sys.stderr)
            return 2
        rows = rescore_rows([json.loads(line) for line in
                             out_path.read_text(encoding="utf-8").splitlines()
                             if line.strip()])
        out_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8")
        print(f"rescored {len(rows)} row(s) in {out_path} (no network)", flush=True)
        for line in summarize(rows, task_ids):
            print(line, flush=True)
        for row in rows:
            if not row["pass"]:
                print(f"  still-failing: {row['mode']}/{row['effort']}/{row['task']} "
                      f"-> {row['failed_check']}", flush=True)
        return 0

    done: set[tuple[str, str, str, int]] = set()
    existing: list[dict[str, Any]] = []
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                existing.append(r)
                done.add((r["task"], r["mode"], r["effort"], int(r["repeat"])))
        print(f"{out_path.name}: {len(done)} run(s) already recorded; skipping them", flush=True)

    plan = [(r, m, e, t) for r in range(1, args.repeats + 1) for m in modes for e in efforts
            for t in tasks if (t["id"], m, e, r) not in done]
    print(f"{len(plan)} run(s) planned: tasks {task_ids}, efforts {efforts}, modes {modes}, "
          f"repeats {args.repeats}, pace {args.pace_sec}s -> {out_path}", flush=True)

    with out_path.open("a", encoding="utf-8") as fh:
        for i, (repeat, mode, effort, task) in enumerate(plan, 1):
            if i > 1 and args.pace_sec > 0:
                time.sleep(args.pace_sec)
            row = run_one(task, mode, effort, repeat, keys)
            if row.get("daily_stop"):
                print("daily token limit reached; rerun the same command to resume", flush=True)
                return 0
            if row.get("transient_stop"):
                # fix round 3 item 2: like the daily stop — do NOT write the
                # row, so a rerun of the same command re-runs it.
                print("transient connection error; rerun the same command to resume",
                      flush=True)
                return 0
            mark = "!" if not row["pass"] else "."
            print(f"  {mark} [{i}/{len(plan)}] r{repeat} {mode} {effort} {task['id']}: "
                  f"{'pass' if row['pass'] else (row['failed_check'] or row['stop_reason'])}"
                  f" ({row['rounds']} rnd, {row['tool_calls']} calls, {row['elapsed_s']}s)",
                  flush=True)
            if row["reasoning_passthrough_supported"] is False:
                print(f"  -- reasoning passthrough REJECTED: {row['error']}", flush=True)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

    # The FILE is the truth: (task, mode, effort, repeat) lines already include
    # what this invocation built. Re-pointing `existing` into the same rows
    # would double-count every pre-recorded run in the printed table.
    rows = [json.loads(line) for line in
            out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in summarize(rows, task_ids):
        print(line, flush=True)
    total = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)
    print(f"file: {out_path} — {len(rows)} runs, {sum(r['tool_calls'] for r in rows)} tool calls, "
          f"{total} tokens (prompt+completion)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
