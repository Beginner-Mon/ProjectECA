"""Memory probe runner (memory-v2 A2) — đo bước lọc fact dài hạn (LTM) với LLM thật.

Một lần gọi LLM đọc hội thoại + các fact đã lưu, trả về danh sách thao tác
ADD / UPDATE / DELETE. Runner này đo: mất bao lâu, và fact đúng / sai / bịa bao
nhiêu. KHÔNG đụng DB, KHÔNG dựng graph — chỉ role `memory` của llm.py.

Mỗi hội thoại trong `memory_probes.yaml` chạy hai kiểu đầu vào:
  session — cả hội thoại trong một lần gọi;
  turn    — mỗi cặp (tin user + trả lời assistant) một lần gọi, mang fact do
            các lượt trước tạo ra sang lượt sau;
  live    — đúng hình dạng production gửi: lát đầu là tin user đầu tiên (đọc từ
            đầu hội thoại), mỗi lát sau là câu trả lời trước của assistant + tin
            user kế tiếp (đọc tiếp), mang fact sang lát sau như turn.

Code kiểm tra lại đầu ra của model (tối đa 5 thao tác, fact <= 300 ký tự, category
trong danh sách đóng, không UPDATE/DELETE id khoá hoặc id lạ, bỏ fact trông giống
định danh) và đếm số thao tác bị bỏ theo từng lý do.

Ví dụ:
    python agenticRAG/langgraph_agents/local_tests/run_memory_probe.py --label V0
    python agenticRAG/langgraph_agents/local_tests/run_memory_probe.py --label V0 --only c01_vi_plain_condition
    python agenticRAG/langgraph_agents/local_tests/run_memory_probe.py --label dev --mode session
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
import traceback
import unicodedata
from pathlib import Path
from typing import Any

_THIS_FILE = Path(__file__).resolve()
_AGENTIC_ROOT = _THIS_FILE.parents[2]
_REPO_ROOT = _AGENTIC_ROOT.parent
_TRACKING_DIR = _REPO_ROOT / "docs" / "tracking"
if str(_AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_ROOT))

import yaml  # noqa: E402

# Prompt, cách dựng user message, lời gọi JSON và các bước kiểm tra là của code
# thật: probe đo đúng thứ đang chạy, không giữ bản sao riêng.
from langgraph_agents.nodes.fact_extractor import (  # noqa: E402
    _SCOPE_CONTINUED,
    _SCOPE_FROM_START,
    MEMORY_PROMPT,
    enforce,
    extraction_llm,
    render_user_message,
    request_operations,
)

OUTPUT_CAP_TOKENS = 512  # trần của role memory trong llm.py (_MAX_TOKENS_FAST)

# ── Chuẩn hoá chuỗi để so khớp ───────────────────────────────────────────

def _fold(s: str) -> str:
    """Hạ chữ thường, bỏ dấu (kể cả đ) — so khớp cụm kỳ vọng không phụ thuộc cách viết."""
    s = unicodedata.normalize("NFD", (s or "").lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _lower_nfc(s: str) -> str:
    return unicodedata.normalize("NFC", (s or "").lower())


def _apply_ops(stored: list[dict], kept: list[dict], counter: list[int]) -> list[dict]:
    """Mang fact sang lượt sau (chế độ turn). ADD cấp id mới n1, n2, ...

    REPEAT giữ nguyên (nói lại điều đã có). FORGET_ALL chỉ giữ lại fact khoá
    (user tự gõ)."""
    facts = [dict(f) for f in stored]
    for op in kept:
        if op["op"] == "ADD":
            counter[0] += 1
            facts.append({"id": f"n{counter[0]}", "fact": op["fact"],
                          "type": op.get("type"),
                          "topic": op.get("topic"),
                          "safety": op.get("safety", False),
                          "locked": False})
        elif op["op"] == "UPDATE":
            for f in facts:
                if f["id"] == op["id"]:
                    f["fact"] = op["fact"]
                    if op.get("type") is not None:
                        f["type"] = op["type"]
                    if op.get("topic") is not None:
                        f["topic"] = op["topic"]
                        f["project"] = op["topic"]
                    if op.get("safety") is not None:
                        f["safety"] = op["safety"]
        elif op["op"] == "DELETE":
            facts = [f for f in facts if f["id"] != op["id"]]
        elif op["op"] == "FORGET_ALL":
            facts = [f for f in facts if f.get("locked")]
    return facts


# ── Gọi LLM ──────────────────────────────────────────────────────────────

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\S*)", re.IGNORECASE)


def _scrub(s: str) -> str:
    return _SECRET_RE.sub("<redacted>", s)[:400]


def _usage(ai_msg: Any) -> tuple[int | None, int | None]:
    um = getattr(ai_msg, "usage_metadata", None) or {}
    if um.get("input_tokens") is not None or um.get("output_tokens") is not None:
        return um.get("input_tokens"), um.get("output_tokens")
    tu = (getattr(ai_msg, "response_metadata", None) or {}).get("token_usage") or {}
    return tu.get("prompt_tokens"), tu.get("completion_tokens")


async def _call(structured_llm: Any, user_msg: str, stored: list[dict]
                ) -> dict[str, Any]:
    """Một lần gọi đo thời gian; không bao giờ ném lỗi (lỗi vào bản ghi)."""
    rec: dict[str, Any] = {
        "elapsed_s": None, "input_tokens": None, "output_tokens": None,
        "parse_ok": False, "truncated": False, "error": None,
        "finish_reason": None, "model": None, "raw_content": "",
        "returned_ops": [], "kept_ops": [], "dropped": [],
    }
    t0 = time.perf_counter()
    try:
        result = await request_operations(structured_llm, user_msg)
    except Exception as exc:  # noqa: BLE001 — timeout/401/... ghi lại rồi đi tiếp
        rec["elapsed_s"] = round(time.perf_counter() - t0, 2)
        rec["error"] = f"{type(exc).__name__}: {_scrub(str(exc))}"
        return rec
    rec["elapsed_s"] = round(time.perf_counter() - t0, 2)

    raw = result.get("raw") if isinstance(result, dict) else None
    parsed = result.get("parsed") if isinstance(result, dict) else result
    content = str(getattr(raw, "content", "") or "")
    rec["raw_content"] = content[:3000]
    rec["input_tokens"], rec["output_tokens"] = _usage(raw)
    meta = getattr(raw, "response_metadata", None) or {}
    rec["finish_reason"] = meta.get("finish_reason")
    rec["model"] = meta.get("model_name") or meta.get("model")

    if parsed is not None:
        rec["parse_ok"] = True
        rec["returned_ops"] = list(parsed.operations)
    rec["truncated"] = bool(
        rec["finish_reason"] == "length"
        or (rec["output_tokens"] is not None
            and rec["output_tokens"] >= OUTPUT_CAP_TOKENS)
        or (not rec["parse_ok"] and not content.rstrip().endswith("}")))
    kept, dropped = enforce(rec["returned_ops"], stored)
    rec["kept_ops"], rec["dropped"] = kept, dropped
    return rec


# ── Chấm điểm ────────────────────────────────────────────────────────────

def _matches(exp: dict, op: dict) -> bool:
    if exp.get("one_of"):  # any one of several alternative expectations
        return any(_matches(alt, op) for alt in exp["one_of"])
    if op["op"] != exp["op"]:
        return False
    if exp.get("id") is not None and op.get("id") != exp["id"]:
        return False
    if op["op"] in ("ADD", "UPDATE"):
        folded = _fold(op["fact"])
        for group in exp.get("groups") or []:
            if not any(_fold(p) in folded for p in group):
                return False
    return True


def _forbidden_op_matches(spec: dict, op: Any) -> bool:
    """True when `op` is an operation the case forbids (`forbidden_ops`).

    spec keys, all optional except op: op (name or list of names), id, type,
    type_not (op type differs), topic_not (op topic differs, case-insensitive),
    safety (bool, op must carry it), groups (phrase groups over the fact)."""
    if not isinstance(op, dict):
        return False
    names = spec["op"] if isinstance(spec["op"], list) else [spec["op"]]
    if op.get("op") not in names:
        return False
    if spec.get("id") is not None and op.get("id") != spec["id"]:
        return False
    if spec.get("type") is not None and op.get("type") != spec["type"]:
        return False
    if spec.get("type_not") is not None and op.get("type") == spec["type_not"]:
        return False
    if spec.get("topic_not") is not None and (
            str(op.get("topic") or "").casefold() == str(spec["topic_not"]).casefold()):
        return False
    if spec.get("safety") is not None and op.get("safety") != spec["safety"]:
        return False
    if spec.get("groups"):
        fact = op.get("fact")
        if not isinstance(fact, str):
            return False
        folded = _fold(fact)
        if not all(any(_fold(p) in folded for p in g) for g in spec["groups"]):
            return False
    return True


def _score(expect: dict, ops: list[dict], dropped: list[dict]) -> dict[str, Any]:
    """ops = mọi thao tác đã qua kiểm tra code (turn mode: gộp mọi lượt)."""
    exp_ops = expect.get("ops") or []
    acceptable = expect.get("acceptable") or []
    forbidden = expect.get("forbidden") or []

    used: set[int] = set()
    found: list[dict] = []
    missed: list[dict] = []
    category_mismatch: list[dict] = []
    for exp in exp_ops:
        hit = next((i for i, op in enumerate(ops)
                    if i not in used and _matches(exp, op)), None)
        if hit is None:
            missed.append(exp)
            continue
        used.add(hit)
        found.append({"expected": exp, "got": ops[hit]})
        if (exp.get("type") and ops[hit].get("type")
                and exp["type"] != ops[hit]["type"]):
            category_mismatch.append({"expected": exp, "got": ops[hit]})
        if (exp.get("topic") and ops[hit].get("topic")
                and exp["topic"] != ops[hit]["topic"]):
            category_mismatch.append({"expected": exp, "got": ops[hit]})
        if (exp.get("safety") is not None and ops[hit].get("safety") is not None
                and exp["safety"] != ops[hit]["safety"]):
            category_mismatch.append({"expected": exp, "got": ops[hit]})

    extras: list[dict] = []
    repeats: list[dict] = []
    accepted: list[dict] = []
    for i, op in enumerate(ops):
        if i in used:
            continue
        if any(_matches(a, op) for a in acceptable):
            accepted.append(op)
        elif any(_matches(e, op) for e in exp_ops):
            repeats.append(op)
        else:
            extras.append(op)

    hits: list[dict] = []
    for op in ops:
        if "fact" not in op:
            continue
        text = _lower_nfc(op["fact"])
        for phrase in forbidden:
            if _lower_nfc(phrase) in text:
                hits.append({"phrase": phrase, "op": op})
    for spec in expect.get("forbidden_ops") or []:
        for op in ops:
            if _forbidden_op_matches(spec, op):
                hits.append({"phrase": "forbidden_ops " + json.dumps(
                    spec, ensure_ascii=False), "op": op})
    dropped_hits: list[dict] = []
    for spec in expect.get("forbidden_ops") or []:
        for d in dropped:
            if _forbidden_op_matches(spec, d["op"]):
                dropped_hits.append({"phrase": "forbidden_ops " + json.dumps(
                    spec, ensure_ascii=False), "reason": d["reason"]})
    for d in dropped:
        fact = d["op"].get("fact") if isinstance(d["op"], dict) else None
        if isinstance(fact, str):
            for phrase in forbidden:
                if _lower_nfc(phrase) in _lower_nfc(fact):
                    dropped_hits.append({"phrase": phrase, "reason": d["reason"]})

    if missed or hits:
        verdict = "FAIL"
    elif extras or repeats:
        verdict = "REVIEW"
    else:
        verdict = "OK"
    return {"found": found, "missed": missed, "extras": extras,
            "repeats": repeats, "acceptable": accepted,
            "category_mismatch": category_mismatch,
            "forbidden_hits": hits, "forbidden_in_dropped": dropped_hits,
            "verdict": verdict}


# ── Chạy một hội thoại ───────────────────────────────────────────────────

def _split_tiers(stored: list[dict]) -> tuple[list, list, list]:
    """Chia stored thành (tier1, tier2, project labels) cho prompt mới."""
    tier1 = [dict(f, kind=f.get("kind", "fact"), project=f.get("project"),
                  tier=1) for f in stored if f.get("tier", 2) == 1]
    tier2 = [dict(f, kind=f.get("kind", "fact"), project=f.get("project"),
                  tier=2) for f in stored if f.get("tier", 2) != 1]
    projects = sorted({f["project"] for f in tier2 if f.get("project")})
    return tier1, tier2, projects


def _render(date: str, stored: list[dict], msgs: list[dict], scope: str) -> str:
    tier1, tier2, projects = _split_tiers(stored)
    return render_user_message(date, tier1, tier2, projects, msgs, scope)


def _pairs(messages: list[dict]) -> list[list[dict]]:
    return [messages[i:i + 2] for i in range(0, len(messages), 2)]


def _live_slices(messages: list[dict]) -> list[tuple[list[dict], str]]:
    """Các lát production sẽ đọc, kèm dòng scope. Lát đầu: tin user đầu tiên, đọc từ
    đầu hội thoại. Lát sau: trả lời trước của assistant + tin user kế tiếp. Câu trả
    lời cuối không có tin user sau nó thì không thành lát (production bỏ qua)."""
    slices: list[tuple[list[dict], str]] = []
    for i, m in enumerate(messages):
        if m["role"] != "user":
            continue
        if i == 0:
            slices.append(([m], _SCOPE_FROM_START))
        else:
            slices.append((messages[i - 1:i + 1], _SCOPE_CONTINUED))
    return slices


async def _run_conv(structured_llm: Any, conv: dict, modes: list[str]
                    ) -> dict[str, Any]:
    stored = [dict(f) for f in conv.get("stored") or []]
    out: dict[str, Any] = {"id": conv["id"], "lang": conv["lang"],
                           "date": conv["date"], "stored": stored,
                           "messages": conv["messages"],
                           "expect": conv["expect"], "modes": {}}

    if "session" in modes:
        msg = _render(conv["date"], stored, conv["messages"],
                      "a whole conversation")
        call = await _call(structured_llm, msg, stored)
        sc = _score(conv["expect"], call["kept_ops"], call["dropped"])
        out["modes"]["session"] = {"calls": [call], "score": sc,
                                   "total_s": call["elapsed_s"]}

    if "turn" in modes:
        facts = [dict(f) for f in stored]
        counter = [0]
        calls: list[dict] = []
        all_ops: list[dict] = []
        all_dropped: list[dict] = []
        for n, pair in enumerate(_pairs(conv["messages"]), start=1):
            msg = _render(
                conv["date"], facts, pair,
                f"one turn, number {n}: a user message and the assistant's reply")
            call = await _call(structured_llm, msg, facts)
            call["turn"] = n
            calls.append(call)
            all_ops += [dict(o, turn=n) for o in call["kept_ops"]]
            all_dropped += call["dropped"]
            facts = _apply_ops(facts, call["kept_ops"], counter)
        sc = _score(conv["expect"], all_ops, all_dropped)
        out["modes"]["turn"] = {
            "calls": calls, "score": sc,
            "total_s": round(sum(c["elapsed_s"] or 0 for c in calls), 2)}

    if "live" in modes:
        facts = [dict(f) for f in stored]
        counter = [0]
        calls = []
        all_ops = []
        all_dropped = []
        for n, (sl, scope) in enumerate(_live_slices(conv["messages"]), start=1):
            msg = _render(conv["date"], facts, sl, scope)
            call = await _call(structured_llm, msg, facts)
            call["turn"] = n
            calls.append(call)
            all_ops += [dict(o, turn=n) for o in call["kept_ops"]]
            all_dropped += call["dropped"]
            facts = _apply_ops(facts, call["kept_ops"], counter)
        sc = _score(conv["expect"], all_ops, all_dropped)
        out["modes"]["live"] = {
            "calls": calls, "score": sc,
            "total_s": round(sum(c["elapsed_s"] or 0 for c in calls), 2)}
    return out


# ── Thống kê và báo cáo ──────────────────────────────────────────────────

def _pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}"


def _mode_stats(results: list[dict], mode: str) -> dict[str, Any]:
    calls = [c for r in results if mode in r["modes"] for c in r["modes"][mode]["calls"]]
    ok_calls = [c for c in calls if not c["error"]]
    times = [c["elapsed_s"] for c in ok_calls]
    scores = [r["modes"][mode]["score"] for r in results if mode in r["modes"]]
    dropped: dict[str, int] = {}
    for c in calls:
        for d in c["dropped"]:
            dropped[d["reason"]] = dropped.get(d["reason"], 0) + 1
    expected_total = sum(len(r["expect"].get("ops") or []) for r in results
                         if mode in r["modes"])
    in_tok = [c["input_tokens"] for c in ok_calls if c["input_tokens"] is not None]
    out_tok = [c["output_tokens"] for c in ok_calls if c["output_tokens"] is not None]
    return {
        "calls": len(calls),
        "call_errors": len(calls) - len(ok_calls),
        "p50": _pct(times, 0.5), "p95": _pct(times, 0.95),
        "max": max(times) if times else None,
        "conv_total_p50": _pct([r["modes"][mode]["total_s"] for r in results
                                if mode in r["modes"]], 0.5),
        "conv_total_p95": _pct([r["modes"][mode]["total_s"] for r in results
                                if mode in r["modes"]], 0.95),
        "conv_total_max": max([r["modes"][mode]["total_s"] for r in results
                               if mode in r["modes"]] or [0]),
        "empty": sum(1 for c in ok_calls if c["parse_ok"] and not c["returned_ops"]),
        "empty_share": (sum(1 for c in ok_calls
                            if c["parse_ok"] and not c["returned_ops"])
                        / len(ok_calls)) if ok_calls else None,
        "expected_total": expected_total,
        "found": sum(len(s["found"]) for s in scores),
        "missed": sum(len(s["missed"]) for s in scores),
        "extras": sum(len(s["extras"]) for s in scores),
        "repeats": sum(len(s["repeats"]) for s in scores),
        "acceptable": sum(len(s["acceptable"]) for s in scores),
        "category_mismatch": sum(len(s["category_mismatch"]) for s in scores),
        "forbidden_hits": sum(len(s["forbidden_hits"]) for s in scores),
        "forbidden_in_dropped": sum(len(s["forbidden_in_dropped"]) for s in scores),
        "parse_failures": sum(1 for c in ok_calls if not c["parse_ok"]),
        "truncations": sum(1 for c in ok_calls if c["truncated"]),
        "dropped": dropped,
        "avg_in": (sum(in_tok) / len(in_tok)) if in_tok else None,
        "avg_out": (sum(out_tok) / len(out_tok)) if out_tok else None,
        "max_out": max(out_tok) if out_tok else None,
        "verdicts": {v: sum(1 for s in scores if s["verdict"] == v)
                     for v in ("OK", "REVIEW", "FAIL")},
    }


def _op_str(op: dict) -> str:
    if op["op"] == "DELETE":
        return f"DELETE {op['id']}"
    if op["op"] == "REPEAT":
        return f"REPEAT {op['id']}"
    if op["op"] == "FORGET_ALL":
        return "FORGET_ALL"
    ident = f" {op['id']}" if op["op"] == "UPDATE" else ""
    turn = f" (lượt {op['turn']})" if op.get("turn") else ""
    typ = op.get("type") or op.get("category")
    topic = op.get("topic")
    label = f"{typ}" if typ else ""
    if topic:
        label = f"{label}/{topic}" if label else topic
    return f"{op['op']}{ident} [{label}] \"{op['fact']}\"{turn}"


def _exp_label(e: dict) -> str:
    bits = [str(e[k]) for k in ("type", "topic") if e.get(k)]
    if e.get("safety") is not None:
        bits.append(f"safety={e['safety']}")
    return f" [{', '.join(bits)}]" if bits else ""


def _exp_str(e: dict) -> str:
    if e.get("one_of"):
        return " OR ".join(_exp_str(x) for x in e["one_of"])
    ident = f" {e['id']}" if e.get("id") else ""
    groups = " AND ".join("(" + " | ".join(g) + ")" for g in e.get("groups") or [])
    return f"{e['op']}{ident}{_exp_label(e)} {groups}".rstrip()


def _render_report(label: str, results: list[dict], modes: list[str]) -> str:
    stats = {m: _mode_stats(results, m) for m in modes}
    n_calls = sum(s["calls"] for s in stats.values())
    L: list[str] = []
    L.append(f"# memory-probe-{label}")
    L.append("")
    L.append("Runner: `local_tests/run_memory_probe.py`, role `memory` "
             "(llm.py: khoá ECA, tắt reasoning, timeout 15 s, không retry, "
             f"temperature 0, trần output {OUTPUT_CAP_TOKENS} token). "
             f"{len(results)} hội thoại tổng hợp, không đụng DB, không dựng graph.")
    L.append("")
    L.append("## Tóm tắt")
    L.append("")
    L.append(f"- Tổng số lần gọi LLM: **{n_calls}** "
             + ", ".join(f"{m}: {stats[m]['calls']}" for m in modes)
             + f"; lỗi gọi (timeout/4xx): "
             + ", ".join(f"{m}: {stats[m]['call_errors']}" for m in modes))
    L.append("")
    L.append("| | " + " | ".join(modes) + " |")
    L.append("|---|" + "---|" * len(modes))

    def row(name: str, fn) -> None:
        L.append(f"| {name} | " + " | ".join(fn(stats[m]) for m in modes) + " |")

    row("thời gian / lần gọi p50 (s)", lambda s: _fmt(s["p50"]))
    row("thời gian / lần gọi p95 (s)", lambda s: _fmt(s["p95"]))
    row("thời gian / lần gọi max (s)", lambda s: _fmt(s["max"]))
    if "turn" in modes or "live" in modes:
        L.append("| tổng thời gian / hội thoại p50 / p95 / max (s) | "
                 + " | ".join(
                     (f"{_fmt(stats[m]['conv_total_p50'])} / "
                      f"{_fmt(stats[m]['conv_total_p95'])} / "
                      f"{_fmt(stats[m]['conv_total_max'])}")
                     for m in modes) + " |")
    row("lần gọi trả danh sách rỗng",
        lambda s: (f"{s['empty']}/{s['calls'] - s['call_errors']} "
                   f"({(s['empty_share'] or 0) * 100:.0f}%)"))
    row("fact kỳ vọng tìm thấy", lambda s: f"{s['found']}/{s['expected_total']}")
    row("fact kỳ vọng bị bỏ sót", lambda s: str(s["missed"]))
    row("thao tác thừa (extra, người xét)", lambda s: str(s["extras"]))
    row("thao tác lặp lại fact kỳ vọng", lambda s: str(s["repeats"]))
    row("thao tác ngoài kỳ vọng nhưng chấp nhận được", lambda s: str(s["acceptable"]))
    row("cụm CẤM xuất hiện (mỗi cái là một lỗi)", lambda s: str(s["forbidden_hits"]))
    row("sai category so với kỳ vọng", lambda s: str(s["category_mismatch"]))
    row("JSON không parse được", lambda s: str(s["parse_failures"]))
    row("output bị cắt (truncated)", lambda s: str(s["truncations"]))
    row("thao tác bị code bỏ, theo lý do",
        lambda s: ", ".join(f"{k}={v}" for k, v in sorted(s["dropped"].items())) or "0")
    row("token vào / ra trung bình (ra max)",
        lambda s: (f"{s['avg_in']:.0f} / {s['avg_out']:.0f} (max {s['max_out']})"
                   if s["avg_in"] is not None and s["avg_out"] is not None else "—"))
    row("hội thoại OK / REVIEW / FAIL",
        lambda s: f"{s['verdicts']['OK']} / {s['verdicts']['REVIEW']} / "
                  f"{s['verdicts']['FAIL']}")
    L.append("")
    L.append("Ghi chú: OK = tìm đủ, không thừa, không cụm cấm. REVIEW = tìm đủ, "
             "không cụm cấm, nhưng có thao tác thừa cần người xét (fact đúng vô hại "
             "hay bịa). FAIL = bỏ sót hoặc dính cụm cấm. Cụm cấm chỉ tính trên "
             "thao tác đã qua kiểm tra của code; nếu model viết số điện thoại và "
             "code bỏ nó thì ghi vào cột \"bị code bỏ\", không tính là dính cụm cấm "
             "(số lần như vậy nằm trong `forbidden_in_dropped` của file JSON).")
    for m in modes:
        if stats[m]["forbidden_in_dropped"]:
            L.append(f"- {m}: {stats[m]['forbidden_in_dropped']} thao tác chứa cụm cấm "
                     "đã bị code chặn.")
    L.append("")

    L.append("## Từng hội thoại")
    L.append("")
    for r in results:
        L.append(f"### {r['id']} ({r['lang']}, {r['date']})")
        L.append("")
        L.append("Tin nhắn người dùng: "
                 + " / ".join(m["content"] for m in r["messages"]
                              if m["role"] == "user")[:600])
        if r["stored"]:
            L.append("")
            L.append("Fact đã lưu: " + "; ".join(
                f"{f['id']}{' (khoá)' if f.get('locked') else ''}: {f['fact']}"
                for f in r["stored"]))
        L.append("")
        L.append("Kỳ vọng: " + (" ; ".join(_exp_str(e) for e in r["expect"].get("ops") or [])
                                 or "không có fact nào"))
        if r["expect"].get("acceptable"):
            L.append("Chấp nhận được: " + " ; ".join(
                _exp_str(e) for e in r["expect"]["acceptable"]))
        if r["expect"].get("forbidden"):
            L.append("Cụm cấm: " + ", ".join(f"`{p}`" for p in r["expect"]["forbidden"]))
        L.append("")
        for m in modes:
            md = r["modes"].get(m)
            if not md:
                continue
            sc = md["score"]
            L.append(f"**{m}** — {sc['verdict']} — {md['total_s']} s, "
                     f"{len(md['calls'])} lần gọi")
            L.append("")
            for c in md["calls"]:
                tag = f"lượt {c['turn']}: " if c.get("turn") else ""
                if c["error"]:
                    L.append(f"- {tag}LỖI GỌI `{c['error']}` ({c['elapsed_s']} s)")
                    continue
                flags = []
                if not c["parse_ok"]:
                    flags.append("JSON hỏng")
                if c["truncated"]:
                    flags.append("CẮT")
                ops = "; ".join(_op_str(o) for o in c["kept_ops"]) or "(rỗng)"
                drop = ""
                if c["dropped"]:
                    drop = " | code bỏ: " + ", ".join(
                        f"{d['reason']}" for d in c["dropped"])
                L.append(f"- {tag}{c['elapsed_s']} s, vào/ra "
                         f"{c['input_tokens']}/{c['output_tokens']}"
                         + (f" [{', '.join(flags)}]" if flags else "")
                         + f": {ops}{drop}")
            L.append("")
            L.append(f"  đúng kỳ vọng {len(sc['found'])}, bỏ sót {len(sc['missed'])}, "
                     f"thừa {len(sc['extras'])}, lặp {len(sc['repeats'])}, "
                     f"chấp nhận được {len(sc['acceptable'])}, "
                     f"cụm cấm {len(sc['forbidden_hits'])}")
            for e in sc["missed"]:
                L.append(f"  - BỎ SÓT: {_exp_str(e)}")
            for o in sc["extras"]:
                L.append(f"  - THỪA (người xét): {_op_str(o)}")
            for o in sc["repeats"]:
                L.append(f"  - LẶP: {_op_str(o)}")
            for h in sc["forbidden_hits"]:
                L.append(f"  - CỤM CẤM `{h['phrase']}` trong {_op_str(h['op'])}")
            for cm in sc["category_mismatch"]:
                g = cm["got"]
                L.append(f"  - lệch type/topic/safety: kỳ vọng{_exp_label(cm['expected'])}, "
                         f"được [type={g.get('type')}, topic={g.get('topic')}, "
                         f"safety={g.get('safety')}]")
            L.append("")
    return "\n".join(L) + "\n"


# ── main ─────────────────────────────────────────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Đo bước lọc fact LTM (memory-v2 A2).")
    p.add_argument("--label", default="V0")
    p.add_argument("--probes", default=str(_THIS_FILE.parent / "memory_probes.yaml"))
    p.add_argument("--only", default="", help="Chỉ chạy các id này, cách nhau bằng dấu phẩy.")
    p.add_argument("--mode", default="both", choices=["both", "all", "session", "turn", "live"])
    p.add_argument("--out", default="",
                   help="Đường dẫn .md (mặc định docs/tracking/memory-probe-<label>.md).")
    p.add_argument("--pace-seconds", type=float, default=0.0,
                   help="Nghỉ giữa các hội thoại (Groq giới hạn token/phút).")
    return p.parse_args(argv)


def _validate(convs: list[dict]) -> None:
    ids = set()
    for c in convs:
        assert c["id"] not in ids, f"trùng id {c['id']}"
        ids.add(c["id"])
        m = c["messages"]
        assert len(m) % 2 == 0 and all(
            x["role"] == ("user" if i % 2 == 0 else "assistant")
            for i, x in enumerate(m)), f"{c['id']}: tin nhắn phải xen kẽ user/assistant"


async def amain(args: argparse.Namespace) -> int:
    from langgraph_agents.shared.env import load_env
    from langgraph_agents.shared.logging import configure_root_logger

    load_env()
    configure_root_logger()

    with open(args.probes, encoding="utf-8") as f:
        convs = yaml.safe_load(f)["probes"]
    _validate(convs)
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    selected = [c for c in convs if not only or c["id"] in only]
    if not selected:
        print("Không có hội thoại nào khớp bộ lọc.")
        return 1
    modes = (["session", "turn", "live"] if args.mode in ("both", "all")
             else [args.mode])

    try:
        structured_llm = extraction_llm()
    except Exception as exc:  # noqa: BLE001
        print(f"Không dựng được model role 'memory': {type(exc).__name__}: "
              f"{_scrub(str(exc))}")
        return 2

    results: list[dict] = []
    for n, conv in enumerate(selected):
        print(f"[{conv['id']}]", flush=True)
        if n and args.pace_seconds > 0:
            await asyncio.sleep(args.pace_seconds)
        try:
            r = await _run_conv(structured_llm, conv, modes)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 3
        results.append(r)
        for m in modes:
            md = r["modes"][m]
            print(f"  {m}: {md['score']['verdict']} {md['total_s']}s "
                  f"({len(md['calls'])} calls)", flush=True)
        # Hai lần gọi đầu đều lỗi cùng kiểu (thiếu khoá, bị từ chối...): dừng, không giả số.
        first_calls = [c for md in r["modes"].values() for c in md["calls"]][:2]
        if len(results) == 1 and first_calls and all(c["error"] for c in first_calls):
            print("Các lần gọi đầu tiên đều lỗi, dừng lại. Lỗi: "
                  f"{first_calls[0]['error']}")
            return 4

    out = args.out or str(_TRACKING_DIR / f"memory-probe-{args.label}.md")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(_render_report(args.label, results, modes))
    json_out = str(Path(out).with_suffix("")) + ".json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump({"label": args.label, "role": "memory", "modes": modes,
                   "prompt": MEMORY_PROMPT,
                   "stats": {m: _mode_stats(results, m) for m in modes},
                   "results": results}, f, ensure_ascii=False, indent=1)
    print(f"Đã ghi {out} ({len(results)} hội thoại).")
    return 0


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(amain(parse_args(sys.argv[1:]))))


if __name__ == "__main__":
    main()
