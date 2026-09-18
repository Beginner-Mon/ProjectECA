# -*- coding: utf-8 -*-
"""T10 — deploy-speechllm.yml: build without AWS on feature, AWS only on release.

Read the workflow file itself rather than running it: what matters is the
static shape — which jobs can reach credentials. A `configure-aws-credentials`
step reachable from feature/tts-streaming would hand deploy power to anyone
who can push that branch, while the OIDC trust policy names only `release`
(and kimodo-release). The `if: github.ref == 'refs/heads/release'` gates
are the enforcement.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github" / "workflows" / "deploy-speechllm.yml"
)

_RELEASE_GATE = "refs/heads/release"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _iter_steps(workflow: dict):
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            yield job_name, job, step


@pytest.mark.unit
def test_every_credentials_step_is_release_gated(workflow):
    """No job reaches configure-aws-credentials unless the branch is release —
    at job level or at step level."""
    found = 0
    for job_name, job, step in _iter_steps(workflow):
        uses = str(step.get("uses") or "")
        if "configure-aws-credentials" not in uses:
            continue
        found += 1
        gate = str(job.get("if") or "") + " " + str(step.get("if") or "")
        assert _RELEASE_GATE in gate, (
            f"job {job_name!r} reaches configure-aws-credentials without a "
            f"{_RELEASE_GATE} gate"
        )
    assert found > 0, "no credentials step found — the release lane lost AWS?"


@pytest.mark.unit
def test_build_lane_needs_no_aws(workflow):
    """The always-run build job contains no AWS step at all (no credentials,
    no ECR login, no lambda call) — it must pass on feature branches that
    the OIDC trust policy never heard of."""
    jobs = workflow.get("jobs") or {}
    assert "build" in jobs, "expected a no-AWS `build` job"
    text = str(jobs["build"])
    for fragment in (
        "configure-aws-credentials",
        "amazon-ecr-login",
        "aws lambda",
        "aws ecr",
    ):
        assert fragment not in text, f"`build` job reaches AWS via {fragment!r}"


@pytest.mark.unit
def test_smoke_checks_state_not_invocation(workflow):
    """Since T9 the CI role is Denied from invoking the function — the old
    `aws lambda invoke` smoke would fail on a healthy deploy. The smoke
    must read state (get-function) instead."""
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "aws lambda invoke" not in text
    assert "LastUpdateStatus" in text
    assert "Configuration.State" in text
