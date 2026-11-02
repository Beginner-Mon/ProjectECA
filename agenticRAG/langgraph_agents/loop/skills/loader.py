"""Skill loader for the loop mode (stage 2 / plan II.2 `skills/`).

A skill is `<id>/SKILL.md` with YAML-ish frontmatter `name` and
`description` (the two lines the `load_skill` tool description lists).
There is no script content: the body is plain instructions the agent
follows.

`PINS.json` next to the skill dirs holds the sha256 of each SKILL.md file
and is written by `python -m langgraph_agents.loop.skills.pin`. Skills are
code (plan §9 supply chain): a body that changed without a re-pin is
skipped with a warning — fail closed, the agent never reads unpinned
skill text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph_agents.shared.logging import get_logger

logger = get_logger("langgraph.loop.skills")

# Skill destinations live beside this module; PINS.json sits in the same
# directory. Both override functions accept an explicit dir for tests.
SKILLS_DIR = Path(__file__).resolve().parent


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class SkillInfo:
    """One discovered skill: id (directory name) + frontmatter fields."""

    id: str
    name: str
    description: str
    path: Path


def _read_frontmatter(path: Path) -> tuple[str, str, str]:
    """Return (name, description, body) from a SKILL.md file."""
    raw = path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(raw)
    body = raw[match.end():].strip() if match else raw.strip()
    meta: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
    return meta.get("name", ""), meta.get("description", ""), body


def file_sha256(path: Path) -> str:
    """sha256 of the file with CRLF folded to LF — what PINS.json stores.

    A Windows checkout (`core.autocrlf=true`) rewrites SKILL.md with CRLF;
    the pin must not depend on which line ending git wrote."""
    data = path.read_bytes().replace(chr(13).encode() + b"\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _pins(skills_dir: Path) -> dict[str, str]:
    """Pinned sha256 per skill id; {} when absent or unreadable (fail closed)."""
    path = skills_dir / "PINS.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 — a corrupt pins file pins nothing
        logger.warning("skill_pins_unreadable", extra={"error": type(exc).__name__})
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def discover(skills_dir: Path | None = None, *,
             pinned_only: bool = False) -> list[SkillInfo]:
    """List every `<id>/SKILL.md` with usable frontmatter, sorted by id.

    `pinned_only=True` keeps only skills whose current hash matches
    PINS.json (what the model may see); the pin CLI uses the default.
    """
    root = skills_dir or SKILLS_DIR
    pins = _pins(root) if pinned_only else {}
    out: list[SkillInfo] = []
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        md = child / "SKILL.md"
        if not child.is_dir() or not md.is_file():
            continue
        name, description, _ = _read_frontmatter(md)
        if not name or not description:
            logger.warning(
                "skill_invalid_frontmatter", extra={"skill": child.name})
            continue
        if pinned_only and pins.get(child.name) != file_sha256(md):
            logger.warning(
                "skill_unpinned_hidden", extra={"skill": child.name})
            continue
        out.append(SkillInfo(id=child.name, name=name,
                             description=description, path=md))
    return out


def load_skill(name: str, skills_dir: Path | None = None) -> dict[str, Any]:
    """Load one skill body by id.

    Returns `{"skill", "loaded", "content", "warning"}`:
    - loaded=True → `content` is the body (frontmatter stripped);
    - loaded=False → `content` is "" and `warning` explains why. Unknown
      skill, invalid frontmatter, and a hash mismatch against PINS.json
      (edited without a re-pin) all skip with a warning.
    """
    root = skills_dir or SKILLS_DIR
    # The name comes from the model: only a discovered id may reach the file
    # system, so "../x" never builds a path.
    known = {info.id for info in discover(root)}
    path = root / name / "SKILL.md"
    if name not in known:
        warning = f"Skill '{name}' does not exist."
        logger.warning("skill_load_skipped",
                       extra={"skill": name, "reason": "unknown"})
        return {"skill": name, "loaded": False, "content": "",
                "warning": warning}

    skill_name, description, body = _read_frontmatter(path)
    if not skill_name or not description or not body:
        warning = (
            f"Skill '{name}' is skipped: SKILL.md has no usable "
            "name / description / body.")
        logger.warning("skill_load_skipped",
                       extra={"skill": name, "reason": "invalid_frontmatter"})
        return {"skill": name, "loaded": False, "content": "",
                "warning": warning}

    pin = _pins(root).get(name)
    digest = file_sha256(path)
    if pin is None:
        warning = (
            f"Skill '{name}' is skipped: SKILL.md is not pinned. "
            "Re-pin with `python -m langgraph_agents.loop.skills.pin`.")
        logger.warning("skill_load_skipped",
                       extra={"skill": name, "reason": "not_pinned"})
        return {"skill": name, "loaded": False, "content": "",
                "warning": warning}
    if pin != digest:
        warning = (
            f"Skill '{name}' is skipped: SKILL.md changed since it was "
            "pinned. Re-pin with `python -m langgraph_agents.loop.skills.pin`.")
        logger.warning("skill_load_skipped",
                       extra={"skill": name, "reason": "hash_mismatch"})
        return {"skill": name, "loaded": False, "content": "",
                "warning": warning}

    return {"skill": name, "loaded": True, "content": body, "warning": None}
