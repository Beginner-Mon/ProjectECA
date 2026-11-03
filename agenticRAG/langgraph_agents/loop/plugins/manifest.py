"""The plugin manifest (PLAN.md §8) and the catalog that ships with ECA.

A manifest is reviewed by a person: `reviewed.tool_definitions_sha256` pins the
tool definitions the reviewer saw. `"UNPINNED"` is a value no hash equals, so
an unpinned plugin counts as a hash mismatch and stays off until a person runs
`python -m langgraph_agents.loop.plugins.review <id>` and pins what it prints.

The `tools` map is an allow-list: a tool the server lists but the manifest does
not declare is never offered to the model.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

UNPINNED = "UNPINNED"
PLACEHOLDER = "TO_VERIFY"
CATALOG_DIR = Path(__file__).parent / "catalog"


class McpServerDecl(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    # A deployment that hosts the server itself names it in an env var (D5).
    url_env: str | None = None
    transport: Literal["streamable_http"] = "streamable_http"
    allowed_hosts: list[str] = Field(default_factory=list)


class ToolDecl(BaseModel):
    model_config = ConfigDict(extra="ignore")

    egress: bool = True
    risk: Literal["read", "write"] = "read"
    idempotency_arg: str | None = None
    # Stage 4 (E2): the argument names shown on the approval card (all of
    # them when empty), and the argument that names the recipients.
    display: list[str] = Field(default_factory=list)
    recipients_arg: str | None = None


class Reviewed(BaseModel):
    model_config = ConfigDict(extra="ignore")

    by: str = ""
    tool_definitions_sha256: str = UNPINNED
    skills_sha256: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=40)
    version: str
    publisher: str
    name: str
    description: str = ""
    mcp_server: McpServerDecl
    auth: dict[str, Any] | None = None
    tools: dict[str, ToolDecl] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    reviewed: Reviewed = Field(default_factory=Reviewed)
    data_class: Literal["none", "personal"] = "none"
    enabled_by_default: bool = False
    requires_gate: bool = False
    # Name of a builtin tool this plugin stands in for when it is offered.
    replaces: str | None = None
    # Loaded only when `dev_fake_write_enabled()` (E9).
    dev_only: bool = False

    def resolved_url(self, env: Mapping[str, str] | None = None) -> str:
        """The endpoint: the env var when the manifest names one, else `url`.
        Empty when the plugin is not configured on this deployment."""
        env = os.environ if env is None else env
        if self.mcp_server.url_env:
            return (env.get(self.mcp_server.url_env) or "").strip()
        return self.mcp_server.url.strip()

    def effective_hosts(self, env: Mapping[str, str] | None = None) -> list[str]:
        """Allow-list for `http.client_factory`: the manifest's hosts plus the
        endpoint's own host (an operator-set URL is the operator's decision)."""
        hosts = [h.lower() for h in self.mcp_server.allowed_hosts]
        host = (urlparse(self.resolved_url(env)).hostname or "").lower()
        if host and host not in hosts:
            hosts.append(host)
        return hosts

    def needs_user_token(self) -> bool:
        """True for an OAuth connector: its calls carry the user's own bearer
        token, so what it lists and returns belongs to that user (stage 5)."""
        return (self.auth or {}).get("type") == "mcp_oauth"

    def is_configured(self, env: Mapping[str, str] | None = None) -> bool:
        """An endpoint is set and is not a `TO_VERIFY` placeholder (the Google
        manifests ship with placeholders until K verifies the real URLs)."""
        url = self.resolved_url(env)
        return bool(url) and PLACEHOLDER not in url

    def is_pinned(self) -> bool:
        return self.reviewed.tool_definitions_sha256 not in ("", UNPINNED)

    def has_write_tools(self) -> bool:
        return any(t.risk == "write" for t in self.tools.values())


def load_manifest_file(path: Path) -> Manifest:
    return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def _shipped_catalog() -> tuple[Manifest, ...]:
    return tuple(load_manifest_file(p) for p in sorted(CATALOG_DIR.glob("*.json")))


def load_catalog(directory: Path | None = None) -> list[Manifest]:
    """The manifests under `catalog/*.json` (cached for the shipped folder).
    A `dev_only` manifest is left out unless the dev fake-write switch is on."""
    if directory is None:
        from langgraph_agents.loop.dev_fake import dev_fake_write_enabled
        allow_dev = dev_fake_write_enabled()
        return [m for m in _shipped_catalog() if allow_dev or not m.dev_only]
    return [load_manifest_file(p) for p in sorted(directory.glob("*.json"))]


def get_manifest(plugin_id: str, catalog: list[Manifest] | None = None) -> Manifest | None:
    for m in catalog if catalog is not None else load_catalog():
        if m.id == plugin_id:
            return m
    return None
