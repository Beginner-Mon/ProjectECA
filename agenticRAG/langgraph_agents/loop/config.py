"""Loop mode settings from env.

All values come from environment variables with sensible defaults, so a
deployment that never turns the loop on pays nothing: `load_settings()` is
cached and read once per process.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel


class LoopSettings(BaseModel):
    """Which graph a request uses, and the per-turn caps the loop obeys."""

    # agent_mode: "pipeline" (the default), "group" (users in loop_group),
    # or "loop" (every request). `disabled` is the kill switch.
    agent_mode: Literal["pipeline", "group", "loop"] = "pipeline"
    loop_group: str = "loop-testers"
    disabled: bool = False

    # Caps (plan §4).
    max_rounds: int = 6
    max_calls_per_round: int = 5
    max_calls_per_turn: int = 12
    max_writes_per_round: int = 1
    loop_time_s: float = 20.0
    request_time_s: float = 60.0
    max_input_tokens: int = 60_000
    max_result_chars_per_tool: int = 8_000
    max_result_chars_per_turn: int = 24_000
    retries_llm: int = 1
    retries_read: int = 1
    retries_write: int = 0

    # Leases: one turn per session, global concurrency cap.
    max_concurrent: int = 3
    lease_ttl_s: float = 120.0

    # Rate: 30 requests/min per (user, bucket), 60 s window.
    rate_limit: int = 30
    rate_window_s: int = 60

    # Reasoning effort sent to hosts that support it (openai.com, groq.com).
    reasoning_effort: str = "low"

    # Stage-3 toggles, present so the settings shape survives later stages.
    # Kill switches (D8). `plugins_disabled` offers no plugin tool at all;
    # `plugins_off` (env LOOP_PLUGINS_OFF=id1,id2) turns off single plugins.
    plugins_disabled: bool = True
    plugins_off: list[str] = []
    google_enabled: bool = False
    # Sealing (stage 4, E4). `kms_key_id` (env LOOP_KMS_KEY_ID) selects KmsSealer;
    # `dev_seal_key` (env LOOP_DEV_SEAL_KEY, 32 bytes, base64) selects LocalSealer
    # and is honoured only when AWS_LAMBDA_FUNCTION_NAME is unset.
    kms_key_id: str | None = None
    dev_seal_key: str | None = None

    @classmethod
    def from_env(cls) -> "LoopSettings":
        def _bool(name: str, default: bool = False) -> bool:
            return os.getenv(name, str(default)).strip().lower() in (
                "1", "true", "yes", "on",
            )

        def _float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        return cls(
            agent_mode=os.getenv("AGENT_MODE", "pipeline"),  # type: ignore[arg-type]
            loop_group=os.getenv("AGENT_LOOP_GROUP", "loop-testers"),
            disabled=_bool("AGENT_DISABLED", False),
            max_rounds=_int("AGENT_MAX_ROUNDS", 6),
            max_calls_per_round=_int("AGENT_MAX_CALLS_PER_ROUND", 5),
            max_calls_per_turn=_int("AGENT_MAX_CALLS_PER_TURN", 12),
            max_writes_per_round=_int("AGENT_MAX_WRITES_PER_ROUND", 1),
            loop_time_s=_float("AGENT_LOOP_TIME_S", 20.0),
            request_time_s=_float("AGENT_REQUEST_TIME_S", 60.0),
            max_input_tokens=_int("AGENT_MAX_INPUT_TOKENS", 60_000),
            max_result_chars_per_tool=_int("AGENT_MAX_RESULT_CHARS_PER_TOOL", 8_000),
            max_result_chars_per_turn=_int("AGENT_MAX_RESULT_CHARS_PER_TURN", 24_000),
            retries_llm=_int("AGENT_RETRIES_LLM", 1),
            retries_read=_int("AGENT_RETRIES_READ", 1),
            retries_write=_int("AGENT_RETRIES_WRITE", 0),
            max_concurrent=_int("AGENT_MAX_CONCURRENT", 3),
            lease_ttl_s=_float("AGENT_LEASE_TTL_S", 120.0),
            rate_limit=_int("AGENT_RATE_LIMIT", 30),
            rate_window_s=_int("AGENT_RATE_WINDOW_S", 60),
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "low"),
            plugins_disabled=_bool("AGENT_PLUGINS_DISABLED", True),
            plugins_off=[p.strip() for p in os.getenv("LOOP_PLUGINS_OFF", "").split(",")
                         if p.strip()],
            google_enabled=_bool("AGENT_GOOGLE_ENABLED", False),
            kms_key_id=os.getenv("LOOP_KMS_KEY_ID") or None,
            dev_seal_key=os.getenv("LOOP_DEV_SEAL_KEY") or None,
        )


@lru_cache(maxsize=1)
def load_settings() -> LoopSettings:
    """Read env once and cache for the process lifetime."""
    return LoopSettings.from_env()
