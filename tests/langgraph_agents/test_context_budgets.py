"""Ngân sách context (plan T11): config là chân lý, code giữ default cũ."""

import pytest

from langgraph_agents.shared import context as ctx_mod
from langgraph_agents.shared.context import (
    budget_chars,
    context_budget,
    estimate_tokens,
)


def test_config_carries_all_plan_budgets():
    assert ctx_mod._load_context_budgets() == {
        "persona": 600,
        "identity_core": 120,
        "about_you": 250,
        "body_state": 40,
        "evidence": 1200,
        "history": 1000,
        "voice_card": 280,
    }


def test_missing_key_falls_back_to_old_behavior(monkeypatch):
    monkeypatch.setattr(ctx_mod, "_load_context_budgets", lambda: {})
    # Số cũ: evidence 4000 chars (=1000 tokens), history/STM 1500, about-you 900.
    assert context_budget("evidence") == 1000
    assert context_budget("history") == 1500
    assert context_budget("about_you") == 225
    assert budget_chars("evidence") == 4000


def test_identity_core_block_within_budget():
    """Khối lõi danh tính của Anne không vượt trần (Tri chốt 120)."""
    from langgraph_agents.nodes._persona_loader import _load_shared_context

    always = _load_shared_context()["always"]
    assert estimate_tokens(always) <= context_budget("identity_core")


def test_evidence_cap_reads_config(monkeypatch):
    """_extract_tool_results tôn trần evidence từ config."""
    from langchain_core.messages import ToolMessage

    from langgraph_agents.nodes import synthesizer as syn

    big = "x" * 3000
    msgs = [ToolMessage(content=big, tool_call_id=f"t{i}", name="kb_search")
            for i in range(3)]
    monkeypatch.setattr(syn, "budget_chars", lambda key: 1000 if key == "evidence"
                        else ctx_mod.budget_chars(key))
    ev = syn._extract_tool_results(msgs)
    assert len(ev) <= 1000 + 1500  # trần tổng + một message đầu luôn sống
