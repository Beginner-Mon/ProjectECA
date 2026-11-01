"""Probe runner for the loop agent — stage 1 (C1).

Drives the real loop graph (`build_loop_graph`) with the G0 fake tools
(`fake_tools.py`) and probes (`probes.py`), writing one JSON line per run into
`results/<label>.jsonl` using the same row schema as `run_probes.py`.

CLI:
    --probes all|P1,P3   --repeats N   --label <name>   --pace-sec N
    [--dry-run]

`--dry-run` swaps the real model for `FakeAgentModel` (scripted replies) so
the runner can be exercised without any LLM API, network, or database.
Never run the non-dry-run path against Groq in tests.

Usage from the repo root:
    python agenticRAG/langgraph_agents/local_tests/agent_loop/run_loop_probes.py \
        --probes all --repeats 1 --label loop-smoke --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
AGENTIC_ROOT = HERE.parents[2]
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

from langgraph_agents.local_tests.agent_loop.fake_tools import (  # noqa: E402
    bind_tools, schemas_for,
)
from tests.langgraph_agents.loop.fakes import (  # noqa: E402
    FakeAgentModel, AgentReply, ScriptExhausted,
)
from langgraph_agents.local_tests.agent_loop.probes import (  # noqa: E402
    PROBES, check_probe, probe_by_id,
)
from langgraph_agents.loop.graph import build_loop_graph  # noqa: E402
from langgraph_agents.loop.limits import new_turn  # noqa: E402


def _probe_message(probe: dict[str, Any]) -> str:
    return probe["message"]


def _fake_replies(tool_names: list[str]) -> list[AgentReply]:
    """Scripted replies: call every offered tool once, then answer.

    Each tool is called exactly once (parallel in round 1 when there is more
    than one), then a final answer. Deterministic and always terminates.
    """
    replies: list[AgentReply] = []
    if tool_names:
        replies.append(AgentReply(
            content="",
            tool_calls=[
                {"id": f"tc_{i}", "name": name, "arguments": "{}"}
                for i, name in enumerate(tool_names)
            ],
        ))
    replies.append(AgentReply(content="Here is what I found."))
    return replies


async def _run_dry_probe(probe: dict[str, Any], repeat: int) -> dict[str, Any]:
    """One dry-run probe row: real loop graph, FakeAgentModel, no network."""
    from langgraph_agents.loop.tools.registry import build_turn_tools, to_openai_tools

    tools = {name: None for name in probe["tools"]}
    specs = [s for s in _all_specs() if s.name in probe["tools"]]
    openai_tools = to_openai_tools(specs)

    graph = build_loop_graph()
    model = FakeAgentModel(_fake_replies(list(probe["tools"])))

    turn = new_turn()
    turn.user_id = "dry-run-user"
    turn.current_message = probe["message"]

    config = {
        "configurable": {
            "turn_ctx": turn,
            "agent_model": model,
            "persona_model": FakeAgentModel([AgentReply(content="Here is what I found.")]),
            "user_id": "dry-run-user",
            "session_id": "dry-session",
            "query": probe["message"],
            "locale": "en",
            "request_id": "dry-request",
            "persona_id": "anne",
        }
    }

    t0 = time.perf_counter()
    state = {"transcript": [], "errors": [], "total_tokens": 0}
    try:
        final = await asyncio.wait_for(
            graph.ainvoke(state, config), timeout=30.0)
    except ScriptExhausted as exc:
        final = state
        error = f"script exhausted: {exc}"
    except asyncio.TimeoutError:
        final = state
        error = "timeout"
    else:
        error = None

    call_log = [
        {"round": m.get("round", 1), "tool": m.get("tool", "?"), "args": {}}
        for m in (final.get("actions") or [])
    ]
    answer = final.get("final_answer") or ""
    failed_check = check_probe(probe, call_log, answer or None)

    return {
        "probe": probe["id"],
        "category": probe["category"],
        "scenario": probe.get("scenario") or {},
        "mode": "dry-run",
        "effort": "low",
        "repeat": repeat,
        "pass": failed_check is None and error is None,
        "failed_check": failed_check,
        "stop_reason": final.get("turn_status", "completed"),
        "rounds": int(final.get("rounds", 0)),
        "tool_calls": len(call_log),
        "final_answer": answer[:2000],
        "per_round_latency": [],
        "per_round_ttft_any": [],
        "per_round_ttft_content": [],
        "per_round_prompt_tokens": [],
        "per_round_completion_tokens": [],
        "per_round_reasoning_tokens": [],
        "ttft_final_s": None,
        "time_to_answer_s": None,
        "prompt_tokens": int((final.get("total_tokens") or 0)),
        "completion_tokens": 0,
        "reasoning_tokens": None,
        "reasoning_chars_received": 0,
        "reasoning_chars_sent": 0,
        "reasoning_passthrough_supported": None,
        "rate_limit_waits": 0,
        "rate_limit_wait_s": 0.0,
        "error": error,
        "call_log": call_log,
        "reminders": [],
        "tool_time_s": [],
        "user_facts_in_args": [],
        "elapsed_s": round(time.perf_counter() - t0, 2),
    }


# Lazy spec discovery: build all builtin specs so the offered probe tools can
# be mapped to OpenAI tool definitions.
_all_specs_cache: list[Any] | None = None


def _all_specs() -> list[Any]:
    global _all_specs_cache
    if _all_specs_cache is None:
        from langgraph_agents.loop.tools.registry import build_turn_tools
        # A TurnContext-like minimal object is enough for builtins.
        class _Ctx:
            config = {"web_search": True}
        _all_specs_cache = build_turn_tools(_Ctx())
    return _all_specs_cache


def _write_rows(rows: list[dict[str, Any]], label: str) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"{label}.jsonl"
    with out_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--probes", default="all",
                    help="all or a comma-separated list of probe ids (P1,P3)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--label", default="loop-probes")
    ap.add_argument("--pace-sec", dest="pace_sec", type=float, default=0.0,
                    help="sleep N seconds between runs")
    ap.add_argument("--dry-run", action="store_true",
                    help="use FakeAgentModel instead of a live model (no network)")
    args = ap.parse_args(argv)

    probe_ids = [p["id"] for p in PROBES] if args.probes == "all" \
        else [t.strip() for t in args.probes.split(",") if t.strip()]
    probes = [probe_by_id(t) for t in probe_ids]

    if not args.dry_run:
        print("non-dry-run mode requires Groq keys; use --dry-run for tests",
              file=sys.stderr)
        return 3

    plan = [(r, probe) for r in range(1, args.repeats + 1) for probe in probes]
    print(f"{len(plan)} run(s) planned: probes {probe_ids}, repeats {args.repeats} "
          f"--dry-run -> {RESULTS / (args.label + '.jsonl')}", flush=True)

    rows: list[dict[str, Any]] = []
    for i, (repeat, probe) in enumerate(plan, 1):
        if i > 1 and args.pace_sec > 0:
            time.sleep(args.pace_sec)
        row = asyncio.run(_run_dry_probe(probe, repeat))
        mark = "!" if not row["pass"] else "."
        print(f"  {mark} [{i}/{len(plan)}] r{repeat} dry-run {probe['id']}: "
              f"{'pass' if row['pass'] else (row['failed_check'] or row['stop_reason'])} "
              f"({row['rounds']} rnd, {row['tool_calls']} calls)", flush=True)
        rows.append(row)

    _write_rows(rows, args.label)

    passes = sum(1 for r in rows if r["pass"])
    print(f"overall: {passes}/{len(rows)} runs passed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
