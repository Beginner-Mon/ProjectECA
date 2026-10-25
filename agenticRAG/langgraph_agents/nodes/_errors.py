"""One place that turns a node's exception into an `errors` entry.

Every node that can fail used to hand-write the same dict. The entry now also
says *what kind* of failure it was (``kind``, ``status``), classified the same
way for ECA's own key as for a user's key (``llm.classify_provider_error``),
so error_handler can name the failure and the client can tell a billing
problem from an outage without anyone reading exception text.
"""
from __future__ import annotations

from datetime import datetime, timezone

from langgraph_agents.llm import CircuitBreakerOpenError, classify_provider_error
from langgraph_agents.state import ErrorSeverity


def classify_error(exc: BaseException) -> dict:
    """``{"kind", "status"}`` for any exception. Never includes its text."""
    if isinstance(exc, CircuitBreakerOpenError):
        return {"kind": "unreachable", "status": None}
    return classify_provider_error(exc)


def error_entry(
    node: str,
    exc: BaseException | None,
    severity: ErrorSeverity,
    message: str | None = None,
) -> dict:
    """An ``errors`` entry: {node, severity, message, timestamp, kind, status}.

    `message` defaults to the exception text (internal use, e.g. logs); it is
    never sent to the client. With no exception (e.g. no model configured) the
    kind is "unreachable": the provider could not be reached at all.
    """
    if exc is None:
        classified = {"kind": "unreachable", "status": None}
    else:
        classified = classify_error(exc)
    return {
        "node": node,
        "severity": severity,
        "message": message if message is not None else (str(exc) or type(exc).__name__),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": classified["kind"],
        "status": classified["status"],
    }


# kind (+ status) of the most severe CRITICAL error -> the code the client sees.
def error_code_for(entry: dict | None) -> str:
    kind = (entry or {}).get("kind")
    if not kind:
        return "internal"
    if kind == "quota" and (entry or {}).get("status") == 402:
        return "provider_billing"
    if kind == "auth":
        return "provider_auth"
    return "provider_unavailable"
