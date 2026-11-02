"""Agent-loop skills (stage 2, plan II.2).

`loop/skills/` holds the loader, the pin writer and the sample skills.
`loop.tools.builtin.load_skill_spec` exposes them to the agent: names and
descriptions go into the tool description, bodies load only on the call.
"""

from langgraph_agents.loop.skills.loader import (
    SKILLS_DIR, SkillInfo, discover, file_sha256, load_skill,
)

__all__ = ["SKILLS_DIR", "SkillInfo", "discover", "file_sha256", "load_skill"]
