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

# speaks_as_performer (M0): lượt motion queued — câu trả lời mời xem hoặc
# nói mình đang/sắp làm (ngôi người biểu diễn), không phải từ chối.
_PERFORMER_RE = re.compile(
    r"xem|nhìn|mình đang|mình sắp|mình sẽ|mình làm mẫu|để mình làm"
    r"|watch|look|let me show|show you|showing|here'?s|here is"
    r"|i'?m (doing|showing)|i will show",
    re.IGNORECASE,
)


def _first_sentence(answer: str, limit: int = 200) -> str:
    for line in (answer or "").splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return ""


# ── Task 5: dose_not_in_evidence + safety_line_repeated ───────────────────

# Cụm "số + đơn vị liều lượng" trong câu trả lời (có/không dấu).
_DOSE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?)\s*"
    r"(hiệp|hiep|lần|lan|sets?|reps?|giây|giay|seconds?)",
    re.IGNORECASE,
)

_SAFETY_TAGS = ("red_flag_screen", "referral_advice", "scope_disclaimer")


def _dose_clusters(answer: str) -> list[str]:
    """Mọi cụm liều lượng trong câu trả lời (nguyên văn, Task 5)."""
    return [m.group(0) for m in _DOSE_RE.finditer(answer or "")]


def _check_dose_not_in_evidence(answer: str, evidence_text: str) -> list[str]:
    """Cụm liều lượng nào có con số không xuất hiện trong evidence của lượt.

    Heuristic cho K/Tri đọc lại: mỗi số trong cụm phải có mặt trong văn bản
    evidence (đúng văn bản model đã thấy — đã qua budget cap của evidence).
    """
    flagged: list[str] = []
    for cluster in _dose_clusters(answer):
        numbers = re.findall(r"\d+", cluster)
        if numbers and not all(n in (evidence_text or "") for n in numbers):
            flagged.append(cluster)
    return flagged


def _check_safety_repeated(answer: str, tags: list,
                           persona_id: str = "anne",
                           lang: str = "vi") -> dict[str, int]:
    """Câu mẫu persona xuất hiện quá một lần trong câu trả lời (Task 5).

    Chỉ xét các tag an toàn của lượt; locale suy từ ngôn ngữ của câu hỏi.
    """
    from langgraph_agents.nodes.grader import get_safety_text

    repeated: dict[str, int] = {}
    for tag in tags or []:
        if tag not in _SAFETY_TAGS:
            continue
        try:
            template = get_safety_text(tag, persona_id, lang)
        except Exception:
            continue
        if not template:
            continue
        n = (answer or "").count(template)
        if n > 1:
            repeated[tag] = n
    return repeated


# ── Grader-contract V8 (T8): stream==final, câu code, retry ───────────────

def _fixed_line_count(answer: str, tags: list, lang: str) -> dict[str, int]:
    """Số lần mỗi câu an toàn của lượt xuất hiện trong câu trả lời (V8: đúng 1)."""
    from langgraph_agents.tag_contract import get_safety_text

    out: dict[str, int] = {}
    for tag in tags or []:
        if tag not in _SAFETY_TAGS:
            continue
        try:
            template = get_safety_text(tag, "anne", lang)
        except Exception:
            continue
        if template:
            out[tag] = (answer or "").count(template)
    return out


def _answer_source_line(answer: str) -> str:
    """Dòng nguồn cuối cùng trong câu trả lời; "" khi không có."""
    for line in reversed((answer or "").splitlines()):
        s = line.strip()
        if s.startswith("*Nguồn:") or s.startswith("*Source:"):
            return s[:200]
    return ""


def _check_addition_repeats_draft(answer: str) -> bool:
    """Câu dài (≥60 ký tự) lặp lại trong câu trả lời — dấu hiệu viết thêm lặp bản cũ."""
    seen: set[str] = set()
    for s in re.split(r"[.!?\n]+", answer or ""):
        s = s.strip()
        if len(s) >= 60:
            if s in seen:
                return True
            seen.add(s)
    return False


def _check_model_wrote_own_safety(answer: str, tags: list, lang: str) -> bool:
    """Thân bài (bỏ các câu do code phát) vẫn tự chạm regex an toàn của tag lượt."""
    from langgraph_agents.nodes.grader import (
        _has_danger_warning, _has_disclaimer, _has_referral,
    )
    from langgraph_agents.tag_contract import get_safety_text

    body = answer or ""
    for tag in ("red_flag_screen", "referral_advice", "scope_disclaimer"):
        try:
            template = get_safety_text(tag, "anne", lang)
        except Exception:
            template = ""
        if template:
            body = body.replace(template, "")
    body = "\n".join(
        line for line in body.splitlines()
        if not line.strip().startswith(("*Nguồn:", "*Source:")))
    checks = {"red_flag_screen": _has_danger_warning,
              "referral_advice": _has_referral,
              "scope_disclaimer": _has_disclaimer}
    return any(fn(body) for tag, fn in checks.items() if tag in (tags or []))

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

def _check_answer(answer: str, lang: str, probe_id: str = "") -> dict[str, Any]:
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
    # M0: dẫn nguồn và sets/reps chỉ tính trên d1–d3 (d4 là câu hỏi trí nhớ).
    if probe_id.startswith("d4"):
        checks["has_sets_reps"] = None
        checks["has_citation"] = None
    if lang == "vi":
        checks["anne_voice_ok"] = (
            "mình" in answer
            and not _EMOJI_RE.search(answer)
            and "~" not in answer
        )
    else:
        checks["anne_voice_ok"] = None
    return checks


def _check_performer(answer: str, motion_state: str | None) -> tuple[Any, str]:
    """M0: chỉ tính cho lượt motion queued. Trả (True/False/None, câu đầu)."""
    first = _first_sentence(answer)
    if motion_state != "queued":
        return None, first
    if not answer:
        return False, first
    return bool(_PERFORMER_RE.search(answer)), first


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
            "needs_motion": False, "needs_clarification": None,
            "tools_called": [], "node_runs": {}, "synth_mode": "?",
            "grader_result": None, "similarity_top1": None, "elapsed_s": 0.0,
            "answer": "", "mentions_exercise": None,
            "calls_library_source": None, "has_sets_reps": None,
            "has_citation": None, "anne_voice_ok": None,
            "technical_reason": None, "kimodo_ran": False,
            "speaks_as_performer": None, "first_sentence": "",
            "evidence_text": "", "dose_not_in_evidence": [],
            "safety_line_repeated": {},
            "stream_equals_final": False, "retriever_runs": 0,
            "fixed_line_count": {}, "source_line": "", "grader_detail": None,
            "retry": False, "addition_repeats_draft": False,
            "model_wrote_own_safety": False,
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

    # Task 5: evidence đúng văn bản model đã thấy (qua budget cap).
    from langgraph_agents.nodes.synthesizer import _extract_tool_results

    try:
        evidence_text = _extract_tool_results(merged["messages"])
    except Exception:
        evidence_text = ""

    result = {
        "id": probe["id"], "group": probe["group"], "lang": lang,
        "query": query, "motion_state": motion_state,
        "planner_tags": planner_out.get("required_outputs", []),
        "needs_retrieval": planner_out.get("needs_retrieval"),
        # S2: cột motion suy từ tag (cờ needs_motion đã xóa).
        "needs_motion": "motion_descriptor" in (
            planner_out.get("required_outputs") or []),
        "needs_clarification": planner_out.get("needs_clarification"),
        "tools_called": tool_calls,
        "node_runs": {n: node_runs.get(n, 0) for n in _LLM_NODES},
        "synth_mode": synth_mode,
        "grader_result": final_updates.get("grader_result"),
        "similarity_top1": similarity_top1,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "answer": final_answer,
        "kimodo_ran": any(n == "generate_motion" for n, _c, _cid in tool_messages),
    }
    result.update(_check_answer(final_answer, lang, probe["id"]))
    performer, first = _check_performer(final_answer, motion_state)
    result["speaks_as_performer"] = performer
    result["first_sentence"] = first
    result["evidence_text"] = evidence_text
    result["dose_not_in_evidence"] = _check_dose_not_in_evidence(
        final_answer, evidence_text)
    result["safety_line_repeated"] = _check_safety_repeated(
        final_answer, planner_out.get("required_outputs", []),
        persona_id="anne", lang=lang)
    # V8 (grader-contract T8): bất biến stream, câu code, retry.
    tags = planner_out.get("required_outputs", [])
    result["stream_equals_final"] = (streamed_answer == final_answer)
    result["retriever_runs"] = node_runs.get("retriever_agent", 0)
    result["fixed_line_count"] = _fixed_line_count(final_answer, tags, lang)
    result["source_line"] = _answer_source_line(final_answer)
    result["grader_detail"] = final_updates.get("grader_detail")
    result["retry"] = node_runs.get("synthesizer", 0) == 2
    result["addition_repeats_draft"] = bool(result["retry"]) and \
        _check_addition_repeats_draft(final_answer)
    result["model_wrote_own_safety"] = _check_model_wrote_own_safety(
        final_answer, tags, lang)
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


def _group_tool_summary(results: list[dict]) -> list[str]:
    """M0: mỗi nhóm một dòng — tập tool được gọi, dạng x/y."""
    order: list[str] = []
    for r in results:
        if r["group"] not in order:
            order.append(r["group"])
    lines: list[str] = []
    for g in order:
        rows = [r for r in results if r["group"] == g]
        n = len(rows)
        parts = [f"no-tool {sum(1 for r in rows if not r['tools_called'])}/{n}"]
        tools_seen: list[str] = []
        for r in rows:
            for t in r["tools_called"]:
                if t not in tools_seen:
                    tools_seen.append(t)
        for t in sorted(tools_seen):
            c = sum(1 for r in rows if t in r["tools_called"])
            parts.append(f"{t} {c}/{n}")
        lines.append(f"| {g} | {n} | {', '.join(parts)} |")
    return lines


def _group_kimodo_summary(results: list[dict]) -> list[str]:
    """M0: Kimodo chạy theo nhóm — x/y lượt có ToolMessage generate_motion."""
    order: list[str] = []
    for r in results:
        if r["group"] not in order:
            order.append(r["group"])
    lines: list[str] = []
    for g in order:
        rows = [r for r in results if r["group"] == g]
        n = len(rows)
        c = sum(1 for r in rows if r.get("kimodo_ran"))
        lines.append(f"| {g} | {c}/{n} |")
    return lines


def _render_report(label: str, results: list[dict],
                   selector_only: bool = False) -> str:
    mode_note = ("selector-only (planner + 1 lượt chọn tool, "
                 "không chạy tool/synthesizer)") if selector_only else "graph thật"
    lines = [f"# context-probe-{label}", "",
             f"Runner: `local_tests/run_context_probe.py`, persona `anne`, "
             f"LLM thật, {len(results)} lượt ({mode_note}).", "",
             "Ký hiệu: ✗ = có/trúng kiểm tra, · = không, — = không áp dụng. "
             "Riêng anne_voice_ok: ✓ = đạt, ✗ = hỏng.",
             "has_citation/has_sets_reps chỉ tính trên d1–d3 (d4 là câu hỏi trí nhớ).",
             "", "## Chọn tool theo nhóm", "",
             "| nhóm | n | tool=x/y |",
             "|---|---|---|"]
    lines += _group_tool_summary(results)
    lines += ["", "## Kimodo chạy theo nhóm", "",
              "| nhóm | kimodo chạy |",
              "|---|---|"]
    lines += _group_kimodo_summary(results)
    queued = [r for r in results if r.get("motion_state") == "queued"]
    if queued:
        lines += ["", "## Lượt motion queued — speaks_as_performer", "",
                  "| id | performer | câu đầu |",
                  "|---|---|---|"]
        for r in queued:
            perf = r.get("speaks_as_performer")
            cell = "—" if perf is None else ("✓" if perf else "✗")
            first = (r.get("first_sentence") or "").replace("|", "/")
            lines.append(f"| {r['id']} | {cell} | {first} |")
    # Task 5: liều lượng ngoài evidence (nhóm d) + câu an toàn lặp.
    d_rows = [r for r in results if r["group"] == "d"]
    if d_rows:
        lines += ["", "## Liều lượng ngoài evidence (nhóm d)", "",
                  "Mỗi cụm số+hiệp/lần/sets/reps/giây phải có con số trong "
                  "evidence của lượt. Ghi nguyên văn cụm bị đánh dấu.",
                  "",
                  "| id | cụm ngoài evidence |",
                  "|---|---|"]
        for r in d_rows:
            flagged = r.get("dose_not_in_evidence") or []
            cell = "—" if not flagged else "; ".join(
                c.replace("|", "/") for c in flagged)
            lines.append(f"| {r['id']} | {cell} |")
    repeated = [(r["id"], r.get("safety_line_repeated") or {})
                for r in results if r.get("safety_line_repeated")]
    lines += ["", "## Câu an toàn lặp (mẫu persona > 1 lần)", "",
              "| id | tag × số lần |",
              "|---|---|"]
    if repeated:
        for pid, rep in repeated:
            lines.append(f"| {pid} | "
                         + ", ".join(f"{t} ×{n}" for t, n in rep.items())
                         + " |")
    else:
        lines.append("| — | 0 lượt |")
    lines += ["", "## Bảng tổng hợp", "",
              "| id | tags | retr | motion | tools | runs P/R/S | mode | "
              + " | ".join(_CHECK_COLS)
              + " | sim_top1 | dose! |",
              "|---|---|---|---|---|---|---|"
              + "|".join(["---"] * len(_CHECK_COLS)) + "|---|---|"]
    for r in results:
        runs = (f"{r['node_runs'].get('planner', 0)}/"
                f"{r['node_runs'].get('retriever_agent', 0)}/"
                f"{r['node_runs'].get('synthesizer', 0)}")
        tags = ",".join(r["planner_tags"]) if r["planner_tags"] else "[]"
        tools = ",".join(r["tools_called"]) if r["tools_called"] else "—"
        sim = r["similarity_top1"] if r["similarity_top1"] is not None else "—"
        dose = r.get("dose_not_in_evidence") or []
        row = [r["id"], f"`{tags}`", str(r["needs_retrieval"]),
               str(r["needs_motion"]), f"`{tools}`", runs, r["synth_mode"]]
        row += [_fmt_cell(c, r[c]) for c in _CHECK_COLS]
        row += [str(sim), str(len(dose)) if dose else "—"]
        lines.append("| " + " | ".join(row) + " |")
    # V8 (grader-contract T8): tổng hợp theo ngôn ngữ.
    lines += ["", "## Tổng hợp theo ngôn ngữ (V8)", "",
              "| lang | lượt | có tag | retry | retriever×2 | TB giây |",
              "|---|---|---|---|---|---|"]
    for lang in ("vi", "en"):
        rows = [r for r in results if r.get("lang") == lang and not r.get("error")]
        n = len(rows)
        tagged = sum(1 for r in rows if r.get("planner_tags"))
        retried = sum(1 for r in rows if r.get("retry"))
        r2 = sum(1 for r in rows if r.get("retriever_runs") == 2)
        avg = (sum(r.get("elapsed_s", 0) for r in rows) / n) if n else 0
        lines.append(f"| {lang} | {n} | {tagged} | {retried} | {r2} | {avg:.1f} |")
    # V8: stream == final, câu code, retry, grader_detail.
    bad_stream = [r["id"] for r in results if not r.get("stream_equals_final")]
    bad_fixed = [(r["id"], t, c) for r in results
                 for t, c in (r.get("fixed_line_count") or {}).items() if c != 1]
    retries = [r["id"] for r in results if r.get("retry")]
    repeats = [r["id"] for r in results if r.get("addition_repeats_draft")]
    own_safety = [r["id"] for r in results if r.get("model_wrote_own_safety")]
    lines += ["", "## V8 — bất biến và dòng code", "",
              f"- stream_equals_final: {len(results) - len(bad_stream)}/{len(results)}"
              + ("" if not bad_stream else f" (lệch: {', '.join(bad_stream)})"),
              "- fixed_line_count ≠ 1: "
              + ("—" if not bad_fixed else ", ".join(
                  f"{pid}/{t}×{c}" for pid, t, c in bad_fixed)),
              f"- retry: {len(retries)}/31 lượt có tag"
              + ("" if not retries else f" ({', '.join(retries)})"),
              f"- addition_repeats_draft: {len(repeats)}"
              + ("" if not repeats else f" ({', '.join(repeats)})"),
              f"- model_wrote_own_safety: {len(own_safety)}"
              + ("" if not own_safety else f" ({', '.join(own_safety)})")]
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
        lines.append(f"- v8: stream==final {r.get('stream_equals_final')}; "
                     f"retriever_runs={r.get('retriever_runs')}; "
                     f"fixed={r.get('fixed_line_count')}; "
                     f"source={r.get('source_line') or '—'}; "
                     f"grader_detail={r.get('grader_detail')}; "
                     f"retry={r.get('retry')}; "
                     f"addition_repeats={r.get('addition_repeats_draft')}; "
                     f"own_safety={r.get('model_wrote_own_safety')}")
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
        if r.get("motion_state") == "queued" or r.get("kimodo_ran"):
            lines.append(f"- kimodo_ran={r.get('kimodo_ran')}; "
                         f"speaks_as_performer={r.get('speaks_as_performer')}; "
                         f"câu đầu: {r.get('first_sentence', '')}")
        if r.get("dose_not_in_evidence"):
            lines.append("- dose_not_in_evidence: "
                         + "; ".join(r["dose_not_in_evidence"]))
        if r.get("safety_line_repeated"):
            lines.append("- safety_line_repeated: "
                         + ", ".join(f"{t} ×{n}" for t, n in
                                     r["safety_line_repeated"].items()))
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
    p.add_argument("--selector-only", action="store_true",
                   help="M0: mỗi câu chỉ chạy planner + 1 lượt LLM chọn tool "
                        "(đúng prompt/tool thật, đọc tool_calls, "
                        "không chạy tool/synthesizer).")
    p.add_argument("--out", default="",
                   help="Đường dẫn file báo cáo (mặc định docs/tracking/context-probe-<label>.md).")
    return p.parse_args(argv)


async def _run_selector_probe(probe: dict, *, label: str, history: list) -> dict[str, Any]:
    """M0 --selector-only: planner thật + 1 lượt LLM chọn tool thật.

    Không chạy tool, không chạy synthesizer. Đọc tool_calls từ AIMessage.
    """
    import uuid as _uuid

    from langchain_core.messages import HumanMessage as _HM, SystemMessage as _SM

    from langgraph_agents.llm import get_chat_model
    from langgraph_agents.nodes._persona_loader import _persona_has_sheet
    from langgraph_agents.nodes.planner import planner_node
    from langgraph_agents.nodes.retriever_agent import (
        _build_retriever_system_prompt, _build_tools,
    )

    query = probe["query"]
    lang = probe.get("lang", "vi")
    t0 = time.perf_counter()
    try:
        session_uuid = str(_uuid.uuid5(_uuid.NAMESPACE_DNS,
                                       f"probe-{label}-{probe['id']}"))
        config = {"configurable": {
            "user_id": "probe-agent-context",
            "session_id": session_uuid,
            "query": query,
            "persona_id": "anne",
            "previous_persona_id": None,
            "output_mode": "text",
            "request_id": f"probe-{_uuid.uuid4().hex[:8]}",
            "token_limit": None,
            "web_search": False,
            "locale": "vi" if lang == "vi" else "en",
        }}
        state = {"messages": list(history), "errors": [],
                 "retry_count": 0, "total_tokens": 0}
        plan_out = await planner_node(state, config)
        tags = plan_out.get("required_outputs", [])
        resolved = plan_out.get("resolved_query") or query
        web_on = bool(config["configurable"].get("web_search", False))
        allow_fallback = not ({"red_flag_screen", "referral_advice"} & set(tags))
        tools = await _build_tools(web_search_enabled=web_on,
                                   persona_id="anne")
        system = _build_retriever_system_prompt(
            web_search_enabled=web_on,
            allow_web_fallback=allow_fallback,
            retry_note="",
            required_outputs=", ".join(tags) if tags else "(none — general/chat)",
            resolved_query=resolved,
            self_tool_available=_persona_has_sheet("anne"),
        )
        llm = get_chat_model("retriever").bind_tools(tools)
        human_text = f"Request: {resolved}"
        ai_msg = await llm.ainvoke([_SM(content=system), _HM(content=human_text)])
        tool_calls = [tc.get("name") for tc in
                      (getattr(ai_msg, "tool_calls", None) or [])
                      if isinstance(tc, dict) and tc.get("name")]
        return {
            "id": probe["id"], "group": probe["group"], "lang": lang,
            "query": query, "motion_state": probe.get("motion_state"),
            "planner_tags": tags,
            "needs_retrieval": plan_out.get("needs_retrieval"),
            # S2: cột motion suy từ tag (cờ needs_motion đã xóa).
            "needs_motion": "motion_descriptor" in (tags or []),
            "needs_clarification": plan_out.get("needs_clarification"),
            "tools_called": tool_calls,
            "node_runs": {"planner": 1, "retriever_agent": 1, "synthesizer": 0},
            "synth_mode": "selector-only",
            "grader_result": None, "similarity_top1": None,
            "elapsed_s": round(time.perf_counter() - t0, 1),
            "answer": "", "mentions_exercise": None,
            "calls_library_source": None, "has_sets_reps": None,
            "has_citation": None, "anne_voice_ok": None,
            "technical_reason": None, "kimodo_ran": False,
            "speaks_as_performer": None, "first_sentence": "",
            "evidence_text": "", "dose_not_in_evidence": [],
            "safety_line_repeated": {},
            "stream_equals_final": True, "retriever_runs": 1,
            "fixed_line_count": {}, "source_line": "", "grader_detail": None,
            "retry": False, "addition_repeats_draft": False,
            "model_wrote_own_safety": False,
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {
            "id": probe["id"], "group": probe["group"],
            "lang": probe.get("lang", "vi"), "query": probe["query"],
            "motion_state": probe.get("motion_state"),
            "planner_tags": [], "needs_retrieval": None,
            "needs_motion": False, "needs_clarification": None,
            "tools_called": [], "node_runs": {}, "synth_mode": "?",
            "grader_result": None, "similarity_top1": None,
            "elapsed_s": 0.0,
            "answer": "", "mentions_exercise": None,
            "calls_library_source": None, "has_sets_reps": None,
            "has_citation": None, "anne_voice_ok": None,
            "technical_reason": None, "kimodo_ran": False,
            "speaks_as_performer": None, "first_sentence": "",
            "evidence_text": "", "dose_not_in_evidence": [],
            "safety_line_repeated": {},
            "stream_equals_final": False, "retriever_runs": 0,
            "fixed_line_count": {}, "source_line": "", "grader_detail": None,
            "retry": False, "addition_repeats_draft": False,
            "model_wrote_own_safety": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

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
    # recall_self (T8b) đọc app.character qua RLS — prod bind ở api/main.py
    # theo từng request; runner đo bằng persona cố định nên bind "anne" ở đây.
    # Thiếu dòng này mọi lượt gọi recall_self crash với
    # 'unrecognized configuration parameter "app.character"' (V5a).
    from langgraph_agents.db.postgres import bind_request_character

    bind_request_character("anne")
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
    graph = None
    if not args.selector_only:
        from langgraph_agents.graph import build_graph_async

        graph = await build_graph_async()

    results: list[dict] = []
    # Lịch sử theo phiên cho probe có `after` (cùng lang, chạy trước).
    histories: dict[str, list] = {}
    ran: set[str] = set()

    async def _run_one(p: dict, history: list, motion_state: str | None) -> dict:
        if args.selector_only:
            return await _run_selector_probe(p, label=args.label, history=history)
        return await _run_probe_safe(graph, p, label=args.label,
                                     history=history, motion_state=motion_state)

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
                pr = await _run_one(prev, [], None)
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
        r = await _run_one(p, history, motion_state)
        results.append(r)
        ran.add(p["id"])
        histories[p["id"]] = history + [HumanMessage(content=p["query"]),
                                        AIMessage(content=r["answer"])]
        print(f"  -> tags={r['planner_tags']} tools={r['tools_called']} "
              f"mode={r['synth_mode']} {r['elapsed_s']}s", flush=True)

    out = args.out or str(_TRACKING_DIR / f"context-probe-{args.label}.md")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(_render_report(args.label, results,
                               selector_only=args.selector_only))
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
