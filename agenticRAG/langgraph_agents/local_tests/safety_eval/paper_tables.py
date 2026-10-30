"""Paper tables with 95 % Wilson intervals, built from the stored results (25/10).

    Table 1  the problem: replies to urgent messages with no referral at all (judge C), by model,
             under memory instructions (instr_x3, override)
    Table 2  the fix: urgent advice (judge A) with memory instructions, and false alarms (A on L0
             and trap messages, all memory conditions), by model and design
    Table 3  HealthBench, four-level re-judge: emergency advice (E) and no advice (N) on emergent
             cases under override; E on non-emergent cases over all conditions

Runs whose files are missing are skipped. Output: results/paper_tables.md.

Usage (from this folder):
    python paper_tables.py
"""

from __future__ import annotations

import math
import sys

from _common import RESULTS, read_jsonl

URGENT, BENIGN = ("L3", "L2"), ("L0", "T")
MEMORY = ("instr_x3", "override")


def wilson(k: int, n: int) -> str:
    if not n:
        return "—"
    z, p = 1.96, k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return f"{k}/{n} ({p:.0%}; {max(0, centre - half):.0%}–{min(1, centre + half):.0%})"


def rows(label: str) -> list[dict]:
    path = RESULTS / f"memory_design_{label}.jsonl"
    if not path.exists():
        return []
    return [r for r in read_jsonl(path) if not r.get("_meta") and not r["error"] and r["judge"] in ("A", "B", "C")]


def count(rs, design, levels, conds, label):
    sel = [r for r in rs if r["design"] == design and r["level"] in levels and r["condition"] in conds]
    return sum(r["judge"] == label for r in sel), len(sel)


TABLE1 = [("gpt-6-luna", "s4-luna"), ("gpt-6-luna, reasoning off", "s4-luna-noreason"),
          ("v4-flash, thinking on", "s3c-deepseek"), ("v4-flash, thinking off", "s4-flash-nothink"),
          ("v4-pro", "s4-pro"), ("gpt-oss-120b", "s4-gptoss"), ("qwen3.8-27b", "s4-qwen")]
# (model, design shown, result label, design in that file)
TABLE2 = [
    ("v4-flash, thinking off", "plain", "m3-flash-nothink", "plain"),
    ("v4-flash, thinking off", "plain_safe", "m3-flash-nothink", "plain_safe"),
    ("v4-flash, thinking off", "reflect", "m3-flash-nothink", "reflect"),
    ("v4-flash, thinking off", "gate", "m3-flash-nothink", "gate"),
    ("v4-flash, thinking off", "gate, Luna guard", "m3-flash-nothink-lg", "gate"),
    ("v4-flash, thinking on", "plain", "s3c-deepseek", "plain"),
    ("v4-flash, thinking on", "plain_safe", "s3c-deepseek", "plain_safe"),
    ("v4-flash, thinking on", "reflect", "m3-flash", "reflect"),
    ("v4-flash, thinking on", "gate", "m3-flash", "gate"),
    ("v4-flash, thinking on", "gate, Luna guard", "m3-flash-lg", "gate"),
    ("v4-pro", "plain", "s4-pro", "plain"),
    ("v4-pro", "plain_safe", "m3-pro", "plain_safe"),
    ("v4-pro", "plain_safe (false alarms)", "m3-pro-fa", "plain_safe"),
    ("v4-pro", "gate", "m3-pro", "gate"),
    ("v4-pro", "gate, Luna guard", "m3-pro-lg", "gate"),
    ("v4-pro", "gate, Luna guard (false alarms)", "m3-pro-fa", "gate"),
    ("gpt-6-luna, reasoning off", "plain", "fa-luna-noreason", "plain"),
    ("gpt-6-luna, reasoning off", "plain_safe", "fa-luna-noreason", "plain_safe"),
    ("gpt-6-luna, reasoning off", "reflect", "m3-luna-noreason", "reflect"),
    ("gpt-6-luna, reasoning off", "gate", "m3-luna-noreason", "gate"),
    ("gpt-6-luna, reasoning off", "gate3", "g3-luna-noreason", "gate3"),
    ("qwen3.8-27b", "plain", "m3-qwen", "plain"),
    ("qwen3.8-27b", "plain_safe", "m3-qwen", "plain_safe"),
    ("qwen3.8-27b", "gate", "m3-qwen", "gate"),
    ("qwen3.8-27b", "gate, Luna guard", "m3-qwen-lg", "gate"),
    ("qwen3.8-27b", "gate3, Luna guard", "g3-qwen-lg", "gate3"),
    ("gpt-oss-120b", "plain", "m3-gptoss", "plain"),
    ("gpt-oss-120b", "plain_safe", "m3-gptoss", "plain_safe"),
    ("gpt-oss-120b", "gate, Luna guard", "m3-gptoss-lg", "gate"),
    ("gpt-oss-120b", "gate3, Luna guard", "m3-gptoss-lg", "gate3"),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    out = ["# Paper tables (95 % Wilson intervals)", "",
           "Built by `paper_tables.py` from `results/`. k/n (rate; interval).", "",
           "## Table 1. No referral at all (C) on urgent messages with memory instructions", "",
           "| model | none | instr_x3 | override |", "|---|---|---|---|"]
    for name, label in TABLE1:
        rs = [r for r in rows(label) if r["design"] == "plain"]
        if rs:
            cells = [wilson(*count(rs, "plain", URGENT, (c,), "C")) for c in ("none", "instr_x3", "override")]
            out.append(f"| {name} | " + " | ".join(cells) + " |")
    out += ["", "## Table 2. Urgent advice (A) with memory instructions, and false alarms", "",
            "Urgent: L3/L2 under instr_x3 and override. False alarms: A on L0 and trap messages, all conditions.", "",
            "| model | design | urgent advice with memory | false alarms |", "|---|---|---|---|"]
    for name, shown, label, design in TABLE2:
        rs = rows(label)
        esc, fa = count(rs, design, URGENT, MEMORY, "A"), count(rs, design, BENIGN, ("none",) + MEMORY, "A")
        if esc[1] or fa[1]:
            out.append(f"| {name} | {shown} | {wilson(*esc)} | {wilson(*fa)} |")
    rj = RESULTS / "rejudge4_hb4.jsonl"
    if rj.exists():
        recs = {r["key"]: r for r in read_jsonl(rj) if r["level4"]}.values()
        out += ["", "## Table 3. HealthBench, four-level re-judge", "",
                "E = emergency advice now; N = no advice to see a doctor.", "",
                "| run | design | emergent, override: E | emergent, override: N | non-emergent, all: E |",
                "|---|---|---|---|---|"]
        keys = sorted({(r["source"], r["design"]) for r in recs})
        for src, design in keys:
            g = [r for r in recs if r["source"] == src and r["design"] == design]
            em = [r for r in g if r["level"] == "L3" and r["condition"] == "override"]
            ne = [r for r in g if r["level"] == "L0"]
            out.append(f"| {src} | {design} | {wilson(sum(r['level4'] == 'E' for r in em), len(em))} "
                       f"| {wilson(sum(r['level4'] == 'N' for r in em), len(em))} "
                       f"| {wilson(sum(r['level4'] == 'E' for r in ne), len(ne))} |")
    text = "\n".join(out) + "\n"
    (RESULTS / "paper_tables.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
