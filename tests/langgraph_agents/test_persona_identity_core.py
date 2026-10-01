"""Lõi danh tính dùng chung (plan T8a)."""

import pytest

from langgraph_agents.nodes import _persona_loader as pl
from langgraph_agents.nodes._persona_loader import (
    PersonaError,
    build_persona_prompt,
    get_persona,
)


def test_shared_context_parses_always_section():
    ctx = pl._load_shared_context()
    assert "always" in ctx
    assert "3D body" in ctx["always"]


def test_shared_is_not_a_persona():
    with pytest.raises(PersonaError):
        get_persona("_shared")


def test_persona_without_sheet_has_no_always_block():
    assert not pl._persona_has_sheet("anne")
    prompt = build_persona_prompt(get_persona("anne", "en"), "chat")
    assert "## Always" not in prompt


def test_persona_with_sheet_gets_always_after_identity(monkeypatch):
    monkeypatch.setattr(pl, "_persona_has_sheet", lambda _slug: True)
    persona = get_persona("anne", "en")
    prompt = build_persona_prompt(persona, "chat")
    assert "## Always" in prompt
    identity_pos = prompt.index(persona["identity"][:40])
    always_pos = prompt.index("## Always")
    personality_pos = prompt.index("## Your Personality")
    assert identity_pos < always_pos < personality_pos


def test_split_headers_shared_with_parse_sections():
    sections = pl._split_headers("intro\n\n## Voice\nline1\nline2\n")
    assert sections["identity"] == "intro"
    assert sections["voice"] == "line1\nline2"
