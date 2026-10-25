"""Paper next step 5 (25/10): English items for run_memory_design.py --lang en.

    healthbench PATH   HealthBench (OpenAI simple-evals, MIT licence) oss_eval file ->
                       data/items_healthbench.jsonl: theme:emergency_referrals, one user turn, English,
                       physician category emergent -> level L3, non-emergent -> level L0. The
                       conditionally-emergent cases are left out: their right answer depends on context.
    translate          the original Vietnamese pilot items -> data/items_pilot_en.jsonl, translated by
                       gpt-6-luna (reasoning off) with every symptom kept.

The HealthBench file is downloaded data: keep it in its own folder and pass its path here.

Usage (from this folder):
    python -I prepare_english_items.py healthbench <scratch>/healthbench_raw/oss_eval.jsonl
    python prepare_english_items.py translate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _common import DATA, read_env_file, write_jsonl  # noqa: E402
from run_eval import _eval_items  # noqa: E402

HB_URL = "https://openaipublic.blob.core.windows.net/simple-evals/healthbench/2025-05-07-06-14-12_oss_eval.jsonl"
LEVEL = {"physician_agreed_category:emergent": "L3", "physician_agreed_category:non-emergent": "L0"}
TRANSLATE_PROMPT = (
    "Translate this message from a user of a Vietnamese fitness app into natural English. Keep every symptom, "
    "number and detail exactly as written, keep the tone, and do not add anything. Output only the translation."
    "\n\nMessage:\n{text}"
)


def healthbench(path: str) -> None:
    raw = Path(path).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    out = []
    for line in raw.decode("utf-8").splitlines():
        ex = json.loads(line)
        tags = ex.get("example_tags") or []
        level = next((LEVEL[t] for t in tags if t in LEVEL), None)
        prompt = ex.get("prompt") or []
        if ("theme:emergency_referrals" not in tags or level is None or len(prompt) != 1
                or prompt[0].get("role") != "user"):
            continue
        text = prompt[0].get("content") or ""
        if not text.strip() or sum(c.isascii() for c in text) / len(text) <= 0.97:
            continue
        pid = ex["prompt_id"]
        out.append({"id": f"hb-{pid[:8]}", "situation": "healthbench", "level": level, "region": "general",
                    "lang": "en", "variant": "healthbench", "text": text, "verify": {"pass": True},
                    "source": {"dataset": "HealthBench oss_eval", "url": HB_URL, "sha256": sha, "prompt_id": pid,
                               "category": next(t for t in tags if t in LEVEL)}})
    ids = [r["id"] for r in out]
    if len(ids) != len(set(ids)):
        sys.exit("duplicate short ids; use more of the prompt_id")
    write_jsonl(DATA / "items_healthbench.jsonl", out)
    print(f"{len(out)} items (L3 {sum(r['level'] == 'L3' for r in out)}, L0 {sum(r['level'] == 'L0' for r in out)}) "
          f"-> data/items_healthbench.jsonl; source sha256 {sha[:16]}…")


def translate() -> None:
    from openai import OpenAI

    key = read_env_file().get("OPENAI_API_KEY", "").split(",")[0].strip()
    client = OpenAI(api_key=key, timeout=60, max_retries=3)
    items = [i for i in _eval_items("pilot") if i["variant"] == "original" and i["level"] in ("L3", "L2", "L0", "T")]

    def one(item: dict) -> dict:
        resp = client.chat.completions.create(model="gpt-6-luna", reasoning_effort="none", messages=[
            {"role": "user", "content": TRANSLATE_PROMPT.format(text=item["text"])}])
        text = (resp.choices[0].message.content or "").strip()
        return {"id": f"{item['id']}-en", "situation": item["situation"], "level": item["level"],
                "region": item["region"], "lang": "en", "variant": "en", "text": text, "verify": {"pass": bool(text)},
                "source": {"translated_from": item["id"], "model": "gpt-6-luna", "original_text": item["text"]}}

    with ThreadPoolExecutor(max_workers=8) as pool:
        out = list(pool.map(one, items))
    write_jsonl(DATA / "items_pilot_en.jsonl", out)
    print(f"{len(out)} items -> data/items_pilot_en.jsonl", {lvl: sum(r["level"] == lvl for r in out)
                                                              for lvl in ("L3", "L2", "L0", "T")})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("healthbench", "translate"))
    parser.add_argument("path", nargs="?", help="healthbench: the downloaded oss_eval file")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "healthbench":
        if not args.path:
            sys.exit("give the path of the downloaded HealthBench file")
        healthbench(args.path)
    else:
        translate()


if __name__ == "__main__":
    main()
