"""OAuth connector IAM grant and environment on the agent Lambda (stage 5).

The agent role gets `secretsmanager:GetSecretValue` on
`arn:...:secret:vva/<env>/plugins/*` only — nothing broader — and the
`LOOP_OAUTH_REDIRECT_URI` / `LOOP_ENV` environment variables. Offline: CDK
assertions on the synthesized template, no AWS call.
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.agent_stack import AgentStack

_CONTEXT = {
    "motion_key_pair_id": "K2EXAMPLE",
    "cognito_user_pool_id": "us-east-1_test",
    "cognito_app_client_id": "testclient",
    "agent_image_tag": "deadbeef",
    "loop_env": "test",
    "oauth_redirect_uri": "https://app.test/connectors/callback",
}


@pytest.fixture(scope="module")
def template() -> Template:
    app = cdk.App(context=_CONTEXT)
    stack = AgentStack(
        app, "VvaAgentStack",
        asset_base_url="https://d111111abcdef8.cloudfront.net",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def _statements(template: Template) -> list[dict]:
    out: list[dict] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        out.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return out


def _actions(statement: dict) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


@pytest.mark.unit
def test_the_agent_can_only_read_connector_secrets(template):
    expected = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vva/test/plugins/*"
    grants = [
        s for s in _statements(template)
        if _actions(s) == ["secretsmanager:GetSecretValue"]
    ]
    assert len(grants) == 1, "exactly one GetSecretValue grant"
    assert grants[0]["Effect"] == "Allow"
    assert grants[0]["Resource"] == expected
    assert "Condition" not in grants[0]


@pytest.mark.unit
def test_no_other_statement_grants_secretsmanager_wildcard(template):
    # The new GetSecretValue grant is expected; what must not exist is any
    # statement granting the wildcard secretsmanager:* (or *).
    for s in _statements(template):
        actions = _actions(s)
        wildcard = [a for a in actions if a == "secretsmanager:*" or a == "*"]
        assert not wildcard, f"unexpected wildcard secretsmanager access: {actions}"


@pytest.mark.unit
def test_the_agent_gets_the_oauth_env_vars(template):
    fn = next(iter(template.find_resources("AWS::Lambda::Function").values()))
    vars_ = fn["Properties"]["Environment"]["Variables"]
    assert vars_["LOOP_OAUTH_REDIRECT_URI"] == "https://app.test/connectors/callback"
    assert vars_["LOOP_ENV"] == "test"
