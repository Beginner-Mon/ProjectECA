"""API streaming shares the asset CDN without changing its signed paths."""

from copy import deepcopy

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Annotations, Match, Template

from infra.asset_stack import AssetStack


_DUMMY_PEM = (
    "-----BEGIN PUBLIC KEY-----\n"
    "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAMDummyKeyForTestingPurposesOnly"
    "NeverUseInProductionAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "ECAwEAAQ==\n"
    "-----END PUBLIC KEY-----\n"
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def stacks():
    result = {}
    for enabled in (False, True):
        context = {"motion_public_key_pem": _DUMMY_PEM}
        if enabled:
            context["rest_api_id"] = "fy1jccsfx3"
        app = cdk.App(context=context)
        stack = AssetStack(
            app, "Assets",
            env=cdk.Environment(account="244203483654", region="us-east-1"),
        )
        result[enabled] = (stack, Template.from_stack(stack).to_json())
    return result


def _distribution(template):
    return next(
        resource["Properties"]["DistributionConfig"]
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::CloudFront::Distribution"
    )


def test_api_behavior_preserves_streaming_and_forwards_auth(stacks):
    distribution = _distribution(stacks[True][1])
    behavior = next(
        item for item in distribution["CacheBehaviors"]
        if item["PathPattern"] == "v1/*"
    )
    assert behavior["Compress"] is False
    assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    assert behavior["OriginRequestPolicyId"] == "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    assert set(behavior["AllowedMethods"]) == {
        "GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE",
    }
    assert behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    assert "ResponseHeadersPolicyId" not in behavior
    assert "TrustedKeyGroups" not in behavior

    origin = next(
        item for item in distribution["Origins"]
        if item["Id"] == behavior["TargetOriginId"]
    )
    assert origin["DomainName"] == "fy1jccsfx3.execute-api.us-east-1.amazonaws.com"
    assert not origin.get("OriginPath")
    assert origin["CustomOriginConfig"]["OriginReadTimeout"] == 60
    assert origin["CustomOriginConfig"]["OriginKeepaliveTimeout"] == 60
    assert origin["CustomOriginConfig"]["OriginProtocolPolicy"] == "https-only"


def test_missing_api_context_warns_and_preserves_asset_routes(stacks):
    stack, template = stacks[False]
    distribution = _distribution(template)
    assert {item["PathPattern"] for item in distribution["CacheBehaviors"]} == {
        "motions/*", "characters/*/audio/*",
    }
    assert "DefaultCacheBehavior" in distribution
    assert "ApiCdnUrl" not in template["Outputs"]
    Annotations.from_stack(stack).has_warning(
        "*", Match.string_like_regexp(".*API traffic still uses the regional.*"),
    )
    assert not Annotations.from_stack(stack).find_error("*", Match.any_value())


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_private_paths_keep_the_signing_key_group(stacks, enabled):
    template = stacks[enabled][1]
    key_group_id = next(
        logical_id for logical_id, resource in template["Resources"].items()
        if resource["Type"] == "AWS::CloudFront::KeyGroup"
    )
    behaviors = {
        item["PathPattern"]: item
        for item in _distribution(template)["CacheBehaviors"]
    }
    for path in ("motions/*", "characters/*/audio/*"):
        assert behaviors[path]["TrustedKeyGroups"] == [{"Ref": key_group_id}]
    assert "TrustedKeyGroups" not in _distribution(template)["DefaultCacheBehavior"]


def test_api_context_only_adds_one_origin_behavior_and_output(stacks):
    baseline = stacks[False][1]
    changed = deepcopy(stacks[True][1])
    distribution = _distribution(changed)
    api_behavior = next(
        item for item in distribution["CacheBehaviors"]
        if item["PathPattern"] == "v1/*"
    )
    distribution["CacheBehaviors"].remove(api_behavior)
    api_origin = next(
        item for item in distribution["Origins"]
        if item["Id"] == api_behavior["TargetOriginId"]
    )
    distribution["Origins"].remove(api_origin)
    output = changed["Outputs"].pop("ApiCdnUrl")
    assert "VITE_API_GATEWAY_URL" in output["Description"]
    assert output["Value"] == {
        "Fn::Join": ["", [
            "https://",
            baseline["Outputs"]["AssetBaseUrl"]["Value"]["Fn::Join"][1][1],
            "/v1",
        ]],
    }
    assert changed == baseline
