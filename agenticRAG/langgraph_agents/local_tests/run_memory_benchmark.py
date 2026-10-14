"""Memory benchmark (memory-v2 step 1) — end-to-end recall over real /chat turns.

Runs the real FastAPI app in-process (`TestClient(app)` in a `with` block so
the lifespan builds the graph), posts one turn per message, and scores each
probe's answer: lowercased, Vietnamese diacritics stripped (NFD); pass = some
`expect_any` phrase present (an empty list is vacuously true — group f only
asserts "no leak") and no `forbid` phrase present. The verbatim answer is kept
in the report.

Identity: `app.dependency_overrides[current_user_id] = override_user(uid)`
per scenario (the override binds the RLS user, like api/auth.py). Each
scenario gets a fresh user, `uuid5(label + scenario id)`; stale rows are
deleted first (`DELETE FROM users WHERE id = ...` through the OWNER dsn —
cascade takes conversations, messages and memories, so reruns stay clean).

Safety: exits(3) unless `VVA_PG_DSN` and `ECA_TEST_PG_DSN` share a host, i.e.
unless this runs through `local_tests/on_dev_branch.py`. The project's own
`.env` points `VVA_PG_DSN` at prod.

Timing per turn: `t_send` → `done` event → response end. The stream ends only
after `housekeep` finishes, so the next turn already sees this turn's facts.

Logs: one `logging.Handler` collects `node_complete` (memory/planner/
synthesizer), `memory_search_done`, `tier2` fields on `node_complete`,
`fact_extraction_done` and `summarizer_done`/`stm_compacted`.

Report: `docs/tracking/memory-benchmark-<label>.md` + `.json` — pass rate per
group; p50/p95 of the memory node; top retrieval similarity of groups b/e
vs f; background LLM calls and tokens per turn; tokens per 1,000 turns.

Usage:
    on_dev_branch.py script .../run_memory_benchmark.py --label A
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlsplit

THIS_FILE = Path(__file__).resolve()
AGENTIC_ROOT = THIS_FILE.parents[2]
REPO_ROOT = AGENTIC_ROOT.parent
TRACKING_DIR = REPO_ROOT / "docs" / "tracking"
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))
# vva_motion (Kimodo job queue) lives under text-to-motion/kimodo — same
# PYTHONPATH pytest.ini uses for the test suite.
KIMODO_ROOT = REPO_ROOT / "text-to-motion" / "kimodo"
if str(KIMODO_ROOT) not in sys.path:
    sys.path.insert(0, str(KIMODO_ROOT))

import yaml  # noqa: E402

TIMEOUT_S = 300
NS = uuid.NAMESPACE_URL


def fold(s: str) -> str:
    """Lowercase, strip Vietnamese diacritics (including d) for matching."""
    s = unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def host_of(dsn: str) -> str:
    try:
        return (urlsplit(dsn or "").hostname or "").lower()
    except Exception:
        return ""


def check_not_prod() -> None:
    from langgraph_agents.shared.env import load_env

    load_env()
    app_host = host_of(os.environ.get("VVA_PG_DSN", ""))
    test_host = host_of(os.environ.get("ECA_TEST_PG_DSN", ""))
    if not app_host or app_host != test_host:
        print(
            "memory-benchmark: REFUSED — VVA_PG_DSN host (%s) != ECA_TEST_PG_DSN "
            "host (%s). Run through local_tests/on_dev_branch.py." % (app_host, test_host),
            file=sys.stderr,
        )
        raise SystemExit(3)


class Collector(logging.Handler):
    """Keep the benchmark's log records with their extras, nothing else."""

    WANTED = {"node_complete", "memory_search_done", "fact_extraction_done",
              "summarizer_done", "stm_compacted", "chat_complete"}

    STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message"}

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.getMessage() not in self.WANTED:
                return
            extras = {k: v for k, v in vars(record).items() if k not in self.STANDARD}
            self.records.append({"message": record.getMessage(),
                                 "created": record.created, **extras})
        except Exception:
            pass


def pct(values: list[float], p: float):
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(p * len(s) + 0.5)) - 1))
    return s[idx]


def parse_sse_lines(lines, on_event) -> None:
    """Reassemble event:/data: pairs; call on_event(event, data) for each."""
    current = None
    for line in lines:
        if line.startswith("event:"):
            current = line[len("event:"):].strip()
        elif line.startswith("data:"):
            raw = line[len("data:"):].strip()
            try:
                on_event(current, json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                on_event(current, raw)
            current = None


def chat_turn(client, session_id: str, query: str, locale: str,
              collector: Collector) -> dict:
    """One /chat turn over the streaming endpoint. Returns its measurements."""
    t_send = time.perf_counter()
    answer_parts: list[str] = []
    done_payload: dict | None = None
    t_done: float | None = None
    status: int | None = None
    error: str | None = None
    mark = len(collector.records)
    try:
        with client.stream("POST", "/chat",
                           json={"query": query, "session_id": session_id,
                                 "output_mode": "text", "web_search": False,
                                 "locale": locale},
                           timeout=TIMEOUT_S) as resp:
            status = resp.status_code
            if status != 200:
                error = f"http_{status}: {resp.read().decode('utf-8', 'replace')[:300]}"
            else:
                def on_event(event, data):
                    nonlocal t_done, done_payload
                    if event == "token" and isinstance(data, dict) and data.get("content"):
                        answer_parts.append(str(data["content"]))
                    elif event == "done":
                        t_done = time.perf_counter()
                        done_payload = data if isinstance(data, dict) else {}

                parse_sse_lines(resp.iter_lines(), on_event)
    except Exception as exc:  # noqa: BLE001 — a dead turn is data, not a crash
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    t_end = time.perf_counter()
    return {"query": query, "session_id": session_id, "locale": locale,
            "status": status, "error": error,
            "answer": "".join(answer_parts),
            "done": done_payload or {},
            "t_to_done_s": round(t_done - t_send, 2) if t_done else None,
            "t_housekeep_s": round(t_end - t_done, 2) if t_done else None,
            "t_total_s": round(t_end - t_send, 2),
            "logs": collector.records[mark:]}


def score_probe(answer: str, expect_any: list[str], forbid: list[str]) -> dict:
    folded = fold(answer)
    hit = [p for p in (expect_any or []) if fold(p) in folded]
    leak = [p for p in (forbid or []) if fold(p) in folded]
    passed = (not expect_any or bool(hit)) and not leak
    return {"passed": passed, "hit": hit, "leak": leak}


async def delete_user(owner_dsn: str, user_id: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        await conn.execute("DELETE FROM users WHERE id = $1::uuid", user_id)
    finally:
        await conn.close()


async def read_last_answer(owner_dsn: str, session_id: str) -> str:
    """Newest persisted assistant message of a session (answer-write source).

    Some turns (grader/refuse paths) emit no `token` events, so the stream
    carries no text even though the model produced an answer. Scoring falls
    back to this row; `answer_source` records which one was scored.
    """
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        return await conn.fetchval(
            "SELECT content FROM messages WHERE session_id = $1::uuid "
            "AND role = 'assistant' ORDER BY seq_id DESC LIMIT 1", session_id) or ""
    finally:
        await conn.close()


async def read_summary(owner_dsn: str, session_id: str) -> dict:
    """stm_summary when migration 016 is live, else the legacy chunk count."""
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        try:
            row = await conn.fetchrow(
                "SELECT stm_summary, stm_up_to_seq FROM conversations "
                "WHERE session_id = $1::uuid", session_id)
            if row is None:
                return {"kind": "stm", "summary": None, "up_to_seq": None}
            return {"kind": "stm", "summary": row["stm_summary"],
                    "up_to_seq": row["stm_up_to_seq"]}
        except Exception:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM summaries WHERE session_id = $1::uuid "
                "AND status = 'active'", session_id)
            return {"kind": "legacy", "summary": None, "chunk_count": n}
    finally:
        await conn.close()


def node_ms(logs: list[dict], node: str) -> list[float]:
    return [r["elapsed_ms"] for r in logs
            if r.get("message") == "node_complete" and r.get("node") == node
            and isinstance(r.get("elapsed_ms"), (int, float))]


def retrieval_top(logs: list[dict]):
    """tier2_top_similarity on B, memory_search_done.top_similarity on A."""
    sims = [r["tier2_top_similarity"] for r in logs
            if r.get("tier2_top_similarity") is not None]
    if sims:
        return max(sims), "tier2"
    sims = [r["top_similarity"] for r in logs
            if r.get("message") == "memory_search_done"
            and r.get("top_similarity") is not None]
    return (max(sims) if sims else None), "memory_search"


def background_cost(logs: list[dict]) -> dict:
    ext = [r for r in logs if r.get("message") == "fact_extraction_done"]
    comp = [r for r in logs if r.get("message") in ("summarizer_done", "stm_compacted")]
    llm_calls = sum(1 for r in ext if r.get("llm_called")) + len(comp)
    tokens = 0
    for r in ext + comp:
        tokens += (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
    elapsed = sum(r.get("elapsed_ms") or 0 for r in ext + comp)
    return {"llm_calls": llm_calls, "tokens": tokens, "elapsed_ms": elapsed}


def run_label(label: str, spec: dict, only: set[str] = frozenset()) -> dict:
    from fastapi.testclient import TestClient

    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.main import create_app
    from langgraph_agents.shared.logging import configure_root_logger

    configure_root_logger()
    collector = Collector()
    try:
        owner_dsn = os.environ["ECA_TEST_PG_DSN_OWNER"]
        app = create_app()
        results: list[dict] = []
        with TestClient(app) as client:
            # Lifespan re-creates the root handlers, so attach only after it ran.
            logging.getLogger().addHandler(collector)
            for sc in spec["scenarios"]:
                if only and sc["id"] not in only:
                    continue
                sid = sc["id"]
                uid = str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}"))
                asyncio.run(delete_user(owner_dsn, uid))
                app.dependency_overrides[current_user_id] = override_user(uid)
                sessions: list[str] = []
                rec: dict = {"scenario": sid, "user_id": uid,
                             "turns": [], "probes": []}
                convs = sc.get("conversations") or []
                for ci, conv in enumerate(convs):
                    if isinstance(conv, dict):
                        locale, messages = conv.get("locale", "vi"), conv["messages"]
                    else:
                        locale, messages = sc.get("locale", "vi"), conv
                    sess = str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}-c{ci}"))
                    sessions.append(sess)
                    for msg in messages:
                        rec["turns"].append(chat_turn(client, sess, msg, locale, collector))
                for pi, probe in enumerate(sc.get("probes") or []):
                    where = probe.get("where", "new")
                    if where == "last":
                        sess = sessions[-1]
                    else:
                        sess = str(uuid.uuid5(
                            NS, f"memory-benchmark-{label}-{sid}-p{pi}"))
                        sessions.append(sess)
                    turn = chat_turn(client, sess, probe["ask"],
                                     probe.get("locale", "vi"), collector)
                    answer = turn["answer"]
                    source = "stream" if answer.strip() else "persisted"
                    if not answer.strip():
                        answer = asyncio.run(read_last_answer(owner_dsn, sess))
                    verdict = score_probe(answer, probe.get("expect_any") or [],
                                          probe.get("forbid") or [])
                    top_sim, top_src = retrieval_top(turn["logs"])
                    rec["probes"].append({
                        "group": probe["group"], "ask": probe["ask"], "where": where,
                        "expect_any": probe.get("expect_any") or [],
                        "forbid": probe.get("forbid") or [],
                        "answer": answer, "answer_source": source,
                        "error": turn["error"],
                        "passed": verdict["passed"] and turn["error"] is None,
                        "hit": verdict["hit"], "leak": verdict["leak"],
                        "retrieval_top_sim": top_sim, "retrieval_src": top_src,
                        "memory_ms": (node_ms(turn["logs"], "memory") or [None])[0],
                        "t_to_done_s": turn["t_to_done_s"],
                        "t_housekeep_s": turn["t_housekeep_s"],
                        "total_tokens": (turn["done"] or {}).get("total_tokens"),
                        "background": background_cost(turn["logs"]),
                    })
                    rec["turns"].append(turn)
                check = sc.get("check_summary")
                if check:
                    summary = asyncio.run(read_summary(owner_dsn, sessions[0]))
                    compactions = sum(
                        1 for t in rec["turns"] for r in t["logs"]
                        if r.get("message") in ("summarizer_done", "stm_compacted")
                        and str(r.get("session_id")) == sessions[0])
                    text = summary.get("summary") or ""
                    kept = [k for k in check["keywords"] if fold(k) in fold(text)]
                    rec["summary_check"] = {
                        "kind": summary.get("kind"), "compactions": compactions,
                        "keywords_kept": kept,
                        "keywords_wanted": check["keywords"],
                        "passed": (compactions >= check["min_compactions"]
                                   and len(kept) == len(check["keywords"])),
                        "summary_chars": len(text),
                    }
                results.append(rec)
                print(f"[{sid}] probes: "
                      + ", ".join(f"{p['group']}:{'PASS' if p['passed'] else 'FAIL'}"
                                  for p in rec["probes"]), flush=True)
        return {"label": label, "scenarios": results}
    finally:
        logging.getLogger().removeHandler(collector)


def summarize(report: dict) -> dict:
    probes = [p for sc in report["scenarios"] for p in sc["probes"]]
    turns = [t for sc in report["scenarios"] for t in sc["turns"]]
    by_group: dict[str, dict] = {}
    for p in probes:
        g = by_group.setdefault(p["group"], {"total": 0, "passed": 0})
        g["total"] += 1
        g["passed"] += p["passed"]
    mem_ms = [m for sc in report["scenarios"] for p in sc["probes"]
              for m in [p["memory_ms"]] if m is not None]
    sim_be = [p["retrieval_top_sim"] for p in probes
              if p["group"] in ("b", "e") and p["retrieval_top_sim"] is not None]
    sim_f = [p["retrieval_top_sim"] for p in probes
             if p["group"] == "f" and p["retrieval_top_sim"] is not None]
    bg = [background_cost(t["logs"]) for t in turns]
    n_turns = len(turns) or 1
    turn_tokens = [t["done"].get("total_tokens") for t in turns
                   if isinstance(t["done"].get("total_tokens"), int)]
    return {
        "groups": {g: {"passed": v["passed"], "total": v["total"],
                       "rate": round(v["passed"] / v["total"], 3) if v["total"] else None}
                   for g, v in sorted(by_group.items())},
        "overall": {"passed": sum(p["passed"] for p in probes), "total": len(probes)},
        "memory_node_ms": {"n": len(mem_ms),
                           "p50": pct(mem_ms, 0.5), "p95": pct(mem_ms, 0.95)},
        "retrieval_top_sim": {
            "b_e_max": max(sim_be) if sim_be else None,
            "b_e_values": sim_be, "f_values": sim_f},
        "background_per_turn": {
            "llm_calls": round(sum(c["llm_calls"] for c in bg) / n_turns, 2),
            "tokens": round(sum(c["tokens"] for c in bg) / n_turns, 1)},
        "turn_tokens_avg": (round(sum(turn_tokens) / len(turn_tokens), 1)
                            if turn_tokens else None),
        "per_1000_turns": {
            "background_tokens": round(sum(c["tokens"] for c in bg) / n_turns * 1000),
            "turn_tokens": (round(sum(turn_tokens) / len(turn_tokens) * 1000)
                            if turn_tokens else None)},
    }


def render_md(label: str, report: dict, stats: dict) -> str:
    L: list[str] = [f"# memory-benchmark-{label}", ""]
    L.append(f"{len(report['scenarios'])} scenarios, "
             f"{stats['overall']['passed']}/{stats['overall']['total']} probes passed.")
    L.append("")
    L.append("## Pass rate by group")
    L.append("")
    L.append("| group | passed/total | rate |")
    L.append("|---|---|---|")
    for g, v in stats["groups"].items():
        L.append(f"| {g} | {v['passed']}/{v['total']} | {v['rate']} |")
    L.append("")
    L.append("## Memory node (ms)")
    L.append("")
    m = stats["memory_node_ms"]
    L.append(f"- n={m['n']}, p50={m['p50']}, p95={m['p95']}")
    L.append("")
    L.append("## Retrieval similarity (b/e vs f)")
    L.append("")
    r = stats["retrieval_top_sim"]
    L.append(f"- b/e top: {r['b_e_max']} values={r['b_e_values']}")
    L.append(f"- f values={r['f_values']}")
    L.append("")
    L.append("## Background cost")
    L.append("")
    b = stats["background_per_turn"]
    L.append(f"- per turn: {b['llm_calls']} LLM calls, {b['tokens']} tokens")
    L.append(f"- avg turn total_tokens: {stats['turn_tokens_avg']}")
    L.append(f"- per 1,000 turns: {stats['per_1000_turns']}")
    L.append("")
    for sc in report["scenarios"]:
        L.append(f"## {sc['scenario']}")
        L.append("")
        for p in sc["probes"]:
            L.append(f"### [{p['group']}] {'PASS' if p['passed'] else 'FAIL'} — {p['ask']}")
            L.append("")
            L.append(f"- where: {p['where']}, expect: {p['expect_any']}, "
                     f"forbid: {p['forbid']}")
            L.append(f"- hit: {p['hit']}, leak: {p['leak']}, error: {p['error']}, "
                     f"answer_source: {p.get('answer_source', 'stream')}")
            L.append(f"- retrieval: {p['retrieval_src']}={p['retrieval_top_sim']}, "
                     f"memory_ms={p['memory_ms']}, to_done={p['t_to_done_s']}s, "
                     f"housekeep={p['t_housekeep_s']}s, "
                     f"total_tokens={p['total_tokens']}, bg={p['background']}")
            L.append(f"- answer: {p['answer'][:1500]}")
            L.append("")
        if sc.get("summary_check"):
            s = sc["summary_check"]
            L.append(f"- summary_check ({s['kind']}): "
                     f"{'PASS' if s['passed'] else 'FAIL'}, "
                     f"compactions={s['compactions']}, "
                     f"kept={s['keywords_kept']}/{s['keywords_wanted']}, "
                     f"chars={s['summary_chars']}")
            L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Memory end-to-end benchmark.")
    ap.add_argument("--label", required=True)
    ap.add_argument("--yaml", default=str(THIS_FILE.parent / "memory_benchmark.yaml"))
    ap.add_argument("--only", default="",
                    help="Comma-separated scenario ids (foreground chunks: the "
                         "harness kills processes that outlive one call, so a "
                         "full label runs as several chunks + --append).")
    ap.add_argument("--append", action="store_true",
                    help="Merge this chunk into the existing label report instead "
                         "of overwriting it.")
    args = ap.parse_args(argv)
    check_not_prod()
    with open(args.yaml, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    report = run_label(args.label, spec, only)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TRACKING_DIR / f"memory-benchmark-{args.label}.json"
    if args.append and json_path.exists():
        prev = json.loads(json_path.read_text(encoding="utf-8"))
        have = {sc["scenario"] for sc in report["scenarios"]}
        merged = [sc for sc in prev["scenarios"] if sc["scenario"] not in have]
        merged.extend(report["scenarios"])
        report = {"label": args.label, "scenarios": merged}
    stats = summarize(report)
    payload = {"label": args.label, "stats": stats, "scenarios": report["scenarios"]}
    md_path = TRACKING_DIR / f"memory-benchmark-{args.label}.md"
    md_path.write_text(render_md(args.label, report, stats), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"Wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv[1:]))
