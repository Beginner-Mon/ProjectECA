"""Context probe runner (plan T1) — bộ câu thử với LLM thật.

Dựng graph THẬT bằng build_graph_async(), KHÔNG thay LLM bằng bản giả
(ngược với run_production_graph_smoke.py). Đọc từ `context_probes.yaml`,
chạy từng câu qua graph.astream(..., stream_mode=["updates", "custom"]) và
ghi số đo ra `docs/tracking/context-probe-<label>.md`.

Đọc (không ghi) Neon: bare graph chỉ đọc memory/kb (ghi messages/motion_job
xảy ra ở api/main.py, runner này không gọi). Dùng user/session probe riêng.

Ví dụ:
    python agenticRAG/langgraph_agents/local_tests/run_context_probe.py --label V0
    python agenticRAG/langgraph_agents/local_tests/run_context_probe.py --label V0 --only a1_vi,d1_vi
    python agenticRAG/langgraph_agents/local_tests/run_context_probe.py --label V1 --group c --motion-state queued
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

_THIS_FILE = Path(__file__).resolve()
_AGENTS_DIR = _THIS_FILE.parents[1]
_AGENTIC_ROOT = _THIS_FILE.parents[2]
_REPO_ROOT = _AGENTIC_ROOT.parent
_TRACKING_DIR = _REPO_ROOT / "docs" / "tracking"
if str(_AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENTIC_ROOT))
# vva_motion (Kimodo job queue) sống ở text-to-motion/kimodo — cùng PYTHONPATH
# pytest.ini dùng cho suite test.
_KIMODO_ROOT = _REPO_ROOT / "text-to-motion" / "kimodo"
if str(_KIMODO_ROOT) not in sys.path:
    sys.path.insert(0, str(_KIMODO_ROOT))

import yaml  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

_LLM_NODES = ("planner", "retriever_agent", "synthesizer")

_MENTIONS_EXERCISE_RE = re.compile(
    r"bài tập|thư viện|động tác|hiệp|lần lặp|exercise|library|stretch|sets?|reps?",
    re.IGNORECASE,
)
_CALLS_LIBRARY_RE = re.compile(r"thư viện|library", re.IGNORECASE)
_TECHNICAL_REASON_RE = re.compile(
    r"module|engine|kimodo|hệ thống|server|GPU", re.IGNORECASE
)
# Emoji: emoticons/dingbats/pictographs/supplemental + variation selector.
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)

# ── Motion override (cờ --motion-state; sau T9 trỏ sang tool show_movement) ──

_MOTION_OVERRIDE: dict[str, Any] = {"state": None}


def _canned_motion_payload(state_name: str, prompt: str) -> dict:
    if state_name == "cache_hit":
        return {"state": "cache_hit", "job_id": "probe-job",
                "prompt": prompt}
    if state_name == "busy":
        return {"state": "busy", "retry_after_seconds": 60}
    if state_name == "unavailable":
        return {"state": "unavailable"}
    return {"state": "queued", "job_id": "probe-job", "prompt": prompt,
            "queue_position": 1, "eta_seconds": 5}


def _install_motion_override() -> None:
    """Chặn node kimodo bằng kết quả đóng hộp, đặt theo từng probe."""
    from langchain_core.messages import ToolMessage

    import langgraph_agents.graph as graph_mod
    from langgraph_agents.nodes import kimodo as kimodo_mod

    real_kimodo = kimodo_mod.kimodo_node

    async def _probe_kimodo(state: dict, config) -> dict:
        forced = _MOTION_OVERRIDE.get("state")
        if not forced:
            return await real_kimodo(state, config)
        resolved = state.get("resolved_query") or config["configurable"]["query"]
        return {"messages": [ToolMessage(
            content=json.dumps(_canned_motion_payload(forced, resolved)),
            tool_call_id="kimodo_motion",
            name="generate_motion",
        )]}

    graph_mod.kimodo_node = _probe_kimodo  # noqa: SLF — hook của probe, không phải prod


# ── Trích xuất từ updates ────────────────────────────────────────────────

def _msg_name(m: Any) -> str | None:
    if isinstance(m, dict):
        return m.get("name")
    return getattr(m, "name", None)


def _msg_content(m: Any) -> str:
    if isinstance(m, dict):
        return str(m.get("content", ""))
    return str(getattr(m, "content", "") or "")


def _msg_tool_calls(m: Any) -> list[dict]:
    if isinstance(m, dict):
        if m.get("type") == "ai" or "tool_calls" in m:
            return list(m.get("tool_calls") or [])
        return []
    if isinstance(m, AIMessage):
        return list(getattr(m, "tool_calls", None) or [])
    return []


def _msg_call_id(m: Any) -> str:
    if isinstance(m, dict):
        for tc in [m.get("tool_call_id"), m.get("id")]:
            if tc:
                return str(tc)
        return ""
    return str(getattr(m, "tool_call_id", "") or "")


def _is_tool_message(m: Any) -> bool:
    if isinstance(m, dict):
        return m.get("type") == "tool"
    from langchain_core.messages import ToolMessage

    return isinstance(m, ToolMessage)


def _parse_json_obj(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Kiểm tra tự động (plan §T1) ─────────────────────────────────────────

def _check_answer(answer: str, lang: str) -> dict[str, Any]:
    from langgraph_agents.nodes.grader import (
        _has_sets_reps_frequency, _has_source,
    )

    checks: dict[str, Any] = {
        "mentions_exercise": bool(_MENTIONS_EXERCISE_RE.search(answer)),
        "calls_library_source": bool(_CALLS_LIBRARY_RE.search(answer)),
        "has_sets_reps": bool(_has_sets_reps_frequency(answer)),
        "has_citation": bool(_has_source(answer)),
        "technical_reason": bool(_TECHNICAL_REASON_RE.search(answer)),
    }
    if lang == "vi":
        checks["anne_voice_ok"] = (
            "mình" in answer
            and not _EMOJI_RE.search(answer)
            and "~" not in answer
        )
    else:
        checks["anne_voice_ok"] = None
    return checks


# ── Chạy một probe ──────────────────────────────────────────────────────

async def _run_probe_safe(graph: Any, probe: dict, **kwargs) -> dict[str, Any]:
    """Một probe hỏng không được giết cả đợt đo."""
    try:
        return await _run_probe(graph, probe, **kwargs)
    except Exception as exc:  # noqa: BLE001 — ghi nhận rồi chạy tiếp
        traceback.print_exc()
        return {
            "id": probe["id"], "group": probe["group"],
            "lang": probe.get("lang", "vi"), "query": probe["query"],
            "motion_state": kwargs.get("motion_state"),
            "planner_tags": [], "needs_retrieval": None,
            "needs_motion": None, "needs_clarification": None,
            "tools_called": [], "node_runs": {}, "synth_mode": "?",
            "grader_result": None, "similarity_top1": None, "elapsed_s": 0.0,
            "answer": "", "mentions_exercise": None,
            "calls_library_source": None, "has_sets_reps": None,
            "has_citation": None, "anne_voice_ok": None,
            "technical_reason": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _run_probe(
    graph: Any,
    probe: dict,
    *,
    label: str,
    history: list,
    motion_state: str | None,
) -> dict[str, Any]:
    from langgraph_agents.nodes.synthesizer import _derive_mode

    query = probe["query"]
    lang = probe.get("lang", "vi")
    t0 = time.perf_counter()

    state = {"messages": list(history), "errors": [],
             "retry_count": 0, "total_tokens": 0}
    # session_id phải là UUID (tool resume_last_session bind trực tiếp vào
    # cột uuid) — uuid5 deterministic để chạy lại vẫn cùng phiên probe.
    session_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS,
                                  f"probe-{label}-{probe['id']}"))
    config = {"configurable": {
        "user_id": "probe-agent-context",
        "session_id": session_uuid,
        "query": query,
        "persona_id": "anne",
        "previous_persona_id": None,
        "output_mode": "text",
        "request_id": f"probe-{uuid.uuid4().hex[:8]}",
        "token_limit": None,
        "web_search": False,
        "locale": "vi" if lang == "vi" else "en",
    }}

    node_runs: dict[str, int] = {}
    planner_out: dict[str, Any] = {}
    tool_calls: list[str] = []
    tool_messages: list[tuple[str, str, str]] = []
    streamed_answer = ""
    final_updates: dict[str, Any] = {}

    _MOTION_OVERRIDE["state"] = motion_state
    try:
        async for mode, payload in graph.astream(
            state, config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                if isinstance(payload, dict) and "content" in payload:
                    streamed_answer += str(payload["content"])
                continue
            if not isinstance(payload, dict):
                continue
            for node_name, node_output in payload.items():
                node_runs[node_name] = node_runs.get(node_name, 0) + 1
                if not isinstance(node_output, dict):
                    continue
                final_updates.update(node_output)
                if node_name == "planner":
                    for k in ("required_outputs", "resolved_query",
                              "needs_retrieval", "needs_motion",
                              "needs_clarification"):
                        if k in node_output:
                            planner_out[k] = node_output[k]
                for m in node_output.get("messages", []) or []:
                    for tc in _msg_tool_calls(m):
                        if isinstance(tc, dict) and tc.get("name"):
                            tool_calls.append(tc["name"])
                    if _is_tool_message(m):
                        tool_messages.append(
                            (_msg_name(m) or "?", _msg_content(m),
                             _msg_call_id(m)))
    finally:
        _MOTION_OVERRIDE["state"] = None

    final_answer = str(final_updates.get("final_answer", "")) or streamed_answer

    # similarity_top1 từ kb_search (ToolMessage content là JSON list).
    similarity_top1: float | None = None
    for name, content, _cid in tool_messages:
        if name != "kb_search":
            continue
        data = _parse_json_obj(content)
        if isinstance(data, list) and data:
            sims = [r.get("similarity") for r in data
                    if isinstance(r, dict)
                    and isinstance(r.get("similarity"), (int, float))]
            if sims:
                top = max(sims)
                similarity_top1 = top if similarity_top1 is None else max(
                    similarity_top1, top)

    # Dựng lại ToolMessage THẬT để _derive_mode chạy đúng (_has_tool_results
    # và _check_tool_ambiguous đều isinstance-check ToolMessage; dict giả
    # luôn cho False và mode đo được sẽ sai).
    from langchain_core.messages import ToolMessage as _TM

    merged = {"required_outputs": planner_out.get("required_outputs", []),
              "needs_clarification": planner_out.get(
                  "needs_clarification", False),
              "messages": [_TM(content=c, tool_call_id=cid or f"probe-{i}",
                                name=n)
                            for i, (n, c, cid) in enumerate(tool_messages)]}
    try:
        synth_mode = _derive_mode(merged)
    except Exception:
        synth_mode = "?"

    result = {
        "id": probe["id"], "group": probe["group"], "lang": lang,
        "query": query, "motion_state": motion_state,
        "planner_tags": planner_out.get("required_outputs", []),
        "needs_retrieval": planner_out.get("needs_retrieval"),
        "needs_motion": planner_out.get("needs_motion"),
        "needs_clarification": planner_out.get("needs_clarification"),
        "tools_called": tool_calls,
        "node_runs": {n: node_runs.get(n, 0) for n in _LLM_NODES},
        "synth_mode": synth_mode,
        "grader_result": final_updates.get("grader_result"),
        "similarity_top1": similarity_top1,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "answer": final_answer,
    }
    result.update(_check_answer(final_answer, lang))
    return result


# ── Báo cáo markdown ────────────────────────────────────────────────────

_CHECK_COLS = ["mentions_exercise", "calls_library_source", "has_sets_reps",
               "has_citation", "anne_voice_ok", "technical_reason"]


def _fmt_cell(col: str, v: Any) -> str:
    if v is None:
        return "—"
    if col == "anne_voice_ok":
        # Ngược với các cột lỗi: True = giọng Anne đạt.
        return "✓" if v else "✗"
    if v is True:
        return "✗"
    if v is False:
        return "·"
    return str(v)


def _render_report(label: str, results: list[dict]) -> str:
    lines = [f"# context-probe-{label}", "",
             f"Runner: `local_tests/run_context_probe.py`, persona `anne`, "
             f"LLM thật, {len(results)} lượt.", "",
             "Ký hiệu: ✗ = có/trúng kiểm tra, · = không, — = không áp dụng. "
             "Riêng anne_voice_ok: ✓ = đạt, ✗ = hỏng.",
             "", "## Bảng tổng hợp", "",
             "| id | tags | retr | motion | tools | runs P/R/S | mode | "
             + " | ".join(_CHECK_COLS)
             + " | sim_top1 |",
             "|---|---|---|---|---|---|---|"
             + "|".join(["---"] * len(_CHECK_COLS)) + "|---|"]
    for r in results:
        runs = (f"{r['node_runs'].get('planner', 0)}/"
                f"{r['node_runs'].get('retriever_agent', 0)}/"
                f"{r['node_runs'].get('synthesizer', 0)}")
        tags = ",".join(r["planner_tags"]) if r["planner_tags"] else "[]"
        tools = ",".join(r["tools_called"]) if r["tools_called"] else "—"
        sim = r["similarity_top1"] if r["similarity_top1"] is not None else "—"
        row = [r["id"], f"`{tags}`", str(r["needs_retrieval"]),
               str(r["needs_motion"]), f"`{tools}`", runs, r["synth_mode"]]
        row += [_fmt_cell(c, r[c]) for c in _CHECK_COLS]
        row += [str(sim)]
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Chi tiết từng câu", ""]
    for r in results:
        lines.append(f"### {r['id']} — {r['query']}")
        lines.append("")
        if r.get("error"):
            lines.append(f"- LỖI: `{r['error']}`")
            lines.append("")
            continue
        lines.append(f"- planner: tags={r['planner_tags']} "
                     f"retrieval={r['needs_retrieval']} motion={r['needs_motion']} "
                     f"clarify={r['needs_clarification']}")
        lines.append(f"- tools: {r['tools_called'] or '—'}; "
                     f"runs P/R/S={r['node_runs']}; mode={r['synth_mode']}; "
                     f"grader={r['grader_result']}; "
                     f"sim_top1={r['similarity_top1']}; {r['elapsed_s']}s")
        lines.append("")
        lines.append("```")
        lines.append((r["answer"] or "(rỗng)")[:800])
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


# ── main ──────────────────────────────────────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Chạy bộ câu thử agent-context.")
    p.add_argument("--label", default="V0")
    p.add_argument("--probes", default=str(_THIS_FILE.parent / "context_probes.yaml"))
    p.add_argument("--only", default="",
                   help="Chỉ chạy các id này, cách nhau bằng dấu phẩy.")
    p.add_argument("--group", default="",
                   help="Chỉ chạy nhóm này (a|b|c|d|e).")
    p.add_argument("--lang", default="", help="Chỉ chạy ngôn ngữ này (vi|en).")
    p.add_argument("--motion-state", default="",
                   help="Ghi đè motion cho mọi probe nhóm c.")
    p.add_argument("--out", default="",
                   help="Đường dẫn file báo cáo (mặc định docs/tracking/context-probe-<label>.md).")
    return p.parse_args(argv)


async def amain(args: argparse.Namespace) -> int:
    from langgraph_agents.shared.env import load_env
    from langgraph_agents.shared.logging import configure_root_logger

    load_env()
    # JSON log ra stderr để đo prompt_blocks / input_tokens (T11).
    configure_root_logger()
    # Tool memory_search/resume_last_session chạy trong pg.transaction(),
    # cần app.user_id đã bind (như api/main.py bind từ Bearer token).
    # Không bind = RLS từ chối đúng như tài liệu trong db/postgres.py.
    from langgraph_agents.db.postgres import bind_request_user

    bind_request_user(str(uuid.uuid5(uuid.NAMESPACE_DNS, "probe-agent-context")))
    # VVA_REPLY_EMOTION=0 ở local làm sai kiểm tra anne_voice_ok? Không:
    # kiểm tra đó chỉ xét mình/emoji/~, không xét emotion tag (đã strip).
    # Giữ nguyên cờ để đo đúng hiện trạng prod.

    with open(args.probes, encoding="utf-8") as f:
        probes = yaml.safe_load(f)["probes"]

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    selected = [p for p in probes
                if (not only or p["id"] in only)
                and (not args.group or p["group"] == args.group)
                and (not args.lang or p["lang"] == args.lang)]
    if not selected:
        print("Không có probe nào khớp bộ lọc.")
        return 1
    by_id = {p["id"]: p for p in probes}

    _install_motion_override()
    from langgraph_agents.graph import build_graph_async

    graph = await build_graph_async()

    results: list[dict] = []
    # Lịch sử theo phiên cho probe có `after` (cùng lang, chạy trước).
    histories: dict[str, list] = {}
    ran: set[str] = set()

    for p in selected:
        if p["id"] in ran:
            continue  # đã chạy làm lượt đi trước cho probe khác
        history: list = []
        if p.get("after"):
            prev_id = p["after"]
            if prev_id not in by_id:
                print(f"Bỏ {p['id']}: after={prev_id} không tồn tại.")
                continue
            if prev_id not in ran:
                prev = by_id[prev_id]
                print(f"[{prev_id}] {prev['query']}", flush=True)
                pr = await _run_probe_safe(graph, prev, label=args.label,
                                           history=[], motion_state=None)
                results.append(pr)
                ran.add(prev_id)
                histories[prev_id] = [
                    HumanMessage(content=prev["query"]),
                    AIMessage(content=pr["answer"]),
                ]
                print(f"  -> mode={pr['synth_mode']} tools={pr['tools_called']} "
                      f"{pr['elapsed_s']}s", flush=True)
            history = list(histories[prev_id])
        motion_state = (args.motion_state or p.get("motion_state") or None)
        print(f"[{p['id']}] {p['query']}", flush=True)
        r = await _run_probe_safe(graph, p, label=args.label,
                                  history=history, motion_state=motion_state)
        results.append(r)
        ran.add(p["id"])
        histories[p["id"]] = history + [HumanMessage(content=p["query"]),
                                        AIMessage(content=r["answer"])]
        print(f"  -> tags={r['planner_tags']} tools={r['tools_called']} "
              f"mode={r['synth_mode']} {r['elapsed_s']}s", flush=True)

    out = args.out or str(_TRACKING_DIR / f"context-probe-{args.label}.md")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(_render_report(args.label, results))
    json_out = str(Path(out).with_suffix("")) + ".json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"Đã ghi {out} ({len(results)} lượt).")
    return 0


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(asyncio.run(amain(parse_args(sys.argv[1:]))))


if __name__ == "__main__":
    main()
