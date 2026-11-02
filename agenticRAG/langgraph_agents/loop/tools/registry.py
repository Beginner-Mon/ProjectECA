"""Tool specs for the read-only loop.

`ToolSpec` carries a tool's declaration plus its async callable. The
`def_sha256` (canonical JSON of name, description, parameters) is computed
in `__post_init__` so the gate and the registry can compare definitions.

`build_turn_tools(ctx)` assembles this turn's specs (lazy-importing
`tools.builtin` so the loop package imports cleanly without pulling in
MCP / DynamoDB / embedding code on every import).
`to_openai_tools(specs)` renders them for the model.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: str  # "internal" | "egress_read" | "write"
    source: str  # "builtin" | "plugin" | "mcp"
    call: Callable[..., Any]
    idempotency_arg: str | None = None
    def_sha256: str = field(init=False, repr=False)
    clinical: bool = False
    data_class: str = "none"  # "none" | "personal"

    def __post_init__(self) -> None:
        payload = json.dumps(
            {"name": self.name, "description": self.description,
             "parameters": self.parameters},
            sort_keys=True, ensure_ascii=False,
        )
        object.__setattr__(self, "def_sha256",
                           hashlib.sha256(payload.encode("utf-8")).hexdigest())


@dataclass
class ToolResult:
    ok: bool
    content: str
    kind: str  # "ok" | "failed" | "empty"
    empty: bool
    source_label: str
    provenance: dict[str, Any]


def to_openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    """Render specs as OpenAI function-calling tool dicts."""
    out: list[dict[str, Any]] = []
    for spec in specs:
        out.append({
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        })
    return out


# Lazy builder: importing builtin here would pull kimodo/MCP/embedding code
# into `import langgraph_agents.loop`, which shared modules must not do
# (PLAN.md II.1). Import inside the function instead.
def build_turn_tools(ctx: Any) -> list[ToolSpec]:
    """Assemble this turn's tool specs from the graph context.

    `ctx` is a `TurnContext`-carrying object; the real caller passes the
    LangGraph config's `configurable`, which holds `web_search` and the
    `turn_ctx`. Builtins are chosen by risk and by the `web_search` flag:
    kb_search + recall_self (internal) are always on; load_skill (internal,
    the agent-loop skills, stage 2) is always on; youtube_transcript
    (egress_read) and request_motion (clinical egress_read) are always on;
    web_search (egress_read) only when `web_search` is true.
    """
    from . import builtin  # lazy

    config = getattr(ctx, "config", None) or {}
    web_search = bool(config.get("web_search", False))

    specs = [
        builtin.kb_search_spec(),
        builtin.recall_self_spec(),
        builtin.load_skill_spec(),
        builtin.youtube_transcript_spec(),
        builtin.request_motion_spec(),
    ]
    if web_search:
        specs.append(builtin.web_search_spec())
    return specs
