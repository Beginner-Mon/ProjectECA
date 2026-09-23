# -*- coding: utf-8 -*-
"""D6 measure knob — temporary third exception to the DenyEveryoneElse clause.

measure_speechllm.py must call the Function URL directly to isolate
SpeechLLm's own synthesis time, but the Deny blocks every principal except
the agent role and the warmer role — including admins (T1/N2 proves it: a
signed admin call still gets 403). The `-c measure_principal_arn=<ARN>`
knob adds that ARN to the Deny exception list — nothing else, no new Allow
(a same-account caller already has lambda:InvokeFunctionUrl in its own
identity policy; lifting the Deny is enough).

The knob is TEMPORARY: T3e redeploys without it and re-runs N2 to prove it
is gone. Without the flag the policy must be byte-identical to the T9
shape (exactly two exempt ARNs), and no AWS::Lambda::Permission may ever
target vva-speechllm (T9 invariant, re-asserted here with the flag on).
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.speechllm_stack import SpeechllmStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")

_AGENT_ROLE = "arn:aws:iam::244203483654:role/vva-agent-test-role"
_MEASURE_USER = "arn:aws:iam::244203483654:user/admin"
_MEASURE_ROLE = "arn:aws:iam::244203483654:role/some-measure-role"


def _template(extra_context: dict | None = None) -> Template:
    context = {
        "speechllm_image_tag": "deadbeef",
        "agent_role_arn": _AGENT_ROLE,
    }
    if extra_context:
        context.update(extra_context)
    app = cdk.App(context=context)
    stack = SpeechllmStack(app, "Speech", env=_ENV)
    return Template.from_stack(stack)


def _deny_exceptions(tpl: Template) -> list:
    body = tpl.to_json()
    policies = [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::ResourcePolicy"
    ]
    assert len(policies) == 1
    doc = policies[0]["PolicyDocument"]
    statements = {s["Sid"]: s for s in doc["Statement"]}
    return statements["DenyEveryoneElse"]["Condition"]["StringNotEquals"]["aws:PrincipalArn"]


@pytest.mark.unit
def test_no_flag_is_exactly_two_arns():
    """Default locks the T9 shape: agent role + warmer role token, no more."""
    not_equals = _deny_exceptions(_template())
    assert _AGENT_ROLE in not_equals
    assert len(not_equals) == 2, f"expected exactly two exempt ARNs: {not_equals}"


@pytest.mark.unit
@pytest.mark.parametrize("measure_arn", [_MEASURE_USER, _MEASURE_ROLE])
def test_flag_adds_exactly_one_third_arn(measure_arn):
    """With the flag the list has exactly three entries and the third one
    is verbatim the value passed in — no Allow added anywhere."""
    tpl = _template({"measure_principal_arn": measure_arn})
    not_equals = _deny_exceptions(tpl)
    assert len(not_equals) == 3, f"expected exactly three exempt ARNs: {not_equals}"
    assert not_equals[2] == measure_arn


@pytest.mark.unit
def test_flag_does_not_add_any_allow_statement():
    """Lifting the Deny is all the knob does — still exactly three Allows."""
    body = _template({"measure_principal_arn": _MEASURE_USER}).to_json()
    policies = [
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::ResourcePolicy"
    ]
    allows = [
        s for s in policies[0]["PolicyDocument"]["Statement"]
        if s["Effect"] == "Allow"
    ]
    assert len(allows) == 3


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["not-an-arn", "arn:aws:iam::123:group/x", ""])
def test_bad_arn_shape_is_synth_error(bad):
    """Wrong shape blocks synth via Annotations error (never raise — app.py
    builds this stack on every `cdk` invocation, raise would break even
    `cdk list`). Empty string means 'flag absent' and must stay silent."""
    ctx = {"speechllm_image_tag": "deadbeef", "agent_role_arn": _AGENT_ROLE}
    if bad != "":
        ctx["measure_principal_arn"] = bad
    app = cdk.App(context=ctx)
    stack = SpeechllmStack(app, "Speech", env=_ENV)
    assembly = app.synth(force=True, validate_on_synthesis=False)
    errors = " ".join(
        str(msg.entry.data)
        for msg in assembly.get_stack_by_name(stack.stack_name).messages
        if msg.level == cdk.cx_api.SynthesisMessageLevel.ERROR
    )
    if bad == "":
        assert "measure_principal_arn" not in errors
    else:
        assert "measure_principal_arn" in errors


@pytest.mark.unit
def test_no_lambda_permission_with_flag_on():
    """T9 invariant holds with the knob on: no AWS::Lambda::Permission may
    target vva-speechllm."""
    body = _template({"measure_principal_arn": _MEASURE_USER}).to_json()
    speechllm_fn = next(
        rid for rid, r in body["Resources"].items()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("FunctionName") == "vva-speechllm"
    )
    for rid, r in body["Resources"].items():
        if r["Type"] == "AWS::Lambda::Permission":
            target = r["Properties"].get("FunctionName", {}).get("Fn::GetAtt", [None])[0]
            assert target != speechllm_fn, (
                f"AWS::Lambda::Permission still targets vva-speechllm: {r['Properties']}"
            )
