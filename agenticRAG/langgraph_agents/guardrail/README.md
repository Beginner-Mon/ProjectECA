# Guardrail Layer

The guardrail is an **outer layer** that wraps the LangGraph agent. It runs in
`api/main.py` in parallel with the graph, reads **only the current user
message** (no memory, no history), and returns a `Verdict`:

```python
@dataclass(frozen=True)
class Verdict:
    level: Literal["emergency", "soon", "routine"]
    out_of_scope: bool
    health_topic: bool
    source: Literal["llm", "fallback"]
    elapsed_ms: int
```

## Architecture

```
request ─► api/main.py ─► guardrail.run_input(ctx)   task starts at once
                 │                 │ verdict (data only)
                 ▼                 ▼
           LangGraph: memory → planner → retriever ⇄ tools → kimodo → synthesizer → END
                 │
                 ▼  (synthesizer reads verdict via guardrail.handoff.current_verdict)
           SSE: head → body → tail
```

- **head**: emitted BEFORE the first body token. `emergency` → `emergency_screen`
  (fallback to `red_flag_screen`). `soon` → `red_flag_screen`. `routine` → `""`.
- **body**: the agent's answer, untouched.
- **tail**: appended after body. Order: `referral_advice` then `scope_disclaimer`.

## Adding a Check

A check is a module implementing the `Check` protocol (`checks/base.py`):

```python
from typing import Protocol, Literal

class Decision:
    action: Literal["allow", "annotate", "block"]
    annotations: dict = {}
    message: str | None = None

class CheckContext:
    message: str
    locale: str
    persona_id: str
    user_id: str
    request_id: str

class Check(Protocol):
    name: str
    stage: Literal["input", "output_tail"]
    async def run(self, ctx: CheckContext) -> Decision: ...
```

Steps to add a new check:

1. Create `guardrail/checks/my_check.py` with a class implementing `Check`.
2. Add it to the appropriate list in `guardrail/policy.py`:
   - `INPUT_CHECKS` — runs **before** the graph starts. Can `block` (turn stops, message sent).
   - `OUTPUT_TAIL_CHECKS` — runs **after** the body, before `tail`. Cannot block; can only
     `annotate` (adds lines to the tail).
3. That's it. No other files need to change.

## Existing Checks

| File | Stage | Purpose |
|------|-------|---------|
| `checks/health_triage.py` | `input` | Calls `triage()`; returns `Decision("annotate", {"verdict": v})` |

## Stage Semantics

| Stage | When it runs | Can block? | Can annotate? |
|-------|--------------|------------|---------------|
| `input` | Before graph starts (parallel with graph) | Yes — `block` stops the turn | Yes — annotations become `InputResult.verdict` |
| `output_tail` | After synthesizer finishes, before `tail()` | No — `block` is treated as `allow` | Yes — annotations add lines to tail |

## Pipeline

`pipeline.run_input(ctx)` executes all `INPUT_CHECKS` with
`asyncio.gather(..., return_exceptions=True)`. An exception is logged as
`guardrail_check_failed` and treated as `allow`. The first `block` decision
wins and becomes `InputResult.blocked`; the turn stops there.

The `Verdict` is extracted from the `verdict` annotation of any check (the
health triage check is the only one today). If missing, a fallback routine
verdict is used.

## Triage

`guardrail/triage.py` contains the measured P4 prompt (27/10). It classifies
the message by **cost of delay**:

- **EMERGENCY** — life-threatening or permanent harm if delayed hours
- **SOON** — needs clinician within a day or two, but hours won't cause lasting harm
- **ROUTINE** — only ordinary strain/soreness, or no symptoms; ties go to the more urgent level

Plus two boolean flags:
- `out_of_scope` — user asks for diagnosis, medication/dose, or test interpretation
- `health_topic` — message is about body, pain, health, or exercise

The prompt is **written from principles, not symptom examples** (an example
list is a list to miss). It is kept verbatim from the measured prompt
(`scratchpad/P4_prompt.txt`); do not reword without a measurement run.

Model: DeepSeek v4-flash with thinking disabled, via `get_chat_model("guardrail")`.
Production timeout: 10 s. Spike overrides (NIM): thinking off, 60 s timeout,
circuit breaker held closed — all documented in the Spike A report.

## Configuration

`guardrail/policy.py` is the single file to touch when adding a check:

```python
INPUT_CHECKS: list[Check] = [HealthTriage()]
OUTPUT_TAIL_CHECKS: list[Check] = []
```

## Testing

Unit tests in `tests/langgraph_agents/test_guardrail.py` cover:
- JSON parse for all three levels
- Garbage/timeout/exception → fallback routine verdict + `guardrail_triage_failed` log
- `safety_keys` table row by row
- Head fallback order: persona `emergency_screen` → `red_flag_screen` → code default
- `directive` empty for routine, non-empty for emergency/soon
- Extensibility: monkeypatch `policy.INPUT_CHECKS` to add a fake `block` check
- Check exception does not break `run_input`
- `current_verdict` awaits provided task; without task runs inline once per `request_id`
- `"guardrail" in llm._ECA_ONLY_ROLES`