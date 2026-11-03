"""What an approval card shows, built from the stored call (stage 4, E2).

Pure functions. `payload_display` is a list of `{label, value}` in plain text:
every top-level argument that `execute` will send, the manifest's `display`
list only setting the order (it never hides a key); non-scalar values as
compact JSON, in full. `recipients` come from the manifest's `recipients_arg`.
The idempotency argument is the one key left out: `execute` overwrites it with
the action id, so showing the model's value would mislead. The whole payload is
limited instead of each value: `PAYLOAD_CAP` characters of canonical JSON,
checked by the gate (GĐ2 review H1, M1).

`card_payload` is the one shape used by the SSE `approval_required` event and
by `GET /me/actions/pending`. It carries no `flags`: external domains, bidi
characters and homoglyphs are computed by the client from `display` and
`recipients` (E11, `lib/approvalDisplay.ts`), on exactly the text it renders.
"""

from __future__ import annotations

import json
from typing import Any

# A write proposal larger than this (canonical JSON characters) is refused
# before a card exists: the card shows every value in full.
PAYLOAD_CAP = 8000


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or value is None:
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def payload_too_large(arguments: dict) -> bool:
    """True when the canonical JSON of the arguments is over `PAYLOAD_CAP`."""
    try:
        canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return True
    return len(canonical) > PAYLOAD_CAP


def build_display(arguments: dict, decl: Any = None) -> list[dict]:
    """`[{label, value}]` for the card: every argument that will be sent,
    except the idempotency one. The manifest's `display` names go first, in
    that order; the rest follow in call order."""
    skip = getattr(decl, "idempotency_arg", None)
    wanted = [n for n in (getattr(decl, "display", None) or []) if n in arguments]
    names = [*dict.fromkeys(wanted),
             *(k for k in arguments if k not in wanted)]
    return [{"label": n, "value": _text(arguments[n])} for n in names if n != skip]


def build_recipients(arguments: dict, decl: Any = None) -> list[str]:
    """Every recipient, uncapped. A string is one recipient."""
    arg = getattr(decl, "recipients_arg", None)
    if not arg or arg not in arguments:
        return []
    value = arguments[arg]
    items = value if isinstance(value, (list, tuple)) else [value]
    return [_text(v) for v in items]


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
