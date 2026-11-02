# -*- coding: utf-8 -*-
"""Agent-loop routes on the REST API (stage 4C; blocker found in 3C).

Until 4C only /chat and /tts reached the agent function, so every loop route
(Stop's /chat/cancel, the approval card's /chat/resume, /me/plugins, ...) was
answered by API Gateway itself with 403 "Missing Authentication Token".

Each route here must use the agent function, /chat's integration options
(STREAM, 120 s: the Lambda runs in response_stream mode, so a buffered
integration answers 502) and the Cognito authorizer, and /chat itself must be
what it was. Offline: CDK assertions on the synthesized template, no AWS call.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk import aws_lambda as lambda_
from aws_cdk.assertions import Template

from infra.rest_api_stack import RestApiStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")

# (path, method): the loop routes added in 4C. The connector routes belong to
# stage 5; routed now, they answer 404 from the function until the app serves them.
LOOP_ROUTES = [
    ("/chat/cancel", "POST"),
    ("/chat/resume", "POST"),
    ("/me/plugins", "GET"),
    ("/me/plugins/{plugin_id}", "POST"),
    ("/me/actions/pending", "GET"),
    ("/connectors/{plugin_id}/start", "POST"),
    ("/connectors/callback", "POST"),
    ("/me/connectors", "GET"),
    ("/me/connectors/{connector_id}", "DELETE"),
]


def _dummy_fn(scope, cid):
    return lambda_.Function(
        scope, cid,
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context):\n    return {}"),
    )


@pytest.fixture(scope="module")
def routes():
    """{(path, method): Method properties} for every non-OPTIONS method, plus the resources."""
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
    resources = Template.from_stack(rest_stack).to_json()["Resources"]

    api_ids = [rid for rid, r in resources.items() if r["Type"] == "AWS::ApiGateway::RestApi"]
    assert len(api_ids) == 1
    root_ref = {"Fn::GetAtt": [api_ids[0], "RootResourceId"]}

    parts = {
        rid: r["Properties"]
        for rid, r in resources.items()
        if r["Type"] == "AWS::ApiGateway::Resource"
    }

    def path_of(rid: str) -> str:
        props = parts[rid]
        parent = props["ParentId"]
        prefix = "" if parent == root_ref else path_of(parent["Ref"])
        return f"{prefix}/{props['PathPart']}"

    paths = {rid: path_of(rid) for rid in parts}
    methods: dict[tuple[str, str], dict] = {}
    for r in resources.values():
        if r["Type"] != "AWS::ApiGateway::Method" or r["Properties"]["HttpMethod"] == "OPTIONS":
            continue
        ref = r["Properties"]["ResourceId"]
        path = "/" if ref == root_ref else paths[ref["Ref"]]
        methods[(path, r["Properties"]["HttpMethod"])] = r["Properties"]
    return {"methods": methods, "paths": list(paths.values()), "parts": list(parts.values()), "root": root_ref}


@pytest.mark.unit
@pytest.mark.parametrize("path,method", LOOP_ROUTES)
def test_loop_route_uses_the_agent_stream_integration_and_the_authorizer(routes, path, method):
    methods = routes["methods"]
    assert (path, method) in methods, f"{method} {path} is not routed"
    props = methods[(path, method)]
    chat = methods[("/chat", "POST")]

    # Same function and options as /chat, copied verbatim.
    assert props["Integration"]["Uri"] == chat["Integration"]["Uri"]
    assert props["Integration"]["ResponseTransferMode"] == "STREAM"
    assert "response-streaming-invocations" in str(props["Integration"]["Uri"])
    assert props["Integration"]["TimeoutInMillis"] == chat["Integration"]["TimeoutInMillis"] == 120_000
    assert props["Integration"]["Type"] == "AWS_PROXY"

    # And the Cognito authorizer, the same one.
    assert props["AuthorizationType"] == "COGNITO_USER_POOLS"
    assert props["AuthorizerId"] == chat["AuthorizerId"]


@pytest.mark.unit
def test_loop_routes_do_not_reach_the_crud_function(routes):
    crud_uri = routes["methods"][("/me/memory", "GET")]["Integration"]["Uri"]
    agent_uri = routes["methods"][("/chat", "POST")]["Integration"]["Uri"]
    assert crud_uri != agent_uri
    for key in LOOP_ROUTES:
        assert routes["methods"][key]["Integration"]["Uri"] != crud_uri


@pytest.mark.unit
def test_there_is_one_me_resource_and_it_keeps_its_old_children(routes):
    mes = [p for p in routes["parts"] if p["PathPart"] == "me" and p["ParentId"] == routes["root"]]
    assert len(mes) == 1, "a second `me` resource would make API Gateway refuse the deploy"
    for path in ("/me/memory", "/me/preferences", "/me/quota", "/me/uploads"):
        assert path in routes["paths"], f"{path} was lost"


@pytest.mark.unit
def test_chat_itself_is_unchanged(routes):
    chat = routes["methods"][("/chat", "POST")]
    assert chat["AuthorizationType"] == "COGNITO_USER_POOLS"
    assert chat["Integration"]["ResponseTransferMode"] == "STREAM"
    assert chat["Integration"]["TimeoutInMillis"] == 120_000
    # /chat has exactly one method: POST.
    assert [m for (p, m) in routes["methods"] if p == "/chat"] == ["POST"]
    # /tts, the other route that already reached the agent, is the same integration.
    tts = routes["methods"][("/tts", "POST")]
    assert tts["Integration"]["Uri"] == chat["Integration"]["Uri"]


@pytest.mark.unit
def test_no_loop_route_is_left_open(routes):
    for key in LOOP_ROUTES:
        assert routes["methods"][key]["AuthorizationType"] != "NONE"
