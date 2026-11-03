"""Code-written result lines for the loop.

`outcome_lines(actions, locale)` emits one template per status (ok / failed /
refused), in vi and en. Lines are emitted only when `turn_status !=
"completed"` or at least one action exists — clean chat replies get no
"done" line.

Stop-note / error fallback strings (code-written English) live here too:
STOP_TIME, STOP_TOKENS, STOP_CANCELLED, EMPTY_ANSWER.
"""

from __future__ import annotations

import re

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
    # Stage 4 (E7): the write actions. Written by code, never by the model.
    "proposed": (
        "[waiting] {tool} ({action_id}): waiting for your approval.",
        "[chờ] {tool} ({action_id}): đang chờ bạn duyệt.",
    ),
    "succeeded": (
        "[done] {tool} ({action_id}): done.",
        "[xong] {tool} ({action_id}): đã thực hiện.",
    ),
    "unknown": (
        "[unknown] {tool} ({action_id}): it is not known whether this ran. It was not retried; please check before asking again.",
        "[chưa rõ] {tool} ({action_id}): chưa rõ việc này đã chạy chưa. Hệ thống không thử lại; hãy kiểm tra trước khi nhờ lần nữa.",
    ),
    "declined": (
        "[declined] {tool} ({action_id}): you declined this; nothing was done.",
        "[đã từ chối] {tool} ({action_id}): bạn đã từ chối; không có gì được thực hiện.",
    ),
    "expired": (
        "[expired] {tool} ({action_id}): the approval expired; nothing was done.",
        "[hết hạn] {tool} ({action_id}): thẻ duyệt đã hết hạn; không có gì được thực hiện.",
    ),
    "invalidated": (
        "[cancelled] {tool} ({action_id}): the approval no longer applies; nothing was done.",
        "[đã hủy] {tool} ({action_id}): thẻ duyệt không còn hiệu lực; không có gì được thực hiện.",
    ),
}

# The saved answer of a paused turn (plan §7), and what the avatar says. It is
# the template plus the tool's name and nothing the model wrote: this text is
# saved, read back as history, sent to the model and shown to the user (GĐ2
# review H2).
_PENDING = ("Waiting for your approval: {tool}", "Đang chờ bạn duyệt: {tool}")
_GENERIC_TOOL = ("an action", "một thao tác")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,60}$")


def pending_line(tool: str, locale: str = "en") -> str:
    vi = 1 if locale.startswith("vi") else 0
    name = tool if _TOOL_NAME.match(tool or "") else _GENERIC_TOOL[vi]
    return _PENDING[vi].format(tool=name)


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
