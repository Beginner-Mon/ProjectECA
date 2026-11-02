"""Sealing (stage 4, E4): round trip, encryption context, tampering, fail closed.

No AWS call anywhere: `KmsSealer` gets a fake client that behaves like KMS (a
data key is only released for the encryption context it was made with), and
one test drives the real boto3 client through botocore's `Stubber`.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid

import pytest

from langgraph_agents.loop import crypto
from langgraph_agents.loop.config import LoopSettings
from langgraph_agents.loop.crypto import (
    KmsSealer, LocalSealer, SealError, SealerUnavailable, payload_context,
    transcript_context,
)


class FakeKms:
    """Same contract as KMS: the data key only comes back for the context it
    was generated with."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._keys: dict[bytes, tuple[bytes, dict]] = {}

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):
        self.calls.append(("generate_data_key", dict(EncryptionContext)))
        assert KeySpec == "AES_256"
        key = os.urandom(32)
        edk = b"edk-" + os.urandom(12)
        self._keys[edk] = (key, dict(EncryptionContext))
        return {"Plaintext": key, "CiphertextBlob": edk, "KeyId": KeyId}

    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId):
        self.calls.append(("decrypt", dict(EncryptionContext)))
        entry = self._keys.get(bytes(CiphertextBlob))
        if entry is None or entry[1] != dict(EncryptionContext):
            raise RuntimeError("InvalidCiphertextException")
        return {"Plaintext": entry[0], "KeyId": KeyId}


class LaxKms(FakeKms):
    """A KMS that ignores the context. The AES-GCM associated data must still
    refuse a wrong context."""

    def decrypt(self, CiphertextBlob, EncryptionContext, KeyId):
        return {"Plaintext": self._keys[bytes(CiphertextBlob)][0], "KeyId": KeyId}


def _local() -> LocalSealer:
    return LocalSealer(os.urandom(32))


def _kms() -> tuple[KmsSealer, FakeKms]:
    fake = FakeKms()
    return KmsSealer(fake, "alias/loop-test"), fake


CTX = payload_context("user-1", "reminders", "action-1")


@pytest.fixture(params=["local", "kms"])
def sealer(request):
    return _local() if request.param == "local" else _kms()[0]


# ── round trip ────────────────────────────────────────────────────────────

def test_round_trip(sealer):
    data = '{"to":["a@x.org"],"body":"xin chào"}'.encode("utf-8")

    async def go():
        blob = await sealer.seal(data, CTX)
        return blob, await sealer.open(blob, CTX)

    blob, back = asyncio.run(go())
    assert back == data
    assert data not in blob and b"a@x.org" not in blob       # not plaintext


def test_same_data_seals_to_different_blobs(sealer):
    async def go():
        return await sealer.seal(b"x", CTX), await sealer.seal(b"x", CTX)

    a, b = asyncio.run(go())
    assert a != b


def test_empty_payload_round_trips(sealer):
    async def go():
        return await sealer.open(await sealer.seal(b"", CTX), CTX)

    assert asyncio.run(go()) == b""


def test_kms_envelope_sends_the_context_to_kms():
    s, fake = _kms()

    async def go():
        blob = await s.seal(b"secret", CTX)
        await s.open(blob, CTX)

    asyncio.run(go())
    assert fake.calls == [("generate_data_key", CTX), ("decrypt", CTX)]


# ── wrong context ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["user_id", "plugin_id", "action_id"])
def test_wrong_payload_context_fails(sealer, field):
    other = dict(CTX, **{field: "someone-else"})

    async def go():
        blob = await sealer.seal(b"secret", CTX)
        await sealer.open(blob, other)

    with pytest.raises(SealError):
        asyncio.run(go())


def test_extra_or_missing_context_key_fails(sealer):
    async def go(ctx):
        blob = await sealer.seal(b"secret", CTX)
        await sealer.open(blob, ctx)

    with pytest.raises(SealError):
        asyncio.run(go(dict(CTX, extra="1")))
    with pytest.raises(SealError):
        asyncio.run(go({"user_id": "user-1"}))


def test_a_blob_cannot_move_between_threads_or_users(sealer):
    t = str(uuid.uuid4())

    async def go(ctx):
        blob = await sealer.seal(b"transcript", transcript_context("u1", t))
        return await sealer.open(blob, ctx)

    with pytest.raises(SealError):
        asyncio.run(go(transcript_context("u2", t)))
    with pytest.raises(SealError):
        asyncio.run(go(transcript_context("u1", str(uuid.uuid4()))))


def test_payload_and_transcript_contexts_are_distinct_domains(sealer):
    async def go():
        blob = await sealer.seal(b"x", payload_context("u1", "p", "a"))
        await sealer.open(blob, transcript_context("u1", "a"))

    with pytest.raises(SealError):
        asyncio.run(go())


def test_empty_context_is_refused(sealer):
    with pytest.raises(SealError):
        asyncio.run(sealer.seal(b"x", {}))


def test_gcm_aad_binds_the_context_even_if_kms_does_not():
    lax = LaxKms()
    s = KmsSealer(lax, "k")

    async def go():
        blob = await s.seal(b"secret", CTX)
        await s.open(blob, dict(CTX, action_id="other"))

    with pytest.raises(SealError):
        asyncio.run(go())


def test_kms_refusal_surfaces_as_seal_error():
    s, fake = _kms()

    async def go():
        blob = await s.seal(b"secret", CTX)
        fake._keys.clear()                    # KMS no longer knows the key
        await s.open(blob, CTX)

    with pytest.raises(SealError):
        asyncio.run(go())


# ── tampering ─────────────────────────────────────────────────────────────

def test_every_flipped_byte_fails(sealer):
    async def go():
        blob = await sealer.seal(b"pay 100 to alice", CTX)
        failures = 0
        for i in range(len(blob)):
            bad = bytearray(blob)
            bad[i] ^= 0x01
            try:
                await sealer.open(bytes(bad), CTX)
            except SealError:
                failures += 1
        return len(blob), failures

    n, failures = asyncio.run(go())
    assert failures == n


@pytest.mark.parametrize("blob", [b"", b"\x01", b"\x02" + b"\x00" * 40, b"\x01\xff\xff",
                                  b"short", b"\x01\x00\x00" + b"\x00" * 5])
def test_malformed_blobs_fail(sealer, blob):
    with pytest.raises(SealError):
        asyncio.run(sealer.open(blob, CTX))


def test_truncated_blob_fails(sealer):
    async def go():
        blob = await sealer.seal(b"hello world", CTX)
        await sealer.open(blob[:-1], CTX)

    with pytest.raises(SealError):
        asyncio.run(go())


def test_local_blob_is_not_accepted_by_kms_and_back():
    local, (kms, _) = _local(), _kms()

    async def go(a, b):
        await b.open(await a.seal(b"x", CTX), CTX)

    with pytest.raises(SealError):
        asyncio.run(go(local, kms))
    with pytest.raises(SealError):
        asyncio.run(go(kms, local))


def test_another_local_key_cannot_open():
    async def go():
        blob = await _local().seal(b"x", CTX)
        await _local().open(blob, CTX)

    with pytest.raises(SealError):
        asyncio.run(go())


# ── LocalSealer rules ─────────────────────────────────────────────────────

def test_local_key_must_be_32_bytes():
    with pytest.raises(SealError):
        LocalSealer(b"short")
    with pytest.raises(SealError):
        LocalSealer.from_base64("not base64 !!")
    s = LocalSealer.from_base64(base64.b64encode(os.urandom(32)).decode())
    assert isinstance(s, LocalSealer)


def test_local_sealer_is_refused_on_lambda(monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "vva-agent")
    with pytest.raises(SealerUnavailable):
        LocalSealer(os.urandom(32))


# ── fail closed: no sealer configured ─────────────────────────────────────

def test_no_sealer_configured_is_refused(monkeypatch):
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    crypto.set_sealer(None)
    assert crypto.build_sealer(LoopSettings()) is None
    with pytest.raises(SealerUnavailable):
        crypto.require_sealer()


def test_no_sealer_means_seal_payload_and_transcript_refuse():
    from langgraph_agents.loop.actions import seal_payload
    from langgraph_agents.loop.turns import seal_transcript
    crypto.set_sealer(None)
    with pytest.raises(SealerUnavailable):
        asyncio.run(seal_payload({"a": 1}, user_id="u", plugin_id="p", action_id="a"))
    with pytest.raises(SealerUnavailable):
        asyncio.run(seal_transcript([{"role": "user"}], user_id="u", thread_id="t"))


def test_dev_key_is_ignored_on_lambda(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "vva-agent")
    assert crypto.build_sealer(LoopSettings(dev_seal_key=key)) is None


def test_dev_key_gives_a_local_sealer_off_lambda(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    assert isinstance(crypto.build_sealer(LoopSettings(dev_seal_key=key)), LocalSealer)


def test_settings_read_the_loop_env_names(monkeypatch):
    monkeypatch.setenv("LOOP_KMS_KEY_ID", "alias/x")
    monkeypatch.setenv("LOOP_DEV_SEAL_KEY", "abc")
    s = LoopSettings.from_env()
    assert (s.kms_key_id, s.dev_seal_key) == ("alias/x", "abc")


def test_kms_key_id_wins_over_the_dev_key(monkeypatch):
    import sys
    import types

    built = {}

    class _Boto:
        @staticmethod
        def client(name):
            built["service"] = name
            return FakeKms()

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=_Boto.client))
    key = base64.b64encode(os.urandom(32)).decode()
    s = crypto.build_sealer(LoopSettings(kms_key_id="alias/loop", dev_seal_key=key))
    assert isinstance(s, KmsSealer) and built == {"service": "kms"}


# ── sealing helpers used by the stores ────────────────────────────────────

def test_seal_payload_round_trip_and_wrong_action_fails():
    from langgraph_agents.loop.actions import open_payload, seal_payload
    s = _local()
    args = {"to": ["a@x.org", "b@y.org"], "title": "Nhắc uống thuốc"}

    async def go():
        blob = await seal_payload(args, user_id="u1", plugin_id="rem", action_id="a1",
                                  sealer=s)
        ok = await open_payload(blob, user_id="u1", plugin_id="rem", action_id="a1",
                                sealer=s)
        with pytest.raises(SealError):
            await open_payload(blob, user_id="u1", plugin_id="rem", action_id="a2",
                               sealer=s)
        with pytest.raises(SealError):
            await open_payload(blob, user_id="u2", plugin_id="rem", action_id="a1",
                               sealer=s)
        with pytest.raises(SealError):
            await open_payload(blob, user_id="u1", plugin_id="other", action_id="a1",
                               sealer=s)
        return ok

    assert asyncio.run(go()) == args


def test_seal_transcript_round_trip_and_wrong_thread_fails():
    from langgraph_agents.loop.turns import open_transcript, seal_transcript
    s = _local()
    msgs = [{"role": "user", "content": "gửi mail"},
            {"role": "assistant", "tool_calls": [{"id": "c1"}]}]
    t = str(uuid.uuid4())

    async def go():
        blob = await seal_transcript(msgs, user_id="u1", thread_id=t, sealer=s)
        ok = await open_transcript(blob, user_id="u1", thread_id=t, sealer=s)
        with pytest.raises(SealError):
            await open_transcript(blob, user_id="u1", thread_id=str(uuid.uuid4()),
                                  sealer=s)
        with pytest.raises(SealError):
            await open_transcript(blob, user_id="u2", thread_id=t, sealer=s)
        return ok

    assert asyncio.run(go()) == msgs


# ── the real boto3 client, with botocore's Stubber (no AWS call) ──────────

def test_kms_sealer_with_a_stubbed_boto3_client():
    boto3 = pytest.importorskip("boto3")
    from botocore.stub import Stubber

    client = boto3.client("kms", region_name="us-east-1",
                          aws_access_key_id="x", aws_secret_access_key="y")
    key = os.urandom(32)
    edk = b"wrapped-key-bytes"
    with Stubber(client) as stub:
        stub.add_response(
            "generate_data_key",
            {"Plaintext": key, "CiphertextBlob": edk, "KeyId": "arn:aws:kms:k"},
            {"KeyId": "alias/loop", "KeySpec": "AES_256", "EncryptionContext": CTX})
        stub.add_response(
            "decrypt", {"Plaintext": key, "KeyId": "arn:aws:kms:k"},
            {"CiphertextBlob": edk, "EncryptionContext": CTX, "KeyId": "alias/loop"})
        s = KmsSealer(client, "alias/loop")

        async def go():
            return await s.open(await s.seal(b"hello", CTX), CTX)

        assert asyncio.run(go()) == b"hello"
        stub.assert_no_pending_responses()
