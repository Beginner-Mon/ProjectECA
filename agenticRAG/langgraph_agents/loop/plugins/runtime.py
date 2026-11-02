"""Plugin runtime: what a turn offers, and how a plugin call reaches its server.

Process level (shared by turns):
  * `_defs_cache`: the tool definitions per plugin, 600 s (D1);
  * `_breakers`: one `CircuitBreaker` per plugin, 3 failures in a row open it
    for 60 s (D7);
  * `_client_overrides`: transport and address policy per plugin id. Tests set
    it to point a plugin at an in-process server; production never does.

Turn level (`PluginTurn`, stored as `turn.plugins`):
  * built once by `prepare_turn`, called from `load_context` (D3): one store
    query, then the cached definitions. A cache miss calls `list_tools` with a
    3 s limit; a failure leaves the plugin out of this turn and the turn goes
    on (`plugins_unavailable` in the trace);
  * a definitions hash that differs from the manifest's pin (and `UNPINNED`
    always differs) gives status `definition_changed`: the plugin is left out
    and stays out until a person re-pins it with `review.py`;
  * one MCP session per plugin, opened on first use (a cache miss reuses the
    session that listed the tools) and closed by `PluginTurn.close()` when the
    turn ends (D2).

`build_turn_tools` reads `turn.plugins.specs`; the gate reads `is_active`,
`plugin_id` and `get_breaker`; `tools_node` records breaker outcomes and the
audit line. The model never sees a tool of a plugin that is off, pending
review, unconfigured or down.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from langgraph_agents.core.circuit_breaker import CircuitBreaker
from langgraph_agents.loop.http import AddressPolicy
from langgraph_agents.loop.mcp_client import McpClient, ToolDef, definitions_sha256
from langgraph_agents.loop.plugins.manifest import Manifest, load_catalog
from langgraph_agents.loop.plugins.store import PluginStore, get_plugin_store
from langgraph_agents.loop.tools.registry import ToolSpec
from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.plugins")

# Statuses (shown by GET /me/plugins in card 3C).
ACTIVE = "active"
DISABLED = "disabled"                      # the user switched it off
OFF = "off"                                # kill switch (D8)
UNCONFIGURED = "unconfigured"              # no endpoint on this deployment
DEFINITION_CHANGED = "definition_changed"  # hash != pin, or unpinned
UNAVAILABLE = "unavailable"                # listing failed or breaker open
WEB_SEARCH_OFF = "web_search_off"          # replaces web_search, flag is off

DEFS_TTL_S = 600.0
LIST_TIMEOUT_S = 3.0
BREAKER_FAILURES = 3
BREAKER_COOL_DOWN_S = 60.0
MAX_TOOL_NAME = 64
MAX_DESCRIPTION = 1500

# Patched by tests that need to age the cache.
_clock: Callable[[], float] = time.monotonic


@dataclass
class _Defs:
    fetched: float
    url: str
    tools: list[ToolDef]
    sha256: str


_defs_cache: dict[str, _Defs] = {}
_breakers: dict[str, CircuitBreaker] = {}
_client_overrides: dict[str, dict[str, Any]] = {}


def get_breaker(plugin_id: str) -> CircuitBreaker:
    br = _breakers.get(plugin_id)
    if br is None:
        br = _breakers[plugin_id] = CircuitBreaker(
            name=f"plugin:{plugin_id}", failure_threshold=BREAKER_FAILURES,
            cool_down_seconds=BREAKER_COOL_DOWN_S)
    return br


def set_client_overrides(overrides: dict[str, dict[str, Any]] | None) -> None:
    """Test hook: `{plugin_id: {"transport": ..., "policy": ...}}`."""
    _client_overrides.clear()
    _client_overrides.update(overrides or {})


def reset_runtime() -> None:
    """Forget the cache, the breakers and the overrides (tests)."""
    _defs_cache.clear()
    _breakers.clear()
    _client_overrides.clear()


def cached_status(manifest: Manifest) -> str | None:
    """`definition_changed` when the cached definitions do not match the pin.
    None when nothing is cached or they match (for the plugins panel)."""
    entry = _defs_cache.get(manifest.id)
    if entry is None:
        return None
    if entry.sha256 != manifest.reviewed.tool_definitions_sha256:
        return DEFINITION_CHANGED
    return None


# ── Tool names (D4) ───────────────────────────────────────────────────────

_BAD = re.compile(r"[^A-Za-z0-9_-]")


def tool_name(plugin_id: str, tool: str, taken: set[str]) -> str:
    """`<plugin_id>__<tool>` mapped to `[A-Za-z0-9_-]`, at most 64 characters.

    When the name is too long, or clashes with one already taken, an 8-hex
    digest of the original pair is appended.
    """
    raw = f"{plugin_id}__{tool}"
    name = _BAD.sub("_", raw)
    if len(name) > MAX_TOOL_NAME or name in taken:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        name = f"{name[:MAX_TOOL_NAME - 9]}_{digest}"
    taken.add(name)
    return name


# ── Sessions ──────────────────────────────────────────────────────────────

_DEV_HOSTS = ("localhost", "127.0.0.1")


class PluginSession:
    """The MCP session of one plugin for one turn (opened on first use)."""

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._client: McpClient | None = None

    @property
    def client(self) -> McpClient:
        if self._client is None:
            url = self.manifest.resolved_url()
            override = _client_overrides.get(self.manifest.id, {})
            policy = override.get("policy")
            if policy is None:
                parsed = urlparse(url)
                # http://localhost is allowed only for a dev endpoint; the
                # transport still refuses it on Lambda (loop/http.py).
                dev = parsed.scheme == "http" and (parsed.hostname or "") in _DEV_HOSTS
                policy = AddressPolicy.default(dev_localhost=dev)
            self._client = McpClient(
                url, self.manifest.effective_hosts(), policy=policy,
                transport=override.get("transport"))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


class PluginTurn:
    """What one turn offers from plugins, and the sessions it holds."""

    def __init__(self, request_id: str = "-") -> None:
        self.request_id = request_id
        self.specs: list[ToolSpec] = []
        self.statuses: dict[str, str] = {}
        self.unavailable: list[str] = []
        self.changed: list[str] = []
        self.replaces: frozenset[str] = frozenset()
        self._sessions: dict[str, PluginSession] = {}
        self._invalid: dict[str, str] = {}

    def session(self, manifest: Manifest) -> PluginSession:
        sess = self._sessions.get(manifest.id)
        if sess is None:
            sess = self._sessions[manifest.id] = PluginSession(manifest)
        return sess

    def is_active(self, plugin_id: str) -> bool:
        return plugin_id not in self._invalid

    def invalidate(self, plugin_id: str, status: str) -> None:
        """Mid-turn: later calls to this plugin are refused by the gate."""
        self._invalid[plugin_id] = status
        self.statuses[plugin_id] = status

    @property
    def open_sessions(self) -> int:
        return sum(1 for s in self._sessions.values()
                   if s._client is not None and s._client.is_open)

    async def close(self) -> None:
        """Close every session. Never raises."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)


# ── Preparing a turn ──────────────────────────────────────────────────────


def _parameters(schema: dict) -> dict:
    out = {k: v for k, v in dict(schema or {}).items() if k != "$schema"}
    out.setdefault("type", "object")
    return out


def _make_call(pt: PluginTurn, manifest: Manifest, remote_name: str):
    async def _call(args: dict, config: dict) -> str:
        return await pt.session(manifest).client.call_tool(
            remote_name, args, token=None, request_id=pt.request_id)
    return _call


def _build_specs(pt: PluginTurn, manifest: Manifest, tools: list[ToolDef]
                 ) -> list[ToolSpec]:
    by_name = {t.name: t for t in tools}
    taken: set[str] = set()
    specs: list[ToolSpec] = []
    for remote_name, decl in manifest.tools.items():
        tool = by_name.get(remote_name)
        if tool is None:
            continue
        specs.append(ToolSpec(
            name=tool_name(manifest.id, remote_name, taken),
            description=(tool.description or remote_name)[:MAX_DESCRIPTION],
            parameters=_parameters(tool.input_schema),
            risk="write" if decl.risk == "write" else "egress_read",
            source="plugin",
            call=_make_call(pt, manifest, remote_name),
            idempotency_arg=decl.idempotency_arg,
            data_class=manifest.data_class,
            plugin_id=manifest.id,
            remote_name=remote_name,
        ))
    return specs


async def _load_one(pt: PluginTurn, manifest: Manifest) -> list[ToolSpec]:
    breaker = get_breaker(manifest.id)
    if not breaker.allow():
        pt.statuses[manifest.id] = UNAVAILABLE
        pt.unavailable.append(manifest.id)
        return []

    url = manifest.resolved_url()
    entry = _defs_cache.get(manifest.id)
    if entry is not None and (entry.url != url or _clock() - entry.fetched >= DEFS_TTL_S):
        entry = None

    if entry is None:
        session = pt.session(manifest)
        try:
            tools = await asyncio.wait_for(session.client.list_tools(), LIST_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - a plugin must not fail the turn
            breaker.record_failure()
            await session.close()
            pt.statuses[manifest.id] = UNAVAILABLE
            pt.unavailable.append(manifest.id)
            logger.warning("plugin_list_failed", extra={
                "request_id": pt.request_id, "plugin_id": manifest.id,
                "error": type(exc).__name__})
            return []
        breaker.record_success()
        entry = _defs_cache[manifest.id] = _Defs(
            _clock(), url, tools, definitions_sha256(tools))

    pinned = manifest.reviewed.tool_definitions_sha256
    if entry.sha256 != pinned:
        pt.statuses[manifest.id] = DEFINITION_CHANGED
        pt.changed.append(manifest.id)
        await pt.session(manifest).close()
        logger.warning("plugin_definition_changed", extra={
            "request_id": pt.request_id, "plugin_id": manifest.id,
            "pinned": pinned, "actual": entry.sha256})
        return []

    pt.statuses[manifest.id] = ACTIVE
    return _build_specs(pt, manifest, entry.tools)


async def prepare_turn(
    *,
    user_id: str,
    request_id: str,
    settings: Any,
    catalog: list[Manifest] | None = None,
    store: PluginStore | None = None,
    web_search: bool = True,
) -> PluginTurn:
    """Decide which plugin tools this turn offers. Never raises."""
    pt = PluginTurn(request_id)
    catalog = load_catalog() if catalog is None else catalog

    if settings.plugins_disabled:                       # D8: the global switch
        pt.statuses = {m.id: OFF for m in catalog
                       if not m.requires_gate or settings.google_enabled}
        return pt

    off = set(getattr(settings, "plugins_off", None) or [])
    candidates: list[Manifest] = []
    for m in catalog:
        if m.requires_gate and not settings.google_enabled:
            continue                                    # D3: invisible
        if m.id in off:
            pt.statuses[m.id] = OFF
        elif m.replaces == "web_search" and not web_search:
            pt.statuses[m.id] = WEB_SEARCH_OFF
        else:
            candidates.append(m)
    if not candidates:
        return pt

    try:
        rows = await (store or get_plugin_store()).get_all(user_id)   # D3: one query
    except Exception as exc:  # noqa: BLE001 - fail closed: offer nothing
        logger.warning("plugin_store_failed", extra={
            "request_id": request_id, "error": type(exc).__name__})
        for m in candidates:
            pt.statuses[m.id] = UNAVAILABLE
            pt.unavailable.append(m.id)
        return pt

    wanted: list[Manifest] = []
    for m in candidates:
        if not rows.get(m.id, m.enabled_by_default):
            pt.statuses[m.id] = DISABLED
        elif not m.resolved_url():
            pt.statuses[m.id] = UNCONFIGURED
        else:
            wanted.append(m)

    loaded = await asyncio.gather(*(_load_one(pt, m) for m in wanted),
                                  return_exceptions=True)
    taken: set[str] = set()
    replaces: set[str] = set()
    for m, result in zip(wanted, loaded):
        if isinstance(result, BaseException):
            pt.statuses[m.id] = UNAVAILABLE
            pt.unavailable.append(m.id)
            continue
        for spec in result:
            if spec.name in taken:        # clash across plugins: disambiguate
                spec.name = tool_name(spec.plugin_id or "", spec.name, taken)
            taken.add(spec.name)
            pt.specs.append(spec)
        if result and m.replaces:
            replaces.add(m.replaces)
    pt.replaces = frozenset(replaces)
    return pt
