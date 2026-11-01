"""Code-written result lines for the loop.

`outcome_lines(actions, locale)` emits one template per status (ok / failed /
refused), in vi and en. Lines are emitted only when `turn_status !=
"completed"` or at least one action exists — clean chat replies get no
"done" line.

Stop-note / error fallback strings (code-written English) live here too:
STOP_TIME, STOP_TOKENS, STOP_CANCELLED, EMPTY_ANSWER.
"""

from __future__ import annotations

# Stop notes written into agent_result when a cap or cancel ends the turn
# (§5 rule: the stop message goes to agent_result, not to messages).
STOP_TIME = ("[stop: the turn's loop-time budget was spent]", "[stop: thời gian vòng lặp đã hết]")
STOP_TOKENS = ("[stop: the input-token budget was spent]", "[stop: ngân sách token đầu vào đã hết]")
STOP_CANCELLED = ("[stop: the turn was cancelled]", "[stop: lượt đã bị hủy]")
EMPTY_ANSWER = ("[stop: no answer — the model returned nothing usable]",
                "[stop: không có câu trả lời — mô hình không trả về gì dùng được]")


def _stop_note(kind: str, locale: str) -> str:
    vi = locale.startswith("vi")
    table = {
        "time": STOP_TIME,
        "tokens": STOP_TOKENS,
        "cancelled": STOP_CANCELLED,
        "empty": EMPTY_ANSWER,
    }
    return table[kind][1] if vi else table[kind][0]


def stop_note(kind: str, locale: str = "en") -> str:
    """A code-written stop note for `agent_result`."""
    return _stop_note(kind, locale)


# ── Per-action templates ─────────────────────────────────────────────────

# One template per status, vi + en. `action_id` echoes tool_call_id so the
# client can match each line to the call it describes.
_ACTION_TEMPLATES: dict[str, tuple[str, str]] = {
    "ok": (
        "[done] {tool} ({action_id}): returned {chars} characters.",
        "[xong] {tool} ({action_id}): trả về {chars} ký tự.",
    ),
    "failed": (
        "[done] {tool} ({action_id}): failed — {error}.",
        "[xong] {tool} ({action_id}): thất bại — {error}.",
    ),
    "refused": (
        "[done] {tool} ({action_id}): refused — {reason}.",
        "[xong] {tool} ({action_id}): từ chối — {reason}.",
    ),
    "repeat_empty": (
        "[done] {tool} ({action_id}): repeated empty call — not retried.",
        "[xong] {tool} ({action_id}): gọi trùng kết quả rỗng — không thử lại.",
    ),
}


def outcome_lines(actions: list[dict], locale: str = "en") -> str:
    """Build the outcome block from this turn's action records.

    One line per action, one template per status. Empty when there are no
    actions — the caller decides whether to append it (only when the turn
    was remarkable).
    """
    if not actions:
        return ""
    vi = locale.startswith("vi")
    lines: list[str] = []
    for action in actions:
        status = action.get("status", "ok")
        template = _ACTION_TEMPLATES.get(status, _ACTION_TEMPLATES["ok"])[1 if vi else 0]
        lines.append(template.format(
            tool=action.get("tool", "?"),
            action_id=action.get("tool_call_id", action.get("action_id", "?")),
            chars=action.get("result_chars", 0),
            error=action.get("error", ""),
            reason=action.get("reason", ""),
        ))
    return "\n".join(lines)
