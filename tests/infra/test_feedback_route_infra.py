# -*- coding: utf-8 -*-
"""T5 — POST/DELETE /me/feedback/messages/{message_id}, declared in the gateway.

Mirrors the /me/preferences lesson already recorded in rest_api_stack.py: the
CRUD Lambda's router (api/routes_feedback.py, T4) can already serve this path,
but without the resource declared HERE API Gateway answers every deployed
call with 403 "Missing Authentication Token" regardless of what the function
supports. This test pins the gateway half: the resource chain exists, both
methods require the Cognito authorizer, and both integrate with the same
buffered CRUD Lambda as /me/preferences (not the streaming agent Lambda used
by /chat, /tts, /motion).

Follows tests/infra/test_characters_route_auth.py and
tests/infra/test_tts_route_infra.py (dummy fns, one shared authorizer,
resource-chain walk via the synthesized template).
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


def _method_for_path_part(rest_template, path_parts: list[str], http_method: str) -> dict:
    """Walk the resource chain (e.g. ["me", "feedback", "messages",
    "{message_id}"]) and return the method properties on the leaf resource."""
    body = rest_template.to_json()
    resources = body["Resources"]
    parent_ref = None
    leaf_res = None
    for part in path_parts:
        leaf_res = next(
            rid for rid, r in resources.items()
            if r["Type"] == "AWS::ApiGateway::Resource"
            and r["Properties"].get("PathPart") == part
            and (
                parent_ref is None
                or r["Properties"].get("ParentId", {}).get("Ref") == parent_ref
            )
        )
        parent_ref = leaf_res
    method = next(
        r for r in resources.values()
        if r["Type"] == "AWS::ApiGateway::Method"
        and r["Properties"].get("ResourceId", {}).get("Ref") == leaf_res
        and r["Properties"].get("HttpMethod") == http_method
    )
    return method["Properties"]


_FEEDBACK_PATH = ["me", "feedback", "messages", "{message_id}"]


@pytest.mark.unit
def test_feedback_message_resource_exists(rest_template):
    """Resolving the full chain without raising IS the assertion that
    /me/feedback/messages/{message_id} exists — StopIteration means a
    segment is missing."""
    props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "POST")
    assert props["HttpMethod"] == "POST"


@pytest.mark.unit
def test_feedback_post_requires_cognito(rest_template):
    props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "POST")
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"
    assert "AuthorizerId" in props


@pytest.mark.unit
def test_feedback_delete_requires_cognito(rest_template):
    props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "DELETE")
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"
    assert "AuthorizerId" in props


@pytest.mark.unit
def test_feedback_methods_share_the_one_authorizer(rest_template):
    """Task 9's rule (test_motion_route_infra.py) applies here too: reuse
    the authorizer built for /sessions, not a second one per route."""
    rest_template.resource_count_is("AWS::ApiGateway::Authorizer", 1)
    post_props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "POST")
    delete_props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "DELETE")
    assert post_props["AuthorizerId"] == delete_props["AuthorizerId"]


@pytest.mark.unit
def test_feedback_post_integrates_with_crud_not_agent(rest_template):
    """Buffered CRUD Lambda, same target as /me/preferences — never the
    streaming agent Lambda /chat, /tts and /motion point at. A vote is one
    small row; there is nothing to stream."""
    post_props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "POST")
    prefs_props = _method_for_path_part(rest_template, ["me", "preferences"], "PATCH")
    assert post_props["Integration"]["Uri"] == prefs_props["Integration"]["Uri"]
    # STREAM integrations (agent_fn) always set ResponseTransferMode; a
    # buffered CRUD integration never does.
    assert "ResponseTransferMode" not in post_props["Integration"]


@pytest.mark.unit
def test_feedback_delete_integrates_with_crud_not_agent(rest_template):
    delete_props = _method_for_path_part(rest_template, _FEEDBACK_PATH, "DELETE")
    prefs_props = _method_for_path_part(rest_template, ["me", "preferences"], "PATCH")
    assert delete_props["Integration"]["Uri"] == prefs_props["Integration"]["Uri"]
    assert "ResponseTransferMode" not in delete_props["Integration"]
