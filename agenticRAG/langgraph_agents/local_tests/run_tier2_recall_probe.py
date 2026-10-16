"""Tier-2 recall probe (plan 16/10, bước 2a) — đo offline, chỉ model embed.

Không LLM, không DB, không tốn tiền. Tính điểm đúng như
nodes/memory.py::_load_tier2_facts: fact embed = passage:"<project>: <fact>",
query embed = query: cả tin nhắn + từng câu tách ra (_split_tier2_queries),
điểm mỗi fact là MAX giữa các vector (cosine similarity).

Đo hai thứ:
  - tỉ lệ fact đúng nằm trong top-K (K = 1, 2, 3, 5), trên các probe có expect.
  - phân bố điểm của câu nhiễu: điểm cao nhất mỗi câu nhiễu đạt với bất kỳ fact nào.

Luật chọn tham số (ghi sẵn để không hỏi lại):
  - K là số nhỏ nhất đạt >= 90% fact đúng nằm trong top-K.
  - tier2_floor là phân vị 95 của điểm nhiễu, làm tròn lên 0.01.
  - Nếu floor loại mất hơn 10% cặp đúng thì DỪNG và báo Tri, kèm bảng số.

Usage:
    python agenticRAG/langgraph_agents/local_tests/run_tier2_recall_probe.py --label V0
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
AGENTIC_ROOT = THIS_FILE.parents[2]
REPO_ROOT = AGENTIC_ROOT.parent
TRACKING_DIR = REPO_ROOT / "docs" / "tracking"
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

import yaml  # noqa: E402
import numpy as np  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(math.ceil(p * len(s))) - 1))
    return s[idx]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Tier-2 recall probe (embed only).")
    ap.add_argument("--label", default="V0")
    ap.add_argument("--yaml", default=str(THIS_FILE.parent / "tier2_recall_probes.yaml"))
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    with open(args.yaml, encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    facts = spec["facts"]
    probes = spec["probes"]

    from langgraph_agents.nodes.memory import _split_tier2_queries
    from langgraph_agents.shared import get_embedding_service

    svc = get_embedding_service()

    # Fact texts đúng kiểu extractor ghi khi embed.
    fact_texts = [
        (f"{f['project']}: {f['text']}" if f.get("project") else f["text"])
        for f in facts
    ]
    fact_ids = [f["id"] for f in facts]
    print(f"tier2-recall: embedding {len(facts)} facts ...", flush=True)
    fact_vecs = svc.embed_passages(fact_texts)

    # Mỗi probe: tách query y như _load_tier2_facts rồi embed một lô.
    results: list[dict] = []
    for p in probes:
        ask = p["ask"]
        queries = _split_tier2_queries(ask)
        q_vecs = svc.embed_queries(queries)
        scored = []
        for fid, fvec in zip(fact_ids, fact_vecs):
            best = max(cosine(qv, fvec) for qv in q_vecs)
            scored.append((fid, round(best, 4)))
        scored.sort(key=lambda t: t[1], reverse=True)
        ranked_ids = [fid for fid, _ in scored]
        score_of = dict(scored)
        expect = p.get("expect")
        rank = ranked_ids.index(expect) + 1 if expect in ranked_ids else None
        results.append({
            "id": p["id"], "ask": ask, "expect": expect,
            "queries": queries,
            "rank": rank,
            "expect_score": score_of.get(expect),
            "top": [{"id": fid, "score": s} for fid, s in scored[:5]],
            "noise_max": scored[0][1] if expect is None else None,
        })
        print(f"  {p['id']}: expect={expect} rank={rank} "
              f"score={score_of.get(expect)} top1={scored[0]}", flush=True)

    correct = [r for r in results if r["expect"] is not None]
    noise = [r for r in results if r["expect"] is None]
    n_correct = len(correct)

    recall_at = {}
    for k in (1, 2, 3, 5):
        hit = sum(1 for r in correct if r["rank"] is not None and r["rank"] <= k)
        recall_at[str(k)] = {"hit": hit, "total": n_correct,
                             "rate": round(hit / n_correct, 4) if n_correct else None}

    noise_maxes = sorted(r["noise_max"] for r in noise)
    p95 = percentile([m for m in noise_maxes if m is not None], 0.95)
    floor = math.ceil((p95 or 0.0) * 100) / 100.0

    # K nhỏ nhất đạt >= 90%.
    chosen_k = None
    for k in (1, 2, 3, 5):
        if (recall_at[str(k)]["rate"] or 0) >= 0.9:
            chosen_k = k
            break

    # Floor loại bao nhiêu cặp đúng (điểm fact đúng < floor)?
    below = [r for r in correct if (r["expect_score"] or 0) < floor]
    below_rate = round(len(below) / n_correct, 4) if n_correct else None
    stop = below_rate is not None and below_rate > 0.10

    report = {
        "label": args.label,
        "facts": len(facts), "probes": len(probes),
        "correct": n_correct, "noise": len(noise),
        "recall_at": recall_at,
        "noise_maxes": noise_maxes,
        "noise_p95": round(p95, 4) if p95 is not None else None,
        "chosen_k": chosen_k,
        "tier2_floor": round(floor, 2),
        "below_floor": [{"id": r["id"], "ask": r["ask"], "expect": r["expect"],
                         "score": r["expect_score"], "rank": r["rank"]} for r in below],
        "below_floor_rate": below_rate,
        "stop_and_report": stop,
        "results": results,
    }

    TRACKING_DIR.mkdir(parents=True, exist_ok=True)
    json_path = TRACKING_DIR / f"tier2-recall-probe-{args.label}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# tier2-recall-probe-{args.label}", "",
             f"- facts: {len(facts)}, probes: {len(probes)} "
             f"(đúng: {n_correct}, nhiễu: {len(noise)})",
             f"- model: intfloat/multilingual-e5-small (embed local, không LLM/DB)", ""]
    lines.append("## Recall@K (fact đúng trong top-K)")
    lines.append("")
    lines.append("| K | hit/total | rate |")
    lines.append("|---|---|---|")
    for k in ("1", "2", "3", "5"):
        r = recall_at[k]
        lines.append(f"| {k} | {r['hit']}/{r['total']} | {r['rate']} |")
    lines.append("")
    lines.append("## Nhiễu")
    lines.append("")
    lines.append(f"- max mỗi câu nhiễu: {noise_maxes}")
    lines.append(f"- p95 nhiễu: {round(p95, 4) if p95 is not None else None} "
                 f"→ floor (làm tròn lên 0.01): {round(floor, 2)}")
    lines.append("")
    lines.append("## Tham số chọn theo luật")
    lines.append("")
    lines.append(f"- K = {chosen_k} (nhỏ nhất đạt ≥ 90%)")
    lines.append(f"- tier2_floor = {round(floor, 2)} (p95 nhiễu, làm tròn lên 0.01)")
    lines.append(f"- cặp đúng dưới floor: {len(below)}/{n_correct} ({below_rate})")
    if stop:
        lines.append(f"- **DỪNG VÀ BÁO TRI**: floor loại > 10% cặp đúng.")
        for r in below:
            lines.append(f"  - {r['id']} expect={r['expect']} "
                         f"score={r['expect_score']} rank={r['rank']}: {r['ask']}")
    else:
        lines.append("- floor giữ được ≥ 90% cặp đúng → làm tiếp bước 2b.")
    lines.append("")
    lines.append("## Từng probe (expect, rank, điểm fact đúng, top-1)")
    lines.append("")
    lines.append("| probe | expect | rank | score | top1 |")
    lines.append("|---|---|---|---|---|")
    for r in results:
        top1 = r["top"][0] if r["top"] else {}
        lines.append(f"| {r['id']} | {r['expect']} | {r['rank']} | "
                     f"{r['expect_score']} | {top1.get('id')}:{top1.get('score')} |")
    lines.append("")
    md_path = TRACKING_DIR / f"tier2-recall-probe-{args.label}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {md_path} and {json_path}")
    print(f"recall@K={ {k: recall_at[k]['rate'] for k in recall_at} } "
          f"p95={p95} floor={floor} K={chosen_k} below={below_rate} stop={stop}")
    return 5 if stop else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
