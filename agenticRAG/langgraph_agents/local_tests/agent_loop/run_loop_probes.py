"""Probe runner for the loop agent: measures the real loop graph.

For each probe in `probes.py` it runs ONE loop turn through
`build_loop_graph()` and writes a row in the `run_probes.py` schema into
`results/<label>.jsonl`.

What is real and what is not:

- Real: the graph, `load_context`, the agent node, the gate (caps, verdict,
  taint, repeat-empty, rate, approval), the tools node, and in `--live` the
  production `loop.client.AgentModel`, pointed at Groq.
- Fake: the tools (`fake_tools.py`, wrapped as `ToolSpec`s and handed to the
  graph through `configurable["turn_tools"]`), the persona (a passthrough:
  it returns the agent's result unchanged, so no second model runs and the
  probe checks score what the agent produced), memory (the graph is built
  with a seed node that hands the probe's USER_FACTS to the loop, so taint
  rule 2 is exercised; the real memory node never runs), the guardrail
  verdict (routine), the lease store and the rate counter (in memory).
- No database: `shared.get_pg_client` and `PostgresClient.connect` raise while
  a probe runs. Attempts are counted in the row (`db_access_attempts`).

CLI (exactly one of --dry-run / --live):
    --probes all|P1,P3   --repeats N   --label NAME   --pace-sec N
    --effort low|medium|high   [--out-dir DIR]

`--dry-run` scripts a `FakeAgentModel` (no network). `--live` needs
GROQ_API_KEY (one key or a comma-separated list; also read from
agenticRAG/.env) and calls Groq.

From the repo root:
    python agenticRAG/langgraph_agents/local_tests/agent_loop/run_loop_probes.py \
        --probes all --repeats 1 --label loop-smoke --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
AGENTIC_ROOT = HERE.parents[2]
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))
REPO_ROOT = AGENTIC_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from langgraph_agents.local_tests.agent_loop.fake_tools import (  # noqa: E402
    bind_tools, schemas_for,
)
from langgraph_agents.local_tests.agent_loop.probes import (  # noqa: E402
    PROBES, check_probe, probe_by_id, user_facts_in_args,
)
from langgraph_agents.local_tests.agent_loop.prompts import USER_FACTS  # noqa: E402
from langgraph_agents.local_tests.agent_loop.run_probes import (  # noqa: E402
    tool_times_per_round,
)
from langgraph_agents.local_tests.agent_loop.run_reasoning_matrix import (  # noqa: E402
    GROQ, WAIT_RE, groq_keys, is_transient_error,
)
from langgraph_agents.loop.client import (  # noqa: E402
    AgentModel, AgentReply, to_wire_messages,
)

MODE_DRY = "dry-run"
MODE_LIVE = "live"

# Risk of each fake tool as the gate sees it (probe-runner card, item 3).
TOOL_RISK: dict[str, dict[str, Any]] = {
    "find_exercise": {"risk": "internal"},
    "get_free_slots": {"risk": "internal"},
    "kb_search": {"risk": "internal"},
    "web_search": {"risk": "egress_read"},
    "read_inbox": {"risk": "egress_read", "data_class": "personal"},
    "create_reminder": {"risk": "write"},
}

_NOTES_RE = re.compile(
    r"\(internal notes\):\n(.*?)\nFrom ECA's library:", re.DOTALL)


# ── facts, tools, persona, memory ────────────────────────────────────────

def probe_user_facts() -> list[str]:
    """`USER_FACTS` ("Facts the user told you: a; b; c.") as the list of
    lines the memory node produces and `_build_user_facts` renders."""
    text = USER_FACTS.split(":", 1)[-1]
    return [p.strip().rstrip(".").strip() for p in text.split(";") if p.strip()]


def build_probe_specs(probe: dict[str, Any], state: dict[str, Any]) -> list[Any]:
    """The probe's offered tools as `ToolSpec`s over the GĐ0 fakes.

    Only `probe["tools"]` is offered. The fake callables are sync; they run
    in a worker thread so a slow tool does not block the loop's parallel
    calls.
    """
    from langgraph_agents.loop.tools.registry import ToolSpec

    fakes = bind_tools(state, probe.get("scenario"))
    specs: list[Any] = []
    for schema in schemas_for(list(probe["tools"])):
        fn = schema["function"]
        name = fn["name"]
        fake = fakes[name]
        opts = TOOL_RISK[name]

        async def _call(args: dict[str, Any], config: Any = None, _fake=fake) -> Any:
            return await asyncio.to_thread(_fake, dict(args))

        specs.append(ToolSpec(
            name=name, description=fn["description"], parameters=fn["parameters"],
            risk=opts["risk"], source="builtin", call=_call,
            data_class=opts.get("data_class", "none"),
        ))
    return specs


class PassthroughPersona:
    """Persona stand-in: returns the agent's result unchanged, no model call.

    The persona node puts the agent result between "(internal notes):" and
    "From ECA's library:" in its system prompt; this reads it back from there.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: Any = None,
                       *, tool_choice: str | None = "none") -> AgentReply:
        self.calls += 1
        system = messages[0].get("content", "") if messages else ""
        found = _NOTES_RE.search(system)
        return AgentReply(content=(found.group(1) if found else "").strip())


def make_seed_memory(facts: list[str]):
    """A `memory` node for `build_loop_graph(memory=...)`: no database; hands
    the probe's facts to the loop and nothing else."""

    async def seed_memory(state, config):
        return {"user_facts": list(facts), "user_instructions": [],
                "session_summary": []}

    return seed_memory


@contextlib.contextmanager
def no_database():
    """While active, any route to the database raises and is counted.

    Patches `shared.get_pg_client` (what the loop's lazy imports resolve) and
    `PostgresClient.connect`. Yields the list of attempts.
    """
    import langgraph_agents.shared as shared

    attempts: list[str] = []

    def _refuse(*_a: Any, **_k: Any):
        attempts.append("get_pg_client")
        raise RuntimeError("probe runner: database access is not allowed")

    async def _refuse_connect(*_a: Any, **_k: Any):
        attempts.append("PostgresClient.connect")
        raise RuntimeError("probe runner: database access is not allowed")

    saved = shared.get_pg_client
    shared.get_pg_client = _refuse  # type: ignore[assignment]
    pg_cls = None
    saved_connect = None
    try:
        from langgraph_agents.db.postgres import PostgresClient as pg_cls  # type: ignore[no-redef]
        saved_connect = getattr(pg_cls, "connect", None)
        if saved_connect is not None:
            pg_cls.connect = _refuse_connect  # type: ignore[method-assign]
    except Exception:  # noqa: BLE001
        pg_cls = None
    try:
        yield attempts
    finally:
        shared.get_pg_client = saved  # type: ignore[assignment]
        if pg_cls is not None and saved_connect is not None:
            pg_cls.connect = saved_connect  # type: ignore[method-assign]


@contextlib.contextmanager
def memory_stores():
    """In-memory lease store and rate counter for the run, restored after."""
    from langgraph_agents.loop import leases, rate

    saved = (leases._store, rate._counter)
    leases.set_store(leases.MemoryLeaseStore())
    rate.set_store(rate.MemoryRateCounter())
    try:
        yield
    finally:
        leases._store, rate._counter = saved


# ── the recording model ──────────────────────────────────────────────────

class RecordingModel:
    """Wraps any agent model (AgentModel live, FakeAgentModel dry) and records
    what a row needs: per-round latency, usage, reasoning characters, and the
    rate-limit waits. A "tokens per minute" 429 waits the time the provider
    names and resends (at most 3 times per call), like the GĐ0 runner; the
    wait is excluded from the latency and from the loop's time budget."""

    MAX_TPM_RESENDS = 3

    def __init__(self, inner: Any, *, sleep=asyncio.sleep) -> None:
        self.inner = inner
        self._sleep = sleep
        self.latency: list[float] = []
        self.prompt_tokens: list[int | None] = []
        self.completion_tokens: list[int | None] = []
        self.reasoning_tokens: list[int | None] = []
        self.reasoning_received = 0
        self.reasoning_sent = 0
        self.waits = 0
        self.wait_seconds = 0.0
        self.passthrough_rejected: str | None = None

    def paused_seconds(self) -> float:
        return self.wait_seconds

    async def complete(self, messages, tools=None, *, tool_choice="auto"):
        self.reasoning_sent += sum(
            len(m.get(k) or "") for m in to_wire_messages(list(messages))
            for k in ("reasoning", "reasoning_content")
            if m.get("role") == "assistant")
        resends = 0
        while True:
            t0 = time.perf_counter()
            try:
                reply = await self.inner.complete(messages, tools, tool_choice=tool_choice)
            except Exception as exc:  # noqa: BLE001
                text = str(exc)
                if ("tokens per minute" in text.lower()
                        and resends < self.MAX_TPM_RESENDS):
                    resends += 1
                    m = WAIT_RE.search(text)
                    wait = (int(m.group(1) or 0) * 60 + float(m.group(2)) + 1.0
                            if m else 10.0)
                    self.waits += 1
                    self.wait_seconds += wait
                    await self._sleep(wait)
                    continue
                raise
            self.latency.append(time.perf_counter() - t0)
            usage = reply.usage or {}
            self.prompt_tokens.append(usage.get("prompt_tokens"))
            self.completion_tokens.append(usage.get("completion_tokens"))
            self.reasoning_tokens.append(usage.get("reasoning_tokens"))
            self.reasoning_received += len(reply.reasoning_text or "")
            return reply


# ── the transcript → call log ────────────────────────────────────────────

_DECODER = json.JSONDecoder()


def _leading_json(content: str) -> Any:
    """The JSON value at the start of a tool message (the budget line is
    appended after it on the round's last message)."""
    try:
        value, _ = _DECODER.raw_decode((content or "").lstrip())
        return value
    except (ValueError, TypeError):
        return None


def build_call_log(transcript: list[Any]) -> list[dict[str, Any]]:
    """One entry per tool call the agent made, read from the turn transcript.

    `{round, tool, args, outcome, id}`. `round` counts the agent's model
    rounds (1-based). `outcome` is `executed` for a call that ran (a tool that
    ran and returned an error is still `executed`, with `ok: False`), the
    gate's refusal kind (`refused_by_policy`, `approval_unavailable`,
    `repeat_empty`, `invalid_args`, `budget`, ...) for a refusal, or
    `unanswered` when no tool message answered it.
    """
    from langgraph_agents.loop.gate import _call_args, _call_id, _call_name
    from langgraph_agents.loop.limits import _msg_role, _msg_tool_calls

    started = False
    rnd = 0
    log: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for m in transcript:
        role = _msg_role(m)
        tcid = m.get("tool_call_id") if isinstance(m, dict) else getattr(m, "tool_call_id", None)
        content = m.get("content", "") if isinstance(m, dict) else (getattr(m, "content", "") or "")
        if role == "tool" and tcid == "load_context":
            started = True  # the synthetic pair ends the context
            continue
        if not started:
            continue
        if role == "assistant":
            rnd += 1
            for call in _msg_tool_calls(m) or []:
                entry = {"round": rnd, "tool": _call_name(call) or "?",
                         "args": _call_args(call), "outcome": "unanswered",
                         "id": _call_id(call)}
                log.append(entry)
                by_id[entry["id"]] = entry
        elif role == "tool" and tcid in by_id:
            entry = by_id[tcid]
            payload = _leading_json(content)
            if isinstance(payload, dict) and "refused" in payload:
                entry["outcome"] = str(payload["refused"])
            else:
                entry["outcome"] = "executed"
                entry["ok"] = '"error"' not in (content or "")
    return log


def split_call_log(log: list[dict[str, Any]]):
    """(executed, refused): executed calls go to `check_probe`; the rest are
    kept under the row's `refused` field."""
    executed = [{k: v for k, v in c.items() if k != "id"}
                for c in log if c["outcome"] == "executed"]
    refused = [{k: v for k, v in c.items() if k != "id"}
               for c in log if c["outcome"] != "executed"]
    return executed, refused


# ── one probe ────────────────────────────────────────────────────────────

def _agent_error(final: dict[str, Any]) -> str | None:
    msgs = [e.get("message", "") for e in (final.get("errors") or [])
            if e.get("node") == "agent"]
    return "; ".join(m for m in msgs if m) or None


def empty_row(probe: dict[str, Any], mode: str, effort: str, repeat: int) -> dict[str, Any]:
    return {
        "probe": probe["id"], "category": probe["category"],
        "scenario": probe.get("scenario") or {},
        "mode": mode, "effort": effort, "repeat": repeat,
        "persona": "passthrough",
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
        "error": None, "call_log": [], "refused": [], "reminders": [],
        "tool_time_s": [], "user_facts_in_args": [], "db_access_attempts": 0,
    }


async def run_probe(probe: dict[str, Any], repeat: int, *, agent_model: Any,
                    mode: str, effort: str = "low",
                    timeout_s: float = 120.0) -> dict[str, Any]:
    """One row: one loop turn on this probe, scored with `check_probe`.

    `agent_model` is the model the agent node calls (a `FakeAgentModel`, or an
    `AgentModel` pointed at Groq). The graph, gate and tools node are the
    production ones; see the module docstring for what is replaced.
    """
    from langgraph_agents.guardrail.types import fallback_verdict
    from langgraph_agents.loop.graph import build_loop_graph
    from langgraph_agents.loop.limits import Budget, new_turn

    row = empty_row(probe, mode, effort, repeat)
    state: dict[str, Any] = {}
    specs = build_probe_specs(probe, state)
    recorder = RecordingModel(agent_model)
    persona = PassthroughPersona()

    # The loop's time budget does not count the rate-limit waits.
    turn = new_turn()
    turn.budget = Budget(time_fn=lambda: time.monotonic() - recorder.paused_seconds())
    turn.user_id = "probe-user"
    turn.current_message = probe["message"]

    routine = SimpleNamespace(verdict=fallback_verdict())
    done = asyncio.get_running_loop().create_future()
    done.set_result(routine)

    async def verdict_getter(_cfg):  # the triage model is never called
        return fallback_verdict()

    config = {"configurable": {
        "turn_ctx": turn,
        "agent_model": recorder,
        "persona_model": persona,
        "turn_tools": specs,
        "guardrail_task": done,        # persona / handoff read the routine verdict
        "verdict_getter": verdict_getter,
        "user_id": "probe-user",
        "session_id": "probe-session",
        "query": probe["message"],
        "locale": "en",
        "request_id": f"probe-{probe['id']}-{repeat}",
        "persona_id": "anne",
    }}
    graph = build_loop_graph(memory=make_seed_memory(probe_user_facts()))
    init = {"transcript": [], "errors": [], "total_tokens": 0}

    t0 = time.perf_counter()
    error: str | None = None
    final: dict[str, Any] = dict(init)
    with no_database() as db_attempts, memory_stores():
        try:
            final = await asyncio.wait_for(graph.ainvoke(init, config), timeout=timeout_s)
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as exc:  # noqa: BLE001  (e.g. ScriptExhausted)
            error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    error = error or _agent_error(final)

    log = build_call_log(list(final.get("transcript") or []))
    executed, refused = split_call_log(log)
    answer = final.get("agent_result") or ""
    stop = final.get("turn_status") or "completed"
    failed_check = check_probe(probe, executed, answer or None)

    waited = recorder.wait_seconds
    lat = recorder.latency
    row.update({
        "stop_reason": stop,
        "rounds": int(final.get("rounds", 0)),
        "tool_calls": len(log),
        "final_answer": answer[:2000],
        "per_round_latency": [round(x, 4) for x in lat],
        "per_round_ttft_any": [None] * len(lat),
        "per_round_ttft_content": [None] * len(lat),
        "per_round_prompt_tokens": recorder.prompt_tokens,
        "per_round_completion_tokens": recorder.completion_tokens,
        "per_round_reasoning_tokens": recorder.reasoning_tokens,
        "time_to_answer_s": round(elapsed - waited, 4),
        "prompt_tokens": sum(x or 0 for x in recorder.prompt_tokens),
        "completion_tokens": sum(x or 0 for x in recorder.completion_tokens),
        "reasoning_tokens": (sum(x or 0 for x in recorder.reasoning_tokens)
                             if any(x is not None for x in recorder.reasoning_tokens)
                             else None),
        "reasoning_chars_received": recorder.reasoning_received,
        "reasoning_chars_sent": recorder.reasoning_sent,
        "rate_limit_waits": recorder.waits,
        "rate_limit_wait_s": round(waited, 3),
        "error": error,
        "failed_check": failed_check,
        "pass": failed_check is None and error is None and stop == "completed",
        "call_log": executed,
        "refused": refused,
        "reminders": list(state.get("reminders", [])),
        "tool_time_s": tool_times_per_round(
            SimpleNamespace(rounds=int(final.get("rounds", 0)), call_log=executed), state),
        "user_facts_in_args": user_facts_in_args(executed),
        "db_access_attempts": len(db_attempts),
        "elapsed_s": round(elapsed, 2),
    })
    return row


# ── models ───────────────────────────────────────────────────────────────

# Valid arguments for the dry-run script, so its calls pass the gate's
# argument check and the rows show real `executed` entries.
DRY_ARGS: dict[str, dict[str, Any]] = {
    "find_exercise": {"condition": "knee"},
    "get_free_slots": {"date": "2026-11-02"},
    "create_reminder": {"title": "wall slide", "date": "2026-11-02", "time": "07:00"},
    "kb_search": {"query": "knee"},
    "web_search": {"query": "gentle knee exercises"},
    "read_inbox": {},
}


def _fake_replies(tool_names: list[str]) -> list[AgentReply]:
    """Dry-run script: call every offered tool once (one round), then answer."""
    replies: list[AgentReply] = []
    if tool_names:
        replies.append(AgentReply(content="", tool_calls=[
            {"id": f"tc_{i}", "name": name,
             "arguments": json.dumps(DRY_ARGS.get(name, {}))}
            for i, name in enumerate(tool_names)]))
    replies.append(AgentReply(content="Here is what I found."))
    return replies


def make_dry_model(probe: dict[str, Any]):
    from tests.langgraph_agents.loop.fakes import FakeAgentModel
    return FakeAgentModel(_fake_replies(list(probe["tools"])))


def make_live_model(api_key: str, effort: str):
    """The production `AgentModel`, pointed at Groq."""
    from langgraph_agents.core.circuit_breaker import CircuitBreaker
    return AgentModel(
        base_url=GROQ["base_url"], api_key=api_key, model=GROQ["model"],
        extra_body={"reasoning_effort": effort, "max_tokens": GROQ["max_tokens"]},
        # a 429 is not a sign the provider is down: keep the breaker shut
        breaker=CircuitBreaker(name="probe:agent", failure_threshold=10_000,
                               cool_down_seconds=1.0),
    )


async def run_live_probe(probe, repeat, keys: list[str], effort: str) -> dict[str, Any]:
    """Live run with key rotation: a key's daily token limit moves to the
    next key; with none left the row carries `daily_stop`."""
    row: dict[str, Any] = {}
    for k_i, key in enumerate(keys):
        model = make_live_model(key, effort)
        try:
            row = await run_probe(probe, repeat, agent_model=model,
                                  mode=MODE_LIVE, effort=effort)
        finally:
            # each probe runs in its own asyncio.run(): close the client
            # before its loop does
            await model.aclose()
        err = (row.get("error") or "").lower()
        if "tokens per day" in err and k_i + 1 < len(keys):
            print(f"  -- {GROQ['env']} key #{k_i + 1} hit its daily token limit: next key",
                  flush=True)
            continue
        break
    if "tokens per day" in (row.get("error") or "").lower():
        row["daily_stop"] = True
    elif is_transient_error(row.get("error") or ""):
        row["transient_stop"] = True
    return row


# ── CLI ──────────────────────────────────────────────────────────────────

def _write_row(out_path: Path, row: dict[str, Any]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _summary(rows: list[dict[str, Any]], probe_ids: list[str]) -> list[str]:
    lines = [f"overall: {sum(1 for r in rows if r['pass'])}/{len(rows)} runs passed"]
    per = []
    for pid in probe_ids:
        mine = [r for r in rows if r["probe"] == pid]
        if mine:
            per.append(f"{pid} {sum(1 for r in mine if r['pass'])}/{len(mine)}")
    lines.append("per probe: " + ", ".join(per))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probes", default="all",
                    help="all or a comma-separated list of probe ids (P1,P3)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--label", default="loop-probes")
    ap.add_argument("--pace-sec", dest="pace_sec", type=float, default=0.0,
                    help="sleep N seconds between runs")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--out-dir", dest="out_dir", default=str(RESULTS),
                    help="where results/<label>.jsonl is written")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--dry-run", action="store_true",
                       help="scripted FakeAgentModel: no network")
    which.add_argument("--live", action="store_true",
                       help="production AgentModel against Groq (needs GROQ_API_KEY)")
    args = ap.parse_args(argv)

    probe_ids = [p["id"] for p in PROBES] if args.probes == "all" \
        else [t.strip() for t in args.probes.split(",") if t.strip()]
    probes = [probe_by_id(t) for t in probe_ids]
    mode = MODE_LIVE if args.live else MODE_DRY

    keys: list[str] = []
    if args.live:
        keys = groq_keys()
        if not keys:
            print(f"{GROQ['env']} is missing in the environment and agenticRAG/.env",
                  file=sys.stderr)
            return 3
        print(f"{len(keys)} Groq key(s) loaded from {GROQ['env']} (values not printed)",
              flush=True)

    out_path = Path(args.out_dir) / f"{args.label}.jsonl"
    done: set[tuple[str, str, str, int]] = set()
    if args.live and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["probe"], r["mode"], r["effort"], int(r["repeat"])))
        print(f"{out_path.name}: {len(done)} run(s) already recorded; skipping them",
              flush=True)

    plan = [(r, probe) for r in range(1, args.repeats + 1) for probe in probes
            if (probe["id"], mode, args.effort, r) not in done]
    print(f"{len(plan)} run(s) planned: probes {probe_ids}, repeats {args.repeats}, "
          f"{mode}, effort {args.effort}, pace {args.pace_sec}s -> {out_path}", flush=True)

    rows: list[dict[str, Any]] = []
    for i, (repeat, probe) in enumerate(plan, 1):
        if i > 1 and args.pace_sec > 0:
            time.sleep(args.pace_sec)
        if args.live:
            row = asyncio.run(run_live_probe(probe, repeat, keys, args.effort))
            if row.get("daily_stop"):
                print("daily token limit reached; rerun the same command to resume",
                      flush=True)
                break
            if row.get("transient_stop"):
                print("transient connection error; rerun the same command to resume",
                      flush=True)
                break
        else:
            row = asyncio.run(run_probe(probe, repeat, agent_model=make_dry_model(probe),
                                        mode=mode, effort=args.effort))
        mark = "." if row["pass"] else "!"
        print(f"  {mark} [{i}/{len(plan)}] r{repeat} {mode} {args.effort} {probe['id']}: "
              f"{'pass' if row['pass'] else (row['failed_check'] or row['error'] or row['stop_reason'])} "
              f"({row['rounds']} rnd, {row['tool_calls']} calls, {row['elapsed_s']}s)",
              flush=True)
        _write_row(out_path, row)
        rows.append(row)

    for line in _summary(rows, probe_ids):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
