"""Built-in tools for the read-only loop (stage 1).

Five builtins:

- `kb_search`, `recall_self` — **internal**: wrap `.ainvoke(input, config)`
  on the LangChain tool objects in `RETRIEVER_BASE_TOOLS`.
- `youtube_transcript` — **egress_read**: `.ainvoke` on its tool object.
- `request_motion` — **clinical, egress_read** (NOT write — keeps stage-1
  motion working like the pipeline's kimodo node): reuses
  `nodes/kimodo.py::kimodo_node` with synthetic state
  `{"resolved_query": args["prompt"]}` and unwraps the `_msg` ToolMessage
  payload via `json.loads(msg.content)`.
- `web_search` — **egress_read**, stage 1 only: calls the SearXNG search of
  `mcp/web_search_server._search_searxng`, built only when the turn's
  `web_search` flag is true.
- `load_skill` — **internal**, stage 2: the agent-loop skills
  (`loop/skills/`). Names and descriptions sit in the tool description; the
  body loads only when the tool is called (plan §8). A skill file changed
  without a re-pin is skipped with a warning.

Each builtin exposes `spec() -> ToolSpec` and an async `call(args, config)`.
"""

from __future__ import annotations

import json
from typing import Any

from langgraph_agents.loop.tools.registry import ToolSpec

# ── kb_search / recall_self / youtube_transcript ──────────────────────────
# Internal via RETRIEVER_BASE_TOOLS (Lazy import: this module is itself
# imported lazily, so a top-level import of pgvector_tool here is fine — it
# only runs when the loop is actually used.)

_RETRIEVER_TOOLS: dict[str, Any] = {}


def _retriever_tools() -> dict[str, Any]:
    if not _RETRIEVER_TOOLS:
        from langgraph_agents.tools.pgvector_tool import (
            kb_search as _kb,
            recall_self as _recall,
            youtube_transcript as _yt,
        )
        _RETRIEVER_TOOLS["kb_search"] = _kb
        _RETRIEVER_TOOLS["recall_self"] = _recall
        _RETRIEVER_TOOLS["youtube_transcript"] = _yt
    return _RETRIEVER_TOOLS


async def _langchain_call(tool, args: dict, config: dict) -> Any:
    return await tool.ainvoke(args, config)


def _make_lc_spec(name: str, description: str, parameters: dict,
                  risk: str, clinical: bool, data_class: str) -> ToolSpec:
    """Bind the LangChain tool into the spec's callable via closure."""
    tool = _retriever_tools()[name]

    async def _call(args: dict, config: dict) -> Any:
        return await tool.ainvoke(args, config)

    return ToolSpec(
        name=name, description=description, parameters=parameters,
        risk=risk, source="builtin", call=_call,
        clinical=clinical, data_class=data_class,
    )


def kb_search_spec() -> ToolSpec:
    return _make_lc_spec(
        "kb_search",
        ("Search ECA's exercise and health knowledge base. Use for "
         "questions about exercises, stretches, anatomy, physiotherapy "
         "techniques and health facts. Returns chunks with content and "
         "similarity score. Empty list (not an error) when nothing matches."),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Search query in Vietnamese or English."},
                "top_k": {"type": "integer",
                          "description": "Max results (default 5, capped at 5).",
                          "default": 5, "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk="internal", clinical=False, data_class="none",
    )


def recall_self_spec() -> ToolSpec:
    return _make_lc_spec(
        "recall_self",
        ("Search this user's own saved memories (facts and standing "
         "instructions). Use when the answer may depend on what this "
         "particular user has said before. Returns matching memory rows."),
        {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "What to look for in the user's memory."},
                "top_k": {"type": "integer",
                          "description": "Max results (default 5).",
                          "default": 5, "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk="internal", clinical=False, data_class="personal",
    )


def youtube_transcript_spec() -> ToolSpec:
    return _make_lc_spec(
        "youtube_transcript",
        ("Fetch the transcript of a YouTube video. Use when the user asks "
         "about a specific video's content. Returns the transcript text "
         "(capped)."),
        {
            "type": "object",
            "properties": {
                "video_id": {"type": "string",
                             "description": "YouTube video ID (the v= value)."},
            },
            "required": ["video_id"],
            "additionalProperties": False,
        },
        risk="egress_read", clinical=False, data_class="none",
    )


# ── request_motion ────────────────────────────────────────────────────────

async def _request_motion_call(args: dict, config: dict) -> dict:
    """Enqueue a Kimodo motion job and return its payload dict.

    Reuses `nodes/kimodo.py::kimodo_node` with a synthetic AgentState
    carrying `resolved_query` from `args["prompt"]`. The node returns
    `{"messages": [ToolMessage(content=json.dumps(payload))]}`; unwraps
    the payload via `json.loads`.
    """
    from langgraph_agents.nodes.kimodo import kimodo_node

    prompt = args.get("prompt", "")
    synthetic_state = {"resolved_query": prompt}
    result = await kimodo_node(synthetic_state, config)  # type: ignore[arg-type]
    messages = result.get("messages") or []
    for msg in messages:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            try:
                return json.loads(content)
            except (json.JSONDecodeError, ValueError):
                return {"state": "unavailable", "error": "unparseable motion payload"}
    return {"state": "unavailable", "error": "no motion payload"}


def request_motion_spec() -> ToolSpec:
    return ToolSpec(
        name="request_motion",
        description=(
            "Ask ECA to render a 3D movement for an exercise the user "
            "wants to SEE (not just read). Pass the cleaned exercise "
            "description as `prompt`. Returns a job id the client polls."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string",
                           "description": "Cleaned exercise description to render."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        risk="egress_read", source="builtin", call=_request_motion_call,
        clinical=True, data_class="none",
    )


# ── web_search ────────────────────────────────────────────────────────────

async def _web_search_call(args: dict, config: dict) -> list[dict]:
    from langgraph_agents.mcp.web_search_server import _search_searxng
    return await _search_searxng(
        query=args.get("query", ""),
        max_results=int(args.get("max_results", 3) or 3),
        domain_filter=args.get("domain_filter"),
    )


def web_search_spec() -> ToolSpec:
    return ToolSpec(
        name="web_search",
        description=(
            "Search the web for current events, prices, news and facts "
            "outside exercise and health. Returns a list of results."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Search query."},
                "max_results": {"type": "integer",
                                "description": "Max results (default 3, max 10).",
                                "default": 3, "minimum": 1, "maximum": 10},
                "domain_filter": {"type": "string",
                                  "description": "Optional site: filter, e.g. site:pubmed.ncbi.nlm.nih.gov"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        risk="egress_read", source="builtin", call=_web_search_call,
        clinical=False, data_class="none",
    )


# ── load_skill (stage 2: loop/skills) ─────────────────────────────────────

def _skill_catalog() -> str:
    """The skills listed in `load_skill`'s description (plan §8 / II.2):
    name and description only — the body loads when the tool is called."""
    from langgraph_agents.loop.skills import discover
    infos = discover()
    lines = [f"- {info.id}: {info.description}" for info in infos]
    if not lines:
        return "No skills are available right now."
    return (
        "Knowledge-base working skills. When a skill fits the task, call "
        "this tool with its id BEFORE you start, then follow its body:\n"
        + "\n".join(lines)
    )


async def _load_skill_call(args: dict, config: dict) -> dict:
    """Run `load_skill(name)` through the hashed loader (loop/skills)."""
    from langgraph_agents.loop.skills import load_skill
    return load_skill(str(args.get("name", "")))


def load_skill_spec() -> ToolSpec:
    return ToolSpec(
        name="load_skill",
        description=_skill_catalog(),
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string",
                         "description": "Skill id to load (from the list in this description)."},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        risk="internal", source="builtin", call=_load_skill_call,
        clinical=False, data_class="none",
    )
