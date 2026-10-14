"""Gemini-as-primary selection for the memory-v2 plan (Tri 14/10/2026).

DeepSeek is out of balance (402), so benchmark A/B run on gemini-2.5-flash:

- the heavy-tier fallback is flash too (pro 404s on all four free-tier keys);
- Gemini-as-primary retries twice (survives 503/504); as a one-shot fallback
  it still never retries;
- `on_dev_branch.py --llm gemini --gemini-key N` isolates exactly one key per
  run (20 calls/day/key) and strips every DeepSeek credential.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _llm_mod():
    from langgraph_agents import llm as llm_mod

    return llm_mod


def _clear(llm_mod):
    llm_mod.get_fallback_chat_model.cache_clear()
    llm_mod._eca_chat_model.cache_clear()


# ── llm.py: flash everywhere, retries depend on primary-vs-fallback ────


def test_heavy_fallback_resolves_to_flash(monkeypatch):
    """gemini-2.5-pro 404s on all four free-tier keys: no role may resolve it."""
    llm_mod = _llm_mod()
    monkeypatch.setattr(llm_mod, "_HEAVY_ROLES", {"synthesizer"})
    assert llm_mod._fallback_model_for_role("synthesizer") == "gemini-2.5-flash"
    assert llm_mod._fallback_model_for_role("planner") == "gemini-2.5-flash"


def test_fallback_defaults_to_no_retries(monkeypatch):
    """One-shot fallback: the caller already has its own error path, a retry
    storm only stacks worst cases (see the max_retries=0 comment in llm.py)."""
    monkeypatch.setenv("GEMINI_API_KEYS", "key-one,key-two")
    llm_mod = _llm_mod()
    _clear(llm_mod)
    try:
        model = llm_mod.get_fallback_chat_model("planner")
    finally:
        _clear(llm_mod)
    assert model is not None
    assert model.model.endswith("gemini-2.5-flash")
    assert model.max_retries == 0


def test_primary_gemini_retries_twice(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEYS", "key-one")
    llm_mod = _llm_mod()
    _clear(llm_mod)
    try:
        model = llm_mod.get_fallback_chat_model("planner", primary=True)
    finally:
        _clear(llm_mod)
    assert model is not None
    assert model.model.endswith("gemini-2.5-flash")
    assert model.max_retries == 2


def test_no_deepseek_key_uses_primary_gemini(monkeypatch):
    """Without any DeepSeek credential the ECA model IS Gemini, with retries."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY_PARAM", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "key-one")
    llm_mod = _llm_mod()
    _clear(llm_mod)
    try:
        model = llm_mod.get_chat_model("planner")
    finally:
        _clear(llm_mod)
    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    assert model.model.endswith("gemini-2.5-flash")
    assert model.max_retries == 2


# ── on_dev_branch.py: one key per run ───────────────────────────────────


def _select():
    from langgraph_agents.local_tests.on_dev_branch import select_llm_env

    return select_llm_env


def test_deepseek_default_changes_nothing():
    env = {"DEEPSEEK_API_KEY": "ds", "GEMINI_API_KEYS": "g1,g2,g3,g4"}
    updates, deletions, label = _select()(dict(env), "deepseek", None)
    assert updates == {} and deletions == []
    assert label == "llm=deepseek"


def test_gemini_key_picks_exactly_one_key():
    env = {"DEEPSEEK_API_KEY": "ds", "DEEPSEEK_API_KEY_PARAM": "p",
           "GEMINI_API_KEYS": "g1,g2,g3,g4"}
    updates, deletions, label = _select()(dict(env), "gemini", 2)
    assert updates == {"GEMINI_API_KEYS": "g2"}
    assert sorted(deletions) == ["DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_PARAM"]
    assert label == "llm=gemini key=2/4"
    assert "g2" not in label


def test_gemini_key_rejects_bad_selection():
    env = {"GEMINI_API_KEYS": "g1,g2,g3,g4"}
    select = _select()
    for llm, num in [("gemini", None), ("gemini", 0), ("gemini", 5),
                     ("deepseek", 2), ("other", None)]:
        with pytest.raises(ValueError):
            select(dict(env), llm, num)
    with pytest.raises(ValueError):
        select({"GEMINI_API_KEYS": "  "}, "gemini", 1)
