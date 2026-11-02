"""`review.py <plugin_id>`: print the definitions hash of a plugin's server.

Run by a person (K) before pinning a plugin:

    python -m langgraph_agents.loop.plugins.review deepwiki

It connects to the manifest's endpoint through the same guarded client the loop
uses, lists the tools, and prints the sha256 of their definitions together with
the tool names and descriptions, so the reviewer can read what they are pinning.
It compares the hash with the manifest and says whether they match. It WRITES
NOTHING: the person copies the hash into `reviewed.tool_definitions_sha256` of
`catalog/<id>.json` (and sets `reviewed.by`).

`--url` overrides the endpoint (for a plugin whose URL comes from an env var, or
to review a staging copy). The allow-list is still the manifest's hosts plus the
host of the URL used.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from langgraph_agents.loop.mcp_client import McpClient, ToolDef, definitions_sha256
from langgraph_agents.loop.plugins.manifest import Manifest, UNPINNED, get_manifest


@dataclass
class ReviewReport:
    plugin_id: str
    url: str
    sha256: str
    tools: list[ToolDef]
    pinned: str
    undeclared: list[str]
    missing: list[str]

    @property
    def matches(self) -> bool:
        return self.sha256 == self.pinned

    def render(self) -> str:
        lines = [
            f"plugin:      {self.plugin_id}",
            f"endpoint:    {self.url}",
            f"tools ({len(self.tools)}):",
        ]
        for t in sorted(self.tools, key=lambda x: x.name):
            lines.append(f"  - {t.name}: {t.description.strip()[:300]}")
        lines += [
            f"sha256:      {self.sha256}",
            f"pinned:      {self.pinned}",
            "status:      " + ("MATCHES the manifest pin" if self.matches else
                               ("UNPINNED" if self.pinned == UNPINNED
                                else "DIFFERS from the manifest pin")),
        ]
        if self.undeclared:
            lines.append("listed by the server but not declared in the manifest "
                         f"(never offered): {', '.join(sorted(self.undeclared))}")
        if self.missing:
            lines.append("declared in the manifest but not listed by the server: "
                         f"{', '.join(sorted(self.missing))}")
        lines.append("To pin: set reviewed.tool_definitions_sha256 to the sha256 "
                     f"above in loop/plugins/catalog/{self.plugin_id}.json")
        return "\n".join(lines)


async def review(manifest: Manifest, *, url: str | None = None,
                 client: McpClient | None = None, timeout: float = 15.0) -> ReviewReport:
    """Connect, list, hash. Writes nothing."""
    endpoint = (url or manifest.resolved_url()).strip()
    if client is None:
        if not endpoint:
            raise SystemExit(
                f"{manifest.id} has no endpoint: pass --url or set "
                f"{manifest.mcp_server.url_env}")
        hosts = list(manifest.effective_hosts())
        from urllib.parse import urlparse
        host = (urlparse(endpoint).hostname or "").lower()
        if host and host not in hosts:
            hosts.append(host)
        from langgraph_agents.loop.http import AddressPolicy
        dev = endpoint.startswith("http://") and host in ("localhost", "127.0.0.1")
        client = McpClient(endpoint, hosts, policy=AddressPolicy.default(dev_localhost=dev))
    try:
        tools = await asyncio.wait_for(client.list_tools(), timeout)
    finally:
        await client.close()
    names = {t.name for t in tools}
    return ReviewReport(
        plugin_id=manifest.id, url=endpoint, sha256=definitions_sha256(tools),
        tools=tools, pinned=manifest.reviewed.tool_definitions_sha256,
        undeclared=[n for n in names if n not in manifest.tools],
        missing=[n for n in manifest.tools if n not in names])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the tool-definitions hash of a catalog plugin (writes nothing).")
    parser.add_argument("plugin_id")
    parser.add_argument("--url", default=None, help="override the endpoint")
    args = parser.parse_args(argv)
    manifest = get_manifest(args.plugin_id)
    if manifest is None:
        print(f"unknown plugin: {args.plugin_id}", file=sys.stderr)
        return 2
    report = asyncio.run(review(manifest, url=args.url))
    print(report.render())
    return 0 if report.matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
