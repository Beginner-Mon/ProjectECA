"""Which graph a request uses: pipeline (default) or loop.

`select_mode(groups, settings) -> "pipeline" | "loop"`.

- `disabled` kill switch → pipeline (loop off entirely)
- `agent_mode == "loop"` → loop for everyone
- `agent_mode == "group"` → loop only for users in `loop_group`
- anything else → pipeline
"""

from __future__ import annotations

from .config import LoopSettings


def select_mode(groups: tuple[str, ...], settings: LoopSettings) -> str:
    """Decide which graph serves this request.

    `groups` are the caller's verified Cognito groups (from the request
    contextvar set by api/auth.py). Empty outside a request.
    """
    if settings.disabled:
        return "pipeline"
    mode = settings.agent_mode
    if mode == "loop":
        return "loop"
    if mode == "group":
        if settings.loop_group in groups:
            return "loop"
    return "pipeline"
