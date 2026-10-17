"""Tier-2 direction probe V1 (plan 16/10, bước 3) — 30 fact + 77 câu của V0.

Ba phần, cùng bộ fact/probe của `tier2_recall_probes.yaml`:

  a. Code hiện tại (top-K, floor 0.75) chọn fact gì vào prompt — chỉ model embed,
     0 lời gọi LLM. Liệt kê fact vào prompt cho từng câu; câu đúng có fact đúng
     không, câu nhiễu kéo bao nhiêu fact vô nghĩa vào.
  b. e5-base so e5-small — chỉ model embed: recall@1/3/5/30, điểm nhiễu cao nhất
     và p95, khoảng chồng lấn, hạng fact tên trong 10 câu về tên, RAM/thời gian
     nạp/thời gian embed; thêm 400 fact nhiễu rồi đo recall@30 (430 fact).
  c. Planner chọn fact — 30 lời gọi DeepSeek deepseek-v4-flash (role planner như
     prod), 15 câu × 2 biến thể (base: planner như hiện tại; select: thêm khối
     ứng viên sau user message, mở rộng PlanOutput bằng relevant_fact_ids).

Không sửa nodes/memory.py hay nodes/planner.py. Không cài thêm thư viện.

Usage:
    python .../run_tier2_direction_probe.py --part all
    python .../run_tier2_direction_probe.py --part a
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import statistics
import sys
import time
from ctypes import wintypes
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
AGENTIC_ROOT = THIS_FILE.parents[2]
REPO_ROOT = AGENTIC_ROOT.parent
TRACKING_DIR = REPO_ROOT / "docs" / "tracking"
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

SMALL = "intfloat/multilingual-e5-small"
BASE = "intfloat/multilingual-e5-base"
FACT_DATE = "2026-10-14"          # synthetic facts: one fixed date
NAME_PROBES = [f"q_name_{i:02d}" for i in range(1, 11)]
PLANNER_TIMEOUT_S = 30


# ── Common helpers ────────────────────────────────────────────────────────

def cosine(a, b) -> float:
    va, vb = np.asarray(a), np.asarray(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0


def percentile(values, p: float):
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(math.ceil(p * len(s))) - 1))
    return s[idx]


def fact_text(f: dict) -> str:
    """Exactly what nodes/memory.py embeds via the extractor's stored form."""
    return f"{f['project']}: {f['text']}" if f.get("project") else f["text"]


def fact_line(f: dict, index: int, with_id: bool = False) -> str:
    label = f"({f['project']}) " if f.get("project") else ""
    prefix = f"f{index + 1:02d} " if with_id else ""
    return f"- {prefix}[{FACT_DATE}] {label}{f['text']}"


def load_spec() -> tuple[list[dict], list[dict]]:
    with open(THIS_FILE.parent / "tier2_recall_probes.yaml", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    return spec["facts"], spec["probes"]


def service(model_name: str, allow_download: bool = False):
    from langgraph_agents.shared.embedding import E5EmbeddingService

    if allow_download:
        os.environ["EMBEDDING_ALLOW_DOWNLOAD"] = "1"
    return E5EmbeddingService(model_name=model_name)


def rank(scores: list[float], facts: list[dict]) -> list[tuple[str, float]]:
    pairs = [(f["id"], round(s, 4)) for f, s in zip(facts, scores)]
    pairs.sort(key=lambda t: t[1], reverse=True)
    return pairs


def score_each(svc, asks: list[str], facts: list[dict]) -> list[list[float]]:
    """MAX cosine across the message + its sentence fragments, as memory.py does."""
    from langgraph_agents.nodes.memory import _split_tier2_queries

    fact_vecs = svc.embed_passages([fact_text(f) for f in facts])
    out = []
    for ask in asks:
        qvecs = svc.embed_queries(_split_tier2_queries(ask))
        out.append([max(cosine(qv, fv) for qv in qvecs) for fv in fact_vecs])
    return out


def rss_mb() -> float:
    """Working set of this process, no psutil (Windows API only)."""
    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PMC()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.windll.kernel32
    # GetCurrentProcess() is a pseudo-handle; psapi needs a real one.
    handle = kernel32.OpenProcess(0x1000, False, kernel32.GetCurrentProcessId())
    try:
        ok = kernel32.K32GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb)
    finally:
        kernel32.CloseHandle(handle)
    if not ok:
        return float("nan")
    return round(counters.WorkingSetSize / (1024 * 1024), 1)


# ── Part a: current selection (top-K + floor + project pull-in) ───────────

def select_current(ranked: list[tuple[str, float]], cfg: dict,
                   facts: list[dict]) -> list[str]:
    """Replicates nodes/memory.py::_load_tier2_facts, without the DB."""
    by_id = {f["id"]: f for f in facts}
    top = ranked[: cfg["top_k"]]
    hits = [fid for fid, s in top if s >= cfg["floor"]]
    labels = sorted({by_id[fid].get("project") for fid in hits
                     if by_id[fid].get("project")})
    mates = [f["id"] for f in facts
             if f.get("project") in labels and f["id"] not in hits]
    return hits + mates


def part_a(facts: list[dict], probes: list[dict], report: dict) -> dict:
    from langgraph_agents.nodes.memory import _tier2_config

    cfg = _tier2_config()
    svc = service(SMALL)
    asks = [p["ask"] for p in probes]
    all_scores = score_each(svc, asks, facts)

    rows = []
    ids_order = [f["id"] for f in facts]
    for p, scores in zip(probes, all_scores):
        ranked = rank(scores, facts)
        selected = select_current(ranked, cfg, facts)
        rows.append({
            "id": p["id"], "ask": p["ask"], "expect": p.get("expect"),
            "rank_of_expect": (next((i + 1 for i, (fid, _) in enumerate(ranked)
                                     if fid == p.get("expect")), None)
                               if p.get("expect") else None),
            "score_of_expect": next((s for fid, s in ranked
                                     if fid == p.get("expect")), None),
            "selected": selected,
            "selected_lines": [fact_line(facts[ids_order.index(fid)],
                                         ids_order.index(fid))
                               for fid in selected],
            "noise_selected_count": (len(selected) if p.get("expect") is None
                                     else None),
        })

    correct = [r for r in rows if r["expect"]]
    noise = [r for r in rows if r["expect"] is None]
    got = [r for r in correct if r["expect"] in r["selected"]]
    name_rows = [r for r in rows if r["id"] in NAME_PROBES]
    result = {
        "config": cfg,
        "correct_total": len(correct),
        "correct_in_prompt": len(got),
        "correct_in_prompt_rate": round(len(got) / len(correct), 4),
        "noise_probes": len(noise),
        "noise_with_any_fact": sum(1 for r in noise if r["selected"]),
        "noise_facts_total": sum(r["noise_selected_count"] or 0 for r in noise),
        "noise_facts_max": max((r["noise_selected_count"] or 0) for r in noise),
        "name_in_prompt": sum(1 for r in name_rows if "f_name" in r["selected"]),
        "name_total": len(name_rows),
        "rows": rows,
    }
    report["a"] = result
    print(f"[a] correct in prompt {len(got)}/{len(correct)}; "
          f"noise probes with facts {result['noise_with_any_fact']}/{len(noise)}; "
          f"name {result['name_in_prompt']}/{result['name_total']}", flush=True)
    return result


# ── Part b: e5-base vs e5-small ──────────────────────────────────────────

def noise_facts(n: int = 400) -> list[dict]:
    """Bilingual filler facts, deterministic, disjoint from the 30 real ones."""
    topics = [
        ("Thích nghe nhạc {x} khi nấu ăn", "Likes {x} music while cooking"),
        ("Đã từng đi du lịch {x} hai lần", "Has travelled to {x} twice"),
        ("Đang đọc sách về {x}", "Is reading a book about {x}"),
        ("Thích xem phim {x} vào tối thứ bảy", "Watches {x} films on Saturday nights"),
        ("Có một người bạn tên {x}", "Has a friend called {x}"),
        ("Từng học {x} hồi cấp ba", "Studied {x} in secondary school"),
        ("Thích uống trà {x} buổi chiều", "Drinks {x} tea in the afternoon"),
        ("Sưu tầm tem về {x}", "Collects {x} stamps"),
        ("Thường nghe podcast về {x}", "Listens to podcasts about {x}"),
        ("Thích màu {x} khi chọn quần áo", "Picks {x} clothes"),
    ]
    fillers = [
        "jazz", "rock", "Huế", "Đà Lạt", "Hà Nội", "Tokyo", "Paris", "Seoul",
        "nấu ăn chay", "lịch sử", "thiên văn", "kinh tế", "tâm lý học",
        "hội họa", "kiến trúc", "nhiếp ảnh", "vườn cây", "cờ vua", "bóng rổ",
        "bóng bàn", "bơi lội", "leo núi", "cắm trại", "đan len", "gốm sứ",
    ]
    out, i = [], 0
    while len(out) < n:
        vi, en = topics[i % len(topics)]
        fill = fillers[i % len(fillers)]
        text = vi.format(x=fill) if i % 2 == 0 else en.format(x=fill)
        out.append({"id": f"n{i + 1:03d}", "text": text, "project": None})
        i += 1
    return out


def model_profile(model_name: str, facts: list[dict], probes: list[dict],
                  noise: list[dict]) -> dict:
    """Recall/overlap/rank/ram/load/embed for one embedding model."""
    rss0 = rss_mb()
    svc = service(model_name, allow_download=True)
    t0 = time.perf_counter()
    svc.warm_up()
    load_ms = round((time.perf_counter() - t0) * 1000)
    rss1 = rss_mb()
    dim = len(svc.embed_query("dimension check"))

    asks = [p["ask"] for p in probes]
    scores = score_each(svc, asks, facts)
    correct_rows = [(p, s) for p, s in zip(probes, scores) if p.get("expect")]
    noise_rows = [(p, s) for p, s in zip(probes, scores) if p.get("expect") is None]

    recall = {}
    for k in (1, 3, 5, 30):
        hits = 0
        for p, sc in correct_rows:
            ids = [fid for fid, _ in rank(sc, facts)[:k]]
            hits += p["expect"] in ids
        recall[str(k)] = {"hit": hits, "total": len(correct_rows),
                          "rate": round(hits / len(correct_rows), 4)}

    name_ranks = {}
    for p, sc in zip(probes, scores):
        if p["id"] in NAME_PROBES:
            ids = [fid for fid, _ in rank(sc, facts)]
            name_ranks[p["id"]] = next((i + 1 for i, fid in enumerate(ids)
                                        if fid == "f_name"), None)

    noise_maxes = [max(sc) for _, sc in noise_rows]
    correct_scores = [sc[[f["id"] for f in facts].index(p["expect"])]
                      for p, sc in correct_rows]
    overlap = {
        "noise_max": round(max(noise_maxes), 4),
        "noise_p95": round(percentile(noise_maxes, 0.95), 4),
        "correct_min": round(min(correct_scores), 4),
        "correct_max": round(max(correct_scores), 4),
        "correct_below_noise_max": sum(1 for s in correct_scores
                                       if s < max(noise_maxes)),
        "correct_total": len(correct_scores),
    }

    # Same selection rule as production, but scored by THIS model: does the
    # name fact land in the prompt, and do noise questions pull facts in?
    from langgraph_agents.nodes.memory import _tier2_config

    cfg = _tier2_config()
    name_ok = 0
    noise_with = 0
    for p, sc in zip(probes, scores):
        selected = select_current(rank(sc, facts), cfg, facts)
        if p["id"] in NAME_PROBES and "f_name" in selected:
            name_ok += 1
        if p.get("expect") is None and selected:
            noise_with += 1

    # 400 filler facts: recall@30 with 430 candidates.
    many = facts + noise
    many_scores = score_each(svc, asks, many)
    many_hits = 0
    for p, sc in zip(probes, many_scores):
        if not p.get("expect"):
            continue
        ids = [fid for fid, _ in rank(sc, many)[:30]]
        many_hits += p["expect"] in ids
    recall430 = {"hit": many_hits, "total": len(correct_rows),
                 "rate": round(many_hits / len(correct_rows), 4)}

    embed_ms = []
    sample = [p["ask"] for p in probes[:50]] or ["warm"]
    for ask in sample:
        t = time.perf_counter()
        svc.embed_query(ask)
        embed_ms.append((time.perf_counter() - t) * 1000)

    return {
        "model": model_name,
        "dim": dim,
        "ram_before_mb": rss0,
        "ram_after_load_mb": rss1,
        "ram_delta_mb": round(rss1 - rss0, 1),
        "load_ms": load_ms,
        "embed_p50_ms": round(statistics.median(embed_ms), 2),
        "recall": recall,
        "recall430": recall430,
        "noise_max": overlap["noise_max"],
        "noise_p95": overlap["noise_p95"],
        "overlap": overlap,
        "name_ranks": name_ranks,
        "selection": {
            "config": cfg,
            "name_in_prompt": name_ok,
            "name_total": len(NAME_PROBES),
            "noise_with_fact": noise_with,
            "noise_total": len(noise_rows),
        },
    }


def part_b(facts: list[dict], probes: list[dict], report: dict,
           only: str | None = None) -> dict:
    """Profile one or both models. `only` keeps RAM numbers per-process."""
    previous = report.get("b") or {}
    extra = noise_facts(400)
    out = dict(previous)
    if only in (None, "small"):
        small = model_profile(SMALL, facts, probes, extra)
        out["small"] = small
        print(f"[b] small: recall@1 {small['recall']['1']['rate']}, "
              f"@30/430 {small['recall430']['rate']}, "
              f"ram {small['ram_before_mb']}->{small['ram_after_load_mb']} MB "
              f"(+{small['ram_delta_mb']}), load {small['load_ms']} ms", flush=True)
    if only in (None, "base"):
        try:
            big = model_profile(BASE, facts, probes, extra)
            big["available"] = True
            print(f"[b] base: recall@1 {big['recall']['1']['rate']}, "
                  f"@30/430 {big['recall430']['rate']}, "
                  f"ram {big['ram_before_mb']}->{big['ram_after_load_mb']} MB "
                  f"(+{big['ram_delta_mb']}), load {big['load_ms']} ms", flush=True)
        except Exception as exc:  # download blocked / no network / disk
            big = {"model": BASE, "available": False,
                   "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            print(f"[b] base unavailable: {big['error']}", flush=True)
        out["base"] = big
    out["noise_facts"] = len(extra)
    report["b"] = out
    return out


# ── Part c: planner selects facts (DeepSeek deepseek-v4-flash) ───────────

_PLANNER_QUESTIONS = [
    ("name", "then who am I?", "f_name"),
    ("name", "do you know my name?", "f_name"),
    ("name", "nhắc lại tên tôi xem nào?", "f_name"),
    ("name", "who am I? tôi bị đau lưng, gợi ý vài bài tập giúp tôi", "f_name"),
    ("name", "who am I?", "f_name"),
    ("name", "tên tôi là gì?", "f_name"),
    ("fact", "tôi chơi cầu lông mấy lần một tuần?", "f_badminton"),
    ("fact", "tôi bị đau ở đâu?", "f_back_pain"),
    ("fact", "what is my favorite food?", "f_pho"),
    ("fact", "Cho project A thì ưu tiên gì?", "f_proj_serverless"),
    ("noise", "giá vàng hôm nay bao nhiêu?", None),
    ("noise", "thời tiết hôm nay thế nào?", None),
    ("noise", "giải thích thuyết tương đối hẹp?", None),
    ("noise", "phim nào hay cuối tuần này?", None),
    ("noise", "chứng khoán hôm nay tăng hay giảm?", None),
]

_SELECT_NOTE = """
Notes this user gave earlier (pick only the ones this message needs; none is fine):
"""

_SELECT_FIELD_NOTE = """
The output also has `relevant_fact_ids`: the f-ids from those notes this message
needs, or [] when none of them is needed. Never invent an id."""


def _select_schema():
    class SelectPlanOutput(BaseModel):
        required_outputs: list[str] = Field(default_factory=list)
        resolved_query: str = ""
        needs_retrieval: bool = False
        needs_clarification: bool = False
        relevant_fact_ids: list[str] = Field(default_factory=list)

    return SelectPlanOutput


async def deepseek_balance() -> dict:
    import httpx

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.deepseek.com/user/balance",
                            headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            return r.json()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}


async def planner_call(structured, messages) -> dict:
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(structured.ainvoke(messages),
                                        timeout=PLANNER_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"invalid": "timeout", "ms": PLANNER_TIMEOUT_S * 1000}
    ms = round((time.perf_counter() - t0) * 1000, 1)
    if isinstance(result, dict):
        raw, parsed = result.get("raw"), result.get("parsed")
    else:
        raw, parsed = None, result
    if parsed is None:
        return {"invalid": "no_plan", "ms": ms}
    usage = getattr(raw, "usage_metadata", None) or {}
    from langgraph_agents.llm import extract_cache_tokens

    hit, miss = extract_cache_tokens(raw)
    return {
        "ms": ms,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_hit_tokens": hit,
        "plan": parsed.model_dump(),
    }


def _current_lines_for(query: str, facts: list[dict], cfg: dict,
                       svc) -> list[str]:
    """What memory.py would feed the planner for this query (part a logic)."""
    scores = score_each(svc, [query], facts)[0]
    selected = select_current(rank(scores, facts), cfg, facts)
    ids = [f["id"] for f in facts]
    return [fact_line(facts[ids.index(fid)], ids.index(fid)) for fid in selected]


async def part_c(facts: list[dict], report: dict) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    from langgraph_agents.llm import get_chat_model
    from langgraph_agents.nodes.memory import _tier2_config
    from langgraph_agents.nodes.planner import _PLANNER_SYSTEM_PROMPT, PlanOutput
    from langgraph_agents.shared.env import load_env

    load_env()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    model = os.environ.get("DEEPSEEK_MODEL_FAST") or "deepseek-v4-flash"
    balance_before = await deepseek_balance()

    llm = get_chat_model("planner")
    if llm is None:
        report["c"] = {"error": "no planner model (DeepSeek key missing)"}
        return report["c"]

    cfg = _tier2_config()
    planner_embed = service(SMALL)
    planner_embed.warm_up()
    # The candidate block numbers facts f01..f30; the labels in the spec use
    # ids like f_name. Keep both so recall compares the right pair.
    enum_of = {f["id"]: f"f{i + 1:02d}" for i, f in enumerate(facts)}
    candidate_block = _SELECT_NOTE + "\n".join(
        fact_line(f, i, with_id=True) for i, f in enumerate(facts))
    select_system = _PLANNER_SYSTEM_PROMPT + _SELECT_FIELD_NOTE
    select_structured = llm.with_structured_output(
        _select_schema(), method="json_mode", include_raw=True)
    base_structured = llm.with_structured_output(
        PlanOutput, method="json_mode", include_raw=True)

    rows, stopped = [], None
    for part, ask, expect in _PLANNER_QUESTIONS:
        base_msg = ask
        current = _current_lines_for(ask, facts, cfg, planner_embed)
        if current:
            base_msg += ("\n\nKnown about the user (what they told you "
                         "themselves; treat it as true):\n" + "\n".join(current))

        base = await planner_call(base_structured, [
            SystemMessage(content=_PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=base_msg),
        ])
        # Stop early if DeepSeek is down (timeout is per-question data; an
        # auth/connection failure is an outage).
        if base.get("invalid") == "no_plan":
            stopped = "deepseek_failed"
            rows.append({"kind": part, "ask": ask, "expect": expect,
                         "base": base, "select": None})
            break

        select = await planner_call(select_structured, [
            SystemMessage(content=select_system),
            HumanMessage(content=ask + "\n" + candidate_block),
        ])
        rows.append({"kind": part, "ask": ask, "expect": expect,
                     "base": base, "select": select})

    balance_after = await deepseek_balance()

    scored = [r for r in rows if r["select"] and "plan" in r["select"]]

    def _ids(entry):
        return [i for i in (entry.get("plan", {}).get("relevant_fact_ids") or [])
                if i]

    labels = [r for r in scored if r["expect"]]
    name_rows = [r for r in labels if r["kind"] == "name"]
    noise_rows = [r for r in scored if r["kind"] == "noise"]

    recall_hits = sum(1 for r in labels
                      if enum_of[r["expect"]] in _ids(r["select"]))
    precision = []
    for r in labels:
        ids = _ids(r["select"])
        if ids:
            precision.append(sum(1 for i in ids
                                 if i == enum_of[r["expect"]]) / len(ids))

    memory_rows = [r for r in scored if r["kind"] in ("name", "fact")]
    skip_base = sum(1 for r in memory_rows
                    if r["base"].get("plan", {}).get("needs_retrieval") is False)
    skip_select = sum(1 for r in memory_rows
                      if r["select"].get("plan", {}).get("needs_retrieval") is False)

    def _lat(entry_key, field):
        vals = [r[entry_key].get(field) for r in scored
                if isinstance(r[entry_key].get(field), (int, float))]
        return vals

    base_ms, sel_ms = _lat("base", "ms"), _lat("select", "ms")
    base_in = sum(r["base"].get("input_tokens", 0) or 0 for r in scored)
    sel_in = sum(r["select"].get("input_tokens", 0) or 0 for r in scored)
    base_out = sum(r["base"].get("output_tokens", 0) or 0 for r in scored)
    sel_out = sum(r["select"].get("output_tokens", 0) or 0 for r in scored)
    base_cache = sum(r["base"].get("cache_hit_tokens", 0) or 0 for r in scored)
    sel_cache = sum(r["select"].get("cache_hit_tokens", 0) or 0 for r in scored)

    result = {
        "model": model, "base_url": base_url,
        "calls": len(rows) * 2,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "stopped": stopped,
        "scored": len(scored),
        "name_total": len(name_rows),
        "name_recalled": sum(1 for r in name_rows
                             if enum_of[r["expect"]] in _ids(r["select"])),
        "noise_total": len(noise_rows),
        "noise_with_ids": sum(1 for r in noise_rows if _ids(r["select"])),
        "recall": ({"hit": recall_hits, "total": len(labels),
                    "rate": round(recall_hits / len(labels), 4)}
                   if labels else None),
        "precision_mean": round(statistics.mean(precision), 4) if precision else None,
        "needs_retrieval_false": {
            "base": {"count": skip_base, "total": len(memory_rows)},
            "select": {"count": skip_select, "total": len(memory_rows)},
        },
        "planner_ms": {
            "base": {"p50": percentile(base_ms, 0.5),
                     "p95": percentile(base_ms, 0.95), "n": len(base_ms)},
            "select": {"p50": percentile(sel_ms, 0.5),
                       "p95": percentile(sel_ms, 0.95), "n": len(sel_ms)},
        },
        "tokens": {
            "base": {"input": base_in, "output": base_out,
                     "cache_hit": base_cache},
            "select": {"input": sel_in, "output": sel_out,
                       "cache_hit": sel_cache},
        },
        "rows": rows,
    }
    report["c"] = result
    print(f"[c] recall {result['recall']} precision {result['precision_mean']}; "
          f"noise with ids {result['noise_with_ids']}/{result['noise_total']}; "
          f"skip retriever base {skip_base}/{len(memory_rows)} "
          f"select {skip_select}/{len(memory_rows)}; stopped={stopped}", flush=True)
    return result


# ── Report ───────────────────────────────────────────────────────────────

def render(report: dict) -> str:
    a, b, c = report.get("a"), report.get("b"), report.get("c")
    small = (b or {}).get("small", {})
    big = (b or {}).get("base", {})
    rows = report.get("rows_table", [])

    lines = ["# tier2-direction-V1", "",
             "30 fact + 77 câu của V0 (`tier2_recall_probes.yaml`); a/b chỉ dùng "
             "model embed (0 LLM), c dùng DeepSeek `deepseek-v4-flash` đúng 30 lời gọi.",
             ""]
    if a:
        lines += ["## a. Hiện tại (top-K, floor)", "",
                  f"- top_k={a['config']['top_k']}, floor={a['config']['floor']}",
                  f"- câu đúng có fact đúng trong prompt: "
                  f"{a['correct_in_prompt']}/{a['correct_total']} "
                  f"({a['correct_in_prompt_rate']})",
                  f"- câu nhiễu kéo fact vào: {a['noise_with_any_fact']}/"
                  f"{a['noise_probes']} (tổng {a['noise_facts_total']} fact, "
                  f"max {a['noise_facts_max']}/câu)",
                  f"- câu về tên có fact tên trong prompt: "
                  f"{a['name_in_prompt']}/{a['name_total']}", ""]
    if small:
        lines.append("## b. e5-small so e5-base")
        lines.append("")
        lines.append("| model | dim | recall@1 | @3 | @5 | @30 | @30/430 | "
                     "noise max | noise p95 | correct min | RAM sau nạp (MB) | load ms | embed p50 ms |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for m in (small, big):
            if not m or not m.get("available", True):
                continue
            lines.append(
                f"| {m['model']} | {m['dim']} | {m['recall']['1']['rate']} | "
                f"{m['recall']['3']['rate']} | {m['recall']['5']['rate']} | "
                f"{m['recall']['30']['rate']} | {m['recall430']['rate']} | "
                f"{m['noise_max']} | {m['noise_p95']} | "
                f"{m['overlap']['correct_min']} | {m['ram_after_load_mb']} | "
                f"{m['load_ms']} | {m['embed_p50_ms']} |")
        if big and big.get("available") is False:
            lines.append(f"| {BASE} | — | — | — | — | — | — | — | — | — | — | — | — |")
            lines.append("")
            lines.append(f"e5-base không nạp được: {big.get('error')}")
        lines += ["", "Hạng fact tên trong 10 câu về tên:", ""]
        for name, m in (("e5-small", small), ("e5-base", big)):
            if m and m.get("name_ranks"):
                lines.append(f"- {name}: {m['name_ranks']}")
        lines.append("")
    if c and "planner_ms" in c:
        lines += ["## c. Planner chọn fact (DeepSeek)", "",
                  f"- model: {c.get('model')} @ {c.get('base_url')}, "
                  f"calls={c.get('calls')}, stopped={c.get('stopped')}",
                  f"- recall {c.get('recall')}, precision trung bình "
                  f"{c.get('precision_mean')}",
                  f"- câu nhiễu có chọn id: {c.get('noise_with_ids')}/"
                  f"{c.get('noise_total')}",
                  f"- needs_retrieval=false trên 10 câu ký ức: base "
                  f"{c['needs_retrieval_false']['base']}, select "
                  f"{c['needs_retrieval_false']['select']}",
                  f"- planner ms: base {c['planner_ms']['base']}, select "
                  f"{c['planner_ms']['select']}",
                  f"- token: base {c['tokens']['base']}, select {c['tokens']['select']}",
                  f"- số dư trước: {c.get('balance_before')}",
                  f"- số dư sau: {c.get('balance_after')}", ""]
    if rows:
        lines += ["## Bảng tổng hợp", "",
                  "| hướng | tên vào prompt (10) | câu nhiễu kéo fact (14) | "
                  "recall@30 (430 fact) | planner ms / input token | RAM sau nạp | "
                  "bỏ được retriever (10) |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append("| " + " | ".join(str(x) for x in r.values()) + " |")
        lines.append("")
    return "\n".join(lines)


def build_table(report: dict) -> list[dict]:
    a, b, c = report.get("a"), report.get("b"), report.get("c")
    small = (b or {}).get("small", {})
    big = (b or {}).get("base", {})
    rows = []

    def skip(entry):
        if not c:
            return "—"
        x = c.get("needs_retrieval_false", {}).get(entry, {})
        return f"{x.get('count', '—')}/{x.get('total', '—')}"

    def planner_ms(token_entry):
        if not c or "planner_ms" not in c:
            return "—"
        base_p50 = c["planner_ms"]["base"]["p50"]
        sel_p50 = c["planner_ms"]["select"]["p50"]
        base_in = c["tokens"]["base"]["input"]
        sel_in = c["tokens"]["select"]["input"]
        if token_entry == "base":
            return f"{base_p50} ms / {base_in} in"
        return f"+{(sel_p50 or 0) - (base_p50 or 0):.0f} ms / +{sel_in - base_in} in"

    rows.append({
        "hướng": "Hiện tại (top-5, floor 0.75)",
        "tên vào prompt": (f"{a['name_in_prompt']}/{a['name_total']}" if a else "—"),
        "nhiễu kéo fact": (f"{a['noise_with_any_fact']}/{a['noise_probes']}" if a else "—"),
        "recall@30/430": (small.get("recall430", {}).get("rate", "—") if small else "—"),
        "planner ms/in": planner_ms("base"),
        "RAM sau nạp": (f"{small.get('ram_after_load_mb', '—')} MB"
                        if small else "—"),
        "bỏ retriever": skip("base"),
    })

    rows.append({
        "hướng": "e5-base thay e5-small",
        "tên vào prompt": (f"{big['selection']['name_in_prompt']}/"
                           f"{big['selection']['name_total']}"
                           if big and big.get("available", True) and big.get("selection")
                           else "—"),
        "nhiễu kéo fact": (f"{big['selection']['noise_with_fact']}/"
                           f"{big['selection']['noise_total']}"
                           if big and big.get("available", True) and big.get("selection")
                           else "—"),
        "recall@30/430": (big.get("recall430", {}).get("rate", "—")
                          if big and big.get("available", True) else "—"),
        "planner ms/in": "0 / 0",
        "RAM sau nạp": (big.get("ram_after_load_mb", "—")
                        if big and big.get("available", True) else "—"),
        "bỏ retriever": "—",
    })

    if c and "planner_ms" in c:
        rows.append({
            "hướng": "Planner chọn trong 30 ứng viên",
            "tên vào prompt": f"{c.get('name_recalled')}/{c.get('name_total')}",
            "nhiễu kéo fact": f"{c.get('noise_with_ids')}/{c.get('noise_total')}",
            "recall@30/430": "—",
            "planner ms/in": planner_ms("select"),
            "RAM sau nạp": "0 (model sẵn có)",
            "bỏ retriever": skip("select"),
        })
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Tier-2 direction probe V1.")
    ap.add_argument("--part", default="all", choices=["a", "b", "c", "all"])
    ap.add_argument("--model", default=None, choices=["small", "base"],
                    help="Part b only: profile one model in a fresh process.")
    ap.add_argument("--label", default="V1")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    facts, probes = load_spec()
    json_path = TRACKING_DIR / f"tier2-direction-{args.label}.json"
    report: dict = {"label": args.label, "facts": len(facts),
                    "probes": len(probes)}
    if json_path.exists():
        try:
            report.update(json.loads(json_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    if args.part in ("a", "all"):
        part_a(facts, probes, report)
    if args.part in ("b", "all"):
        part_b(facts, probes, report, only=args.model)
    if args.part in ("c", "all"):
        asyncio.run(part_c(facts, report))

    report["rows_table"] = build_table(report)
    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    md_path = TRACKING_DIR / f"tier2-direction-{args.label}.md"
    md_path.write_text(render(report), encoding="utf-8")
    print(f"Wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
