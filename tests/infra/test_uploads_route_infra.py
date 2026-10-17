"""POST /me/uploads in the gateway, and what the CRUD Lambda may do in the bucket.

Without the resource declared in RestApiStack, API Gateway answers 403
"Missing Authentication Token" whatever the function supports (the
/me/preferences lesson). The IAM grant is the ceiling of every presigned POST
the function signs, so it is pinned to PutObject under images/.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.crud_api_stack import _ZIP_PATH, CrudApiStack
from infra.uploads_stack import uploads_bucket_name
from aws_cdk import aws_lambda as lambda_
from infra.rest_api_stack import RestApiStack


def _dummy_fn(scope, cid):
    return lambda_.Function(
        scope, cid,
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context):\n    return {}"),
    )



def _method_for_path_part(template, path_parts, http_method):
    resources = template.to_json()["Resources"]
    parent = None
    for part in path_parts:
        parent = next(
            rid for rid, r in resources.items()
            if r["Type"] == "AWS::ApiGateway::Resource"
            and r["Properties"].get("PathPart") == part
            and (parent is None or r["Properties"].get("ParentId", {}).get("Ref") == parent)
        )
    return next(
        r["Properties"] for r in resources.values()
        if r["Type"] == "AWS::ApiGateway::Method"
        and r["Properties"].get("ResourceId", {}).get("Ref") == parent
        and r["Properties"].get("HttpMethod") == http_method
    )

_ENV = cdk.Environment(account="244203483654", region="us-east-1")


@pytest.fixture(scope="module")
def rest_template():
    app = cdk.App()
    deps = cdk.Stack(app, "DummyDeps", env=_ENV)
    stack = RestApiStack(
        app, "Rest",
        crud_fn=_dummy_fn(deps, "Crud"),
        characters_fn=_dummy_fn(deps, "Characters"),
        cognito_pool_id="us-east-1_TESTPOOL",
        agent_fn=_dummy_fn(deps, "Agent"),
        env=_ENV,
    )
    return Template.from_stack(stack)


def test_post_me_uploads_needs_a_cognito_token(rest_template):
    method = _method_for_path_part(rest_template, ["me", "uploads"], "POST")
    assert method["AuthorizationType"] == "COGNITO_USER_POOLS"


@pytest.mark.skipif(not _ZIP_PATH.exists(), reason="python infra/build_crud_api.py first")
def test_crud_lambda_may_only_put_under_images():
    app = cdk.App()
    template = Template.from_stack(CrudApiStack(app, "Crud", env=_ENV))
    (fn,) = [r for r in template.find_resources("AWS::Lambda::Function").values()
             if r["Properties"].get("FunctionName") == "vva-crud-api"]
    assert fn["Properties"]["Environment"]["Variables"]["UPLOADS_BUCKET"] == \
        uploads_bucket_name("244203483654")

    s3_statements = [
        s for p in template.find_resources("AWS::IAM::Policy").values()
        for s in p["Properties"]["PolicyDocument"]["Statement"]
        if any(str(a).startswith("s3:") for a in
               (s["Action"] if isinstance(s["Action"], list) else [s["Action"]]))
    ]
    assert s3_statements == [{
        "Action": "s3:PutObject",
        "Effect": "Allow",
        "Resource": "arn:aws:s3:::vva-user-uploads-244203483654/images/*",
    }]
