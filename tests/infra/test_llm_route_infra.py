# -*- coding: utf-8 -*-
"""T4 — GET /llm/config, declared in the gateway.

Mirrors the /me/preferences lesson already recorded in rest_api_stack.py: the
CRUD Lambda's router (api/routes_llm.py, T4) can already serve this path, but
without the resource declared HERE API Gateway answers every deployed call
with 403 "Missing Authentication Token" regardless of what the function
supports. This test pins the gateway half: the resource chain exists, GET
requires the Cognito authorizer, and it integrates with the same buffered
CRUD Lambda as /me/preferences (not the streaming agent Lambda used by
/chat, /tts, /motion).

Follows tests/infra/test_feedback_route_infra.py (dummy fns, one shared
authorizer, resource-chain walk via the synthesized template).
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
    """Walk the resource chain (e.g. ["llm", "config"]) and return the method
    properties on the leaf resource."""
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


_LLM_PATH = ["llm", "config"]


@pytest.mark.unit
def test_llm_config_resource_exists(rest_template):
    """Resolving the full chain without raising IS the assertion that
    /llm/config exists — StopIteration means a segment is missing."""
    props = _method_for_path_part(rest_template, _LLM_PATH, "GET")
    assert props["HttpMethod"] == "GET"


@pytest.mark.unit
def test_llm_config_requires_cognito(rest_template):
    props = _method_for_path_part(rest_template, _LLM_PATH, "GET")
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"
    assert "AuthorizerId" in props


@pytest.mark.unit
def test_llm_config_shares_the_one_authorizer(rest_template):
    """One authorizer for the whole API — /llm/config reuses it."""
    rest_template.resource_count_is("AWS::ApiGateway::Authorizer", 1)


@pytest.mark.unit
def test_llm_config_integrates_with_crud_not_agent(rest_template):
    """Buffered CRUD Lambda, same target as /me/preferences — never the
    streaming agent Lambda /chat, /tts and /motion point at. A config read is
    one small JSON document; there is nothing to stream."""
    llm_props = _method_for_path_part(rest_template, _LLM_PATH, "GET")
    prefs_props = _method_for_path_part(rest_template, ["me", "preferences"], "PATCH")
    assert llm_props["Integration"]["Uri"] == prefs_props["Integration"]["Uri"]
    # STREAM integrations (agent_fn) always set ResponseTransferMode; a
    # buffered CRUD integration never does.
    assert "ResponseTransferMode" not in llm_props["Integration"]
