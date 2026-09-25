"""Where VvaAgentStack gets the image tag from.

Regression 25-09-2026: the template said a6c99cba while the function had been
running 23e5e59a for days, because CI rolls images on with
update-function-code and never touches the template. A `cdk deploy` carrying a
tag copied from an older runbook would have rolled the function back a week —
silently, and reported as success.
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
}


def _template(extra_context: dict) -> Template:
    app = cdk.App(context={**_CONTEXT, **extra_context})
    stack = AgentStack(
        app, "VvaAgentStack",
        asset_base_url=_ASSET_BASE,
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def _image_uri(template: Template) -> str:
    fn = list(template.find_resources("AWS::Lambda::Function").values())[0]
    return str(fn["Properties"]["Code"]["ImageUri"])


@pytest.mark.unit
def test_explicit_tag_still_wins():
    """The flag is how a deliberate rollback to a named build is expressed."""
    uri = _image_uri(_template({"agent_image_tag": "deadbeef"}))
    assert "deadbeef" in uri
    assert "/vva/agent/image-tag" not in uri


@pytest.mark.unit
def test_without_a_flag_the_tag_comes_from_ssm_at_deploy_time():
    template = _template({})
    uri = _image_uri(template)

    # A CloudFormation parameter of type AWS::SSM::Parameter::Value<String>:
    # resolved on every stack operation, so a deploy lands on whatever CI last
    # rolled out. NOT a synth-time lookup, which would cache into
    # cdk.context.json and go stale — the same drift in a new hiding place.
    params = template.to_json().get("Parameters", {})
    ssm_params = {
        name: spec for name, spec in params.items()
        if spec.get("Type") == "AWS::SSM::Parameter::Value<String>"
        and spec.get("Default") == "/vva/agent/image-tag"
    }
    assert ssm_params, f"expected an SSM-valued parameter, got {params}"
    assert any(name in uri for name in ssm_params)


@pytest.mark.unit
def test_bootstrap_still_builds_the_repository_without_a_function():
    """Step 1 of the two-step bootstrap: no image exists yet, so no function."""
    template = _template({"agent_bootstrap": "1"})
    template.resource_count_is("AWS::Lambda::Function", 0)
    template.resource_count_is("AWS::ECR::Repository", 1)
