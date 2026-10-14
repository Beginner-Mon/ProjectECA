"""Run pytest / alembic / scripts against the Neon DEV branch, never prod.

`agenticRAG/.env` points at Neon prod. Every DB touch in the memory-v2 plan
must go through this helper, which swaps the process environment to the dev
branch DSNs and refuses to run when that swap looks unsafe.

Usage (from the repo root, PowerShell 5.1)::

    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py pytest tests/... -m unit
    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py alembic current
    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py script <path> <args...>

There is deliberately NO free-form SQL mode: schema changes go through
alembic revisions, data cleanup through committed scripts.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from urllib.parse import urlsplit

THIS_FILE = Path(__file__).resolve()
AGENTS_DIR = THIS_FILE.parents[1]
AGENTIC_ROOT = THIS_FILE.parents[2]
REPO_ROOT = AGENTIC_ROOT.parent
if str(AGENTIC_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENTIC_ROOT))


def _host(dsn: str) -> str:
    try:
        return (urlsplit(dsn).hostname or "").lower()
    except Exception:
        return ""


def _refuse(reason: str) -> int:
    print(f"on_dev_branch: REFUSED — {reason}", file=sys.stderr)
    return 2


def resolve_dev_env() -> tuple[dict[str, str], int]:
    """Load .env, validate dev DSNs, return (env_overrides, exit_code)."""
    from langgraph_agents.shared.env import load_env

    load_env()
    prod_app = os.environ.get("VVA_PG_DSN", "")
    prod_owner = os.environ.get("VVA_PG_DSN_OWNER", "")
    dev_app = os.environ.get("ECA_TEST_PG_DSN", "")
    dev_owner = os.environ.get("ECA_TEST_PG_DSN_OWNER", "")

    if not dev_app.strip() or not dev_owner.strip():
        return {}, _refuse("ECA_TEST_PG_DSN / ECA_TEST_PG_DSN_OWNER is missing.")
    if "-pooler" in dev_app or "-pooler" in dev_owner:
        return {}, _refuse("dev DSN uses a -pooler host; use the direct branch endpoint.")
    prod_hosts = {h for h in (_host(prod_app), _host(prod_owner)) if h}
    dev_app_host, dev_owner_host = _host(dev_app), _host(dev_owner)
    if not dev_app_host or not dev_owner_host:
        return {}, _refuse("could not parse a hostname from the dev DSNs.")
    if dev_app_host in prod_hosts or dev_owner_host in prod_hosts:
        return {}, _refuse(
            f"dev host matches prod (dev={dev_app_host},{dev_owner_host} prod={sorted(prod_hosts)})."
        )
    return {"VVA_PG_DSN": dev_app, "VVA_PG_DSN_OWNER": dev_owner}, 0


def main(argv: list[str]) -> int:
    overrides, code = resolve_dev_env()
    if code != 0:
        return code
    os.environ["VVA_PG_DSN"] = overrides["VVA_PG_DSN"]
    os.environ["VVA_PG_DSN_OWNER"] = overrides["VVA_PG_DSN_OWNER"]
    # db/postgres.py also honours POSTGRES_DSN as an alias; point it at dev
    # too so a stale export cannot split brain the app away from alembic.
    os.environ["POSTGRES_DSN"] = overrides["VVA_PG_DSN"]
    print(
        "on_dev_branch: host app=%s owner=%s"
        % (_host(overrides["VVA_PG_DSN"]), _host(overrides["VVA_PG_DSN_OWNER"]))
    )
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    mode, rest = argv[0], argv[1:]
    if mode == "pytest":
        import pytest

        return pytest.main(rest)
    if mode == "alembic":
        os.chdir(str(AGENTS_DIR))
        from alembic.config import main as alembic_main

        alembic_main(argv=["-c", "alembic.ini"] + rest)
        return 0
    if mode == "script":
        if not rest:
            return _refuse("script mode needs a path.")
        target = rest[0]
        path = Path(target)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            return _refuse(f"script not found: {target}")
        sys.argv = [str(path)] + rest[1:]
        runpy.run_path(str(path), run_name="__main__")
        return 0
    return _refuse(f"unknown mode {mode!r}; want pytest|alembic|script.")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
