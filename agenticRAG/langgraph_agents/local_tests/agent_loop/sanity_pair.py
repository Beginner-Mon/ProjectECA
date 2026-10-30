"""F7 sanity pair for the agent-loop probe (probe only; exactly 2 Groq requests).

One request per mode with the knee task's FIRST-round messages (same system,
user and tools, effort `low`). `keep` (raw OpenAI SDK) and `drop` (ChatOpenAI)
have to report the same `prompt_tokens` — F2 fixed `drop`'s counts, and this is
the proof. If the two numbers differ, exit 2: the token fix is not done
(a STOP point in fix-1.md).

For `drop` the stream's usage-bearing chunks are printed (F2 evidence, what
was seen — not guessed): Groq sends usage more than once per stream, so the
old merge (`full = full + chunk`) summed every chunk's usage_metadata; the
merge-sum is printed next to the last-wins count the fixed code uses.

Key handling: GROQ_API_KEY via run_reasoning_matrix.groq_keys(); values never
printed. Never calls DeepSeek.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
AGENTIC_ROOT = HERE.parents[2]
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))

from langgraph_agents.local_tests.agent_loop.fake_tools import TOOL_SCHEMAS          # noqa: E402
from langgraph_agents.local_tests.agent_loop.loop import KeepClient, SYSTEM_PROMPT  # noqa: E402
from langgraph_agents.local_tests.agent_loop.run_reasoning_matrix import GROQ, groq_keys  # noqa: E402
from langgraph_agents.local_tests.agent_loop.tasks import task_by_id               # noqa: E402


def _uniq(usages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for u in usages:
        pair = (u.get("input_tokens"), u.get("output_tokens"))
        if not out or pair != (out[-1].get("input_tokens"), out[-1].get("output_tokens")):
            out.append(u)
    return out


def main() -> int:
    keys = groq_keys()
    if not keys:
        print(f"{GROQ['env']} is missing; cannot run the sanity pair", file=sys.stderr)
        return 3
    key = keys[0]
    task = task_by_id("knee")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task["prompt"]}]
    effort = "low"

    # ------------------------------------------------------------------ keep
    keep = KeepClient(api_key=key, base_url=GROQ["base_url"], model=GROQ["model"],
                      reasoning_effort=effort)
    kr = keep.complete(messages, TOOL_SCHEMAS)
    print(f"keep (raw SDK): prompt_tokens={kr.prompt_tokens} completion_tokens="
          f"{kr.completion_tokens} ({kr.latency_s:.2f}s, content "
          f"{len(kr.content or '')} chars, tool_calls {len(kr.tool_calls)})")

    # ------------------------------------------------------------------ drop
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(model=GROQ["model"], api_key=key, base_url=GROQ["base_url"],
                      streaming=True, timeout=120.0, max_retries=0,
                      max_tokens=GROQ["max_tokens"], stream_usage=True,
                      reasoning_effort=effort)
    bound = chat.bind_tools(TOOL_SCHEMAS)
    lc = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=task["prompt"])]

    usage_chunks: list[dict[str, Any]] = []
    merged: Any = None
    t0 = time.perf_counter()
    for chunk in bound.stream(lc):
        um = getattr(chunk, "usage_metadata", None)
        if um:
            usage_chunks.append(um)
        merged = chunk if merged is None else merged + chunk    # the old merge, kept for evidence
    latency = time.perf_counter() - t0

    print(f"drop (ChatOpenAI stream): {latency:.2f}s, "
          f"{len(usage_chunks)} usage-bearing chunk(s) of the stream")
    uniq = _uniq(usage_chunks)
    for i, u in enumerate(uniq[:6], 1):
        print(f"  usage#{i}: input={u.get('input_tokens')} output={u.get('output_tokens')}"
              f" reasoning={((u.get('output_token_details') or {}).get('reasoning'))}"
              f"{'' if len(uniq) <= 6 else '  (first 6 distinct shown)'}")
    merge_sum = {k: sum(u.get(k) or 0 for u in usage_chunks)
                 for k in ("input_tokens", "output_tokens")}
    last = usage_chunks[-1] if usage_chunks else {}
    merged_usage = getattr(merged, "usage_metadata", None) or {}
    print(f"  merge-sum over all usage-bearing chunks (old bug): "
          f"prompt={merge_sum['input_tokens']} completion={merge_sum['output_tokens']}")
    print(f"  last-wins (fixed F2):       prompt={last.get('input_tokens')} "
          f"completion={last.get('output_tokens')}")
    print(f"  what full=full+chunk aggregates to: {merged_usage}")

    # ------------------------------------------------------------- the pair
    drop_prompt = last.get("input_tokens") or 0
    equal = drop_prompt == kr.prompt_tokens
    print(f"SANITY PAIR [{'EQUAL' if equal else 'DIFFERENT'}]: "
          f"drop={drop_prompt} keep={kr.prompt_tokens}")
    return 0 if equal else 2


if __name__ == "__main__":
    sys.exit(main())
