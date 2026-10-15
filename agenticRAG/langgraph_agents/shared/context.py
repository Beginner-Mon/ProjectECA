"""Context assembly — M.7 prompt caching layout (Tier 1).

Tier 1 layout (applies to ALL providers, always needed):

    [tools][system + user_memory facts][summary chunks (frozen)]   ← STATIC (prefix cache)
    ─────────────────────────────────────────────────────────────
    [recent raw][query]                                            ← DYNAMIC (each turn)

Principles (provider-independent):
  - Static first, dynamic last → prefix stays unchanged between turns
  - Summary chunks "frozen" on write (append-only) → prefix never invalidated
  - Cost per turn = size of recent raw (dynamic part) — NOT total context
  - Append-only: new summary chunk added at END of static block → cache hit

Tier 2 (provider-specific):
  - DeepSeek (current): auto-caches prefix. No cache_control needed.
  - Anthropic (future): add cache_control: ephemeral after last static block.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 chars per token (Vietnamese/English)."""
    return len(text) // 4 if text else 0


# ── Prompt budgets (plan T11) ───────────────────────────────────────────

_CONTEXT_BUDGET_DEFAULTS = {
    # Code defaults = hành vi cũ khi config thiếu. Config hiện tại
    # (config/langgraph.yaml) mang số mới của plan — thêm key là đổi hành vi.
    "persona": 600,
    "identity_core": 100,
    "about_you": 225,      # _ABOUT_YOU_CHAR_BUDGET cũ (900 chars)
    "body_state": 40,
    "evidence": 1000,       # _EVIDENCE_CHAR_BUDGET cũ (4000 chars)
    "history": 1500,        # _STM_TOKEN_BUDGET cũ
    "voice_card": 280,
    # Memory v2. These three had no earlier behaviour to fall back to, so the
    # default is the same number the config carries.
    "user_instructions": 300,
    "user_facts": 400,
    "session_summary": 500,
    # Unsummarised history above this is compacted down to `history`.
    "history_compact_at": 2000,
}


# The numbers above were chosen for a model with about this context window.
_BASELINE_WINDOW_TOKENS = 10_000
# Clamp so a tiny window still gets a usable prompt and a huge one does not
# turn every turn into a 100k-token request (cost and latency).
_MIN_BUDGET_SCALE = 0.3
_MAX_BUDGET_SCALE = 4.0
# The blocks that grow with the conversation. The rest (persona, facts, ...) is
# fixed-size text whose value does not depend on how big the window is.
_WINDOW_SCALED_KEYS = frozenset(
    {"history", "evidence", "session_summary", "history_compact_at"}
)

# Parsed once per process: context_budget runs several times per turn.
_budgets_cache: dict | None = None


def clear_context_budget_cache() -> None:
    """Forget the parsed YAML, so the next read parses it again (tests)."""
    global _budgets_cache
    _budgets_cache = None


def _load_context_budgets() -> dict:
    global _budgets_cache
    if _budgets_cache is not None:
        return _budgets_cache
    try:
        import yaml
        from pathlib import Path

        config_path = (
            Path(__file__).resolve().parents[3] / "config" / "langgraph.yaml"
        )
        if not config_path.exists():
            budgets: dict = {}
        else:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            budgets = (cfg.get("langgraph", {}) or {}).get("context_budgets", {}) or {}
    except Exception:
        return {}                 # not cached: a transient failure may clear
    _budgets_cache = budgets
    return budgets


def _window_scale() -> float:
    """Factor for the window-scaled budgets: 1.0 unless the user's model has a known window."""
    # Imported here: llm_byok is imported by modules that import this one.
    from langgraph_agents.llm_byok import current_request_llm

    req = current_request_llm()
    window = getattr(req, "context_window", None)
    if req is None or req.failure is not None or not window:
        return 1.0                # after a failure ECA answers on its own model
    return max(_MIN_BUDGET_SCALE,
               min(_MAX_BUDGET_SCALE, window / _BASELINE_WINDOW_TOKENS))


def context_budget(key: str) -> int:
    """Token budget cho một khối prompt: config, thiếu thì giá trị cũ.

    Với model của user có cửa sổ đã biết, bốn khối trong _WINDOW_SCALED_KEYS
    co giãn theo cửa sổ đó.
    """
    try:
        value = _load_context_budgets().get(key)
        base = int(value) if value is not None else _CONTEXT_BUDGET_DEFAULTS[key]
    except (KeyError, TypeError, ValueError):
        base = _CONTEXT_BUDGET_DEFAULTS[key]
    if key in _WINDOW_SCALED_KEYS:
        scale = _window_scale()
        if scale != 1.0:
            return int(round(base * scale))
    return base


def budget_chars(key: str) -> int:
    """Budget ra chars theo tỉ lệ ước lượng (~4 chars/token)."""
    return context_budget(key) * 4


def render_user_facts(facts: list[str]) -> str:
    """The user's facts as a "- fact" list, within the `user_facts` budget.

    The list arrives in priority order (memory node: direct hits first, then
    same-project rows), so what does not fit is dropped from the end. A fact
    is kept whole or not at all. "" when there are none or none fits.
    """
    limit = budget_chars("user_facts")
    lines: list[str] = []
    used = 0
    for fact in facts:
        line = f"- {fact}"
        cost = len(line) + (1 if lines else 0)
        if used + cost > limit:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)


def render_user_instructions(instructions: list[str]) -> str:
    """Standing instructions verbatim, within the `user_instructions` budget.

    Same shape as `render_user_facts`: priority order in, whole lines only,
    dropped from the end. "" when there are none or none fits.
    """
    limit = budget_chars("user_instructions")
    lines: list[str] = []
    used = 0
    for instruction in instructions:
        line = f"- {instruction}"
        cost = len(line) + (1 if lines else 0)
        if used + cost > limit:
            break
        lines.append(line)
        used += cost
    return "\n".join(lines)


def estimate_context_tokens(
    system_prompt: str = "",
    user_facts: list[str] | None = None,
    summary_chunks: list[str] | None = None,
    recent_messages: list | None = None,
    query: str = "",
) -> dict:
    """Estimate token usage for context window per M.7 layout.

    Returns breakdown: {static_tokens, dynamic_tokens, total_tokens}
    """
    static = estimate_tokens(system_prompt)
    for f in (user_facts or []):
        static += estimate_tokens(f)
    for s in (summary_chunks or []):
        static += estimate_tokens(s)

    dynamic = 0
    for m in (recent_messages or []):
        if hasattr(m, "content"):
            dynamic += estimate_tokens(str(m.content))
    dynamic += estimate_tokens(query)

    return {
        "static_tokens": static,
        "dynamic_tokens": dynamic,
        "total_tokens": static + dynamic,
    }


def assemble_context_messages(
    system_prompt: str = "",
    user_facts: list[str] | None = None,
    summary_chunks: list[str] | None = None,
    recent_messages: list | None = None,
) -> list:
    """Assemble context messages in M.7 Tier 1 layout.

    Returns list of LangChain messages in correct order for LLM call.
    Static blocks first (cacheable), then dynamic recent messages.

    The caller adds the current HumanMessage(query) after these.
    """
    messages = []

    # STATIC block 1: system prompt + user facts
    static_parts = []
    if system_prompt:
        static_parts.append(system_prompt)
    if user_facts:
        facts_block = "[USER FACTS]\n" + "\n".join(f"- {f}" for f in user_facts)
        static_parts.append(facts_block)
    if static_parts:
        messages.append(SystemMessage(content="\n\n".join(static_parts)))

    # STATIC block 2: summary chunks (frozen, append-only)
    if summary_chunks:
        summaries = "[SESSION HISTORY]\n" + "\n---\n".join(summary_chunks)
        messages.append(SystemMessage(content=summaries))

    # DYNAMIC: recent raw messages
    if recent_messages:
        messages.extend(recent_messages)

    return messages
