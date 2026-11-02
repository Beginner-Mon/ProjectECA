"""What an approval card shows, built from the stored call (stage 4, E2).

Pure functions. `payload_display` is a list of `{label, value}` in plain text:
the manifest's `display` field list when it has one, else every top-level
argument; non-scalar values as compact JSON; each value capped at 500
characters with an ellipsis. `recipients` come from the manifest's
`recipients_arg` and are never capped. The idempotency argument is left out:
`execute` overwrites it with the action id, so showing the model's value would
mislead.

`card_payload` is the one shape used by the SSE `approval_required` event and
by `GET /me/actions/pending`. It carries no `flags`: external domains, bidi
characters and homoglyphs are computed by the client from `display` and
`recipients` (E11, `lib/approvalDisplay.ts`), on exactly the text it renders.
"""

from __future__ import annotations

import json
from typing import Any

VALUE_CAP = 500
SUMMARY_CAP = 120


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _cap(text: str, limit: int = VALUE_CAP) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_display(arguments: dict, decl: Any = None) -> list[dict]:
    """`[{label, value}]` for the card."""
    wanted = list(getattr(decl, "display", None) or [])
    skip = getattr(decl, "idempotency_arg", None)
    names = wanted or [k for k in arguments if k != skip]
    return [{"label": n, "value": _cap(_text(arguments[n]))}
            for n in names if n in arguments]


def build_recipients(arguments: dict, decl: Any = None) -> list[str]:
    """Every recipient, uncapped. A string is one recipient."""
    arg = getattr(decl, "recipients_arg", None)
    if not arg or arg not in arguments:
        return []
    value = arguments[arg]
    items = value if isinstance(value, (list, tuple)) else [value]
    return [_text(v) for v in items]


def summary(display: list[dict]) -> str:
    """One short line for the paused turn's saved answer."""
    line = ", ".join(f"{d['label']}: {d['value']}" for d in display)
    return _cap(line.replace("\n", " "), SUMMARY_CAP)


def card_payload(record: Any) -> dict:
    """The `approval_required` payload of an `ActionRecord`."""
    return {
        "thread_id": str(record.thread_id),
        "action_id": str(record.action_id),
        "tool": record.tool,
        "plugin_id": record.plugin_id,
        "display": list(record.payload_display or []),
        "recipients": list(record.recipients or []),
        "provenance": list(record.provenance or []),
        "expires_at": record.expires_at.isoformat(),
    }
