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
    # Set for tools that come from a plugin (stage 3): the manifest id. The gate
    # uses it for the rate bucket and the breaker, the audit line and the
    # source label use it as the name of the origin.
    plugin_id: str | None = None

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
    kb_search (internal) is always on; recall_self (internal) only when the persona has a sheet; load_skill (internal,
    the agent-loop skills, stage 2) is always on; youtube_transcript
    (egress_read) and request_motion (clinical egress_read) are always on;
    web_search (egress_read) only when `web_search` is true and no plugin replaces
    it; plugin tools come from `turn_ctx.plugins` (set by `load_context`).
    """
    # Probe and test hook: `configurable["turn_tools"]` (a list of ToolSpec)
    # replaces the builtins for this turn. Production never sets it
    # (`api/main.py` builds no such key).
    if isinstance(ctx, dict) and ctx.get("turn_tools") is not None:
        return list(ctx["turn_tools"])

    from . import builtin  # lazy

    from langgraph_agents.nodes._persona_loader import _persona_has_sheet

    cfg = ctx if isinstance(ctx, dict) else (getattr(ctx, "config", None) or {})
    web_search = bool(cfg.get("web_search", False))
    persona_id = str(cfg.get("persona_id") or "")

    plugins = getattr(cfg.get("turn_ctx"), "plugins", None)
    replaced = plugins.replaces if plugins is not None else frozenset()

    specs = [builtin.kb_search_spec()]
    # recall_self searches the character's sheet, so it is offered only to a
    # persona that has one (same rule as the pipeline's retriever_agent).
    if _persona_has_sheet(persona_id):
        specs.append(builtin.recall_self_spec())
    specs += [
        builtin.load_skill_spec(),
        builtin.youtube_transcript_spec(),
        builtin.request_motion_spec(),
    ]
    if web_search and "web_search" not in replaced:
        specs.append(builtin.web_search_spec())
    # Stage 3: the tools of the plugins this user has on. Prepared once, in
    # `load_context` (D1, D3); nothing here touches the network or the database.
    if plugins is not None:
        specs += plugins.specs
    return specs
