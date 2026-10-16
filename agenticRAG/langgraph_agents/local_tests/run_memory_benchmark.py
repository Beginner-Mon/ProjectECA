"""Memory benchmark (memory-v2 step 1, Gemini free-tier edition).

Seeded histories, live probes. Past conversations are NOT chatted: user
messages + prewritten assistant answers go straight into `messages` via
`db/session_store.py`, then `housekeep(user_id, session_id)` runs once per
conversation (A: turn indexing + fact extraction; k: compaction only). Each
probe in groups a–f is one real /chat turn with `VVA_LTM=0`, so a probe never
triggers its own extraction. Group k compacts one long conversation in
3 batches and checks the summary for keywords — no chat turn at all.

Identity: `app.dependency_overrides[current_user_id] = override_user(uid)`
per scenario (the override binds the RLS user, like api/auth.py). Each
scenario gets a fresh user, `uuid5(label + scenario id)`; stale rows are
deleted first (`DELETE FROM users WHERE id = ...` through the OWNER dsn —
cascade takes conversations, messages and memories, so reruns stay clean).

Safety: exits(3) unless `VVA_PG_DSN` and `ECA_TEST_PG_DSN` share a host, i.e.
unless this runs through `local_tests/on_dev_branch.py`. The project's own
`.env` points `VVA_PG_DSN` at prod.

Quota: any 429/ResourceExhausted/QuotaExceeded aborts the run (exit 4) after
saving the partial report — the free tier resets at midnight Pacific.

Invalid turns (never scored, retried exactly once via --retry-invalid):
node_failed, llm_fallback_used, errors in state, or 429/503/504 after the
SDK's own retries. A persisted answer is only scored for a valid turn, with
its request_id recorded.

LLM-call counting: node_complete(planner|retriever_agent|synthesizer) +
fact_extraction_done[llm_called] + summarizer_done/stm_compacted. One node run
is one call; invisible SDK retries are a documented blind spot.

Report: `docs/tracking/memory-benchmark-<label>.md` + `.json`.

Usage (one LLM key per invocation, see on_dev_branch.py):
    on_dev_branch.py --llm gemini --gemini-key 2 script .../run_memory_benchmark.py --label A --phase seed
    on_dev_branch.py --llm gemini --gemini-key 3 script .../run_memory_benchmark.py --label A --phase probes --append
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime
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


def model_label() -> str:
    """What actually answered, e.g. qwen/qwen3.8-27b via Groq.

    Read from the environment at run time: A and B must share it, and the
    report must name it (quality, latency and tokens do not represent prod
    unless this is prod's own model).
    """
    base = os.environ.get("DEEPSEEK_BASE_URL", "")
    host = host_of(base)
    if "groq" in host:
        via = "Groq"
    elif "deepseek" in host:
        via = "DeepSeek"
    elif host:
        via = host
    else:
        via = "the default endpoint"
    model = (os.environ.get("DEEPSEEK_MODEL_FAST")
             or os.environ.get("DEEPSEEK_MODEL", ""))
    return f"{model} via {via}" if model else f"unknown model via {via}"
QUOTA_RE = re.compile(r"429|ResourceExhausted|QuotaExceeded", re.IGNORECASE)
HTTP_STATUS_RE = re.compile(r"HTTP/1\.1 (\d{3})")


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

    WANTED = {"node_complete", "node_failed", "llm_fallback_used",
              "memory_search_done", "fact_extraction_done",
              "summarizer_done", "stm_compacted", "chat_complete"}

    STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message"}

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if msg in self.WANTED:
                extras = {k: v for k, v in vars(record).items() if k not in self.STANDARD}
                self.records.append({"message": msg, "created": record.created, **extras})
            elif msg.startswith("HTTP Request"):
                m = HTTP_STATUS_RE.search(msg)
                if m and int(m.group(1)) >= 400:
                    self.records.append({"message": msg, "created": record.created})
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


def score_probe(answer: str, expect_any: list[str], forbid: list[str],
                  expect_all: list[list[str]] | None = None) -> dict:
    folded = fold(answer)
    hit = [p for p in (expect_any or []) if fold(p) in folded]
    leak = [p for p in (forbid or []) if fold(p) in folded]
    all_groups = expect_all or []
    all_hit = []
    all_miss = []
    for group in all_groups:
        opts = list(group or [])
        found = [p for p in opts if fold(p) in folded]
        if found:
            all_hit.append(found[0])
        else:
            all_miss.append(opts)
    passed = (not expect_any or bool(hit)) and not leak and not all_miss
    return {"passed": passed, "hit": hit, "leak": leak,
            "all_hit": all_hit, "all_miss": all_miss}


def turn_tool_names(logs: list[dict]) -> list[str]:
    """Tool names the retriever_agent actually called this turn (all rounds)."""
    names: list[str] = []
    for r in logs:
        if r.get("message") == "node_complete" and r.get("node") == "retriever_agent":
            for n in r.get("tool_names") or []:
                if n not in names:
                    names.append(n)
    return names


def turn_synth_mode(logs: list[dict]) -> str | None:
    """Mode the synthesizer derived this turn (None when it never ran)."""
    for r in reversed(logs):
        if r.get("message") == "node_complete" and r.get("node") == "synthesizer":
            return r.get("mode")
    return None


def check_tools_mode(logs: list[dict], expect_tools: list[str] | None,
                     forbid_mode: str | list[str] | None) -> dict:
    """expect_tools: every listed tool must appear in retriever tool_names.
    forbid_mode: synthesizer mode must not equal any listed mode."""
    tools = turn_tool_names(logs)
    mode = turn_synth_mode(logs)
    want = list(expect_tools or [])
    missing = [t for t in want if t not in tools]
    banned = ([forbid_mode] if isinstance(forbid_mode, str)
              else list(forbid_mode or []))
    mode_hit = mode is not None and mode in banned
    return {"tool_names": tools, "synth_mode": mode,
            "tools_missing": missing, "mode_hit": mode_hit,
            "passed": not missing and not mode_hit}


def find_invalid(turn: dict, logs: list[dict]) -> list[str]:
    """Why a turn must not be scored (empty = valid)."""
    reasons: list[str] = []
    if turn.get("error"):
        reasons.append(f"turn_error: {turn['error'][:120]}")
    if turn.get("status") is not None and turn["status"] != 200:
        reasons.append(f"http_{turn['status']}")
    if turn.get("done") is None and not (turn.get("answer") or "").strip():
        reasons.append("no_done_no_answer")
    for r in logs:
        msg = r.get("message", "")
        if msg == "node_failed":
            reasons.append(f"node_failed:{r.get('node', '?')}")
        if msg == "llm_fallback_used" or r.get("llm_fallback_used") is True:
            reasons.append("llm_fallback_used")
        blob = msg + " " + " ".join(
            str(r.get(k) or "") for k in ("error", "error_type", "reason"))
        m = re.search(r"429|503|504|ResourceExhausted|QuotaExceeded|"
                      r"DeadlineExceeded", blob, re.IGNORECASE)
        if m:
            reasons.append(f"provider_{m.group(0)}")
        for err in r.get("errors") or []:
            reasons.append(f"state_error: {str(err)[:120]}")
    seen, unique = set(), []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


def is_quota(reasons: list[str]) -> bool:
    return any(QUOTA_RE.search(r) for r in reasons)


# ── Seeding (no agent, no TestClient) ───────────────────────────────────


async def delete_user(owner_dsn: str, user_id: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        await conn.execute("DELETE FROM users WHERE id = $1::uuid", user_id)
    finally:
        await conn.close()


async def seed_session(uid: str, session_id: str, turns: list[dict]) -> None:
    from langgraph_agents.db import session_store as store

    for turn in turns:
        mid = str(uuid.uuid4())
        await store.write_turn_question(uid, session_id, mid, turn["user"])
        await store.write_turn_answer(session_id, turn["user"], turn["assistant"])


async def run_housekeep(uid: str, session_id: str, collector: Collector) -> list[dict]:
    from langgraph_agents.nodes.summarizer import housekeep

    mark = len(collector.records)
    await housekeep(uid, session_id)
    return collector.records[mark:]


async def read_memory_rows(owner_dsn: str, user_id: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        rows = await conn.fetch(
            "SELECT fact_text, category, kind, tier, status, project, valid,"
            " seen_count FROM user_memory WHERE user_id = $1::uuid",
            user_id)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def check_db_probe(rows: list[dict], check: dict) -> dict:
    """Evaluate one db probe against the user's memory rows."""
    problems: list[str] = []
    for want in check.get("assert_rows") or []:
        needle = want.get("fact_contains", "")
        hit = next((r for r in rows
                    if needle in (r.get("fact_text") or "")
                    and all(r.get(k) == v for k, v in want.items()
                            if k != "fact_contains")), None)
        if hit is None:
            problems.append(f"no row matching {want}")
    if "assert_count" in check:
        valid = [r for r in rows if r.get("valid")]
        if len(valid) != check["assert_count"]:
            problems.append(f"want {check['assert_count']} valid rows, "
                            f"have {len(valid)}")
    shown = "; ".join(
        f"{r['fact_text'][:60]} [{r.get('kind')}/t{r.get('tier')}/"
        f"{r.get('status')}{'/' + r['project'] if r.get('project') else ''}]"
        for r in rows if r.get("valid"))
    return {"passed": not problems, "problems": problems, "rows_shown": shown}


async def read_last_answer(owner_dsn: str, session_id: str) -> str:
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        return await conn.fetchval(
            "SELECT content FROM messages WHERE session_id = $1::uuid "
            "AND role = 'assistant' ORDER BY seq_id DESC LIMIT 1", session_id) or ""
    finally:
        await conn.close()


async def read_summary_text(owner_dsn: str, session_id: str) -> dict:
    """Summary text for the k check: stm_summary on B, joined chunks on A."""
    import asyncpg

    conn = await asyncpg.connect(owner_dsn)
    try:
        try:
            row = await conn.fetchrow(
                "SELECT stm_summary, stm_up_to_seq FROM conversations "
                "WHERE session_id = $1::uuid", session_id)
            if row is None:
                return {"kind": "stm", "text": "", "up_to_seq": None}
            return {"kind": "stm", "text": row["stm_summary"] or "",
                    "up_to_seq": row["stm_up_to_seq"]}
        except Exception:
            rows = await conn.fetch(
                "SELECT summary_text FROM summaries WHERE session_id = $1::uuid "
                "AND status = 'active' ORDER BY covers_from_seq", session_id)
            return {"kind": "legacy",
                    "text": "\n\n".join(r["summary_text"] for r in rows),
                    "chunk_count": len(rows)}
    finally:
        await conn.close()


def conv_session(label: str, sid: str, ci: int) -> str:
    return str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}-c{ci}"))


def probe_session(label: str, sid: str, pi: int, attempt: int) -> str:
    tag = f"p{pi}" if attempt <= 1 else f"p{pi}r{attempt}"
    return str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}-{tag}"))


# ── Measurement helpers ─────────────────────────────────────────────────


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


def count_llm_calls(logs: list[dict]) -> int:
    """Lower bound on real model calls (SDK retries are invisible)."""
    n = sum(1 for r in logs
            if r.get("message") == "node_complete"
            and r.get("node") in ("planner", "retriever_agent", "synthesizer"))
    n += sum(1 for r in logs
             if r.get("message") == "fact_extraction_done" and r.get("llm_called"))
    n += sum(1 for r in logs
             if r.get("message") in ("summarizer_done", "stm_compacted"))
    return n


# ── Phases ──────────────────────────────────────────────────────────────


def set_ltm(value: str) -> None:
    os.environ["VVA_LTM"] = value
    print(f"memory-benchmark: VVA_LTM={value}", flush=True)


def seed_label(label: str, spec: dict, only: set[str], collector: Collector,
               owner_dsn: str) -> tuple[list[dict], list[str]]:
    """Seed conversations + k batches. Returns (scenario recs, quota stops).

    All pool-backed work (session_store writes, housekeep) runs in ONE event
    loop: the pg pool binds connections to the loop that opened them, so one
    asyncio.run per scenario corrupts it (InterfaceError: another operation
    is in progress). Raw-asyncpg helpers (delete/read) are loop-agnostic.
    """
    from langgraph_agents.db.postgres import bind_request_user

    async def _seed_all() -> tuple[list[dict], list[str]]:
        recs, quota_stops = [], []
        for sc in spec["scenarios"]:
            if only and sc["id"] not in only:
                continue
            if sc.get("only_labels") and label not in sc["only_labels"]:
                continue
            sid = sc["id"]
            uid = str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}"))
            await delete_user(owner_dsn, uid)
            bind_request_user(uid)
            sessions: list[str] = []
            hlogs: list[dict] = []
            if sc.get("conversations"):
                if sc.get("seed_ltm", True):
                    set_ltm("1")
                for ci, conv in enumerate(sc["conversations"]):
                    sess = conv_session(label, sid, ci)
                    sessions.append(sess)
                    await seed_session(uid, sess, conv["turns"])
                    hlogs += await run_housekeep(uid, sess, collector)
            if sc.get("batches"):
                set_ltm("0")
                sess = conv_session(label, sid, 0)
                sessions.append(sess)
                for batch in sc["batches"]:
                    await seed_session(uid, sess, batch["turns"])
                    hlogs += await run_housekeep(uid, sess, collector)
            rec: dict = {"scenario": sid, "user_id": uid, "sessions": sessions,
                         "seed_turns": sum(len(c["turns"]) for c in sc.get("conversations", []))
                         + sum(len(b["turns"]) for b in sc.get("batches", [])),
                         "turns": [], "probes": []}
            rec["seed_llm_calls"] = count_llm_calls(hlogs)
            if any(is_quota(find_invalid({"done": {}}, [r])) for r in hlogs):
                quota_stops.append(sid)
            if sc.get("db_probes"):
                rows = await read_memory_rows(owner_dsn, uid)
                for check in sc["db_probes"]:
                    verdict = check_db_probe(rows, check)
                    rec["probes"].append({
                        "group": check["group"],
                        "ask": check.get("note", ""),
                        "where": "db",
                        "session_id": sessions[0] if sessions else None,
                        "expect_any": [w.get("fact_contains", "")
                                       for w in check.get("assert_rows") or []],
                        "forbid": [],
                        "answer": verdict["rows_shown"][:1500],
                        "answer_source": "db",
                        "request_id": None, "error": None, "attempt": 1,
                        "invalid": [], "passed": verdict["passed"],
                        "hit": [], "leak": verdict["problems"],
                        "retrieval_top_sim": None, "retrieval_src": "db",
                        "memory_ms": None, "t_to_done_s": None,
                        "t_housekeep_s": None, "total_tokens": None,
                        "background": {"llm_calls": 0, "tokens": 0,
                                       "elapsed_ms": 0},
                    })
                    print(f"[seed {sid}] {check['group']}:"
                          f"{'PASS' if verdict['passed'] else 'FAIL'}"
                          + (f" ({verdict['problems']})" if verdict["problems"] else ""),
                          flush=True)
            check = sc.get("check_summary")
            if check:
                summary = await read_summary_text(owner_dsn, sessions[0])
                compactions = sum(
                    1 for r in hlogs
                    if r.get("message") in ("summarizer_done", "stm_compacted")
                    and str(r.get("session_id")) == sessions[0])
                kept = [k for k in check["keywords"] if fold(k) in fold(summary["text"])]
                rec["summary_check"] = {
                    "kind": summary.get("kind"), "compactions": compactions,
                    "keywords_kept": kept, "keywords_wanted": check["keywords"],
                    "passed": (compactions >= check["min_compactions"]
                               and len(kept) == len(check["keywords"])),
                    "summary_chars": len(summary["text"]),
                    "summary_excerpt": summary["text"][:800],
                }
            recs.append(rec)
            print(f"[seed {sid}] llm_calls={rec['seed_llm_calls']}"
                  + (f" summary={'PASS' if rec['summary_check']['passed'] else 'FAIL'}"
                     f" compactions={rec['summary_check']['compactions']}" if check else ""),
                  flush=True)
            if quota_stops:
                break
        return recs, quota_stops

    return asyncio.run(_seed_all())


def run_probe(client, owner_dsn: str, uid: str, sess: str, probe: dict,
              collector: Collector, attempt: int) -> dict:
    turn = chat_turn(client, sess, probe["ask"], probe.get("locale", "vi"), collector)
    invalid = find_invalid(turn, turn["logs"])
    answer, source = turn["answer"], "stream"
    request_id = (turn["done"] or {}).get("request_id")
    if not (answer or "").strip() and not turn.get("error") and not invalid:
        answer = asyncio.run(read_last_answer(owner_dsn, sess))
        source = "persisted"
    verdict = {"passed": False, "hit": [], "leak": [],
               "all_hit": [], "all_miss": []}
    gate = {"tool_names": [], "synth_mode": None,
            "tools_missing": [], "mode_hit": False, "passed": True}
    if not invalid:
        verdict = score_probe(answer, probe.get("expect_any") or [],
                              probe.get("forbid") or [],
                              probe.get("expect_all"))
        gate = check_tools_mode(turn["logs"], probe.get("expect_tools"),
                                probe.get("forbid_mode"))
    top_sim, top_src = retrieval_top(turn["logs"])
    return {
        "group": probe["group"], "ask": probe["ask"],
        "where": probe.get("where", "new"), "session_id": sess,
        "expect_any": probe.get("expect_any") or [],
        "expect_all": probe.get("expect_all") or [],
        "expect_tools": probe.get("expect_tools") or [],
        "forbid_mode": probe.get("forbid_mode"),
        "forbid": probe.get("forbid") or [],
        "answer": answer, "answer_source": source, "request_id": request_id,
        "error": turn["error"], "attempt": attempt,
        "invalid": invalid, "passed": (not invalid) and verdict["passed"]
        and gate["passed"] and turn["error"] is None,
        "hit": verdict["hit"], "leak": verdict["leak"],
        "all_hit": verdict.get("all_hit", []),
        "all_miss": verdict.get("all_miss", []),
        "tool_names": gate["tool_names"], "synth_mode": gate["synth_mode"],
        "tools_missing": gate["tools_missing"],
        "mode_hit": gate["mode_hit"],
        "retrieval_top_sim": top_sim, "retrieval_src": top_src,
        "memory_ms": (node_ms(turn["logs"], "memory") or [None])[0],
        "t_to_done_s": turn["t_to_done_s"],
        "t_housekeep_s": turn["t_housekeep_s"],
        "total_tokens": (turn["done"] or {}).get("total_tokens"),
        "background": background_cost(turn["logs"]),
        "turn": turn,
    }


def probes_label(label: str, spec: dict, only: set[str], collector: Collector,
                 owner_dsn: str, recs: list[dict]) -> list[str]:
    """Live /chat probes (VVA_LTM=0). Returns quota stops."""
    from fastapi.testclient import TestClient

    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.main import create_app
    from langgraph_agents.shared.logging import configure_root_logger

    configure_root_logger()
    set_ltm("0")
    by_id = {r["scenario"]: r for r in recs}
    quota_stops: list[str] = []
    app = create_app()
    with TestClient(app) as client:
        # Lifespan re-creates the root handlers, so attach only after it ran.
        logging.getLogger().addHandler(collector)
        try:
            for sc in spec["scenarios"]:
                if only and sc["id"] not in only:
                    continue
                if sc.get("only_labels") and label not in sc["only_labels"]:
                    continue
                if not sc.get("probes"):
                    continue
                sid = sc["id"]
                rec = by_id.get(sid)
                if rec is None:
                    rec = {"scenario": sid,
                           "user_id": str(uuid.uuid5(NS, f"memory-benchmark-{label}-{sid}")),
                           "sessions": [], "seed_turns": 0, "turns": [], "probes": []}
                uid = rec["user_id"]
                app.dependency_overrides[current_user_id] = override_user(uid)
                for pi, probe in enumerate(sc["probes"]):
                    if probe.get("where") == "last":
                        sess = rec["sessions"][-1] if rec["sessions"] else None
                        if sess is None:
                            prec = {"group": probe["group"], "ask": probe["ask"],
                                    "where": "last", "session_id": None,
                                    "expect_any": probe.get("expect_any") or [],
                                    "expect_all": probe.get("expect_all") or [],
                                    "expect_tools": probe.get("expect_tools") or [],
                                    "forbid_mode": probe.get("forbid_mode"),
                                    "forbid": probe.get("forbid") or [],
                                    "answer": "", "answer_source": "none",
                                    "request_id": None, "error": None, "attempt": 1,
                                    "invalid": ["no_session_for_last"], "passed": False,
                                    "hit": [], "leak": [], "all_hit": [],
                                    "all_miss": [],
                                    "tool_names": [], "synth_mode": None,
                                    "tools_missing": [], "mode_hit": False,
                                    "retrieval_top_sim": None,
                                    "retrieval_src": "none", "memory_ms": None,
                                    "t_to_done_s": None, "t_housekeep_s": None,
                                    "total_tokens": None,
                                    "background": {"llm_calls": 0, "tokens": 0,
                                                   "elapsed_ms": 0}}
                            rec["probes"].append(prec)
                            continue
                    else:
                        sess = probe_session(label, sid, pi, 1)
                        rec["sessions"].append(sess)
                    prec = run_probe(client, owner_dsn, uid, sess, probe,
                                     collector, attempt=1)
                    turn_data = prec.pop("turn")
                    rec["turns"].append(turn_data)
                    rec["probes"].append(prec)
                    print(f"[{sid}] {probe['group']}:"
                          f"{'INVALID' if prec['invalid'] else ('PASS' if prec['passed'] else 'FAIL')}"
                          + (f" ({prec['invalid']})" if prec["invalid"] else ""),
                          flush=True)
                    if is_quota(prec["invalid"]):
                        quota_stops.append(sid)
                        return quota_stops
        finally:
            logging.getLogger().removeHandler(collector)
    return quota_stops


def retry_label(label: str, spec: dict, only: set[str], collector: Collector,
                owner_dsn: str, recs: list[dict]) -> list[str]:
    """Rerun invalid probes exactly once (no reseeding, users persist)."""
    from fastapi.testclient import TestClient

    from langgraph_agents.api.auth import current_user_id, override_user
    from langgraph_agents.api.main import create_app
    from langgraph_agents.shared.logging import configure_root_logger

    configure_root_logger()
    set_ltm("0")
    quota_stops: list[str] = []
    app = create_app()
    with TestClient(app) as client:
        logging.getLogger().addHandler(collector)
        try:
            for sc in spec["scenarios"]:
                if only and sc["id"] not in only:
                    continue
                sid = sc["id"]
                rec = next((r for r in recs if r["scenario"] == sid), None)
                if rec is None:
                    continue
                uid = rec["user_id"]
                app.dependency_overrides[current_user_id] = override_user(uid)
                for prec in rec["probes"]:
                    if not prec.get("invalid") or prec.get("attempt", 1) >= 2:
                        continue
                    probe = next((p for p in sc["probes"]
                                  if p["group"] == prec["group"]
                                  and p.get("where") == prec.get("where")), None)
                    if probe is None:
                        continue
                    if prec.get("where") == "last":
                        sess = prec["session_id"]
                    else:
                        sess = probe_session(
                            label, sid, sc["probes"].index(probe), 2)
                        rec["sessions"].append(sess)
                    new = run_probe(client, owner_dsn, uid, sess, probe,
                                    collector, attempt=prec.get("attempt", 1) + 1)
                    new["retry_of"] = {"error": prec.get("error"),
                                       "invalid": prec.get("invalid")}
                    turn_data = new.pop("turn")
                    rec["turns"].append(turn_data)
                    prec.update(new)
                    print(f"[retry {sid}] {probe['group']}:"
                          f"{'INVALID' if new['invalid'] else ('PASS' if new['passed'] else 'FAIL')}",
                          flush=True)
                    if is_quota(new["invalid"]):
                        quota_stops.append(sid)
                        return quota_stops
        finally:
            logging.getLogger().removeHandler(collector)
    return quota_stops


# ── Report ──────────────────────────────────────────────────────────────


def summarize(report: dict) -> dict:
    probes = [p for sc in report["scenarios"] for p in sc.get("probes", [])]
    scored = [p for p in probes if not p.get("invalid")]
    invalid = [p for p in probes if p.get("invalid")]
    by_group: dict[str, dict] = {}
    for p in probes:
        g = by_group.setdefault(p["group"], {"total": 0, "passed": 0,
                                             "invalid": 0})
        g["total"] += 1
        g["passed"] += p["passed"]
        g["invalid"] += 1 if p.get("invalid") else 0
    mem_ms = [p["memory_ms"] for p in scored if p.get("memory_ms") is not None]
    sim_be = [p["retrieval_top_sim"] for p in scored
              if p["group"] in ("b", "e") and p["retrieval_top_sim"] is not None]
    sim_f = [p["retrieval_top_sim"] for p in scored
             if p["group"] == "f" and p["retrieval_top_sim"] is not None]
    turns = [t for sc in report["scenarios"] for t in sc.get("turns", [])]
    bg = [background_cost(t.get("logs", [])) for t in turns]
    n_turns = len(turns) or 1
    turn_tokens = [t["done"].get("total_tokens") for t in turns
                   if isinstance(t.get("done", {}).get("total_tokens"), int)]
    return {
        "groups": {g: {"passed": v["passed"], "total": v["total"],
                       "invalid": v["invalid"],
                       "rate": round(v["passed"] / v["total"], 3) if v["total"] else None}
                   for g, v in sorted(by_group.items())},
        "overall": {"passed": sum(p["passed"] for p in scored),
                    "scored": len(scored), "invalid": len(invalid)},
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
    model = report.get("model", "unknown model")
    L: list[str] = [f"# memory-benchmark-{label}", ""]
    L.append(f"Model: {model}. A và B cùng chạy trên model này nên so sánh hai thiết "
             "kế memory là công bằng, nhưng chất lượng, độ trễ và token KHÔNG đại "
             "diện cho prod (prod dùng DeepSeek trực tiếp).")
    L.append("")
    for run in report.get("runs", []):
        L.append(f"- run {run['started_at']} phase={run['phase']} only={run['only']} "
                 f"key={run['key_label']} llm_calls={run['llm_calls']} "
                 f"turns={run['turns']} probes={run['probes']}")
    L.append("")
    L.append(f"{stats['overall']['passed']}/{stats['overall']['scored']} probes passed "
             f"({stats['overall']['invalid']} invalid, không chấm).")
    L.append("")
    L.append("## Pass rate by group")
    L.append("")
    L.append("| group | passed/total | invalid | rate |")
    L.append("|---|---|---|---|")
    for g, v in stats["groups"].items():
        L.append(f"| {g} | {v['passed']}/{v['total']} | {v['invalid']} | {v['rate']} |")
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
        for p in sc.get("probes", []):
            verdict = "INVALID" if p.get("invalid") else ("PASS" if p["passed"] else "FAIL")
            L.append(f"### [{p['group']}] {verdict} — {p['ask']}")
            L.append("")
            L.append(f"- where: {p.get('where')}, session: {p.get('session_id')}, "
                     f"attempt: {p.get('attempt', 1)}, expect: {p['expect_any']}, "
                     f"expect_all: {p.get('expect_all', [])}, "
                     f"expect_tools: {p.get('expect_tools', [])}, "
                     f"forbid: {p['forbid']}, forbid_mode: {p.get('forbid_mode')}")
            L.append(f"- hit: {p['hit']}, all_hit: {p.get('all_hit', [])}, "
                     f"all_miss: {p.get('all_miss', [])}, leak: {p['leak']}, "
                     f"error: {p['error']}, "
                     f"invalid: {p.get('invalid')}, answer_source: {p.get('answer_source')}, "
                     f"request_id: {p.get('request_id')}")
            L.append(f"- mode: {p.get('synth_mode')}, tools: {p.get('tool_names', [])}, "
                     f"tools_missing: {p.get('tools_missing', [])}, "
                     f"mode_hit: {p.get('mode_hit')}, "
                     f"retrieval: {p['retrieval_src']}={p['retrieval_top_sim']}, "
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
            L.append(f"- excerpt: {s.get('summary_excerpt', '')[:800]}")
            L.append("")
    return "\n".join(L) + "\n"


def _is_b_label(label: str) -> bool:
    return label in ("B", "B2")


def _is_m_label(label: str) -> bool:
    return label in ("M0", "M1")


def validate_spec(spec: dict, label: str) -> list[str]:
    """Offline checks for --dry-run (no DB, no LLM). Returns error strings.

    Groups g–j only exist for label B/B2 (they read tier rows directly).
    Group m only exists for labels M0/M1 (multi-intent + clarify-gate probes).
    """
    errors: list[str] = []
    seen: set[str] = set()
    groups: dict[str, int] = {}
    for sc in spec.get("scenarios", []):
        if sc.get("only_labels") and label not in sc["only_labels"]:
            continue
        sid = sc.get("id", "?")
        if sid in seen:
            errors.append(f"duplicate scenario id {sid}")
        seen.add(sid)
        if not sc.get("conversations") and not sc.get("batches"):
            errors.append(f"{sid}: needs conversations or batches")
        for probe in sc.get("probes", []):
            groups[probe.get("group", "?")] = groups.get(probe.get("group", "?"), 0) + 1
            if probe.get("where") == "last" and not sc.get("conversations") \
                    and not sc.get("batches"):
                errors.append(f"{sid}: where=last without a session")
            if not probe.get("ask"):
                errors.append(f"{sid}: probe without ask")
        for check in sc.get("db_probes", []):
            groups[check.get("group", "?")] = groups.get(check.get("group", "?"), 0) + 1
    if not _is_m_label(label):
        for g in ("a", "b", "c", "d", "e", "f"):
            if groups.get(g, 0) != 1:
                errors.append(f"group {g}: want exactly 1 probe, have {groups.get(g, 0)}")
    for g in ("g", "h", "i", "j"):
        want = 2 if (g == "g" or g == "j") else 1
        have = groups.get(g, 0)
        if _is_b_label(label):
            if have != want:
                errors.append(f"group {g}: want exactly {want}, have {have}")
        elif have:
            errors.append(f"group {g}: B-only, must not run for {label}")
    m_have = groups.get("m", 0)
    if _is_m_label(label):
        if m_have != 3:
            errors.append(f"group m: want exactly 3 probes, have {m_have}")
    elif m_have:
        errors.append(f"group m: M-only, must not run for {label}")
    return errors


def estimate_calls(spec: dict, only: set[str], label: str) -> dict:
    def _in(sc):
        return ((not only or sc["id"] in only)
                and (not sc.get("only_labels") or label in sc["only_labels"]))
    seeds = sum(1 for sc in spec["scenarios"] if _in(sc)
                for _ in (sc.get("conversations", []) + sc.get("batches", [])))
    probes = sum(1 for sc in spec["scenarios"] if _in(sc)
                 for _ in sc.get("probes", []))
    return {"housekeeps": seeds, "probes": probes,
            "estimate": seeds * 2 + probes * 3}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Memory seeded-history benchmark.")
    ap.add_argument("--label", required=True)
    ap.add_argument("--yaml", default=str(THIS_FILE.parent / "memory_benchmark.yaml"))
    ap.add_argument("--only", default="",
                    help="Comma-separated scenario ids (one key per invocation).")
    ap.add_argument("--append", action="store_true",
                    help="Start from the existing label report instead of empty.")
    ap.add_argument("--phase", default="all", choices=["seed", "probes", "all"])
    ap.add_argument("--retry-invalid", action="store_true",
                    help="Rerun invalid probes once (no reseeding).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Validate the YAML and print the call estimate.")
    args = ap.parse_args(argv)
    with open(args.yaml, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    errors = validate_spec(spec, args.label)
    if errors:
        print("memory-benchmark: invalid spec:")
        for error in errors:
            print(f"  - {error}")
        return 2
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if args.dry_run:
        print(json.dumps({"errors": [], "estimate": estimate_calls(spec, only, args.label),
                          "only": sorted(only) or "all"}, ensure_ascii=False, indent=1))
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    check_not_prod()
    from langgraph_agents.shared.logging import configure_root_logger

    configure_root_logger()
    collector = Collector()
    owner_dsn = os.environ["ECA_TEST_PG_DSN_OWNER"]
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    key_label = os.environ.get("VVA_LLM_KEY_LABEL", "llm=?")
    mark = len(collector.records)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TRACKING_DIR / f"memory-benchmark-{args.label}.json"
    report: dict = {"label": args.label, "model": model_label(),
                     "scenarios": [], "runs": []}
    if (args.append or args.retry_invalid) and json_path.exists():
        prev = json.loads(json_path.read_text(encoding="utf-8"))
        report = {"label": args.label, "model": model_label(),
                  "scenarios": prev["scenarios"], "runs": prev.get("runs", [])}
    quota_stops: list[str] = []
    if args.retry_invalid:
        if not json_path.exists():
            print("memory-benchmark: nothing to retry.", file=sys.stderr)
            return 2
        quota_stops = retry_label(args.label, spec, only, collector,
                                  owner_dsn, report["scenarios"])
    else:
        if args.phase in ("seed", "all"):
            # Seed logs are collected here: probes attach their own handler
            # later (lifespan clears root handlers on TestClient entry).
            logging.getLogger().addHandler(collector)
            try:
                recs, quota_stops = seed_label(args.label, spec, only, collector,
                                               owner_dsn)
            finally:
                logging.getLogger().removeHandler(collector)
            by_id = {r["scenario"]: r for r in report["scenarios"]}
            for rec in recs:
                by_id[rec["scenario"]] = rec
            order = [sc["id"] for sc in spec["scenarios"]]
            report["scenarios"] = [by_id[sid] for sid in order if sid in by_id]
        if not quota_stops and args.phase in ("probes", "all"):
            quota_stops = probes_label(args.label, spec, only, collector,
                                       owner_dsn, report["scenarios"])
    chunk_calls = count_llm_calls(collector.records[mark:])
    chunk_turns = sum(len(sc.get("turns", [])) for sc in report["scenarios"])
    chunk_probes = sum(len(sc.get("probes", [])) for sc in report["scenarios"])
    report["runs"].append({"started_at": started_at, "phase": args.phase,
                           "only": sorted(only) or "all",
                           "retry_invalid": args.retry_invalid,
                           "key_label": key_label, "llm_calls": chunk_calls,
                           "turns": chunk_turns, "probes": chunk_probes,
                           "quota_stops": quota_stops})
    stats = summarize(report)
    md_path = TRACKING_DIR / f"memory-benchmark-{args.label}.md"
    md_path.write_text(render_md(args.label, report, stats), encoding="utf-8")
    json_path.write_text(json.dumps({"label": args.label, "model": report.get("model"),
                                     "stats": stats,
                                     "scenarios": report["scenarios"],
                                     "runs": report["runs"]},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {md_path} and {json_path} "
          f"(chunk llm_calls={chunk_calls}, quota_stops={quota_stops})")
    return 4 if quota_stops else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv[1:]))
