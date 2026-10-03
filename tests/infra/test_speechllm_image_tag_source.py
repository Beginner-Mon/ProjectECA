"""Where VvaSpeechllmStack gets the image tag from.

Same seam as VvaAgentStack (see test_agent_image_tag_source.py): CI rolls
images on with update-function-code and never touches the template, so the
template drifts behind and a `cdk deploy` carrying a tag from an older runbook
rolls the function backwards. For SpeechLLm that would mean losing HOME=/tmp
and the de-symlinked HF cache — TTS goes silent, and CloudFormation reports
success.
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.speechllm_stack import SpeechllmStack

_AGENT_ROLE = "arn:aws:iam::123456789012:role/VvaAgentStack-AgentServiceRole-test"


def _template(extra_context: dict) -> Template:
    app = cdk.App(context={"agent_role_arn": _AGENT_ROLE, **extra_context})
    stack = SpeechllmStack(
        app, "VvaSpeechllmStack",
        env=cdk.Environment(account="123456789012", region="us-east-1"),
    )
    return Template.from_stack(stack)


def _speechllm_image_uri(template: Template) -> str:
    for fn in template.find_resources("AWS::Lambda::Function").values():
        code = fn["Properties"].get("Code", {})
        if "ImageUri" in code:
            return str(code["ImageUri"])
    raise AssertionError("no container function in the template")


@pytest.mark.unit
def test_explicit_tag_still_wins():
    uri = _speechllm_image_uri(_template({"speechllm_image_tag": "cafebabe"}))
    assert "cafebabe" in uri
    assert "/vva/speechllm/image-tag" not in uri


@pytest.mark.unit
def test_without_a_flag_the_tag_comes_from_ssm_at_deploy_time():
    template = _template({})
    uri = _speechllm_image_uri(template)

    params = template.to_json().get("Parameters", {})
    ssm_params = {
        name: spec for name, spec in params.items()
        if spec.get("Type") == "AWS::SSM::Parameter::Value<String>"
        and spec.get("Default") == "/vva/speechllm/image-tag"
    }
    assert ssm_params, f"expected an SSM-valued parameter, got {params}"
    assert any(name in uri for name in ssm_params)


@pytest.mark.unit
def test_bootstrap_still_builds_the_repository_without_a_function():
    template = _template({"speechllm_bootstrap": "1"})
    template.resource_count_is("AWS::ECR::Repository", 1)
    for fn in template.find_resources("AWS::Lambda::Function").values():
        assert "ImageUri" not in fn["Properties"].get("Code", {})
