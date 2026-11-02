"""Sealing: payloads and paused transcripts are encrypted at rest (stage 4, E4).

    Sealer.seal(data, context) -> blob
    Sealer.open(blob, context) -> data

Every seal binds an *encryption context*, a string map:

* an action payload (`agent_actions.payload_enc`): {user_id, plugin_id, action_id}
* a paused transcript (`loop_turns.transcript_enc`): {user_id, purpose="loop_turn", thread_id}

Opening with a different context fails with `SealError`, so a blob copied to
another row, user or turn cannot be read. The context is bound twice:

1. KMS: `generate_data_key(EncryptionContext=ctx)` / `decrypt(EncryptionContext=ctx)`.
   KMS refuses a data key whose context differs.
2. AES-GCM: the canonical JSON of the context is the associated data (AAD),
   so the check holds even if the key service did not enforce it (LocalSealer
   has no KMS at all).

Blob layout (versioned):

    0x01 | u16 len(edk) | edk | 12-byte nonce | AES-GCM ciphertext+tag

`edk` is the KMS-encrypted data key (empty for LocalSealer).

`KmsSealer` takes an injected KMS client (a boto3 `kms` client, a botocore
`Stubber`-wrapped one, or a fake with the same two methods). Nothing here
imports boto3 at module scope or calls AWS by itself.

Fail closed: `get_sealer()` returns None when nothing is configured and
`require_sealer()` raises `SealerUnavailable`. The gate maps that to
`approval_unavailable`. `LocalSealer` is refused when `AWS_LAMBDA_FUNCTION_NAME`
is set.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import struct
from typing import Any, Mapping, Optional, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_VERSION = b"\x01"
_NONCE_LEN = 12
_KEY_LEN = 32


class SealError(Exception):
    """A blob could not be sealed or opened (wrong context, tampering, bad key)."""


class SealerUnavailable(SealError):
    """No sealer is configured. Loop write actions are refused (fail closed)."""


class Sealer(Protocol):
    async def seal(self, data: bytes, context: Mapping[str, str]) -> bytes: ...

    async def open(self, blob: bytes, context: Mapping[str, str]) -> bytes: ...


# ── Encryption contexts ───────────────────────────────────────────────────

def payload_context(user_id: str, plugin_id: str, action_id: str) -> dict[str, str]:
    return {"user_id": str(user_id), "plugin_id": str(plugin_id),
            "action_id": str(action_id)}


def transcript_context(user_id: str, thread_id: str) -> dict[str, str]:
    return {"user_id": str(user_id), "purpose": "loop_turn",
            "thread_id": str(thread_id)}


def _norm(context: Mapping[str, Any]) -> dict[str, str]:
    if not context:
        raise SealError("an encryption context is required")
    return {str(k): str(v) for k, v in context.items()}


def _aad(context: dict[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("ascii")


def _pack(edk: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    if len(edk) > 0xFFFF:
        raise SealError("encrypted data key too long")
    return _VERSION + struct.pack(">H", len(edk)) + edk + nonce + ciphertext


def _unpack(blob: bytes) -> tuple[bytes, bytes, bytes]:
    try:
        if not blob or blob[:1] != _VERSION or len(blob) < 3:
            raise ValueError("bad version")
        (n,) = struct.unpack(">H", blob[1:3])
        edk = blob[3:3 + n]
        rest = blob[3 + n:]
        if len(edk) != n or len(rest) < _NONCE_LEN + 16:
            raise ValueError("truncated")
        return edk, rest[:_NONCE_LEN], rest[_NONCE_LEN:]
    except (ValueError, struct.error, TypeError) as exc:
        raise SealError("sealed blob is malformed") from exc


def _encrypt(key: bytes, data: bytes, context: dict[str, str]) -> tuple[bytes, bytes]:
    nonce = os.urandom(_NONCE_LEN)
    return nonce, AESGCM(key).encrypt(nonce, data, _aad(context))


def _decrypt(key: bytes, nonce: bytes, ciphertext: bytes,
             context: dict[str, str]) -> bytes:
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, _aad(context))
    except InvalidTag as exc:
        raise SealError("cannot open: wrong context or tampered blob") from exc


# ── LocalSealer: dev and tests ────────────────────────────────────────────

class LocalSealer:
    """AES-GCM with one local 32-byte key. Never on Lambda."""

    def __init__(self, key: bytes) -> None:
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            raise SealerUnavailable("LocalSealer is refused on Lambda")
        if not isinstance(key, (bytes, bytearray)) or len(key) != _KEY_LEN:
            raise SealError("LocalSealer needs a 32-byte key")
        self._key = bytes(key)

    @classmethod
    def from_base64(cls, value: str) -> "LocalSealer":
        try:
            key = base64.b64decode(value.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SealError("LOOP_DEV_SEAL_KEY is not valid base64") from exc
        return cls(key)

    async def seal(self, data: bytes, context: Mapping[str, str]) -> bytes:
        ctx = _norm(context)
        nonce, ct = _encrypt(self._key, data, ctx)
        return _pack(b"", nonce, ct)

    async def open(self, blob: bytes, context: Mapping[str, str]) -> bytes:
        ctx = _norm(context)
        edk, nonce, ct = _unpack(blob)
        if edk:
            raise SealError("blob was sealed by KMS, not by the local key")
        return _decrypt(self._key, nonce, ct, ctx)


# ── KmsSealer: envelope encryption ────────────────────────────────────────

class KmsSealer:
    """Envelope encryption. `kms` is an injected client with the boto3 methods
    `generate_data_key(KeyId, KeySpec, EncryptionContext)` and
    `decrypt(CiphertextBlob, EncryptionContext, KeyId)`.

    The calls are blocking boto3 calls, so they run in a worker thread.
    """

    def __init__(self, kms: Any, key_id: str) -> None:
        if not key_id:
            raise SealerUnavailable("no KMS key id")
        self._kms = kms
        self._key_id = key_id

    async def seal(self, data: bytes, context: Mapping[str, str]) -> bytes:
        ctx = _norm(context)
        try:
            resp = await asyncio.to_thread(
                self._kms.generate_data_key,
                KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=ctx)
            plaintext_key = bytes(resp["Plaintext"])
            edk = bytes(resp["CiphertextBlob"])
        except SealError:
            raise
        except Exception as exc:  # noqa: BLE001 - any KMS failure fails closed
            raise SealError(f"KMS generate_data_key failed: {type(exc).__name__}") from exc
        nonce, ct = _encrypt(plaintext_key, data, ctx)
        return _pack(edk, nonce, ct)

    async def open(self, blob: bytes, context: Mapping[str, str]) -> bytes:
        ctx = _norm(context)
        edk, nonce, ct = _unpack(blob)
        if not edk:
            raise SealError("blob has no encrypted data key")
        try:
            resp = await asyncio.to_thread(
                self._kms.decrypt,
                CiphertextBlob=edk, EncryptionContext=ctx, KeyId=self._key_id)
            plaintext_key = bytes(resp["Plaintext"])
        except Exception as exc:  # noqa: BLE001 - wrong context included
            raise SealError(f"KMS decrypt failed: {type(exc).__name__}") from exc
        return _decrypt(plaintext_key, nonce, ct, ctx)


# ── Resolution (fail closed) ──────────────────────────────────────────────

_sealer: Optional[Sealer] = None


def set_sealer(sealer: Optional[Sealer]) -> None:
    """Install a sealer explicitly (tests, api/main.py). None resets."""
    global _sealer
    _sealer = sealer


def build_sealer(settings=None) -> Optional[Sealer]:
    """KMS when `kms_key_id` is set; else the local key when
    `AWS_LAMBDA_FUNCTION_NAME` is unset and `dev_seal_key` is set; else None."""
    if settings is None:
        from .config import load_settings
        settings = load_settings()
    if settings.kms_key_id:
        import boto3  # lazy: only prod has this path
        return KmsSealer(boto3.client("kms"), settings.kms_key_id)
    if settings.dev_seal_key and not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return LocalSealer.from_base64(settings.dev_seal_key)
    return None


def get_sealer() -> Optional[Sealer]:
    global _sealer
    if _sealer is None:
        _sealer = build_sealer()
    return _sealer


def require_sealer() -> Sealer:
    """The configured sealer, or `SealerUnavailable` (the gate answers
    `approval_unavailable`)."""
    sealer = get_sealer()
    if sealer is None:
        raise SealerUnavailable("no sealer configured")
    return sealer
