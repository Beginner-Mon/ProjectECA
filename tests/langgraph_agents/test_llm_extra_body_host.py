"""DeepSeek's `thinking` flag goes only to DeepSeek hosts.

Other OpenAI-compatible hosts (Groq, used for benchmarks) reject the field with
400 "property 'thinking' is unsupported".
"""

import pytest

pytestmark = pytest.mark.unit


def _model(monkeypatch, base_url):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    if base_url is None:
        monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("DEEPSEEK_BASE_URL", base_url)
    from langgraph_agents import llm as llm_mod

    llm_mod.get_chat_model.cache_clear()
    llm_mod._eca_chat_model.cache_clear()
    try:
        return llm_mod.get_chat_model("memory")
    finally:
        llm_mod.get_chat_model.cache_clear()
        llm_mod._eca_chat_model.cache_clear()


def test_default_base_url_keeps_thinking_disabled(monkeypatch):
    m = _model(monkeypatch, None)
    assert m.extra_body == {"thinking": {"type": "disabled"}}


def test_non_deepseek_host_gets_no_extra_body(monkeypatch):
    m = _model(monkeypatch, "https://api.groq.com/openai/v1")
    assert not m.extra_body
