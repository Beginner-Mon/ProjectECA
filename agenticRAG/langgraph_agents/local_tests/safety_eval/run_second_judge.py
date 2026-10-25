"""Paper check 1 (25/10): does a second judge agree with gpt-6-luna?

gpt-6-luna labels every reply in S3b-S4 and is also the model that held up best, so its labels
might favour its own kind of answer. This script re-labels a stratified sample of stored `plain`
replies to urgent messages with another judge on Groq's free tier, using the same prompt
(run_memory_design.JUDGE_PROMPT), and reports agreement (Cohen's kappa) and each model's rates
under every judge.

Rows are appended to results/second_judge_<label>.jsonl as they finish, so a run stopped by
Groq's daily limit carries on where it stopped when started again; failed rows are retried.

Usage (from this folder):
    python -u run_second_judge.py --judge openai/gpt-oss-120b --groq-key 3
    python -u run_second_judge.py --judge qwen/qwen3.8-27b --groq-key 2
    python run_second_judge.py --report-only
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict

from _common import RESULTS, read_env_file, read_jsonl
from run_eval import _eval_items
from run_memory_design import JUDGE_PROMPT, URGENT_LEVELS

# Stored runs to sample: model name in the report -> result label (design `plain` only).
SOURCES = {"luna": "s4-luna", "luna-off": "s4-luna-noreason", "flash-on": "s3c-deepseek",
           "flash-off": "s4-flash-nothink", "pro": "s4-pro", "gptoss": "s4-gptoss", "qwen": "s4-qwen"}
# Replies per model and condition; the memory conditions get more, since that is where labels move.
PER_CELL = {"none": 10, "instr_x3": 16, "override": 16}
GROQ_URL = "https://api.groq.com/openai/v1"
LABELS = ("A", "B", "C")
PRIMARY = "gpt-6-luna"


def sample(seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for name, label in SOURCES.items():
        rows = [r for r in read_jsonl(RESULTS / f"memory_design_{label}.jsonl")
                if not r.get("_meta") and r["design"] == "plain" and r["level"] in URGENT_LEVELS
                and not r["error"] and r["judge"] in LABELS and (r["reply"] or "").strip()]
        for condition, k in PER_CELL.items():
            cell = sorted((r for r in rows if r["condition"] == condition), key=lambda r: (r["id"], r["repeat"]))
            for r in rng.sample(cell, min(k, len(cell))):
                out.append({"key": f"{name}|{condition}|{r['id']}|{r['repeat']}", "model": name,
                            "condition": condition, "id": r["id"], "repeat": r["repeat"], "level": r["level"],
                            "luna": r["judge"], "reply": r["reply"]})
    return out


def judge_all(args, todo: list[dict], out_path) -> None:
    from openai import OpenAI

    texts = {i["id"]: i["text"] for i in _eval_items("pilot")}
    keys = [k.strip() for k in read_env_file().get("GROQ_API_KEY", "").split(",") if k.strip()]
    if not keys:
        sys.exit("GROQ_API_KEY is empty in agenticRAG/.env")
    # Start at --groq-key and move on through the others when a key's daily budget is gone.
    start = args.groq_key - 1
    order = keys[start:] + keys[:start]
    at = 0
    client = OpenAI(api_key=order[at], base_url=GROQ_URL, timeout=60, max_retries=0)
    if "qwen" in args.judge:
        kw = {"max_tokens": 20, "reasoning_effort": "none", "extra_body": {"reasoning_format": "hidden"}}
    else:   # gpt-oss reasons before answering; low effort keeps that short
        kw = {"max_tokens": 600, "reasoning_effort": "low"}
    with out_path.open("a", encoding="utf-8") as f:
        for n, row in enumerate(todo, 1):
            prompt = JUDGE_PROMPT.format(message=texts[row["id"]], reply=row["reply"])
            verdict = error = usage = None
            for attempt in range(6):
                try:
                    resp = client.chat.completions.create(
                        model=args.judge, messages=[{"role": "user", "content": prompt}], **kw)
                    content = (resp.choices[0].message.content or "").strip().upper()
                    found = re.search(r"\b([ABC])\b", content)
                    verdict = found.group(1) if found else None
                    error = None if verdict else f"judge said {content[:80]!r}"
                    usage = {"in": resp.usage.prompt_tokens, "out": resp.usage.completion_tokens}
                    break
                except Exception as exc:      # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"[:300]
                    if "tokens per day" in error:
                        if at + 1 < len(order):
                            at += 1
                            client = OpenAI(api_key=order[at], base_url=GROQ_URL, timeout=60, max_retries=0)
                            print(f"  -- switched to the next Groq key ({at + 1} of {len(order)})", flush=True)
                            continue
                        print("  -- every Groq key is at its daily token limit: stopping", flush=True)
                        return
                    if not ("429" in error or "rate" in error.lower() or "503" in error):
                        break
                    wait = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", error)
                    time.sleep(min(90.0, int(wait.group(1) or 0) * 60 + float(wait.group(2)) + 1)
                               if wait else 15.0 * (attempt + 1))
            rec = {"key": row["key"], "judge_model": args.judge, "verdict": verdict, "usage": usage,
                   "error": error, **{k: row[k] for k in ("model", "condition", "id", "repeat", "level", "luna")}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  {n}/{len(todo)} {row['key']} luna {row['luna']} -> {verdict or error}", flush=True)
            time.sleep(args.pace)


def kappa(pairs: list[tuple]) -> float:
    """Cohen's kappa for two raters over the same items."""
    n = len(pairs)
    if not n:
        return float("nan")
    cats = {x for p in pairs for x in p}
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def report(recs: list[dict], label: str) -> str:
    ok = [r for r in recs if r["verdict"]]
    judges = sorted({r["judge_model"] for r in ok})
    raters = [PRIMARY] + judges
    votes: dict[str, dict] = defaultdict(dict)
    info: dict[str, dict] = {}
    for r in ok:
        votes[r["key"]][r["judge_model"]] = r["verdict"]
        votes[r["key"]][PRIMARY] = r["luna"]
        info[r["key"]] = r
    lines = [
        f"# Second judge for the memory-safety replies ({label})", "",
        f"Stratified sample of stored `plain` replies to urgent messages (S3c, S4): per model, {PER_CELL} "
        "replies by condition. Same prompt as `run_memory_design.JUDGE_PROMPT`. Kappa is Cohen's kappa; "
        "'no referral' compares C with A or B, 'urgent' compares A with B or C.", "",
        "## Agreement", "",
        "| judges | n | same label | kappa (A/B/C) | kappa (no referral) | kappa (urgent) |",
        "|---|---|---|---|---|---|",
    ]
    for i, a in enumerate(raters):
        for b in raters[i + 1:]:
            pairs = [(v[a], v[b]) for v in votes.values() if a in v and b in v]
            if not pairs:
                continue
            same = sum(x == y for x, y in pairs)
            lines.append(f"| {a} vs {b} | {len(pairs)} | {same}/{len(pairs)} ({same / len(pairs):.0%}) "
                         f"| {kappa(pairs):.2f} | {kappa([(x == 'C', y == 'C') for x, y in pairs]):.2f} "
                         f"| {kappa([(x == 'A', y == 'A') for x, y in pairs]):.2f} |")
    for j in judges:
        cm = Counter((v[PRIMARY], v[j]) for v in votes.values() if j in v)
        lines += ["", f"### {PRIMARY} (rows) versus {j} (columns)", "", "| | A | B | C |", "|---|---|---|---|"]
        lines += [f"| {a} | " + " | ".join(str(cm[(a, b)]) for b in LABELS) + " |" for a in LABELS]
    for title, label_of, conds in (
            ("No referral (C) under memory instructions (instr_x3 and override)", "C", ("instr_x3", "override")),
            ("Urgent advice (A) under memory instructions (instr_x3 and override)", "A", ("instr_x3", "override")),
            ("Urgent advice (A) with no memory", "A", ("none",))):
        lines += ["", f"## {title}, by judge", "",
                  "| model | n | " + " | ".join(raters) + " |", "|---|---|" + "---|" * len(raters)]
        for m in SOURCES:
            vs = [v for k, v in votes.items() if info[k]["model"] == m and info[k]["condition"] in conds]
            if not vs:
                continue
            cells = []
            for rt in raters:
                have = [v[rt] for v in vs if rt in v]
                cells.append(f"{sum(x == label_of for x in have)}/{len(have)}" if have else "—")
            lines.append(f"| {m} | {len(vs)} | " + " | ".join(cells) + " |")
    for j in judges:
        us = [r["usage"] for r in ok if r["judge_model"] == j and r["usage"]]
        if us:
            lines.append(f"\n{j}: {len(us)} calls, {sum(u['in'] + u['out'] for u in us):,} tokens "
                         f"({sum(u['out'] for u in us) / len(us):.0f} output tokens per call).")
    errors = [r for r in recs if not r["verdict"]]
    lines += ["", f"Failed calls (left out, retried on the next run): {len(errors)}."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--judge", default="openai/gpt-oss-120b", help="a Groq model")
    parser.add_argument("--groq-key", type=int, default=1, help="which key in GROQ_API_KEY to start with (1-based)")
    parser.add_argument("--label", default="check1")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--pace", type=float, default=3.0, help="seconds between calls (Groq per-minute limits)")
    parser.add_argument("--limit", type=int, default=0, help="only the first N sampled replies (quick checks)")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    out = RESULTS / f"second_judge_{args.label}.jsonl"
    if not args.report_only:
        done = {(r["key"], r["judge_model"]) for r in (read_jsonl(out) if out.exists() else []) if r["verdict"]}
        rows = sample(args.seed)[:args.limit or None]
        todo = [r for r in rows if (r["key"], args.judge) not in done]
        print(f"{len(rows)} sampled replies, {len(todo)} still to judge with {args.judge}", flush=True)
        judge_all(args, todo, out)
    text = report(read_jsonl(out) if out.exists() else [], args.label)
    (RESULTS / f"report_second_judge_{args.label}.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
