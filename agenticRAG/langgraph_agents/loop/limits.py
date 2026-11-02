"""Caps and per-turn budget for the read-only loop.

`Caps` holds the stage-1 limits (plan §4). `Budget` tracks spend against
them with an injectable `time_fn` (tests freeze the clock). `TurnContext`
is the per-turn runtime: caps, budget, trackers (repeat-empty signatures,
taint snapshot, call registry, round/turn positions, motion_jobs,
`force_no_tools`, used result chars, breaker registry) and the round
planner `plan_round`.

A `TurnContext` is built by the CALLER of `astream` and passed as
`config["configurable"]["turn_ctx"]`. LangGraph re-merges config per node
task, so lazy creation inside a node would not propagate; `ensure_turn`
raises a clear error when it is missing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable


def _msg_content(m: Any) -> str:
    if isinstance(m, dict):
        return m.get("content", "") or ""
    return getattr(m, "content", "") or ""


def _msg_role(m: Any) -> str | None:
    if isinstance(m, dict):
        role = m.get("role")
    else:
        # LangChain messages expose .type ("system"/"human"/"ai"/"tool"), not .role.
        role = getattr(m, "role", None) or getattr(m, "type", None)
    # Normalise LangChain's human/ai to user/assistant for consistency.
    return {"human": "user", "ai": "assistant"}.get(role, role)


def _msg_tool_call_id(m: Any) -> str | None:
    if isinstance(m, dict):
        return m.get("tool_call_id")
    return getattr(m, "tool_call_id", None)


def _msg_tool_calls(m: Any) -> list[dict] | None:
    if isinstance(m, dict):
        return m.get("tool_calls")
    return getattr(m, "tool_calls", None) or None


class Caps:
    """Stage-1 limits.

    max_rounds=6, 5 calls per round and 12 per turn, 1 write per round,
    20 s of loop time (not counting time waiting for approval), 60 s per
    request, 60k input tokens, results 8k per tool / 24k per turn,
    retries LLM 1 / read 1 / write 0.
    """

    def __init__(
        self,
        max_rounds: int = 6,
        max_calls_per_round: int = 5,
        max_calls_per_turn: int = 12,
        max_writes_per_round: int = 1,
        loop_time_s: float = 20.0,
        request_time_s: float = 60.0,
        max_input_tokens: int = 60_000,
        max_result_chars_per_tool: int = 8_000,
        max_result_chars_per_turn: int = 24_000,
        retries_llm: int = 1,
        retries_read: int = 1,
        retries_write: int = 0,
        rate_limit: int = 30,
        rate_window_s: int = 60,
    ) -> None:
        self.max_rounds = max_rounds
        self.max_calls_per_round = max_calls_per_round
        self.max_calls_per_turn = max_calls_per_turn
        self.max_writes_per_round = max_writes_per_round
        self.loop_time_s = loop_time_s
        self.request_time_s = request_time_s
        self.max_input_tokens = max_input_tokens
        self.max_result_chars_per_tool = max_result_chars_per_tool
        self.max_result_chars_per_turn = max_result_chars_per_turn
        self.retries_llm = retries_llm
        self.retries_read = retries_read
        self.retries_write = retries_write
        self.rate_limit = rate_limit
        self.rate_window_s = rate_window_s


@dataclass
class Budget:
    """Spend tracker with an injectable clock.

    `time_fn` returns seconds (monotonic by default). Tests inject a fixed
    or advancing function so cap behaviour is deterministic.
    """

    time_fn: Callable[[], float] = time.monotonic
    started: float = 0.0

    def __post_init__(self) -> None:
        if self.started == 0.0:
            self.started = self.time_fn()

    def elapsed(self) -> float:
        return self.time_fn() - self.started


@dataclass
class TurnContext:
    """Per-turn runtime, one per `/chat` turn in loop mode.

    Built by the caller (`api/main.py` or a test) and handed to the graph
    through `config["configurable"]["turn_ctx"]`.
    """

    caps: Caps
    budget: Budget = field(default_factory=Budget)

    # Who this turn is for and what they said.
    user_id: str = ""
    current_message: str = ""

    # Cancel flag, set by the agent node each round after asking the lease
    # store. When True the gate refuses every call as cancelled.
    cancelled: bool = False

    # Repeat-empty signatures already seen this turn: "{tool}|{json args}".
    repeat_empty_signatures: set[str] = field(default_factory=set)

    # Taint snapshot refreshed by the agent node each round.
    banned_terms: list[str] = field(default_factory=list)
    allowed_terms: list[str] = field(default_factory=list)
    personal_sources: list[dict] = field(default_factory=list)

    # The turn's guardrail verdict, fetched ONCE per round by the tools node
    # (only when a round runs an egress_read or clinical call), stored here so
    # the gate sees it. Inject `verdict_getter`; default
    # `guardrail.handoff.current_verdict`.
    verdict: Any = None
    verdict_getter: Any = None

    # Call registry: tool_call_id → tool name (every tool_call_id answered
    # exactly once). round_positions and turn_call_positions are assigned by
    # plan_round.
    call_registry: dict[str, str] = field(default_factory=dict)
    round_positions: dict[str, int] = field(default_factory=dict)
    turn_call_positions: dict[str, int] = field(default_factory=dict)

    motion_jobs: list[dict] = field(default_factory=list)

    # Stage 4. `payload_sha256` of every action the user declined in this turn
    # (it survives a pause in `loop_turns.state_json`); the gate refuses the
    # same payload again. `pending_card` is set by the `propose` node once the
    # action is stored, so `api/main.py` can invalidate it if the guardrail
    # blocks the turn (E10).
    declined_hashes: set[str] = field(default_factory=set)
    pending_card: dict | None = None

    # When True the next agent call must use tool_choice="none" (round cap or
    # speed cap already spent).
    force_no_tools: bool = False

    # Characters of tool results already emitted this turn (24k turn cap).
    used_result_chars: int = 0

    # Breaker registry: name → breaker instance. Gate refuses with kind
    # `transient` when a breaker is open.
    breakers: dict[str, object] = field(default_factory=dict)

    # ── round bookkeeping ─────────────────────────────────────────

    round_calls: int = 0       # calls made this round
    round_writes: int = 0      # write calls made this round
    turn_calls: int = 0        # calls made this turn

    def plan_round(self) -> None:
        """Start a new round: reset per-round counters."""
        self.round_calls = 0
        self.round_writes = 0

    def call_position(self, tool_call_id: str, tool_name: str) -> int:
        """Register a call and return its turn-wide position (1-based)."""
        self.call_registry[tool_call_id] = tool_name
        self.turn_calls += 1
        self.round_calls += 1
        self.turn_call_positions[tool_call_id] = self.turn_calls
        self.round_positions[tool_call_id] = self.round_calls
        return self.turn_calls

    def record_write(self) -> None:
        self.round_writes += 1

    def result_chars(self, n: int) -> None:
        self.used_result_chars += n

    @property
    def rate_limit(self) -> int:
        return self.caps.rate_limit if hasattr(self.caps, "rate_limit") else 30

    @property
    def rate_window_s(self) -> int:
        return self.caps.rate_window_s if hasattr(self.caps, "rate_window_s") else 60


def new_turn(caps: Caps | None = None) -> TurnContext:
    """Build a fresh `TurnContext` for a new turn."""
    return TurnContext(caps=caps or Caps())


def ensure_turn(config: dict) -> TurnContext:
    """Read the turn context from a LangGraph config."""
    configurable = config.get("configurable") or {}
    ctx = configurable.get("turn_ctx")
    if ctx is None:
        raise RuntimeError(
            "loop: no TurnContext in config[\"configurable\"][\"turn_ctx\"] — "
            "the caller must build one and pass it before astream()"
        )
    return ctx
