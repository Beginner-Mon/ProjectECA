"""Probes runner for the loop agent on Groq openai/gpt-oss-120b (probe only).

g0-probes: one JSON line per run into results/<label>.jsonl. Row = the
reasoning-matrix row fields (run_reasoning_matrix.py) plus probe id, category,
scenario, `tool_time_s` per round (fake-tool seconds, apart from the model
latency) and `final_answer`.

Reuses the harness — imported, not copied: `loop.run_turn` with the probe
request ([system] AGENT_PROMPT, [system] USER_FACTS, [user] probe message and
the budget line on the last tool message of every round), both clients via
`make_client`, `RetryingClient` (TPM-429 wait-then-resend, ONE resend on
transient connection/timeout errors), the daily-token-limit stop, the
transient-error resend, `--redo-errors`, `--rescore` and the shared check
normalizer (tasks._norm). Never calls DeepSeek; no database.

CLI:
    --probes all|P1,P3 --repeats N --mode drop|keep --effort low|medium|high
    --pace-sec N --label <name> [--rescore] [--redo-errors]

Resumable: (probe, mode, effort, repeat) combos already in the file are
skipped. A "tokens per day" wall or a still-transient error exits 0 WITHOUT
writing a failure row, so rerunning the same command resumes. Ends with a
table: per probe passes/runs and pass_all (all runs passed), per category,
the overall share of probes with pass_all, p50/p95 of `time_to_answer_s`,
total calls and tokens; rows that need `manual` are printed with their
answers.

Usage from the repo root:
    python agenticRAG/langgraph_agents/local_tests/agent_loop/run_probes.py \
        --probes all --repeats 1 --mode keep --effort low --pace-sec 20 \
        --label probes-smoke
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
AGENTIC_ROOT = HERE.parents[2]

# siblings put agenticRAG on sys.path so `langgraph_agents.*` imports resolve
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

from langgraph_agents.local_tests.agent_loop.run_reasoning_matrix import (  # noqa: E402
    GROQ,
    RetryingClient,
    groq_keys,
    is_transient_error,
    make_client,
    pctl,
)
from langgraph_agents.local_tests.agent_loop.probes import (  # noqa: E402
    PROBES,
    check_probe,
    probe_by_id,
    user_facts_in_args,
)

MANUAL_IDS: set[str] = {p["id"] for p in PROBES if p["checks"].get("manual")}
# manual rows are K-read: never fail automatically, printed with their answers


def tool_times_per_round(result: Any, state: dict[str, Any]) -> list[float]:
    """Executed fake-tool seconds per model round.

    `state["calls"]` records one entry per executed call (with `elapsed_s`)
    in the same order as `result.call_log`, except that a call the loop could
    not dispatch (unknown tool) has no state entry — so matching walks both
    lists with a name pointer.
    """
    times = [0.0] * max(result.rounds, 0)
    executed = state.get("calls", [])
    si = 0
    for entry in result.call_log:
        rec = None
        if si < len(executed) and executed[si].get("tool") == entry.get("tool"):
            rec = executed[si]
            si += 1
        if rec is not None and 1 <= entry.get("round", 0) <= len(times):
            times[entry["round"] - 1] += rec.get("elapsed_s") or 0.0
    return [round(t, 4) for t in times]


def run_one_probe(probe: dict[str, Any], mode: str, effort: str, repeat: int,
                  keys: list[str]) -> dict[str, Any]:
    """One row: run the loop on this probe and score it with check_probe."""
    from langgraph_agents.local_tests.agent_loop.fake_tools import bind_tools, schemas_for
    from langgraph_agents.local_tests.agent_loop.loop import ANSWER, run_turn
    from langgraph_agents.local_tests.agent_loop.prompts import AGENT_PROMPT, USER_FACTS

    row: dict[str, Any] = {
        "probe": probe["id"], "category": probe["category"],
        "scenario": probe.get("scenario") or {},
        "mode": mode, "effort": effort, "repeat": repeat,
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
        "tool_time_s": [], "user_facts_in_args": [],
    }
    t0 = time.perf_counter()
    for k_i in range(len(keys)):
        retry = RetryingClient().wrap(make_client(mode, keys[k_i], effort))
        state: dict[str, Any] = {}
        all_tools = bind_tools(state, probe.get("scenario"))
        tools = {name: all_tools[name] for name in probe["tools"]}   # only offered tools run
        result = run_turn(retry, tools, schemas_for(probe["tools"]),
                          probe["message"],
                          system=AGENT_PROMPT, extra_system=USER_FACTS,
                          budget_line=True)
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
            "tool_time_s": tool_times_per_round(result, state),
            # fix round 1 item 3: recorded field, never a pass/fail — the
            # USER_FACTS terms each executed web_search call sent out
            "user_facts_in_args": user_facts_in_args(result.call_log),
        })
        err = result.error or ""
        if "tokens per day" in err.lower() and k_i + 1 < len(keys):
            print(f"  -- {GROQ['env']} key #{k_i + 1} hit its daily token limit: next key",
                  flush=True)
            continue
        answer = result.final_content if result.stop_reason == ANSWER else None
        row["failed_check"] = check_probe(probe, result.call_log, answer)
        row["pass"] = result.stop_reason == ANSWER and row["failed_check"] is None \
            and not bool(result.error)
        break
    if "tokens per day" in (row["error"] or "").lower():
        row["daily_stop"] = True
    elif is_transient_error(row["error"] or ""):
        row["transient_stop"] = True
    row["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return row


def summarize(rows: list[dict[str, Any]], probe_ids: list[str]) -> list[str]:
    """Table: per probe passes/runs + pass_all; per category; overall share
    of probes with pass_all; p50/p95 of time_to_answer_s; totals; manual rows
    with their answers."""
    lines = ["", "probe  category             passed  runs  pass_all"]
    for pid in probe_ids:
        sel = [r for r in rows if r["probe"] == pid]
        if not sel:
            continue
        passed = sum(1 for r in sel if r["pass"])
        pass_all = "yes" if all(r["pass"] for r in sel) else "no"
        cat = sel[0]["category"]
        lines.append(f"{pid:6} {cat:20} {passed:>4}/{len(sel):<4}  {pass_all:>4}"
                     f"{'  [manual]' if pid in MANUAL_IDS else ''}")
    lines.append("")
    for cat in sorted({r["category"] for r in rows}):
        pids = sorted({r["probe"] for r in rows if r["category"] == cat})
        ok = sum(1 for p in pids if all(r["pass"] for r in rows if r["probe"] == p))
        lines.append(f"category {cat:20} {ok}/{len(pids)} probes pass_all")
    with_rows = [p for p in probe_ids
                 if any(r["probe"] == p for r in rows)]
    pass_all = [p for p in with_rows
                if all(r["pass"] for r in rows if r["probe"] == p)]
    tta = [r["time_to_answer_s"] for r in rows
           if r.get("time_to_answer_s") is not None]
    lines.append(f"overall: {len(pass_all)}/{len(with_rows)} probes pass_all "
                 f"({(100 * len(pass_all) / len(with_rows)):.0f}%)" if with_rows
                 else "overall: no rows")
    lines.append(f"time_to_answer_s: p50 {pctl(tta, 0.5)} p95 {pctl(tta, 0.95)} s")
    total = sum(r["prompt_tokens"] + r["completion_tokens"] for r in rows)
    lines.append(f"file totals: {len(rows)} runs, {sum(r['tool_calls'] for r in rows)} "
                 f"tool calls, {total} tokens (prompt+completion)")
    manuals = [r for r in rows if r["probe"] in MANUAL_IDS]
    if manuals:
        lines.append("")
        lines.append("manual rows (K reads the saved answer; never fails automatically):")
        for r in manuals:
            lines.append(f"  {r['probe']} {r['mode']}/{r['effort']} r{r['repeat']}: "
                         f"{r['final_answer'] or '(no answer text)'}")
    return lines


def rescore_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`--rescore`: recompute every row's `failed_check` and `pass` from its
    saved call_log/final_answer with the CURRENT probe checks, backfill
    `time_to_answer_s` and fill `user_facts_in_args` from the saved call_log
    (fix round 1 item 3). Pure file math — no network."""
    from langgraph_agents.local_tests.agent_loop.loop import ANSWER

    for row in rows:
        probe = probe_by_id(row["probe"])
        answer = (row.get("final_answer") or None) \
            if row.get("stop_reason") == ANSWER else None
        row["failed_check"] = check_probe(probe, row.get("call_log") or [], answer)
        row["pass"] = (row.get("stop_reason") == ANSWER
                       and row["failed_check"] is None
                       and not (row.get("error") or ""))
        lat = row.get("per_round_latency") or []
        if row.get("stop_reason") == ANSWER and row.get("ttft_final_s") is not None:
            row["time_to_answer_s"] = round(sum(lat[:-1]) + row["ttft_final_s"], 4)
        row["user_facts_in_args"] = user_facts_in_args(row.get("call_log") or [])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probes", default="all",
                    help="all or a comma-separated list of probe ids (P1,P3)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--mode", default="keep", choices=["drop", "keep"])
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--pace-sec", dest="pace_sec", type=float, default=0.0,
                    help="sleep N seconds between runs")
    ap.add_argument("--label", default="probes")
    ap.add_argument("--rescore", action="store_true",
                    help="recompute pass/failed_check of results/<label>.jsonl "
                         "with the current checks, rewrite the file, print the "
                         "table; no network")
    ap.add_argument("--redo-errors", dest="redo_errors", action="store_true",
                    help="drop rows whose error is transient (connection or "
                         "timeout) from results/<label>.jsonl and re-run them")
    args = ap.parse_args()

    keys = groq_keys()
    if not keys:
        print(f"{GROQ['env']} is missing in the environment and agenticRAG/.env",
              file=sys.stderr)
        return 3
    print(f"{len(keys)} Groq key(s) loaded from {GROQ['env']} (values not printed)",
          flush=True)

    probe_ids = [p["id"] for p in PROBES] if args.probes == "all" \
        else [t.strip() for t in args.probes.split(",") if t.strip()]
    probes = [probe_by_id(t) for t in probe_ids]

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"{args.label}.jsonl"

    if args.redo_errors:
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
        for line in summarize(rows, probe_ids):
            print(line, flush=True)
        for row in rows:
            if not row["pass"]:
                print(f"  still-failing: {row['mode']}/{row['effort']}/{row['probe']} "
                      f"-> {row['failed_check']}", flush=True)
        return 0

    done: set[tuple[str, str, str, int]] = set()
    if out_path.exists():
        count = 0
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["probe"], r["mode"], r["effort"], int(r["repeat"])))
                count += 1
        print(f"{out_path.name}: {count} run(s) already recorded; skipping them",
              flush=True)

    plan = [(r, probe) for r in range(1, args.repeats + 1)
            for probe in probes
            if (probe["id"], args.mode, args.effort, r) not in done]
    print(f"{len(plan)} run(s) planned: probes {probe_ids}, mode {args.mode}, "
          f"effort {args.effort}, repeats {args.repeats}, pace {args.pace_sec}s "
          f"-> {out_path}", flush=True)

    with out_path.open("a", encoding="utf-8") as fh:
        for i, (repeat, probe) in enumerate(plan, 1):
            if i > 1 and args.pace_sec > 0:
                time.sleep(args.pace_sec)
            row = run_one_probe(probe, args.mode, args.effort, repeat, keys)
            if row.get("daily_stop"):
                print("daily token limit reached; rerun the same command to resume",
                      flush=True)
                return 0
            if row.get("transient_stop"):
                print("transient connection error; rerun the same command to resume",
                      flush=True)
                return 0
            mark = "!" if not row["pass"] else "."
            print(f"  {mark} [{i}/{len(plan)}] r{repeat} {args.mode} {args.effort} "
                  f"{probe['id']}: "
                  f"{'pass' if row['pass'] else (row['failed_check'] or row['stop_reason'])}"
                  f" ({row['rounds']} rnd, {row['tool_calls']} calls, "
                  f"{row['elapsed_s']}s)", flush=True)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

    rows = [json.loads(line) for line in
            out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for line in summarize(rows, probe_ids):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
