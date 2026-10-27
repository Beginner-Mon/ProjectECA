"""How a graph node reads the turn's verdict.

Two callers, two shapes:

  * production (api/main.py) starts the guardrail task before the graph runs
    and puts it in `config["configurable"]["guardrail_task"]`. Nodes await that
    task and pay nothing beyond its own latency.
  * tests and local probes call the graph directly, with no task. Then the
    verdict is computed inline and cached per `request_id`, so retriever and
    synthesizer — both of which ask — cost one triage call, not two.

The cache is small on purpose: it exists for the lifetime of one graph turn,
and the request ids are never reused. 256 entries is a generous upper bound on
concurrent turns, not a memory strategy. A turn with no request id (the local
probes and older tests pass `"-"`) is never cached: without a key there is no
way to tell two turns apart, and a cached routine verdict would silence a
later urgent one.
"""

from __future__ import annotations

import asyncio

from langgraph_agents.guardrail.pipeline import run_input
from langgraph_agents.guardrail.types import CheckContext, Verdict, fallback_verdict
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.guardrail")

_CACHE: dict[str, Verdict] = {}
_CACHE_MAX = 256


def _configurable(config) -> dict:
    if isinstance(config, dict):
        return config.get("configurable") or {}
    return getattr(config, "configurable", None) or {}


def _remember(request_id: str, verdict: Verdict) -> Verdict:
    if len(_CACHE) >= _CACHE_MAX:
        # Oldest first: insertion order is the only ordering a dict guarantees.
        _CACHE.pop(next(iter(_CACHE)), None)
    _CACHE[request_id] = verdict
    return verdict


def clear_cache() -> None:
    """Drop every cached verdict (tests, and a graph invocation that leaked)."""
    _CACHE.clear()


async def current_verdict(config) -> Verdict:
    """This turn's verdict. Never raises: a broken guardrail is a routine turn."""
    cfg = _configurable(config)
    request_id = cfg.get("request_id") or "-"

    task = cfg.get("guardrail_task")
    if task is not None:
        try:
            result = await asyncio.shield(task)
            return result.verdict
        except Exception as exc:                              # noqa: BLE001
            logger.warning("guardrail_handoff_failed", extra={
                "node": "guardrail", "request_id": request_id,
                "error": type(exc).__name__,
            })
            return fallback_verdict()

    # No id, no cache: two turns must never share a verdict by accident. Local
    # probes and older tests pass "-"; production always sets a request id.
    cacheable = request_id != "-"
    if cacheable:
        cached = _CACHE.get(request_id)
        if cached is not None:
            return cached

    ctx = CheckContext(
        message=cfg.get("query") or "",
        locale=cfg.get("locale", "en"),
        persona_id=cfg.get("persona_id", "anne"),
        user_id=cfg.get("user_id"),
        request_id=request_id,
    )
    verdict = (await run_input(ctx)).verdict
    return _remember(request_id, verdict) if cacheable else verdict
