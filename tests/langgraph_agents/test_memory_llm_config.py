"""Role memory chay tren key cua ECA, khong suy nghi ngam (memory-v2 A1)."""

import pytest
from pydantic import SecretStr

pytestmark = pytest.mark.unit


def _model(role, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    from langgraph_agents import llm as llm_mod

    llm_mod.get_chat_model.cache_clear()
    try:
        return llm_mod.get_chat_model(role)
    finally:
        llm_mod.get_chat_model.cache_clear()


def test_memory_disables_hidden_reasoning(monkeypatch):
    from langgraph_agents.llm import role_timeout

    m = _model("memory", monkeypatch)
    assert m.extra_body == {"thinking": {"type": "disabled"}}
    assert m.request_timeout == 15.0 == role_timeout("memory")
    assert m.max_retries == 0
    assert m.temperature == 0.0


def test_memory_stays_on_eca_when_user_llm_bound(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    import langgraph_agents.llm as llm
    import langgraph_agents.llm_byok as byok

    user_base = "https://user.example/v1"
    llm.get_chat_model.cache_clear()
    byok.bind_request_llm(None)
    built = []
    monkeypatch.setattr(
        llm, "_byok_chat_model", lambda *a, **k: built.append(a) or pytest.fail("BYOK built")
    )
    try:
        byok.bind_request_llm(
            byok.RequestLLM(
                base_url=user_base,
                model="user-model-x",
                api_key=SecretStr("user-key-123"),
                capabilities=frozenset({"text", "answer", "tools"}),
                omit=frozenset(),
                failure=None,
            )
        )
        m = llm.get_chat_model("memory")
        assert getattr(m, "openai_api_base", "") != user_base
        assert m.extra_body == {"thinking": {"type": "disabled"}}
        assert built == []
    finally:
        byok.bind_request_llm(None)
        llm.get_chat_model.cache_clear()
