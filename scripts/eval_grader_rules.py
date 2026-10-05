"""Measure the grader's rule functions against labelled answers.

    python scripts/eval_grader_rules.py            # table per tag
    python scripts/eval_grader_rules.py --errors   # plus every disagreement

Data: tests/langgraph_agents/fixtures/grader_eval.yaml. Full answers are
labelled for all 8 tags; the targeted cases for one tag each.

  false pass = rule says the element is there, it is not   (the grader lets a gap through)
  miss       = rule says it is absent, it is there          (safety: a template is stapled on
                                                              needlessly; quality: a wasted retry)
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "agenticRAG"))
FIXTURE = REPO_ROOT / "tests" / "langgraph_agents" / "fixtures" / "grader_eval.yaml"


def load_items() -> list[dict]:
    """Flatten to one (id, tag, lang, expect, text) row per label."""
    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    items = [
        {"id": a["id"], "tag": tag, "lang": a["lang"], "expect": expect, "text": a["text"]}
        for a in data["answers"]
        if a.get("lang") in ("vi", "en")
        for tag, expect in a["labels"].items()
    ]
    items += [{k: c[k] for k in ("id", "tag", "lang", "expect", "text")}
              for c in data["cases"] if c.get("lang") in ("vi", "en")]
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--errors", action="store_true", help="list every disagreement")
    args = parser.parse_args()

    from langgraph_agents.nodes.grader import TAG_RULES

    items = load_items()
    unknown = {i["tag"] for i in items} - set(TAG_RULES)
    if unknown:
        print(f"ERROR: fixture uses tags the grader does not have: {sorted(unknown)}")
        return 1

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    errors: list[tuple[str, str, str, str]] = []
    for it in items:
        kind, rule, _ = TAG_RULES[it["tag"]]
        got = bool(rule(it["text"]))
        cell = {(True, True): "tp", (False, True): "fp", (True, False): "fn", (False, False): "tn"}[(it["expect"], got)]
        counts[it["tag"]][cell] += 1
        if cell in ("fp", "fn"):
            errors.append((it["tag"], "FALSE PASS" if cell == "fp" else "MISS", it["id"], it["lang"]))

    print(f"{'tag':18} {'kind':8} {'n':>3}  {'false pass':>12}  {'miss':>10}")
    for tag, (kind, _, _) in TAG_RULES.items():
        c = counts[tag]
        neg, pos = c["fp"] + c["tn"], c["tp"] + c["fn"]
        fp_rate = f"{c['fp']}/{neg} ({c['fp'] / neg:.0%})" if neg else "-"
        fn_rate = f"{c['fn']}/{pos} ({c['fn'] / pos:.0%})" if pos else "-"
        print(f"{tag:18} {kind:8} {neg + pos:3d}  {fp_rate:>12}  {fn_rate:>10}")

    total = sum(sum(c.values()) for c in counts.values())
    print(f"\n{total} labels, {len(errors)} disagreements.")
    if args.errors:
        for tag, what, item_id, lang in sorted(errors):
            print(f"  {what:10} {tag:18} [{lang}] {item_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
