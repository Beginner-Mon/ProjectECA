"""Do LLM cham tren bo nhan (grader-contract T8).

Khong nam trong pytest: goi LLM that.

    python scripts/eval_grader_judge.py
    python scripts/eval_grader_judge.py --errors   # kem moi ca sai

Voi moi muc answers va moi muc cases co tag thuoc 4 tag do model viet:
  - goi _judge(check_items(...), "(none)", text, "eval")
  - tag duoc coi la "co" khi moi muc kiem cua tag do ra ok
  - so voi nhan; dem sai theo tag va theo lang; do thoi gian tung loi goi
  - ket qua {} (LLM cham loi) tinh la loi goi hong, bao rieng.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "agenticRAG"))
FIXTURE = REPO_ROOT / "tests" / "langgraph_agents" / "fixtures" / "grader_eval.yaml"

MODEL_TAGS = ["exercise_protocol", "exercise_steps",
              "contraindication", "motion_descriptor"]


def load_rows() -> list[dict]:
    """Moi (id, lang, text, labels{tag: expect}) — chi tag do model viet."""
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    rows = []
    for a in data["answers"]:
        labels = {t: e for t, e in a["labels"].items() if t in MODEL_TAGS}
        rows.append({"id": a["id"], "lang": a["lang"],
                     "text": a["text"], "labels": labels,
                     "tags": MODEL_TAGS})
    for c in data["cases"]:
        if c["tag"] not in MODEL_TAGS:
            continue
        rows.append({"id": c["id"], "lang": c["lang"], "text": c["text"],
                     "labels": {c["tag"]: c["expect"]}, "tags": [c["tag"]]})
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--errors", action="store_true",
                        help="liet ke moi ca sai")
    args = parser.parse_args()

    from langgraph_agents.shared.env import load_env
    load_env()
    from langgraph_agents.nodes.grader import _judge
    from langgraph_agents.tag_contract import TAG_CONTRACT, check_items

    rows = load_rows()
    unknown = {t for r in rows for t in r["tags"]} - set(TAG_CONTRACT)
    if unknown:
        print(f"ERROR: fixture uses tags the contract does not have: {sorted(unknown)}")
        return 1

    per_tag_wrong: dict[str, int] = defaultdict(int)
    per_tag_n: dict[str, int] = defaultdict(int)
    per_lang_wrong: dict[str, int] = defaultdict(int)
    per_lang_n: dict[str, int] = defaultdict(int)
    failed_calls = 0
    elapsed: list[float] = []
    errors: list[tuple[str, str, str, str]] = []
    n_labels = 0

    for r in rows:
        checks = check_items(r["tags"])
        t0 = time.perf_counter()
        detail = await _judge(checks, "(none)", r["text"], "eval")
        elapsed.append(time.perf_counter() - t0)
        if not detail:
            failed_calls += 1
            continue
        for tag, expect in r["labels"].items():
            n_labels += 1
            per_tag_n[tag] += 1
            per_lang_n[r["lang"]] += 1
            tag_keys = [c.key for c in TAG_CONTRACT[tag].checks]
            got = all(detail.get(k) == "ok" for k in tag_keys)
            if got != bool(expect):
                per_tag_wrong[tag] += 1
                per_lang_wrong[r["lang"]] += 1
                errors.append((tag, r["lang"], r["id"],
                               f"expect={expect} got={got}"))

    avg_ms = (sum(elapsed) / len(elapsed) * 1000) if elapsed else 0.0
    print(f"{n_labels} labels, {sum(per_tag_wrong.values())} wrong, "
          f"{failed_calls} failed calls, avg {avg_ms:.0f}ms/call.")
    print(f"{'tag':18} {'wrong/n':>10}")
    for tag in MODEL_TAGS:
        print(f"{tag:18} {per_tag_wrong[tag]}/{per_tag_n[tag]:>3}")
    print(f"{'lang':18} {'wrong/n':>10}")
    for lang in sorted(per_lang_n):
        print(f"{lang:18} {per_lang_wrong[lang]}/{per_lang_n[lang]:>3}")
    if args.errors:
        for tag, lang, item_id, what in sorted(errors):
            print(f"  WRONG {tag:18} [{lang}] {item_id} {what}")

    out = REPO_ROOT / "docs" / "tracking" / "grader-judge-eval.md"
    lines = ["# grader-judge-eval", "",
             f"LLM cham that tren `{FIXTURE.name}`: {n_labels} nhan, "
             f"{len(elapsed)} loi goi.", "",
             "| tag | sai/n |", "|---|---|"]
    for tag in MODEL_TAGS:
        lines.append(f"| {tag} | {per_tag_wrong[tag]}/{per_tag_n[tag]} |")
    lines += ["", "| lang | sai/n |", "|---|---|"]
    for lang in sorted(per_lang_n):
        lines.append(f"| {lang} | {per_lang_wrong[lang]}/{per_lang_n[lang]} |")
    lines += ["",
              f"- Loi goi hong: {failed_calls}",
              f"- Trung binh mot loi goi: {avg_ms:.0f}ms",
              ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Da ghi {out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
