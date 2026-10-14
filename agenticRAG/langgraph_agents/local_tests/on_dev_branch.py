"""Run pytest / alembic / scripts against the Neon DEV branch, never prod.

`agenticRAG/.env` points at Neon prod. Every DB touch in the memory-v2 plan
must go through this helper, which swaps the process environment to the dev
branch DSNs and refuses to run when that swap looks unsafe.

Usage (from the repo root, PowerShell 5.1)::

    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py pytest tests/... -m unit
    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py alembic current
    python agenticRAG/langgraph_agents/local_tests/on_dev_branch.py script <path> <args...>

    python .../on_dev_branch.py --llm gemini --gemini-key 2 script <path> ...

LLM selection (memory-v2 plan, Gemini free tier: 20 calls/day/key):
`--llm gemini --gemini-key N` keeps exactly the Nth key of GEMINI_API_KEYS
(1-based) and strips every DeepSeek credential, so llm.py resolves Gemini as
the primary model. Without the flags nothing about the LLM changes. One key
per run — never rotate inside a process. The key ordinal (never the key) is
printed.

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


def select_llm_env(env: dict, llm: str, key_num: int | None
                   ) -> tuple[dict, list, str]:
    """Pure LLM selection: (updates, deletions, label) or ValueError.

    `env` is a snapshot (os.environ-like); nothing is mutated. `updates` are
    set into the process env, `deletions` removed. `label` names the key
    ordinal only — it must never carry key material.
    """
    if llm == "deepseek":
        if key_num is not None:
            raise ValueError("--gemini-key needs --llm gemini.")
        return {}, [], "llm=deepseek"
    if llm != "gemini":
        raise ValueError(f"unknown --llm {llm!r}; want deepseek|gemini.")
    if key_num is None:
        raise ValueError("--llm gemini needs --gemini-key N.")
    if isinstance(key_num, bool) or not isinstance(key_num, int):
        raise ValueError(f"--gemini-key must be a number, got {key_num!r}.")
    keys = [k.strip() for k in env.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
    if not keys:
        raise ValueError("GEMINI_API_KEYS is empty.")
    if not 1 <= key_num <= len(keys):
        raise ValueError(f"--gemini-key {key_num} out of range (have {len(keys)} keys).")
    return ({"GEMINI_API_KEYS": keys[key_num - 1]},
            ["DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_PARAM"],
            f"llm=gemini key={key_num}/{len(keys)}")


def _take_llm_flags(argv: list[str]) -> tuple[str, int | None, list[str]]:
    """Split leading --llm/--gemini-key options off; return (llm, key, rest)."""
    llm, key, rest = "deepseek", None, []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--llm" and i + 1 < len(argv):
            llm, i = argv[i + 1], i + 2
        elif arg == "--gemini-key" and i + 1 < len(argv):
            try:
                key = int(argv[i + 1])
            except ValueError:
                key = argv[i + 1]  # select_llm_env reports it properly
            i += 2
        else:
            rest.append(arg)
            i += 1
    return llm, key, rest


def main(argv: list[str]) -> int:
    llm, key_num, argv = _take_llm_flags(list(argv))
    overrides, code = resolve_dev_env()
    if code != 0:
        return code
    os.environ["VVA_PG_DSN"] = overrides["VVA_PG_DSN"]
    os.environ["VVA_PG_DSN_OWNER"] = overrides["VVA_PG_DSN_OWNER"]
    # db/postgres.py also honours POSTGRES_DSN as an alias; point it at dev
    # too so a stale export cannot split brain the app away from alembic.
    os.environ["POSTGRES_DSN"] = overrides["VVA_PG_DSN"]
    try:
        llm_updates, llm_deletions, llm_label = select_llm_env(
            dict(os.environ), llm, key_num)
    except ValueError as exc:
        return _refuse(str(exc))
    for name in llm_deletions:
        os.environ.pop(name, None)
    os.environ.update(llm_updates)
    # So the benchmark report can record which key a chunk ran on.
    os.environ["VVA_LLM_KEY_LABEL"] = llm_label
    print(
        "on_dev_branch: host app=%s owner=%s %s"
        % (_host(overrides["VVA_PG_DSN"]), _host(overrides["VVA_PG_DSN_OWNER"]),
           llm_label))
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
