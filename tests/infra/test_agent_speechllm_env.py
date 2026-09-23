"""VIENEU_TTS_URL wiring on AgentStack (D3) — the kill switch is ABSENCE.

api/main.py's tts_enabled() treats presence of VIENEU_TTS_URL as the on-switch
for speech synthesis, not a separate ENABLE_TTS flag. That means the
CloudFormation template must never carry the key set to "" — a deploy that
forgot -c speechllm_url has to leave TTS off, the same way asset_base_url and
motion_key_pair_id defaulting to "" used to leave motion silently broken (see
test_motion_route_infra.py's "I5" section). Unlike those two, an empty
speechllm_url is not a synth error: TTS is optional infrastructure (unhosted
as of 21-08, Owner deferred it), so a deploy with nothing to point at is a
valid, TTS-off deploy — only a non-https value is malformed enough to fail
synth.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.agent_stack import AgentStack
from infra.asset_stack import AssetStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")

_DUMMY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAMDummyKeyForTestingPurposesOnly"
    "NeverUseInProductionAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "ECAwEAAQ==\n"
    "-----END PUBLIC KEY-----\n"
)

_BASE_CONTEXT = {
    "motion_public_key_pem": _DUMMY_PEM,
    "agent_image_tag": "deadbeef",
    "motion_key_pair_id": "K2EXAMPLE",
}


def _agent_template(extra_context: dict | None = None) -> Template:
    app = cdk.App(context={**_BASE_CONTEXT, **(extra_context or {})})
    asset_stack = AssetStack(app, "Assets", env=_ENV)
    agent_stack = AgentStack(
        app, "Agent",
        asset_base_url=f"https://{asset_stack.distribution.distribution_domain_name}",
        env=_ENV,
    )
    return Template.from_stack(agent_stack)


def _agent_stack(extra_context: dict | None = None) -> AgentStack:
    app = cdk.App(context={**_BASE_CONTEXT, **(extra_context or {})})
    asset_stack = AssetStack(app, "Assets", env=_ENV)
    return AgentStack(
        app, "Agent",
        asset_base_url=f"https://{asset_stack.distribution.distribution_domain_name}",
        env=_ENV,
    )


def _errors(stack) -> str:
    """Same helper as test_motion_route_infra.py's _errors — annotations are
    construct-tree metadata, not exceptions, so they are read after a
    non-validating synth rather than caught."""
    app = stack.node.root
    assembly = app.synth(force=True, validate_on_synthesis=False)
    return " ".join(
        str(msg.entry.data)
        for msg in assembly.get_stack_by_name(stack.stack_name).messages
        if msg.level == cdk.cx_api.SynthesisMessageLevel.ERROR
    )


@pytest.mark.unit
def test_speechllm_url_becomes_vieneu_tts_url_env_var():
    template = _agent_template({
        "speechllm_url": "https://example.lambda-url.us-east-1.on.aws",
    })
    template.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "Environment": {"Variables": Match.object_like({
            "VIENEU_TTS_URL": "https://example.lambda-url.us-east-1.on.aws",
        })},
    }))


@pytest.mark.unit
def test_no_speechllm_url_means_the_key_is_absent_not_empty():
    """The regression this whole change closes: the old shape would have set
    VIENEU_TTS_URL="" here, which tts_enabled() happens to also read as off —
    but absence is what every other reader of the environment (including a
    human on the Lambda console) has to see too."""
    template = _agent_template()
    body = template.to_json()
    fn = next(
        r for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
    )
    env_vars = fn["Properties"]["Environment"]["Variables"]
    assert "VIENEU_TTS_URL" not in env_vars


@pytest.mark.unit
def test_non_https_speechllm_url_fails_synth():
    stack = _agent_stack({"speechllm_url": "http://example.lambda-url.us-east-1.on.aws"})
    assert "https://" in _errors(stack)


@pytest.mark.unit
def test_trailing_slash_is_stripped():
    template = _agent_template({
        "speechllm_url": "https://example.lambda-url.us-east-1.on.aws/",
    })
    template.has_resource_properties("AWS::Lambda::Function", Match.object_like({
        "Environment": {"Variables": Match.object_like({
            "VIENEU_TTS_URL": "https://example.lambda-url.us-east-1.on.aws",
        })},
    }))
