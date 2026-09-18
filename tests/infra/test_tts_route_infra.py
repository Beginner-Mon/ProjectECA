# -*- coding: utf-8 -*-
"""T8 — POST /tts streams like /chat; the polling route is gone.

Since 885ec019 POST /tts IS the audio (SSE speech_start/chunk/end in the
response body) and GET /tts/{task_id}/result has no handler and no caller.
A buffered integration would hold the whole synthesis (504 past ~29s, 10 MB
ceiling past ~3 min), so /tts copies /chat's STREAM integration — pinned
here the same way the Phase 0 spike pinned /chat's: ResponseTransferMode
STREAM plus an integration URI ending /response-streaming-invocations.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Template

from infra.rest_api_stack import RestApiStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")


def _dummy_fn(scope, cid):
    return lambda_.Function(
        scope, cid,
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context):\n    return {}"),
    )


@pytest.fixture
def rest_template():
    app = cdk.App()
    deps = cdk.Stack(app, "DummyDeps", env=_ENV)
    rest_stack = RestApiStack(
        app, "Rest",
        crud_fn=_dummy_fn(deps, "Crud"),
        characters_fn=_dummy_fn(deps, "Characters"),
        cognito_pool_id="us-east-1_TESTPOOL",
        agent_fn=_dummy_fn(deps, "Agent"),
        env=_ENV,
    )
    return Template.from_stack(rest_stack)


def _tts_post_method(rest_template) -> dict:
    """The Method on the `tts` resource with HttpMethod POST."""
    body = rest_template.to_json()
    resources = body["Resources"]
    tts_res = next(
        rid for rid, r in resources.items()
        if r["Type"] == "AWS::ApiGateway::Resource"
        and r["Properties"].get("PathPart") == "tts"
    )
    method = next(
        r for r in resources.values()
        if r["Type"] == "AWS::ApiGateway::Method"
        and r["Properties"].get("ResourceId", {}).get("Ref") == tts_res
        and r["Properties"].get("HttpMethod") == "POST"
    )
    return method["Properties"]


@pytest.mark.unit
def test_tts_post_is_response_streaming(rest_template):
    props = _tts_post_method(rest_template)
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"
    integration = props["Integration"]
    assert integration["ResponseTransferMode"] == "STREAM"
    assert "response-streaming-invocations" in str(integration["Uri"])


@pytest.mark.unit
def test_tts_has_no_result_resource(rest_template):
    """The polling route died with the Redis path — no `result` under `tts`,
    so nothing can 404 after waking the agent for nothing."""
    body = rest_template.to_json()
    resources = body["Resources"]
    path_parts = [
        r["Properties"].get("PathPart")
        for r in resources.values()
        if r["Type"] == "AWS::ApiGateway::Resource"
    ]
    assert "result" not in path_parts
    assert "{task_id}" not in path_parts
