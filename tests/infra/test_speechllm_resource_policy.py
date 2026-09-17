# -*- coding: utf-8 -*-
"""T9 — only vva-agent (+ the warmer) may invoke the SpeechLLm Function URL.

An Allow-only policy adds permission without blocking anyone in the same
account (admin could still call). The whole policy therefore lives in ONE
AWS::Lambda::ResourcePolicy with an explicit Deny for everyone else — and
no AWS::Lambda::Permission may target vva-speechllm at all (AWS warns
against mixing the two on one function; grant_invoke would mint one).
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.speechllm_stack import SpeechllmStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")

_AGENT_ROLE = "arn:aws:iam::244203483654:role/vva-agent-test-role"


@pytest.fixture
def speech_template():
    app = cdk.App(context={
        "speechllm_image_tag": "deadbeef",
        "agent_role_arn": _AGENT_ROLE,
    })
    stack = SpeechllmStack(app, "Speech", env=_ENV)
    return Template.from_stack(stack)


def _walk(node):
    """Yield every dict/list nested anywhere under node (tokens included)."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _policies(speech_template) -> list[dict]:
    body = speech_template.to_json()
    return [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::ResourcePolicy"
    ]


def _policy_text_and_tokens(props: dict) -> tuple[str, list[dict]]:
    """Split a to_json_string PolicyDocument (Fn::Join) into its literal
    text and its live token dicts (function ARN, warmer role GetAtt)."""
    doc = props["PolicyDocument"]
    assert isinstance(doc, dict) and "Fn::Join" in doc, (
        f"expected PolicyDocument as Fn::Join JSON string, got: {str(doc)[:200]}"
    )
    text = "".join(p for p in doc["Fn::Join"][1] if isinstance(p, str))
    tokens = [p for p in doc["Fn::Join"][1] if isinstance(p, dict)]
    return text, tokens


@pytest.mark.unit
def test_exactly_one_resource_policy(speech_template):
    assert len(_policies(speech_template)) == 1


@pytest.mark.unit
def test_no_lambda_permission_targets_speechllm(speech_template):
    """add_permission / grant_invoke on vva-speechllm would each mint one —
    none may exist, including the warmer's (it is covered by an Allow
    inside the resource policy instead)."""
    body = speech_template.to_json()
    permissions = [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::Permission"
    ]
    fn_refs = str(body["Resources"])
    speechllm_fn = next(
        rid for rid, r in body["Resources"].items()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("FunctionName") == "vva-speechllm"
    )
    for perm in permissions:
        assert perm.get("FunctionName", {}).get("Fn::GetAtt", [None])[0] != speechllm_fn, (
            f"AWS::Lambda::Permission still targets vva-speechllm: {perm}"
        )
    assert "vva-speechllm-warmer" in fn_refs  # the warmer itself still exists


@pytest.mark.unit
def test_deny_everyone_else_with_two_arn_exceptions(speech_template):
    text, tokens = _policy_text_and_tokens(_policies(speech_template)[0])

    # All four statements, both actions, both conditions, the Deny shape.
    for sid in (
        "AllowAgentInvokeFunctionUrl",
        "AllowAgentInvokeFunction",
        "AllowWarmerInvokeFunction",
        "DenyEveryoneElse",
    ):
        assert sid in text, f"missing statement {sid}"
    for fragment in (
        "lambda:InvokeFunctionUrl",
        "lambda:InvokeFunction",
        "lambda:FunctionUrlAuthType",
        "lambda:InvokedViaFunctionUrl",
        "aws:PrincipalArn",
        "StringNotEquals",
        '"AWS":"*"',
        '"Effect":"Deny"',
    ):
        assert fragment in text, f"missing policy fragment: {fragment}"

    # The agent ARN is a literal (three mentions: two Allows + the Deny
    # exception list); the function ARN and the warmer role survive only as
    # GetAtt tokens that resolve at deploy. The Deny exception list is
    # therefore one literal plus one token — presence of both is the point.
    assert text.count(_AGENT_ROLE) == 3
    targets = {tuple(t["Fn::GetAtt"]) for t in tokens if "Fn::GetAtt" in t}
    assert len(targets) == 2, f"expected function + warmer-role tokens: {tokens}"
    assert any(t[0].startswith("WarmerServiceRole") and t[1] == "Arn" for t in targets)


@pytest.mark.unit
def test_missing_agent_role_arn_is_still_an_error():
    """T9 keeps the gate: image tag without the agent ARN blocks this stack
    (add_error, scoped to VvaSpeechllmStack — other stacks unaffected)."""
    app = cdk.App(context={"speechllm_image_tag": "deadbeef"})
    stack = SpeechllmStack(app, "Speech", env=_ENV)
    assembly = app.synth(force=True, validate_on_synthesis=False)
    errors = " ".join(
        str(msg.entry.data)
        for msg in assembly.get_stack_by_name(stack.stack_name).messages
        if msg.level == cdk.cx_api.SynthesisMessageLevel.ERROR
    )
    assert "agent_role_arn" in errors
