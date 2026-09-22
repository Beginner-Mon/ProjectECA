# -*- coding: utf-8 -*-
"""T9 — only vva-agent (+ the warmer) may invoke the SpeechLLm Function URL.

An Allow-only policy adds permission without blocking anyone in the same
account (admin could still call). The whole policy therefore lives in ONE
AWS::Lambda::ResourcePolicy with an explicit Deny for everyone else — and
no AWS::Lambda::Permission may target vva-speechllm at all (AWS warns
against mixing the two on one function; grant_invoke would mint one).
"""

from __future__ import annotations

import json

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


def _policy_object_and_tokens(props: dict) -> tuple[dict, list[dict]]:
    """CloudFormation's schema for AWS::Lambda::ResourcePolicy.PolicyDocument
    requires a JSON OBJECT (despite the CDK docstring saying "formatted as a
    JSON string") — assert it is one, not a string / Fn::Join, then collect
    the live token dicts (function ARN, warmer role GetAtt) nested inside."""
    doc = props["PolicyDocument"]
    assert isinstance(doc, dict), (
        f"expected PolicyDocument as a JSON object, got: {type(doc)}: {str(doc)[:200]}"
    )
    assert "Fn::Join" not in doc, (
        f"PolicyDocument must not be a stringified Fn::Join: {str(doc)[:200]}"
    )
    assert doc.get("Version") == "2012-10-17"
    assert isinstance(doc.get("Statement"), list), "PolicyDocument.Statement must be a list"
    tokens = [n for n in _walk(doc) if isinstance(n, dict) and "Fn::GetAtt" in n]
    return doc, tokens


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
    doc, tokens = _policy_object_and_tokens(_policies(speech_template)[0])
    statements = {s["Sid"]: s for s in doc["Statement"]}

    # All four statements present, with their actions/effects.
    for sid in (
        "AllowAgentInvokeFunctionUrl",
        "AllowAgentInvokeFunction",
        "AllowWarmerInvokeFunction",
        "DenyEveryoneElse",
    ):
        assert sid in statements, f"missing statement {sid}"

    allow_url = statements["AllowAgentInvokeFunctionUrl"]
    assert allow_url["Effect"] == "Allow"
    assert allow_url["Action"] == "lambda:InvokeFunctionUrl"
    assert allow_url["Principal"] == {"AWS": _AGENT_ROLE}
    assert allow_url["Condition"]["StringEquals"]["lambda:FunctionUrlAuthType"] == "AWS_IAM"

    allow_fn = statements["AllowAgentInvokeFunction"]
    assert allow_fn["Effect"] == "Allow"
    assert allow_fn["Action"] == "lambda:InvokeFunction"
    assert allow_fn["Principal"] == {"AWS": _AGENT_ROLE}
    assert allow_fn["Condition"]["Bool"]["lambda:InvokedViaFunctionUrl"] is True

    allow_warmer = statements["AllowWarmerInvokeFunction"]
    assert allow_warmer["Effect"] == "Allow"
    assert allow_warmer["Action"] == "lambda:InvokeFunction"

    deny = statements["DenyEveryoneElse"]
    assert deny["Effect"] == "Deny"
    assert deny["Principal"] == {"AWS": "*"}
    assert set(deny["Action"]) == {"lambda:InvokeFunctionUrl", "lambda:InvokeFunction"}
    not_equals = deny["Condition"]["StringNotEquals"]["aws:PrincipalArn"]
    assert _AGENT_ROLE in not_equals
    assert len(not_equals) == 2, f"expected exactly two exempt ARNs: {not_equals}"

    # The agent ARN is a literal (three mentions: two Allows + the Deny
    # exception list); the function ARN and the warmer role survive only as
    # GetAtt tokens that resolve at deploy.
    doc_text = json.dumps(doc)
    assert doc_text.count(_AGENT_ROLE) == 3
    targets = {tuple(t["Fn::GetAtt"]) for t in tokens}
    assert len(targets) == 2, f"expected function + warmer-role tokens: {tokens}"
    assert any(t[0].startswith("WarmerServiceRole") and t[1] == "Arn" for t in targets)


@pytest.mark.unit
def test_warmer_schedule_has_zero_retries_and_120s_timeout(speech_template):
    """A missed warm ping is harmless (the next ping 5 minutes later covers
    it); retrying a timed-out warmer only multiplies stuck 300s SpeechLLm
    invocations against the account's Lambda concurrency quota. Also pins
    the warmer's own timeout at >= 120s so a slow cold start (INIT ~10s +
    model load + voice enrolment) does not time the warmer out in the first
    place."""
    body = speech_template.to_json()

    schedules = [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Scheduler::Schedule"
    ]
    assert len(schedules) == 1
    retry_policy = schedules[0]["Target"]["RetryPolicy"]
    assert retry_policy["MaximumRetryAttempts"] == 0
    assert 60 <= retry_policy["MaximumEventAgeInSeconds"] <= 86400

    warmer_fns = [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("FunctionName") == "vva-speechllm-warmer"
    ]
    assert len(warmer_fns) == 1
    assert warmer_fns[0]["Timeout"] >= 120


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
