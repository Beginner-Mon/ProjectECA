"""VvaUploadsStack — the private bucket for files users upload.

SCRUM-45 part 1 (photos in chat) and part 0 of docs/plans/user-documents-pdf.md.
Objects are written only through a presigned POST that the CRUD Lambda signs
(api/routes_uploads.py); nothing here is ever public.

Key layout is `<kind>/<user_id>/<random>.<ext>`, kind first, because a
lifecycle rule can only filter by prefix:

  * `images/` — photos sent in chat. Expire after `upload_image_retention_days`
    (default 30). Deleting a message or conversation deletes the object sooner.
  * `docs/`   — the user's own PDFs. No expiry: they are the user's private
    knowledge base and stay until the user deletes them.

The bucket name is derived from the account rather than exported. Other stacks
(CRUD, agent, ingest) build the same string with `uploads_bucket_name`, so no
CloudFormation export ties their deploys to this one — the same reason
VvaMonitoringStack names functions by string.
"""

from __future__ import annotations

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct

from infra.origins import resolve as resolve_origins

_DEFAULT_IMAGE_RETENTION_DAYS = 30


def uploads_bucket_name(account: str) -> str:
    return f"vva-user-uploads-{account}"


class UploadsStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        retention = int(self.node.try_get_context("upload_image_retention_days")
                        or _DEFAULT_IMAGE_RETENTION_DAYS)

        self.bucket = s3.Bucket(
            self, "UploadsBucket",
            bucket_name=uploads_bucket_name(self.account),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            # User data: a stack delete must not take it with it, and must not
            # leave it orphaned silently either — RETAIN, then delete by hand.
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            # The browser POSTs the file straight here. Only POST, only from the
            # app; reads go through presigned GET URLs, which <img> loads
            # without CORS.
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.POST],
                allowed_origins=resolve_origins(self.node),
                allowed_headers=["*"],
                max_age=3000,
            )],
        )

        self.bucket.add_lifecycle_rule(
            id="ExpireChatImages",
            prefix="images/",
            expiration=Duration.days(retention),
            enabled=True,
        )

        CfnOutput(self, "UploadsBucketName", value=self.bucket.bucket_name)
