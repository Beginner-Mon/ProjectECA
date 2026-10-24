"""Agent Lambda memory size and fact-extraction switch (Owner, 24/10/2026).

3008 MB because warm containers sat at ~1900 MB of 2048 MB. VVA_LTM=0 keeps
automatic fact extraction off on prod; the env var wins over langgraph.yaml.
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.agent_stack import AgentStack

_ASSET_BASE = "https://d111111abcdef8.cloudfront.net"
_CONTEXT = {
    "motion_key_pair_id": "K2EXAMPLE",
    "cognito_user_pool_id": "us-east-1_test",
    "cognito_app_client_id": "testclient",
    "agent_image_tag": "deadbeef",
}


def _agent_function() -> dict:
    app = cdk.App(context=_CONTEXT)
    stack = AgentStack(
        app, "VvaAgentStack",
        asset_base_url=_ASSET_BASE,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    template = Template.from_stack(stack)
    return list(template.find_resources("AWS::Lambda::Function").values())[0]


@pytest.mark.unit
def test_agent_lambda_memory_is_3008_mb():
    assert _agent_function()["Properties"]["MemorySize"] == 3008


@pytest.mark.unit
def test_fact_extraction_is_off_on_prod():
    env_vars = _agent_function()["Properties"]["Environment"]["Variables"]
    assert env_vars["VVA_LTM"] == "0"
