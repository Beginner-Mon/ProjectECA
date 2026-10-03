"""Exercise Rules tách theo mode (plan T10, nhánh synthesizer).

V0 kết luận P4 nằm ở synthesizer (planner 0/8 sai): mode chat vẫn mang luật
giao bài tập. Luật đó chuyển vào `## Exercise Rules`, chỉ nạp khi mode khác
chat. Persona chưa có mục này chạy như cũ.
"""

import pytest

from langgraph_agents.nodes import _persona_loader as pl
from langgraph_agents.nodes._persona_loader import build_persona_prompt, get_persona
from langgraph_agents.nodes.synthesizer import _CHAT_TASK


def test_chat_task_offers_no_wellness_help():
    assert "wellness help" not in _CHAT_TASK
    assert "PT/wellness" not in _CHAT_TASK


def test_chat_mode_excludes_exercise_rules():
    persona = get_persona("anne", "en")
    assert persona.get("exercise_rules"), "anne _core.md thiếu ## Exercise Rules"
    prompt = build_persona_prompt(persona, "chat")
    assert "## Exercise Rules" not in prompt
    assert "the sign that means stop" not in prompt
    assert "one thing they can do right now" not in prompt


@pytest.mark.parametrize("mode", ["synthesize", "refuse", "clarify"])
def test_non_chat_modes_include_exercise_rules(mode):
    persona = get_persona("anne", "en")
    prompt = build_persona_prompt(persona, mode)
    assert "## Exercise Rules" in prompt


def test_persona_without_section_runs_unchanged():
    persona = dict(get_persona("anne", "en"))
    persona.pop("exercise_rules", None)
    for mode in ("chat", "synthesize"):
        prompt = build_persona_prompt(persona, mode)
        assert "## Exercise Rules" not in prompt
