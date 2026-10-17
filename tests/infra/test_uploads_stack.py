"""VvaUploadsStack: the private bucket for user uploads (SCRUM-45 P1, PDF part 0).

Images expire after N days; user documents (`docs/`) are kept until the user
deletes them, which is why keys start with the kind and the lifecycle rule is
scoped to `images/`. The browser uploads with a presigned POST, so the bucket
needs CORS for POST from the app's origins and nothing else.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from infra.origins import DEFAULT_ALLOWED_ORIGINS
from infra.uploads_stack import UploadsStack, uploads_bucket_name

_ENV = cdk.Environment(account="244203483654", region="us-east-1")


def _template(context: dict | None = None) -> Template:
    app = cdk.App(context=context or {})
    return Template.from_stack(UploadsStack(app, "Uploads", env=_ENV))


@pytest.fixture(scope="module")
def template():
    return _template()


def _bucket(template) -> dict:
    (bucket,) = template.find_resources("AWS::S3::Bucket").values()
    return bucket


def test_name_is_derived_from_the_account_so_other_stacks_need_no_export(template):
    assert uploads_bucket_name("244203483654") == "vva-user-uploads-244203483654"
    assert _bucket(template)["Properties"]["BucketName"] == "vva-user-uploads-244203483654"
    assert not template.to_json().get("Outputs", {}) or all(
        "Export" not in o for o in template.to_json()["Outputs"].values())


def test_is_private_encrypted_and_kept_on_stack_delete(template):
    props = _bucket(template)["Properties"]
    assert props["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True, "BlockPublicPolicy": True,
        "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
    }
    assert props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
    assert _bucket(template)["DeletionPolicy"] == "Retain"


def test_refuses_plain_http(template):
    template.has_resource_properties("AWS::S3::BucketPolicy", {
        "PolicyDocument": {"Statement": Match.array_with([Match.object_like({
            "Effect": "Deny",
            "Condition": {"Bool": {"aws:SecureTransport": "false"}},
        })])},
    })


def test_images_expire_after_30_days_and_documents_do_not(template):
    rules = _bucket(template)["Properties"]["LifecycleConfiguration"]["Rules"]
    images = [r for r in rules if r.get("Prefix") == "images/"]
    assert len(images) == 1 and images[0]["ExpirationInDays"] == 30
    assert not [r for r in rules if r.get("Prefix", "").startswith("docs")]
    assert not [r for r in rules if "Prefix" not in r and "ExpirationInDays" in r]


def test_image_retention_is_a_context_value():
    rules = _bucket(_template({"upload_image_retention_days": "7"}))["Properties"][
        "LifecycleConfiguration"]["Rules"]
    assert [r["ExpirationInDays"] for r in rules if r.get("Prefix") == "images/"] == [7]


def test_cors_allows_post_from_the_app_only(template):
    (rule,) = _bucket(template)["Properties"]["CorsConfiguration"]["CorsRules"]
    assert rule["AllowedMethods"] == ["POST"]
    assert set(rule["AllowedOrigins"]) == set(DEFAULT_ALLOWED_ORIGINS)
    assert "*" not in rule["AllowedOrigins"]
