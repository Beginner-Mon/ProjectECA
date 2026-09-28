"""Agent Stack — the LangGraph service and /chat, as a Lambda container image.

    ECR repo `vva-agent`  ←── CI pushes  vva-agent:<git-sha>
              │
              └── Lambda `vva-agent` (1024 MB, 120s)
                       ├─ Neon (pooled DSN from SSM)
                       ├─ DeepSeek / Gemini (keys from SSM)
                       └─ no VPC, no NAT

Shaped like VvaCrudApiStack — no VPC configuration, because Neon is a public TLS
endpoint and putting the function in the project's private isolated subnets would
need either a NAT gateway (~$32/month) or interface endpoints purely to reach
something already on the internet, while adding ENI attachment to every cold
start.

TWO-STEP BOOTSTRAP, and the reason it is not one step
------------------------------------------------------
A container function cannot be created before an image exists, and the image
cannot be pushed before the repository exists. So:

    # 1. repository only, once
    cdk deploy VvaAgentStack -c agent_bootstrap=1

    # 2. CI builds and pushes vva-agent:<sha>  (deploy-agent.yml)

    # 3. the function, pinned to a tag that now exists
    cdk deploy VvaAgentStack -c agent_image_tag=<sha>

Passing NEITHER raises at synth rather than deploying something. That is
deliberate and copied from crud_api_stack.py's Cognito check: a `cdk deploy` that
silently omitted the function would DELETE a live one, and CloudFormation would
report it as a successful deployment.

IMAGE TAGS ARE IMMUTABLE, never `latest`. `kimodo:latest` in
deploy-production.yml is the counter-example: with a floating tag you cannot tell
which build is running and cannot roll back to a previous one.

NO Function URL. VvaRestApiStack is the one front door; it holds the Cognito
authorizer and the stage throttle, and a second entrance would be one URL away
from bypassing both. Response streaming reaches this function through API
Gateway's AWS_PROXY integration with ResponseTransferMode.STREAM — proved end to
end by the Phase 0 spike, see infra/infra/streaming_probe_stack.py.
"""

from __future__ import annotations

from aws_cdk import (
    Annotations,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_ecr as ecr,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_ssm as ssm,
)
from constructs import Construct

from infra.origins import resolve as resolve_origins

_REPOSITORY_NAME = "vva-agent"

# Fixed table name rather than a construct reference. KimodoEcsStack (which owns
# the real `dynamodb.Table` construct) is built AFTER this stack in app.py — see
# app.py's stack order — so a `motion_table.grant_read_write_data(fn)` call here
# is structurally impossible without reordering the stacks. Both sides agree on
# the name instead: kimodo_ecs_stack.py hardcodes the identical string. Cost if
# they drift: an IAM grant that points at a table that does not exist, caught
# immediately by the first `read_status`/`enqueue` call failing with
# AccessDenied rather than silently.
_MOTION_TABLE_NAME = "vva-motion-jobs"

# The CloudFront PRIVATE signing key, one degree more sensitive than the public
# key asset_stack.py registers: asset_stack.py's docstring calls the private
# half "kept outside CDK entirely — it never appears here", and that holds here
# too. This env var carries the SSM parameter NAME, not the key — motion_status.py
# resolves it at call time via ssm:GetParameter(WithDecryption=True), the same
# shape llm.py's _secret_from_ssm already uses for the LLM API keys. The key
# material itself never touches a CDK context value or the CloudFormation
# template.
_DEFAULT_MOTION_SIGNING_KEY_PARAM = "/vva/motion/signing-key-pem"

# Ruling R24: same treatment as the signing key above, and for the same reason
# — the brief that introduced this stack's motion wiring named it explicitly
# as one of three secrets to come "from SSM", and an earlier version of this
# file baked the raw value into a CDK context flag instead, which lands it in
# the Lambda's Environment.Variables as a literal in the CloudFormation
# template. This env var carries the SSM SecureString parameter NAME;
# nodes/kimodo.py resolves it at call time (`_resolve_hash_secret` /
# `_hash_secret_from_ssm_cached`), env-first-then-SSM, the same precedence
# llm.py's `_resolve_api_key` uses.
_DEFAULT_MOTION_HASH_SECRET_PARAM = "/vva/motion/hash-secret"

# The POOLED Neon endpoint, same as the CRUD function and for the same measured
# reason — see the long note in crud_api_stack.py. A Lambda scales out to N
# ephemeral containers each opening its own connections, which is exactly the
# churn PgBouncer exists to absorb; and anything session-scoped goes through
# PostgresClient.user_scope(), which is transaction-scoped and survives it.
_DEFAULT_DSN_PARAM = "/vva/neon/dsn-pooler"

# Where CI records the image it just rolled onto the function.
#
# CDK owns the shape, CI owns the code: deploy-agent.yml pushes an image
# and calls update-function-code, which never touches this template. The
# template therefore remembers whichever tag the last `cdk deploy` was
# given, and drifts further behind with every CI run - on 25/09 it still
# said a6c99cba while the function had been running 23e5e59a for days.
#
# That drift is a loaded gun. A `cdk deploy VvaAgentStack` copied from an
# older runbook rolls the function BACK to a week-old build, silently and
# successfully: on 23/09 that would have taken VIENEU_TTS_URL and the
# spoken-length cap with it. Reading the tag from here means a deploy
# without an explicit -c agent_image_tag lands on what is actually running.
#
# Resolved by CloudFormation AT DEPLOY TIME (an
# AWS::SSM::Parameter::Value<String> parameter), not at synth time: a
# synth-time lookup caches into cdk.context.json and goes stale, which is
# the same gun pointed at the same foot.
_IMAGE_TAG_PARAM = "/vva/agent/image-tag"

# LLM credentials as SSM SecureStrings rather than environment variables.
# Lambda environment variables are plaintext in the CloudFormation template, and
# CloudFormation's {{resolve:ssm-secure}} dynamic reference is not supported for
# them. llm.py reads these at run time; see _secret_from_ssm there.
_DEFAULT_DEEPSEEK_PARAM = "/vva/llm/deepseek-api-key"
_DEFAULT_GEMINI_PARAM = "/vva/llm/gemini-api-keys"

# Same production pool the CRUD function verifies against. Public identifiers —
# they ship inside the frontend bundle — so they are defaults rather than
# required flags.
_DEFAULT_COGNITO_POOL_ID = "us-east-1_6mSqgs4BA"
_DEFAULT_COGNITO_CLIENT_ID = "1rsd1gn5i3heshuo0hf1s6cvm"


class AgentStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        asset_base_url: str | None = None,
        **kwargs,
    ) -> None:
        """
        asset_base_url: the motions CDN's public origin, e.g.
            f"https://{distribution.distribution_domain_name}". Passed in from
            AssetStack (built before this stack in app.py — see the ordering
            comment there), the same way RestApiStack receives crud_fn/
            characters_fn.

            Optional in the SIGNATURE so the two-step bootstrap can construct
            this stack before there is a function at all — but required by the
            time one is created, and enforced below with
            Annotations.add_error.

            An earlier version of this docstring claimed motion_status() "would
            fail loudly at call time (KeyError on ASSET_BASE_URL)". It could
            not: the environment variable is set unconditionally, just to "".
            The real failure was silent and downstream — sign_url() would build
            "/motions/x.bvh" with no origin, and the browser would fetch a URL
            that goes nowhere. Same class of problem for motion_key_pair_id: an
            empty key pair id signs a URL CloudFront answers with 403. Both
            failures surface as a broken avatar in production, hours after a
            deploy that CloudFormation called a success. Hence a synth-time
            error instead.
        """
        super().__init__(scope, construct_id, **kwargs)

        ctx = self.node.try_get_context
        image_tag = ctx("agent_image_tag")
        bootstrap = str(ctx("agent_bootstrap") or "").strip() in ("1", "true", "yes")

        # ── ECR repository ──────────────────────────────────────────────

        self.repository = ecr.Repository(
            self, "AgentRepository",
            repository_name=_REPOSITORY_NAME,
            # Immutable: pushing over an existing tag is how "which build is
            # running?" becomes unanswerable and rollback becomes impossible.
            # It also makes a CI re-run of the same commit fail loudly rather
            # than silently replace what is deployed.
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep the last 10 images; each is ~1.2 GB",
                    max_image_count=10,
                ),
            ],
            # RETAIN: destroying the stack must not delete the images the
            # running function is built from, nor the history to roll back to.
            removal_policy=RemovalPolicy.RETAIN,
        )

        CfnOutput(
            self, "AgentRepositoryUri",
            value=self.repository.repository_uri,
            description="Push vva-agent:<git-sha> here (deploy-agent.yml)",
        )

        if bootstrap:
            # Step 1 of the bootstrap. The function is deliberately absent
            # because no image exists yet.
            CfnOutput(
                self, "BootstrapNote",
                value="Repository only. Push an image, then deploy with "
                      "-c agent_image_tag=<sha>",
            )
            self.fn = None
            return

        # No explicit tag: take the one CI last deployed, from SSM. The flag
        # still wins when given - that is how a deliberate rollback to a named
        # build is expressed.
        #
        # This used to raise. Raising stopped a deploy from DELETING the
        # function (an absent tag was never "skip the function"), and that
        # danger is gone either way now - but it also forced every caller to
        # supply a tag by hand, and a hand-supplied tag is how the function got
        # rolled backwards. A missing parameter still fails the deploy, loudly,
        # from CloudFormation.
        if not image_tag:
            image_tag = ssm.StringParameter.value_for_string_parameter(
                self, _IMAGE_TAG_PARAM,
            )

        # ── Function ────────────────────────────────────────────────────

        allowed_origins = resolve_origins(self.node)
        dsn_param = ctx("crud_dsn_param") or _DEFAULT_DSN_PARAM
        deepseek_param = ctx("deepseek_param") or _DEFAULT_DEEPSEEK_PARAM
        gemini_param = ctx("gemini_param") or _DEFAULT_GEMINI_PARAM
        motion_signing_key_param = (
            ctx("motion_signing_key_param") or _DEFAULT_MOTION_SIGNING_KEY_PARAM
        )
        # R24: same *_PARAM shape as motion_signing_key_param above — the raw
        # secret never becomes a CDK context value or a template literal, only
        # its SSM parameter name does. nodes/kimodo.py resolves it at call time.
        motion_hash_secret_param = (
            ctx("motion_hash_secret_param") or _DEFAULT_MOTION_HASH_SECRET_PARAM
        )
        # motion_key_pair_id is NOT a secret — it names which trusted public
        # key CloudFront should verify against, the public half asset_stack.py
        # registers. It only exists after VvaAssetStack has been deployed once
        # (CloudFront assigns it), so it is supplied the same way
        # motion_public_key_pem is: a `-c` flag at deploy time, not a construct
        # reference.
        motion_key_pair_id = ctx("motion_key_pair_id") or ""

        # speechllm_url is the opt-in switch for TTS (D3). api/main.py's
        # tts_enabled() treats presence of VIENEU_TTS_URL as THE on-switch —
        # no separate ENABLE_TTS flag — so the key must be ABSENT, not "",
        # when this context value is not passed. That is the kill switch:
        # any deploy that omits -c speechllm_url keeps TTS off, the same way
        # omitting agent_image_tag keeps the function from being replaced.
        # Plain context string, not a cross-stack reference to
        # VvaSpeechllmStack — that stack's Function URL is only known once it
        # has been deployed, exactly like motion_key_pair_id above.
        #
        # rstrip a trailing "/" so a context value like ".../on.aws/" cannot
        # produce a "//synthesize/stream" request: VieNeuTTSClient.base_url
        # already does the same rstrip defensively (services/vieneu_tts/
        # client.py), but normalising here too means a bad value never even
        # reaches the template.
        speechllm_url = (ctx("speechllm_url") or "").strip().rstrip("/")
        if speechllm_url and not speechllm_url.startswith("https://"):
            Annotations.of(self).add_error(
                "VvaAgentStack's speechllm_url must start with https:// — got "
                f"{speechllm_url!r}. This is the vva-speechllm Function URL "
                "(*.lambda-url.<region>.on.aws), which the client SigV4-signs "
                "only when it looks like one (see "
                "services/vieneu_tts/client.py::_is_lambda_url)."
            )

        # ── Deploy readiness, loudly ────────────────────────────────────
        # Both of these used to default to "" and synthesize cleanly, deploy
        # cleanly, and then break motion in production: an empty key pair id
        # produces a signed URL CloudFront answers with 403, and an empty origin
        # produces a URL with no host at all. Neither raises anywhere — the
        # environment variable is present, merely empty — so the first signal is
        # a broken avatar, hours after a green deployment.
        #
        # Annotations.add_error, NOT raise, and for the reason asset_stack.py
        # spells out for its own public-key check: app.py constructs this stack
        # on every `cdk` invocation, so raising would break `cdk list`,
        # `cdk diff` and `cdk deploy VvaVpcStack` — commands with nothing to do
        # with motion. add_error fails synth/deploy for THIS stack only.
        #
        # Reached only past the `bootstrap` early-return and the image_tag
        # check above, so step 1 of the two-step bootstrap is unaffected: there
        # is no function then, and nothing to configure wrongly.
        if not motion_key_pair_id:
            Annotations.of(self).add_error(
                "VvaAgentStack needs the CloudFront key pair id that verifies "
                "the motion signed URLs it hands out. Pass:\n"
                "  cdk deploy VvaAgentStack -c agent_image_tag=<sha> "
                '-c motion_key_pair_id="K2EXAMPLE..."\n'
                "The id is assigned by CloudFront and only exists after "
                "VvaAssetStack has been deployed once with "
                "-c motion_public_key_pem. Read it back with:\n"
                "  aws cloudfront list-public-keys "
                "--query 'PublicKeyList.Items[].{Id:Id,Name:Name}'\n"
                "Without it every GET /motion/{job_id} returns a URL "
                "CloudFront answers with 403."
            )

        if not asset_base_url:
            Annotations.of(self).add_error(
                "VvaAgentStack needs asset_base_url — the motions CDN origin "
                "that signed URLs are built on. app.py passes it from "
                "VvaAssetStack's distribution domain name; a direct "
                "construction must do the same.\n"
                "Without it motion_status() returns URLs with no host, and "
                "nothing raises: ASSET_BASE_URL is set, just empty."
            )

        self.cognito_pool_id = ctx("cognito_user_pool_id") or _DEFAULT_COGNITO_POOL_ID
        cognito_client_id = ctx("cognito_app_client_id") or _DEFAULT_COGNITO_CLIENT_ID
        cognito_region = ctx("cognito_region") or self.region

        # Built as a variable, not a dict literal, so VIENEU_TTS_URL can be left
        # out of the CloudFormation template entirely rather than set to "" —
        # Python has no `{"K": v if cond}`-style conditional key inside a `{...}`
        # literal, so the only way to guarantee absence is to add the key after
        # the fact, under `if`.
        agent_environment = {
            "VVA_PG_DSN_PARAM": dsn_param,
            "DEEPSEEK_API_KEY_PARAM": deepseek_param,
            "GEMINI_API_KEYS_PARAM": gemini_param,
            "AUTH_PROVIDER": "cognito",
            "COGNITO_REGION": cognito_region,
            "COGNITO_USER_POOL_ID": self.cognito_pool_id,
            "COGNITO_APP_CLIENT_ID": cognito_client_id,
            "ALLOWED_ORIGINS": ",".join(allowed_origins),
            "LOG_LEVEL": "INFO",
            # No cache. Short-term memory is a cache over PostgreSQL, and
            # the plan is to run without one for a month and measure the
            # Neon CU-hours it would have saved before paying for anything.
            # Switching later is this value plus STM_TABLE — the code and
            # its tests are already in place. See shared/stm.py.
            "STM_BACKEND": ctx("stm_backend") or "none",
            # ENABLE_MCP, EMBEDDING_BACKEND, E5_ONNX_DIR and the LWA
            # settings are baked into the image: they describe what the
            # image IS, not where it is deployed. See agenticRAG/Dockerfile.
            #
            # ── Motion (Task 9, R24) ────────────────────────────────
            # Fixed name, not a construct reference — see _MOTION_TABLE_NAME.
            "MOTION_TABLE": _MOTION_TABLE_NAME,
            # SSM parameter NAMES only, for both secrets — nodes/kimodo.py
            # and motion_status.py resolve them at call time. Neither raw
            # value ever appears in this Lambda's environment or the
            # CloudFormation template (ruling R24).
            "MOTION_HASH_SECRET_PARAM": motion_hash_secret_param,
            "MOTION_SIGNING_KEY_PARAM": motion_signing_key_param,
            "MOTION_KEY_PAIR_ID": motion_key_pair_id,
            "ASSET_BASE_URL": asset_base_url or "",
        }
        if speechllm_url:
            # VIENEU_TTS_URL (D3) — presence is the on-switch, so this key is
            # added ONLY when speechllm_url is non-empty; the else-branch is
            # simply "do nothing", not "set it to something falsy". api/main.py's
            # tts_enabled() would actually still read `""` as off (it does
            # `bool(os.getenv(...).strip())`), so this isn't the only thing
            # standing between a forgotten -c speechllm_url and TTS turning on
            # — but leaving the key out entirely means every OTHER reader agrees
            # too, including a future one that checks `"VIENEU_TTS_URL" in
            # os.environ` instead of truthiness, or a Lambda console operator
            # who greps the environment tab and sees nothing rather than a
            # suspicious empty string.
            #
            # The value is the plain https:// Function URL, unsigned — there is
            # no signed request to precompute at synth time. The client
            # (services/vieneu_tts/client.py::_is_lambda_url) decides, at CALL
            # TIME, whether to SigV4-sign each request by matching this URL's
            # host against `*.lambda-url.*.on.aws`; an unsigned request to that
            # host 403s, which is exactly the case that check exists to avoid.
            agent_environment["VIENEU_TTS_URL"] = speechllm_url

        self.fn = lambda_.DockerImageFunction(
            self, "Agent",
            function_name="vva-agent",
            # from_ecr, NOT from_image_asset. from_image_asset builds the image
            # during `cdk synth`, on the machine running the deploy — which
            # requires a local Docker daemon. Owner chose CI-only builds on
            # 21-08, so the image arrives already built and CDK only references
            # it. This is also the boundary the plan draws: CDK owns the shape of
            # the infrastructure, CI owns the code.
            code=lambda_.DockerImageCode.from_ecr(
                self.repository, tag_or_digest=image_tag,
            ),
            environment=agent_environment,
            # 2048 MB — bumped 14/09/2026 after measured OOM on every
            # retrieval turn (see docs/worklogs/14-09-2026.md, defect #1).
            # 1024 bought ~0.58 vCPU and was sized for "just waiting on DeepSeek"
            # — that assumption broke once the ONNX embedding model (470 MB + 50 MB
            # runtime) is held in memory alongside kb_search results. Cold start
            # Max Memory Used hit 1024/1024 on all needs_retrieval=true turns.
            # 2048 buys ~1.15 vCPU and headroom for 2-3 tool results; re-measure
            # Max Memory Used after fix and consider 1536 if headroom is large.
            # Cost is still ~$0.0006/turn extra vs 1024 (20s * 1GB * $0.0000167).
            memory_size=2048,
            # 300s to match vva-speechllm (D3): a turn with voice = graph
            # (5-10s) + synthesis (up to 78s measured) ~90s, near the old 120s
            # ceiling; hitting it mid-stream kills the agent and the browser
            # loses speech_end. Cost ceiling still matters — streaming is billed
            # for full duration even after client disconnect — but 120s was four
            # times the text-only bad case, not the voice bad case. 300s is the
            # speech case's equivalent, and matches speechllm's own 300s so the
            # caller does not die before the callee.
            timeout=Duration.seconds(300),
            # No reserved concurrency: AWS refuses any reservation that leaves
            # the account under 100 unreserved units, and this account's whole
            # limit is 10. Set one and the deploy fails outright. That limit is
            # shared with Amplify's Cognito triggers, and /chat holds a container
            # for 10-30 SECONDS where the CRUD function holds one for ~100 ms —
            # so a few concurrent chats can throttle sign-in. Raise the quota
            # (the AWS default is 1000) before this sees real traffic, then
            # reserve here.
            description="LangGraph agent — /chat, SSE over API Gateway response streaming",
        )

        # ── IAM: read the DSN and the LLM keys ──────────────────────────

        self.fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter{param}"
                for param in (
                    dsn_param, deepseek_param, gemini_param,
                    motion_signing_key_param, motion_hash_secret_param,
                )
            ],
        ))
        # Scoped by kms:ViaService so this grant cannot be turned on anything
        # that is not SSM.
        self.fn.add_to_role_policy(iam.PolicyStatement(
            actions=["kms:Decrypt"],
            resources=[f"arn:aws:kms:{self.region}:{self.account}:key/*"],
            conditions={"StringEquals": {"kms:ViaService": f"ssm.{self.region}.amazonaws.com"}},
        ))

        # ── IAM: motion job table (Task 9, ruling R3) ───────────────────
        #
        # Built from a fixed ARN rather than `motion_table.grant_read_write_data(fn)`
        # because the construct does not exist in this stack: app.py builds
        # KimodoEcsStack (which owns it) AFTER this stack, so there is no
        # `motion_table` object here to grant from. See _MOTION_TABLE_NAME.
        #
        # Scoped to what this Lambda actually calls (nodes/kimodo.py +
        # api/motion_status.py): GetItem (read_status/worker_alive), PutItem
        # (enqueue), Query (queue_depth, over the status-created_at-index —
        # hence the second resource ARN). UpdateItem/DeleteItem are
        # deliberately excluded: only the GPU worker (kimodo_ecs_stack.py's
        # task role) claims, completes or recovers jobs.
        motion_table_arn = (
            f"arn:aws:dynamodb:{self.region}:{self.account}:table/{_MOTION_TABLE_NAME}"
        )
        self.fn.add_to_role_policy(iam.PolicyStatement(
            actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"],
            resources=[motion_table_arn, f"{motion_table_arn}/index/*"],
        ))

        # ── IAM: invoke SpeechLLm Function URL (D3, D2 Security) ──────────
        #
        # vva-speechllm's Function URL is AuthType=AWS_IAM with resource policy
        # requiring BOTH lambda:InvokeFunctionUrl and lambda:InvokeFunction
        # (10/2025, missing one fails silently). This IAM grant is the caller
        # side; the resource policy on vva-speechllm (speechllm_stack.py) is
        # the callee side. Both must exist or the call 403s before the function
        # runs (no invoke cost). Fixed ARN by name — speechllm_stack is built
        # AFTER this one in app.py (like the motion table), so no construct
        # reference is possible without reordering. Name is hard-coded in both
        # stacks: speechllm_stack.py's function_name == "vva-speechllm".
        speechllm_fn_arn = f"arn:aws:lambda:{self.region}:{self.account}:function:vva-speechllm"
        self.fn.add_to_role_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunctionUrl", "lambda:InvokeFunction"],
            resources=[speechllm_fn_arn, f"{speechllm_fn_arn}:*"],
        ))

        CfnOutput(self, "AgentFunctionName", value=self.fn.function_name)
        CfnOutput(self, "AgentImageTag", value=image_tag)
