"""Re-pin the skill definitions used by the agent loop.

Run from a directory where `langgraph_agents` is importable (from the repo:
`agenticRAG/langgraph_agents/loop/skills` → run inside `agenticRAG/`):

    python -m langgraph_agents.loop.skills.pin

It rewrites `PINS.json` with the sha256 of every usable `<id>/SKILL.md`.
Skill files are code (plan §9 supply chain): any change goes through a
review and a re-pin; the loader skips and warns on every unpinned change.
"""

from __future__ import annotations

import json
from pathlib import Path

from langgraph_agents.loop.skills.loader import (
    SKILLS_DIR, discover, file_sha256,
)


def compute_pins(skills_dir: Path | None = None) -> dict[str, str]:
    """sha256 per skill id for every discoverable SKILL.md."""
    root = skills_dir or SKILLS_DIR
    return {info.id: file_sha256(info.path) for info in discover(root)}


def write_pins(pins: dict[str, str], skills_dir: Path | None = None) -> Path:
    """Write PINS.json next to the skill dirs; returns its path."""
    root = skills_dir or SKILLS_DIR
    path = root / "PINS.json"
    path.write_text(
        json.dumps(pins, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    root = SKILLS_DIR
    pins = compute_pins(root)
    path = write_pins(pins, root)
    print(f"PINS.json written: {path} ({len(pins)} skill(s))")
    for skill_id, digest in sorted(pins.items()):
        print(f"  {skill_id}: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
