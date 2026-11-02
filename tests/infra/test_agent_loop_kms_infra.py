"""KMS key that seals agent-loop state, and the agent role's grant on it (stage 4C).

loop/crypto.py KmsSealer does envelope encryption: generate_data_key to seal,
decrypt to open. The function role therefore gets kms:GenerateDataKey and
kms:Decrypt on this one key and nothing else, and LOOP_KMS_KEY_ID points at it
(loop/config.py reads that variable to choose the sealer). Offline: CDK
assertions on the synthesized template.
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


def _key(template: Template) -> tuple[str, dict]:
    keys = template.find_resources("AWS::KMS::Key")
    assert len(keys) == 1
    return next(iter(keys.items()))


def _statements(template: Template) -> list[dict]:
    out: list[dict] = []
    for policy in template.find_resources("AWS::IAM::Policy").values():
        out.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return out


def _actions(statement: dict) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


@pytest.mark.unit
def test_the_seal_key_rotates_and_survives_stack_deletion(template):
    key_id, key = _key(template)
    assert key["Properties"]["EnableKeyRotation"] is True
    assert key["DeletionPolicy"] == "Retain"
    assert key["UpdateReplacePolicy"] == "Retain"
    template.has_resource_properties("AWS::KMS::Alias", {
        "AliasName": "alias/vva-loop-seal",
        "TargetKeyId": {"Fn::GetAtt": [key_id, "Arn"]},
    })


@pytest.mark.unit
def test_the_agent_gets_the_key_id_in_its_environment(template):
    key_id, _ = _key(template)
    fn = next(iter(template.find_resources("AWS::Lambda::Function").values()))
    value = fn["Properties"]["Environment"]["Variables"]["LOOP_KMS_KEY_ID"]
    assert value == {"Fn::GetAtt": [key_id, "Arn"]}


@pytest.mark.unit
def test_the_role_may_only_generate_data_keys_and_decrypt_on_that_key(template):
    key_id, _ = _key(template)
    key_arn = {"Fn::GetAtt": [key_id, "Arn"]}
    on_key = [s for s in _statements(template) if s.get("Resource") == key_arn]
    assert len(on_key) == 1, "exactly one statement may name the seal key"
    assert on_key[0]["Effect"] == "Allow"
    assert sorted(_actions(on_key[0])) == ["kms:Decrypt", "kms:GenerateDataKey"]
    assert "Condition" not in on_key[0]


@pytest.mark.unit
def test_no_other_statement_widens_kms_access(template):
    key_id, _ = _key(template)
    key_arn = {"Fn::GetAtt": [key_id, "Arn"]}
    others = [s for s in _statements(template) if s.get("Resource") != key_arn]
    for s in others:
        kms_actions = [a for a in _actions(s) if a.startswith("kms:") or a == "*"]
        # The only other KMS grant is the old SSM SecureString read, scoped by kms:ViaService.
        if kms_actions:
            assert kms_actions == ["kms:Decrypt"]
            assert "kms:ViaService" in str(s["Condition"])


@pytest.mark.unit
def test_the_key_policy_adds_nothing_for_the_function(template):
    # Same account: the role's own IAM statement is enough, so the key policy stays
    # the default (account root) and carries no extra principal.
    _, key = _key(template)
    principals = [str(s.get("Principal")) for s in key["Properties"]["KeyPolicy"]["Statement"]]
    assert all(":root" in p for p in principals)
