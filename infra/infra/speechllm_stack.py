"""SpeechLLm Stack — VieNeu-TTS v3 Turbo on Lambda (container, response_stream).

    ECR repo `vva-speechllm`  <-- CI pushes  vva-speechllm:<git-sha>
              |
              +-- Lambda `vva-speechllm` (3538 MB ~ 2 vCPU, 300s, response_stream)
                       |-- no VPC, no NAT (nhu vva-agent, agent_stack.py:8-14)
                       +-- Function URL, AuthType=AWS_IAM, InvokeMode=RESPONSE_STREAM
                       +-- EventBridge ping /health every 5m (~$0.03/mo, nhu Kimodo)

    Container image vuot tran 250 MB cua zip (522 MB weights + 86 MB codec), nen
    phai container. Khong doi cach tinh tien — zip hay container deu tinh request + GB-giay.

    TWO-STEP BOOTSTRAP, copy tu VvaAgentStack (agent_stack.py:16-42) vi ly do y het:
    container function khong tao duoc truoc khi image ton tai, va image khong push
    duoc truoc khi repo ton tai. Nen:

        # 1. repository only, once
        cdk deploy VvaSpeechllmStack -c speechllm_bootstrap=1

        # 2. CI builds and pushes vva-speechllm:<sha>  (deploy-speechllm.yml)

        # 3. the function, pinned to a tag that now exists
        cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha>

    IMAGE TAGS ARE IMMUTABLE, never `latest`. Ly do nhu agent_stack.py:34-36:
    floating tag lam mat kha nang biet build nao dang chay va khong rollback duoc.

    NO VPC. SpeechLLm goi la API cua dich vu Lambda, khong phai noi chuyen qua VPC.
    Cau hinh VPC chi quyet dinh ham RA NGOAI toi dau. Agent co y ngoai VPC de
    tranh NAT (~$32/mo) va ENI attach moi cold start; SpeechLLm theo dung khuon do.

    SECURITY — chi agent goi duoc. Function URL AuthType=AWS_IAM => request khong
    ky bi Lambda tu choi truoc khi ham chay (403, khong ton luot invoke). Resource
    policy tren ham TTS cho phep ca hai action ma Function URL moi doi tu 10/2025:

        Principal: <ARN role thuc thi cua vva-agent>
        Action:    lambda:InvokeFunctionUrl  VA  lambda:InvokeFunction
        Condition: lambda:FunctionUrlAuthType = AWS_IAM

    Thieu mot trong hai la hong va loi tra ve khong noi ro thieu cai nao.
    Agent cung can IAM policy tuong ung (them o VvaAgentStack, D3).

    COST: 100 audio/ngay x 20s = 60k giay/thang = 207.3k GB-giay + ping 1.5k = 208.8k
    GB-giay — con trong free tier 400k => $0. Neu het free tier: $2.78 (arm64) /
    $3.48 (x86). Bang nay gia dinh tong hop ~ realtime (D6 kiem). Cham gap doi
    thi nhan doi — van duoi free tier, tran ~$7.

    COLD START 25s (model 13s + pre-enrol) => EventBridge ping /health moi 5 phut
    thay vi Provisioned Concurrency ($30-37/mo, gap hon nghin lan).

    D6 GATE: memory 3538 MB ~ 2 vCPU la TAM. D6 do 1769/3538/5308 MB x 3 lan/muc,
    Max Memory Used, arm64 vs x86_64, va tong hop co nhanh bang realtime khong
    (78s cho 84.5s audio local). Ket qua quay lai sua memory/kien truc o day
    va timeout o agent_stack. Neu cham hon realtime: DUNG, tinh lai, dung bat
    cho user.
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
    aws_scheduler as scheduler,
)
from constructs import Construct

_REPOSITORY_NAME = "vva-speechllm"

# Default memory TAM — D6 chot. Lambda cap CPU theo RAM: 1769 MB = 1 vCPU,
# 3538 MB = 2 vCPU. De tham so CDK de D6 chinh khong hardcode.
_DEFAULT_MEMORY_MB = 3538

# Timeout 300s: cau dai nhat do duoc ton 78s tong hop, + graph 5-10s = ~90s,
# de du phong va khop agent timeout (D3 nang agent tu 120s len 300s).
_DEFAULT_TIMEOUT_S = 300


class SpeechllmStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        voice_bucket_name: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        ctx = self.node.try_get_context
        image_tag = ctx("speechllm_image_tag")
        bootstrap = str(ctx("speechllm_bootstrap") or "").strip() in ("1", "true", "yes")

        # Memory la tham so CDK, mac dinh 3538 — D6 se chinh, dung hardcode.
        # Cho phep -c speechllm_memory=1769 / 3538 / 5308 de do.
        try:
            memory_mb = int(ctx("speechllm_memory") or _DEFAULT_MEMORY_MB)
        except (ValueError, TypeError):
            Annotations.of(self).add_error(
                f"speechllm_memory phai la so nguyen MB, nhan duoc: {ctx('speechllm_memory')!r}"
            )
            memory_mb = _DEFAULT_MEMORY_MB

        # Kien truc: arm64 mac dinh (re hon 20%), x86_64 la phuong an lui.
        # D6 do roi chot. Cho phep -c speechllm_arch=x86_64 de so sanh.
        arch_str = str(ctx("speechllm_arch") or "arm64").strip().lower()
        if arch_str in ("arm64", "arm", "aarch64"):
            architecture = lambda_.Architecture.ARM_64
        elif arch_str in ("x86_64", "x86", "amd64"):
            architecture = lambda_.Architecture.X86_64
        else:
            Annotations.of(self).add_error(
                f"speechllm_arch khong hop le: {arch_str!r}. Dung 'arm64' hoac 'x86_64'."
            )
            architecture = lambda_.Architecture.ARM_64

        # Agent role ARN de han che Function URL — chi agent goi duoc.
        # Truyen luc deploy that:
        #   cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> -c agent_role_arn=arn:aws:iam::...:role/vva-agent-xxx
        # Lay tu VvaAgentStack output: AgentFunctionName -> role ARN suy ra.
        # Neu thieu, synth van pass (de cdk synth khong can deploy agent truoc)
        # nhung deploy se chua co bao ve — Annotations.add_error de bao.
        agent_role_arn = ctx("agent_role_arn") or ""

        # ── ECR repository ──────────────────────────────────────────────

        self.repository = ecr.Repository(
            self, "SpeechllmRepository",
            repository_name=_REPOSITORY_NAME,
            # Immutable: pushing over existing tag lam mat kha nang biet build
            # nao dang chay va khong rollback duoc. CI re-run cung commit se
            # fail to thay vi lang le thay the thu dang deploy.
            image_tag_mutability=ecr.TagMutability.IMMUTABLE,
            image_scan_on_push=True,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="Keep last 10 images; each is ~1.1 GB (weights baked)",
                    max_image_count=10,
                ),
            ],
            # RETAIN: xoa stack khong duoc xoa images ham dang dung, cung khong
            # xoa lich su de rollback.
            removal_policy=RemovalPolicy.RETAIN,
        )

        CfnOutput(
            self, "SpeechllmRepositoryUri",
            value=self.repository.repository_uri,
            description="Push vva-speechllm:<git-sha> here (deploy-speechllm.yml)",
        )

        if bootstrap:
            # Step 1: repository only, chua co image
            CfnOutput(
                self, "BootstrapNote",
                value="Repository only. Push an image, then deploy with "
                      "-c speechllm_image_tag=<sha>",
            )
            self.fn = None
            self.fn_url = None
            return

        if not image_tag:
            raise ValueError(
                "VvaSpeechllmStack needs the image tag to deploy.\n\n"
                "  First time (repository only):\n"
                "    cdk deploy VvaSpeechllmStack -c speechllm_bootstrap=1\n\n"
                "  Every time after that:\n"
                "    cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<git-sha>\n\n"
                "Neither flag is NOT treated as 'skip the function'. A deploy "
                "that quietly omitted it would DELETE the live one, and "
                "CloudFormation would call that a success — same failure "
                "VvaAgentStack guards against."
            )

        # ── Function ────────────────────────────────────────────────────

        # D5d: voice_bucket_name tu AssetStack.voice_bucket — SpeechLLm doc
        # S3 key (voice_vi_key / voice_en_key) truc tiep qua IAM read-only.
        # Local dev khi thieu bucket thi fallback ve voices/*.wav.
        voice_bucket_env = voice_bucket_name or ctx("voice_bucket_name") or ""
        env_vars = {
            "LOG_LEVEL": "INFO",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            # Khong co VIENEU_TTS_URL o day — day la callee, khong phai caller.
            # Agent se tro toi Function URL cua ham nay (D3).
        }
        if voice_bucket_env:
            env_vars["VOICE_BUCKET"] = voice_bucket_env
        self.fn = lambda_.DockerImageFunction(
            self, "Speechllm",
            function_name="vva-speechllm",
            # from_ecr, NOT from_image_asset: from_image_asset build image
            # trong `cdk synth` tren may deploy — can Docker daemon local.
            # Owner chon CI-only builds tu 21-08, image da build san va CDK
            # chi reference. Giong VvaAgentStack.
            code=lambda_.DockerImageCode.from_ecr(
                self.repository, tag_or_digest=image_tag,
            ),
            architecture=architecture,
            # Memory la tham so CDK — D6 se do 1769/3538/5308 va chot muc nho
            # nhat khong cham hon. Dung hardcode.
            memory_size=memory_mb,
            timeout=Duration.seconds(_DEFAULT_TIMEOUT_S),
            environment=env_vars,
            description="VieNeu-TTS v3 Turbo — /synthesize/stream, NDJSON, response_stream",
        )

        # ── IAM: read voices from private bucket (D5d) ───────────────
        # Bucket rieng, private, KHONG CloudFront — chi SpeechLLm doc qua S3.
        # Neu voice_bucket_env thieu (synth don le truoc khi AssetStack), van
        # tao ham nhung ghi warning — deploy that phai truyen dung.
        if voice_bucket_env:
            # ListBucket on bucket, GetObject on prefix (voices/* and characters/*)
            # The bucket holds only voices, so ListBucket + GetObject on "*" is fine;
            # scope to "arn:aws:s3:::bucket/*" for objects, bucket ARN for List.
            self.fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[f"arn:aws:s3:::{voice_bucket_env}"],
            ))
            self.fn.add_to_role_policy(iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{voice_bucket_env}/*"],
            ))
        else:
            Annotations.of(self).add_warning(
                "VvaSpeechllmStack: thieu voice_bucket_name — SpeechLLm se fallback ve voices/*.wav local. "
                "Deploy that truyen ten bucket tu VvaAssetStack:\n"
                "  cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> "
                "-c voice_bucket_name=<from VvaAssetStack output VoiceBucketName>\n"
                "Hoac qua app.py: SpeechllmStack(..., voice_bucket_name=asset_stack.voice_bucket.bucket_name)"
            )

        # ── Function URL: AWS_IAM + RESPONSE_STREAM ─────────────────────

        self.fn_url = self.fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
            invoke_mode=lambda_.InvokeMode.RESPONSE_STREAM,
        )

        CfnOutput(
            self, "SpeechllmFunctionUrl",
            value=self.fn_url.url,
            description="VIENEU_TTS_URL for VvaAgentStack (SigV4 signed)",
        )
        CfnOutput(
            self, "SpeechllmFunctionName",
            value=self.fn.function_name,
        )
        CfnOutput(
            self, "SpeechllmImageTag",
            value=image_tag,
        )
        CfnOutput(
            self, "SpeechllmMemory",
            value=str(memory_mb),
            description="D6 chot — TAM, chua phai final",
        )
        CfnOutput(
            self, "SpeechllmArch",
            value=arch_str,
            description="arm64 (re 20%) vs x86_64 — D6 chot",
        )

        # ── Resource policy: chi agent duoc goi ─────────────────────────
        # Tu 10/2025 Function URL moi doi ca hai quyen, thieu mot la hong va
        # loi khong noi ro thieu cai nao. Ca hai deu can. Template phai co ca
        # hai permission voi InvokeMode RESPONSE_STREAM de `cdk synth` sach
        # (nghiem thu D2) — nen tao permission ngay ca khi agent_role_arn chua
        # co, voi placeholder, va chi canh bao thay vi block synth.
        #
        # Giong VvaAssetStack/VvaAgentStack: app.py construct moi stack moi lan
        # `cdk synth`/`cdk list`, nen raise o day se lam hong lenh khong lien
        # quan. Dung add_warning de deploy that van thay canh bao nhung synth
        # don le van pass; add_error chi khi thieu image_tag (truong hop xoa ham).
        if not agent_role_arn:
            Annotations.of(self).add_warning(
                "VvaSpeechllmStack: thieu agent_role_arn — Function URL dang dung placeholder "
                f"arn:aws:iam::{self.account}:root. Deploy that phai thay bang ARN that cua vva-agent:\n"
                "  cdk deploy VvaSpeechllmStack -c speechllm_image_tag=<sha> "
                "-c agent_role_arn=<ARN role thuc thi cua vva-agent>\n"
                "Placeholder van tao du 2 permission (InvokeFunctionUrl + InvokeFunction) de cdk synth sach,"
                " nhung chua han che dung principal — chua du bao ve that su."
            )
            # Placeholder de template van co du 2 action cho nghiem thu D2
            placeholder = iam.AccountPrincipal(self.account)
            self.fn.add_permission(
                "AllowAgentInvokeFunctionUrl",
                principal=placeholder,
                action="lambda:InvokeFunctionUrl",
                function_url_auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
            )
            self.fn.add_permission(
                "AllowAgentInvokeFunction",
                principal=placeholder,
                action="lambda:InvokeFunction",
            )
        else:
            # Dua tren agent_role_arn cu the
            self.fn.add_permission(
                "AllowAgentInvokeFunctionUrl",
                principal=iam.ArnPrincipal(agent_role_arn),
                action="lambda:InvokeFunctionUrl",
                function_url_auth_type=lambda_.FunctionUrlAuthType.AWS_IAM,
            )
            self.fn.add_permission(
                "AllowAgentInvokeFunction",
                principal=iam.ArnPrincipal(agent_role_arn),
                action="lambda:InvokeFunction",
            )

        # ── Warmer: EventBridge ping /health moi 5 phut ──────────────────
        # Cold start 25s (model 13s + pre-enrol). Provisioned concurrency
        # $30-37/mo, gap hon nghin lan ping $0.03/mo. Dung Scheduler nhu
        # VvaCrudApiStack._add_warmer — Scheduler -> warmer lambda -> invoke
        # speechllm voi payload HTTP 2.0 (LWA can HTTP shape, khong phai JSON
        # event thuong cua EventBridge).
        self._add_warmer()

    def _add_warmer(self) -> None:
        """Ping /health moi 5 phut de giu model warm, qua Scheduler + warmer lambda."""
        warmer = lambda_.Function(
            self, "Warmer",
            function_name="vva-speechllm-warmer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(
                "import json, os, boto3\n"
                "_lambda = boto3.client('lambda')\n"
                "def _event(path):\n"
                "    return {\n"
                "        'version': '2.0',\n"
                "        'rawPath': path,\n"
                "        'rawQueryString': '',\n"
                "        'headers': {'host': 'warmer.internal'},\n"
                "        'requestContext': {\n"
                "            'http': {'method': 'GET', 'path': path,\n"
                "                     'protocol': 'HTTP/1.1', 'sourceIp': '127.0.0.1'},\n"
                "        },\n"
                "        'isBase64Encoded': False,\n"
                "    }\n"
                "def handler(event, context):\n"
                "    name = os.environ['TARGET_FN']\n"
                "    try:\n"
                "        r = _lambda.invoke(FunctionName=name, InvocationType='RequestResponse',\n"
                "                           Payload=json.dumps(_event('/health')).encode())\n"
                "        raw = (r['Payload'].read() or b'{}').decode('utf-8', 'replace')\n"
                "        head, _ = json.JSONDecoder().raw_decode(raw.lstrip())\n"
                "        code = head.get('statusCode')\n"
                "        print(json.dumps({'warmer': name, 'statusCode': code}))\n"
                "        return {'statusCode': code}\n"
                "    except Exception as exc:\n"
                "        print(json.dumps({'warmer': name, 'error': str(exc)}))\n"
                "        return {'error': str(exc)}\n"
            ),
            environment={
                "TARGET_FN": self.fn.function_name,
            },
            memory_size=128,
            timeout=Duration.seconds(30),
            description="Keeps vva-speechllm warm (model + pre-enrol) via /health ping",
        )
        # Warmer duoc phep invoke speechllm (khac voi agent permission o tren)
        self.fn.grant_invoke(warmer)

        scheduler_role = iam.Role(
            self, "WarmerSchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        warmer.grant_invoke(scheduler_role)

        scheduler.CfnSchedule(
            self, "WarmerSchedule",
            name="vva-speechllm-warmer",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF",
            ),
            # Moi 5 phut, 24/7 — khac warmer cua crud_api (chi gio hanh chinh)
            # vi TTS cold start 25s dat hon va khong phu thuoc Neon. $0.03/thang.
            schedule_expression="rate(5 minutes)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=warmer.function_arn,
                role_arn=scheduler_role.role_arn,
            ),
            description="Ping vva-speechllm /health every 5m to avoid 25s cold start",
        )
