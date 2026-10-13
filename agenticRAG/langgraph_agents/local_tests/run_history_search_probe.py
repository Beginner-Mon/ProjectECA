"""History-search probe (plan memory-v2, A3) — tìm trên lượt hội thoại gốc.

Đo xem tìm trên TỪNG LƯỢT (câu user + câu trả lời) có ra đúng lượt không, và đặt
ngưỡng similarity ở đâu. Không dùng database: mọi thứ tính trong bộ nhớ, bằng
E5EmbeddingService (đúng prefix query:/passage:), cosine = 1 - cosine distance
(cùng đại lượng pgvector `<=>` tính trong SQL).

3 cách tạo văn bản index  x  3 cách chấm điểm = 9 tổ hợp:
  index:  A = user + assistant cắt 1500 ký tự | B = chỉ user | C = chỉ assistant
  score:  1 = retriever_query | 2 = user_words | 3 = max(1, 2) theo từng lượt

Ví dụ:
    python agenticRAG/langgraph_agents/local_tests/run_history_search_probe.py --label V0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

_THIS_FILE = Path(__file__).resolve()
_AGENTIC_ROOT = _THIS_FILE.parents[2]
_REPO_ROOT = _AGENTIC_ROOT.parent
_TRACKING_DIR = _REPO_ROOT / "docs" / "tracking"
if str(_AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_ROOT))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

DEFAULT_PROBES = _THIS_FILE.with_name("history_search_probes.yaml")
INDEX_VARIANTS = {"A": "user+assistant (1500)", "B": "user only", "C": "assistant only"}
SCORE_VARIANTS = {"1": "retriever_query", "2": "user_words", "3": "max(1,2)"}
MAX_INDEX_CHARS = 1500


# ── helpers ───────────────────────────────────────────────────────────────

def _normalise(m: np.ndarray) -> np.ndarray:
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def _index_text(variant: str, turn: dict) -> str:
    if variant == "A":
        return f"{turn['user']}\n{turn['assistant']}"[:MAX_INDEX_CHARS]
    if variant == "B":
        return turn["user"]
    return turn["assistant"]


def _rng(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {
        "n": len(values),
        "min": round(min(values), 4),
        "median": round(statistics.median(values), 4),
        "max": round(max(values), 4),
    }


def _fmt_rng(r: dict) -> str:
    if not r["n"]:
        return "-"
    return f"{r['min']:.3f}-{r['max']:.3f} (med {r['median']:.3f})"


def _pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "-"


def _evaluate(sims: np.ndarray, queries: list[dict], turn_ids: list[str]) -> list[dict]:
    """sims: [n_queries, n_turns]. Một dòng kết quả cho mỗi query."""
    rows = []
    for qi, q in enumerate(queries):
        order = np.argsort(-sims[qi], kind="stable")
        ranked = [turn_ids[j] for j in order]
        correct = set(q.get("correct") or [])
        rank = next((i + 1 for i, t in enumerate(ranked) if t in correct), None) if correct else None
        rows.append({
            "id": q["id"],
            "user_lang": q["user_lang"],
            "correct": sorted(correct),
            "rank": rank,
            "top1_turn": ranked[0],
            "top1_sim": float(sims[qi, order[0]]),
            "top3": [(ranked[i], round(float(sims[qi, order[i]]), 4)) for i in range(3)],
            "correct_sim": (float(sims[qi, turn_ids.index(ranked[rank - 1])]) if rank else None),
            "best_correct_in_top3_sim": next(
                (float(sims[qi, order[i]]) for i in range(3) if ranked[i] in correct), None),
        })
    return rows


def _summarise(recall_rows: list[dict], off_rows: list[dict]) -> dict:
    n = len(recall_rows)
    top1 = [r for r in recall_rows if r["rank"] == 1]
    top3 = [r for r in recall_rows if r["rank"] is not None and r["rank"] <= 3]
    mrr = sum(1 / r["rank"] for r in recall_rows if r["rank"]) / n
    return {
        "n_recall": n,
        "top1": len(top1),
        "top3": len(top3),
        "mrr": round(mrr, 4),
        "top1_sim_when_correct": _rng([r["top1_sim"] for r in top1]),
        "offtopic_top1_sim": _rng([r["top1_sim"] for r in off_rows]),
    }


def _floor_table(recall_rows, off_rows, floors):
    out = []
    for f in floors:
        # Công cụ trả tối đa 3 kết quả có sim >= floor.
        kept_top1 = sum(1 for r in recall_rows if r["rank"] == 1 and r["top1_sim"] >= f)
        kept_top3 = sum(1 for r in recall_rows
                        if r["best_correct_in_top3_sim"] is not None
                        and r["best_correct_in_top3_sim"] >= f)
        off_ret = sum(1 for r in off_rows if r["top1_sim"] >= f)
        out.append({
            "floor": round(f, 3),
            "recall_top1_kept": kept_top1,
            "recall_top3_kept": kept_top3,
            "offtopic_returning": off_ret,
        })
    return out


# ── main ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", required=True)
    ap.add_argument("--probes", default=str(DEFAULT_PROBES))
    ap.add_argument("--out", default=None, help="đường dẫn .md (mặc định docs/tracking/history-search-probe-<label>.md)")
    args = ap.parse_args()

    out_md = Path(args.out) if args.out else _TRACKING_DIR / f"history-search-probe-{args.label}.md"
    out_json = out_md.with_suffix(".json")

    data = yaml.safe_load(Path(args.probes).read_text(encoding="utf-8"))
    turns, turn_conv = [], {}
    for conv in data["corpus"]:
        for t in conv["turns"]:
            turns.append(t)
            turn_conv[t["id"]] = conv
    turn_ids = [t["id"] for t in turns]
    recall, offtopic = data["recall"], data["offtopic"]
    known = set(turn_ids)
    for q in recall:
        assert set(q["correct"]) <= known, f"{q['id']}: unknown turn id"
    assert len(known) == len(turns), "duplicate turn ids"

    from langgraph_agents.shared import get_embedding_service
    svc = get_embedding_service()
    t0 = time.time()
    svc.embed_query("warm-up")  # fails here if the model is not in the local cache
    print(f"backend={svc.backend} model_ready={time.time() - t0:.1f}s")

    all_q = recall + offtopic
    ret_vecs = _normalise(np.array(svc.embed_queries([q["retriever_query"] for q in all_q])))
    usr_vecs = _normalise(np.array(svc.embed_queries([q["user_words"] for q in all_q])))
    nr = len(recall)

    results, details = {}, {}
    for iv in INDEX_VARIANTS:
        texts = [_index_text(iv, t) for t in turns]
        tv = _normalise(np.array(svc.embed_passages(texts)))
        s_ret, s_usr = ret_vecs @ tv.T, usr_vecs @ tv.T
        for sv, sims in (("1", s_ret), ("2", s_usr), ("3", np.maximum(s_ret, s_usr))):
            rows = _evaluate(sims, all_q, turn_ids)
            rec_rows, off_rows = rows[:nr], rows[nr:]
            key = f"{iv}{sv}"
            details[key] = {"recall": rec_rows, "offtopic": off_rows}
            results[key] = _summarise(rec_rows, off_rows)
            print(f"{key}: top1 {results[key]['top1']}/{nr} top3 {results[key]['top3']} mrr {results[key]['mrr']}")

    # Tổ hợp tốt nhất: top1 cao nhất, rồi MRR, rồi top3.
    best = max(results, key=lambda k: (results[k]["top1"], results[k]["mrr"], results[k]["top3"]))
    b_rec, b_off = details[best]["recall"], details[best]["offtopic"]

    # Ngưỡng: lưới 0.01 trong vùng dữ liệu.
    lo = min(r["top1_sim"] for r in b_off + b_rec)
    hi = max(r["top1_sim"] for r in b_off + b_rec)
    grid = [round(x, 3) for x in np.arange(np.floor(lo * 100) / 100, hi + 0.011, 0.01)]
    table = _floor_table(b_rec, b_off, grid)
    n_off = len(b_off)
    # Khuyến nghị: tối đa (recall top-3 giữ lại - off-topic bị trả), hoà thì chọn ngưỡng thấp hơn.
    rec_row = max(table, key=lambda r: (r["recall_top3_kept"] / nr - r["offtopic_returning"] / n_off, -r["floor"]))
    # Các ngưỡng gợi ý hiển thị: lưới thô 0.02 + ngưỡng khuyến nghị.
    shown = sorted({r["floor"] for r in table if round(r["floor"] * 100) % 2 == 0} | {rec_row["floor"]})
    shown_rows = [r for r in table if r["floor"] in shown]

    correct_top1_sims = [r["top1_sim"] for r in b_rec if r["rank"] == 1]
    off_sims = [r["top1_sim"] for r in b_off]
    overlap_lo = min(correct_top1_sims)
    overlap_hi = max(off_sims)
    n_off_above_min_correct = sum(1 for s in off_sims if s >= overlap_lo)
    n_correct_below_max_off = sum(1 for s in correct_top1_sims if s <= overlap_hi)

    # Tách theo ngôn ngữ (của lượt được lưu vs của user_words), cho cả 9 tổ hợp.
    def same_lang(r):
        langs = {turn_conv[c]["lang"] for c in r["correct"]}
        return r["user_lang"] in langs
    split = {}
    for key, d in details.items():
        grp = {"same": [], "other": []}
        for r in d["recall"]:
            grp["same" if same_lang(r) else "other"].append(r)
        split[key] = {
            g: {"n": len(rs),
                "top1": sum(1 for r in rs if r["rank"] == 1),
                "top3": sum(1 for r in rs if r["rank"] is not None and r["rank"] <= 3),
                "mrr": round(sum(1 / r["rank"] for r in rs if r["rank"]) / len(rs), 4) if rs else None}
            for g, rs in grp.items()
        }

    # Câu lỗi trong tổ hợp tốt nhất.
    tmap = {t["id"]: t for t in turns}
    q_by_id = {q["id"]: q for q in all_q}
    failures = []
    for r in b_rec:
        if r["rank"] != 1:
            t1 = tmap[r["top1_turn"]]
            failures.append({
                "id": r["id"], "user_words": q_by_id[r["id"]]["user_words"],
                "retriever_query": q_by_id[r["id"]]["retriever_query"],
                "correct": r["correct"], "correct_rank": r["rank"],
                "top1_turn": r["top1_turn"], "top1_sim": round(r["top1_sim"], 4),
                "top1_user": t1["user"], "same_lang": same_lang(r),
            })

    backend = svc.backend
    meta = {"label": args.label, "backend": backend, "model": svc.model_name,
            "n_turns": len(turns), "n_conversations": len(data["corpus"]),
            "n_recall": nr, "n_offtopic": n_off}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({
        "meta": meta, "results": results, "best": best, "floor_table": table,
        "recommended_floor": rec_row, "split": split, "failures": failures,
        "details": details,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── Markdown ──
    bs = results[best]
    L = []
    L.append(f"# History search probe — {args.label}\n")
    L.append("## Tóm tắt\n")
    L.append(f"- Backend: `{backend}` ({svc.model_name}); {meta['n_turns']} lượt trong {meta['n_conversations']} cuộc hội thoại; "
             f"{nr} câu nhắc lại, {n_off} câu ngoài chủ đề. Dữ liệu tổng hợp.")
    L.append(f"- Tổ hợp tốt nhất: **{best}** — index {best[0]} ({INDEX_VARIANTS[best[0]]}), chấm điểm {best[1]} ({SCORE_VARIANTS[best[1]]}).")
    L.append(f"- Đúng lượt đứng đầu: **{_pct(bs['top1'], nr)}**; trong top 3: **{_pct(bs['top3'], nr)}**; MRR {bs['mrr']:.3f}.")
    L.append(f"- Similarity top-1 khi đúng: {_fmt_rng(bs['top1_sim_when_correct'])}.")
    L.append(f"- Similarity top-1 của câu ngoài chủ đề: {_fmt_rng(bs['offtopic_top1_sim'])}.")
    if overlap_hi >= overlap_lo:
        L.append(f"- Hai nhóm **chồng lên nhau**: {n_off_above_min_correct}/{n_off} câu ngoài chủ đề có top-1 >= {overlap_lo:.3f} "
                 f"(đáy của nhóm đúng), và {n_correct_below_max_off}/{len(correct_top1_sims)} câu đúng có top-1 <= {overlap_hi:.3f} (đỉnh của nhóm ngoài chủ đề).")
    else:
        L.append(f"- Hai nhóm **không chồng nhau**: đáy nhóm đúng {overlap_lo:.3f} > đỉnh nhóm ngoài chủ đề {overlap_hi:.3f} "
                 f"(khoảng trống {overlap_lo - overlap_hi:.3f}).")
    L.append(f"- Ngưỡng khuyến nghị: **{rec_row['floor']:.2f}** — giữ lại đúng lượt (trong top 3) cho "
             f"{_pct(rec_row['recall_top3_kept'], nr)} câu nhắc lại (đúng ở hạng 1: {rec_row['recall_top1_kept']}/{nr}); "
             f"còn {rec_row['offtopic_returning']}/{n_off} câu ngoài chủ đề vẫn trả kết quả.")
    L.append("")
    L.append("## 9 tổ hợp (câu nhắc lại)\n")
    L.append("| combo | index | scoring | top-1 | top-3 | MRR | top-1 sim khi đúng | off-topic top-1 sim |")
    L.append("|---|---|---|---|---|---|---|---|")
    for k, s in results.items():
        mark = " **(best)**" if k == best else ""
        L.append(f"| {k}{mark} | {INDEX_VARIANTS[k[0]]} | {SCORE_VARIANTS[k[1]]} | {_pct(s['top1'], nr)} | {_pct(s['top3'], nr)} | "
                 f"{s['mrr']:.3f} | {_fmt_rng(s['top1_sim_when_correct'])} | {_fmt_rng(s['offtopic_top1_sim'])} |")
    L.append("")
    L.append(f"## Ngưỡng cho {best}\n")
    L.append("Một câu nhắc lại \"còn trả đúng\" nếu lượt đúng nằm trong top 3 và có sim >= ngưỡng (công cụ trả tối đa 3 kết quả); "
             "câu ngoài chủ đề \"trả gì đó\" nếu top-1 >= ngưỡng.\n")
    L.append("| ngưỡng | nhắc lại còn đúng (top 3) | nhắc lại đúng ở hạng 1 | ngoài chủ đề vẫn trả kết quả |")
    L.append("|---|---|---|---|")
    for r in shown_rows:
        mark = " **(khuyến nghị)**" if r["floor"] == rec_row["floor"] else ""
        L.append(f"| {r['floor']:.2f}{mark} | {r['recall_top3_kept']}/{nr} | {r['recall_top1_kept']}/{nr} | {r['offtopic_returning']}/{n_off} |")
    L.append("")
    L.append("## Cùng ngôn ngữ vs khác ngôn ngữ với lượt đã lưu\n")
    L.append("| combo | cùng ngôn ngữ: top-1 / top-3 / MRR | khác ngôn ngữ: top-1 / top-3 / MRR |")
    L.append("|---|---|---|")
    for k in results:
        s, o = split[k]["same"], split[k]["other"]
        L.append(f"| {k}{' **(best)**' if k == best else ''} | {s['top1']}/{s['n']} / {s['top3']}/{s['n']} / {s['mrr']} | "
                 f"{o['top1']}/{o['n']} / {o['top3']}/{o['n']} / {o['mrr']} |")
    L.append("")
    L.append(f"## Câu nhắc lại sai trong {best}\n")
    if not failures:
        L.append("Không có.")
    for f in failures:
        L.append(f"- **{f['id']}** ({'cùng' if f['same_lang'] else 'khác'} ngôn ngữ) \"{f['user_words']}\" — đúng: {', '.join(f['correct'])}, "
                 f"hạng của đúng: {f['correct_rank']}; đứng đầu: {f['top1_turn']} (sim {f['top1_sim']:.3f}) \"{f['top1_user']}\"")
    L.append("")
    L.append(f"Dữ liệu thô: `{out_json.name}`.")
    out_md.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out_md}\nwrote {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
