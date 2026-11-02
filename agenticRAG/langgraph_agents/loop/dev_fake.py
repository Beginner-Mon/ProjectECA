"""The dev-only fake write tool switch (stage 4, E9).

Only the fake `create_reminder` (manifest `dev-reminders.json`, served by
`plugins/dev_reminders.py`) can be proposed for approval, and only when both
hold:

  * `LOOP_DEV_FAKE_WRITE=1`;
  * `AWS_LAMBDA_FUNCTION_NAME` is unset (never on Lambda).

Every other write tool stays refused by the gate with `approval_unavailable`.
Stdlib only: the catalog loader and the gate both import this.
"""

from __future__ import annotations

import os

DEV_PLUGIN_ID = "dev-reminders"


def dev_fake_write_enabled() -> bool:
    return (os.environ.get("LOOP_DEV_FAKE_WRITE", "").strip() == "1"
            and not os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def writable_plugins() -> frozenset[str]:
    """Plugin ids whose write tools may be proposed for approval."""
    return frozenset({DEV_PLUGIN_ID}) if dev_fake_write_enabled() else frozenset()
