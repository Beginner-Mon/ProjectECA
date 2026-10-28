"""ECA's own model provider is one setting: LLM_PROVIDER (deepseek by default, or openai).

Plan: docs/plans/llm-openai-switch.md. What is pinned here:

  * nothing changes for DeepSeek unless LLM_PROVIDER says otherwise;
  * on OpenAI, gpt-6-luna gets no temperature (it accepts only its default),
    a reasoning effort per role (none unless a variable asks for more), and more
    output room when it reasons, because reasoning tokens count against the cap;
  * each provider keeps its own key variable and SSM parameter, so switching back
    is one value;
  * an unknown provider name falls back to DeepSeek instead of failing every turn.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langgraph_agents import llm

pytestmark = pytest.mark.unit

_PROVIDER_VARS = (
    "LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_API_KEY_PARAM", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "OPENAI_MODEL_FAST", "OPENAI_REASONING_EFFORT", "OPENAI_REASONING_EFFORT_PLANNER",
    "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_PARAM", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL_FAST",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _PROVIDER_VARS:
        monkeypatch.delenv(name, raising=False)
    llm._eca_chat_model.cache_clear()
    llm._secret_from_ssm.cache_clear()
    yield
    llm._eca_chat_model.cache_clear()
    llm._secret_from_ssm.cache_clear()


def _openai(monkeypatch, **env):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_default_provider_is_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek")
    m = llm.get_chat_model("planner")
    assert llm.llm_provider() == "deepseek"
    assert m.model_name == "deepseek-v4-flash"
    assert "deepseek.com" in m.openai_api_base
    assert m.temperature == 0.0
    assert m.reasoning_effort is None


def test_openai_provider_uses_luna_without_temperature(monkeypatch):
    _openai(monkeypatch)
    m = llm.get_chat_model("planner")
    assert m.model_name == "gpt-6-luna"
    assert m.openai_api_base == "https://api.openai.com/v1"
    assert m.openai_api_key.get_secret_value() == "fake-openai"
    assert m.temperature is None
    assert m.reasoning_effort == "none"
    assert m.stream_usage is True
    assert not m.extra_body             # DeepSeek's `thinking` flag never goes to OpenAI
    assert m.max_tokens == llm._max_tokens_for_role("planner")


def test_reasoning_effort_per_role_gets_more_output_room(monkeypatch):
    _openai(monkeypatch, OPENAI_REASONING_EFFORT_PLANNER="low")
    planner = llm.get_chat_model("planner")
    synthesizer = llm.get_chat_model("synthesizer")
    assert planner.reasoning_effort == "low"
    assert planner.max_tokens == llm._max_tokens_for_role("planner") + llm._REASONING_HEADROOM
    assert synthesizer.reasoning_effort == "none"
    assert synthesizer.max_tokens == llm._max_tokens_for_role("synthesizer")


def test_reasoning_effort_for_every_role(monkeypatch):
    _openai(monkeypatch, OPENAI_REASONING_EFFORT="medium")
    assert llm.get_chat_model("grader").reasoning_effort == "medium"


def test_openai_model_can_be_overridden(monkeypatch):
    _openai(monkeypatch, OPENAI_MODEL_FAST="gpt-5.6-luna")
    assert llm.get_chat_model("synthesizer").model_name == "gpt-5.6-luna"


def test_each_provider_reads_its_own_key(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm._resolve_api_key() is None       # the DeepSeek key is not borrowed


def test_openai_key_from_ssm(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY_PARAM", "/vva/llm/openai-api-key")
    client = MagicMock()
    client.get_parameter.return_value = {"Parameter": {"Value": "sk-from-ssm"}}
    boto3 = MagicMock()
    boto3.client.return_value = client
    with patch.dict("sys.modules", {"boto3": boto3}):
        assert llm._resolve_api_key() == "sk-from-ssm"
    assert client.get_parameter.call_args.kwargs["Name"] == "/vva/llm/openai-api-key"


def test_unknown_provider_falls_back_to_deepseek(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "opneai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-deepseek")
    assert llm.llm_provider() == "deepseek"
    assert llm.get_chat_model("planner").model_name == "deepseek-v4-flash"
