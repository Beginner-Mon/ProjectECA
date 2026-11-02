"""Hidden text (stage 3): `strip_hidden(text) -> str`.

The implementation sits in `guardrail/checks/hidden_text.py`, because the
guardrail must not import `loop/` while `loop/` may import the guardrail. This
module keeps the name the plan gives it.
"""

from __future__ import annotations

from langgraph_agents.guardrail.checks.hidden_text import strip_hidden

__all__ = ["strip_hidden"]
