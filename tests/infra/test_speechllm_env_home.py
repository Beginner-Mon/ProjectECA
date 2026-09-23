# -*- coding: utf-8 -*-
"""HOME + XDG/NUMBA/MPL/TORCH cache redirects on vva-speechllm.

Lambda leaves HOME unset, so a library on the voice-cloning path that
resolves HOME itself lands on "/home/sbx_user<uid>" — read-only on Lambda,
only /tmp is writable. The failure mode is a 422 from /synthesize/stream at
voice-ENCODING time ("[Errno 30] Read-only file system:
'/home/sbx_user1051'"), not at model load (HF_HOME=/opt/hf-cache in the
Dockerfile already covers the model cache). This was applied by hand to the
live function on 2026-09-23 to unblock TTS immediately and is codified here
so the next `cdk deploy VvaSpeechllmStack` does not erase it.
"""

from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from infra.speechllm_stack import SpeechllmStack

_ENV = cdk.Environment(account="244203483654", region="us-east-1")

_AGENT_ROLE = "arn:aws:iam::244203483654:role/vva-agent-test-role"


@pytest.fixture
def speech_template():
    app = cdk.App(context={
        "speechllm_image_tag": "deadbeef",
        "agent_role_arn": _AGENT_ROLE,
    })
    stack = SpeechllmStack(app, "Speech", env=_ENV)
    return Template.from_stack(stack)


def _speechllm_env(speech_template) -> dict:
    body = speech_template.to_json()
    fn = next(
        r["Properties"] for r in body["Resources"].values()
        if r["Type"] == "AWS::Lambda::Function"
        and r["Properties"].get("FunctionName") == "vva-speechllm"
    )
    return fn["Environment"]["Variables"]


@pytest.mark.unit
def test_home_redirected_to_tmp(speech_template):
    env = _speechllm_env(speech_template)
    assert env["HOME"] == "/tmp"


@pytest.mark.unit
def test_cache_dirs_redirected_to_tmp(speech_template):
    env = _speechllm_env(speech_template)
    assert env["XDG_CACHE_HOME"] == "/tmp/.cache"
    assert env["XDG_DATA_HOME"] == "/tmp/.local/share"
    assert env["XDG_CONFIG_HOME"] == "/tmp/.config"
    assert env["NUMBA_CACHE_DIR"] == "/tmp/numba"
    assert env["MPLCONFIGDIR"] == "/tmp/mpl"
    assert env["TORCH_HOME"] == "/tmp/torch"


@pytest.mark.unit
def test_existing_env_vars_untouched(speech_template):
    """The HOME/XDG fix must not clobber the pre-existing offline/log vars."""
    env = _speechllm_env(speech_template)
    assert env["LOG_LEVEL"] == "INFO"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"
