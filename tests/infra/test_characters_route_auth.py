# -*- coding: utf-8 -*-
"""T2 — /characters auth split (contract A).

Only the LIST is public (the picker grid renders before sign-in, rows are
identical for every viewer). Detail, avatar-profile and audio carry
per-viewer material — audio_version, the profile, signed clip URLs — so
they require the Cognito authorizer. T1 removed signed URLs from the public
responses; this test pins the gateway half of that fix: no token, no detail.

Follows tests/infra/test_motion_route_infra.py (dummy fns, one authorizer).
T4 extends the audio assertions once GET /characters/{slug}/audio exists.
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
    """Walk the resource chain (e.g. ["characters", "{slug}"]) and return the
    method on the leaf resource."""
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


@pytest.mark.unit
def test_characters_list_stays_public(rest_template):
    props = _method_for_path_part(rest_template, ["characters"], "GET")
    assert props["AuthorizationType"] == "NONE"


@pytest.mark.unit
def test_character_detail_requires_cognito(rest_template):
    props = _method_for_path_part(rest_template, ["characters", "{slug}"], "GET")
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"


@pytest.mark.unit
def test_avatar_profile_requires_cognito(rest_template):
    props = _method_for_path_part(
        rest_template, ["characters", "{slug}", "avatar-profile"], "GET",
    )
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"


@pytest.mark.unit
def test_audio_requires_cognito(rest_template):
    """T4: signed clip URLs are per-viewer — same authorizer, no public door."""
    props = _method_for_path_part(
        rest_template, ["characters", "{slug}", "audio"], "GET",
    )
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"


@pytest.mark.unit
def test_still_a_single_authorizer(rest_template):
    """T2 reuses the authorizer built for /sessions — no second one."""
    rest_template.resource_count_is("AWS::ApiGateway::Authorizer", 1)
