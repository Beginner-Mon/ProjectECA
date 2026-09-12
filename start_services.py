"""Launch all ECA dev services, each in its own PowerShell window.

Usage:
    python start_services.py                # start everything (docker excluded)
    python start_services.py --only backend frontend
    python start_services.py --skip tts     # start all except TTS
    python start_services.py --only docker  # optional SearXNG fallback
    python start_services.py --list         # show services and exit

Notes:
- Kimodo + web_search MCP servers are NOT here — the backend spawns them as
  stdio subprocesses automatically (see config/mcp_servers.yaml).
- No Redis by default. TTS now streams audio to the agent over SSE instead of
  going through a Redis-backed task queue, and STM runs without Redis
  (STM_BACKEND=none unless a developer's own .env already says otherwise — see
  _stm_backend_configured below). `docker` is opt-in — `--only docker` — and
  starts SearXNG only, for the rare case that needs it; Docker Desktop must
  already be running for that. Postgres is NOT started: the database lives on
  Neon (VVA_PG_DSN in agenticRAG/.env).
- The backend needs text-to-motion/kimodo on PYTHONPATH or it will not import;
  this launcher sets it. See BACKEND_PYTHONPATH.
- Each window stays open after the process exits (-NoExit) so you can read logs
  / errors. Close the window to stop that service.

Ports: backend :8000, UI :5173, TTS :5000, SearXNG :6666.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Windows consoles default to a legacy code page (cp1252 here), and this file
# prints arrows, check marks and em-dashes — in the service notes, in the launch
# line, and in the module docstring that --help echoes. Every one of those paths
# died with UnicodeEncodeError before this block, so the launcher crashed on a
# machine whose console had not been switched to UTF-8.
#
# errors="replace" as well as utf-8: a console that still cannot render a glyph
# should print "?" and carry on, not take the launcher down. Guarded because a
# wrapped or redirected stdout may not expose reconfigure().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# ── Import roots the backend needs on PYTHONPATH ─────────────────────────────
#
# `langgraph_agents/api/motion_status.py` does `from vva_motion.jobs import ...`
# at module scope, and `vva_motion` lives under text-to-motion/kimodo — a
# sibling top-level directory, not a dependency. Without it uvicorn dies at
# import with "ModuleNotFoundError: No module named 'vva_motion'" before the app
# is ever constructed. In the deployed image the question does not arise:
# agenticRAG/Dockerfile COPYs vva_motion to /var/task/vva_motion.
#
# This is the THIRD copy of this list, and the other two are deliberately not
# reused:
#
#   pytest.ini            adds SpeechLLm as well, because the test suite imports
#                         it. Do NOT borrow that list — a surplus root SHADOWS
#                         modules (sys.modules has one slot per top-level name),
#                         which is the whole reason pytest.ini excludes
#                         text-to-motion/DART. SpeechLLm/api_server.py would be
#                         one such collision here.
#   .github/workflows/deploy-agent.yml:111
#                         same two roots as here, POSIX-separated, for the
#                         import smoke test.
#
# If this list changes, check whether those two need the same change.
BACKEND_PYTHONPATH = ["agenticRAG", "text-to-motion/kimodo"]

# ── Service registry ─────────────────────────────────────────────────────────
# Each service: a shell command to run inside a fresh PowerShell window.
#   cwd        : working dir (relative to repo root)
#   conda_env  : conda env to activate first (None = no activation)
#   pythonpath : repo-relative roots to put on PYTHONPATH (absent = none)
#   command    : the actual command to run
#   note       : printed in --list
SERVICES: dict[str, dict] = {
    "docker": {
        "cwd": ".",
        "conda_env": None,
        # Redis dropped from this command. It served two things and both are
        # gone: TTS now streams audio to the agent over SSE instead of going
        # through a Redis-backed task queue, and STM runs with
        # STM_BACKEND=none (see _stm_backend_configured() below) because the
        # owner's machine has neither Docker nor Redis installed. That leaves
        # only SearXNG, and even that is opt-in now (see DEFAULT_ORDER) rather
        # than started by default.
        #
        # The compose file still defines a local pgvector service, but the
        # database moved to Neon (VVA_PG_DSN in agenticRAG/.env points at
        # ep-snowy-sky-...neon.tech). Starting it anyway spends RAM on a
        # database nothing connects to.
        #
        # To bring Redis back for a one-off reason, the service definition is
        # still in the compose file, just not started here:
        #     docker compose -f docker-compose.langgraph.yml up redis
        "command": "docker compose -f docker-compose.langgraph.yml up searxng",
        "note": "Optional SearXNG :6666 fallback (needs Docker Desktop) — not started by default",
    },
    "backend": {
        "cwd": "agenticRAG",
        "conda_env": "firstconda",
        "pythonpath": BACKEND_PYTHONPATH,
        # Port 8000, matching VITE_API_GATEWAY_URL in ECA_UI/frontend/.env.local.
        # It said 8080 until 04/09, so the frontend called a port nothing served
        # and every request failed with ERR_CONNECTION_REFUSED.
        "command": "python -m uvicorn langgraph_agents.api.main:create_app --factory --port 8000",
        "note": "LangGraph FastAPI :8000 (spawns Kimodo + web_search MCP itself)",
    },
    "frontend": {
        "cwd": "ECA_UI/frontend",
        "conda_env": None,
        # The Vite app. This entry used to be `python -m http.server 3000` from
        # ECA_UI/, which has no index.html — it served a bare directory listing,
        # not the product.
        "command": "npm run dev",
        "note": "ECA UI (Vite) :5173  →  http://localhost:5173",
    },
    "tts": {
        "cwd": "SpeechLLm",
        "conda_env": "tts",
        "command": "python -m uvicorn api_server:app --host 127.0.0.1 --port 5000",
        "note": (
            "VieNeu TTS :5000 — streams audio to the agent over SSE (no Redis); "
            "/health answers 503 for ~14s while the model + reference voices warm up"
        ),
    },
    # Static debug harnesses (health-test, sse-test). Off by default — they are
    # not part of the product and only one person at a time ever wants them.
    #   python start_services.py --only test-ui
    "test-ui": {
        "cwd": "ECA_UI/test-ui",
        "conda_env": None,
        "command": "python -m http.server 3000",
        "note": "Debug harnesses :3000 (health-test/, sse-test/) — not started by default",
    },
}


def _stm_backend_configured() -> bool:
    """True if STM_BACKEND is already decided, by the process env or by the
    developer's own agenticRAG/.env, and this launcher should leave it alone.

    python-dotenv (loaded by the backend on startup) does NOT override a
    variable already set in the process environment, so forcing
    STM_BACKEND=none unconditionally here would silently defeat a developer's
    own .env choice — e.g. someone who does have Redis running locally and
    wants STM backed by it. This only scans agenticRAG/.env for the KEY,
    never opens it for a VALUE: that file holds secrets.
    """
    if "STM_BACKEND" in os.environ:
        return True
    env_file = ROOT / "agenticRAG" / ".env"
    if not env_file.is_file():
        return False
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().startswith("STM_BACKEND="):
                    return True
    except OSError:
        return False
    return False


# The owner's machine has neither Docker nor Redis, so STM defaults to
# STM_BACKEND=none unless a developer has already made their own choice (see
# _stm_backend_configured above).
if not _stm_backend_configured():
    SERVICES["backend"]["env"] = {"STM_BACKEND": "none"}

# Order matters: backend first, then frontend, then tts.
# `docker` and `test-ui` are both absent from DEFAULT_ORDER on purpose — both
# are opt-in via `--only <name>`. See their entries above for why.
DEFAULT_ORDER = ["backend", "frontend", "tts"]

# Everything selectable, default set first. `--list` and `--only` read this, not
# DEFAULT_ORDER: a service that is opt-in must still be listable and startable,
# and filtering `--only` against DEFAULT_ORDER would silently match nothing for
# exactly the services that can only be reached through `--only`.
ALL_SERVICES = DEFAULT_ORDER + [n for n in SERVICES if n not in DEFAULT_ORDER]


def find_conda_hook() -> Path | None:
    """Locate conda's PowerShell hook, or None if conda cannot be found.

    A bare `conda activate` only works in a shell that has been through
    `conda init`, which writes a hook into the user's PowerShell profile. On a
    machine where that never ran — conda not on PATH, no profile — every window
    this launcher opened for a conda service died on
    "conda : The term 'conda' is not recognized", in a separate window, several
    seconds after the launcher had already reported success.

    Dot-sourcing the hook ourselves is exactly what `conda init` would have
    installed, so activation behaves identically without touching the user's
    profile. Preferred over `<root>/envs/<name>/python.exe` because that skips
    activation entirely, and on Windows that means the env's Library/bin never
    joins the DLL search path.
    """
    candidates: list[Path] = []

    # Set whenever this script is itself run from a conda env — the cheapest and
    # most accurate source, since it points at the installation actually in use.
    for var in ("CONDA_ROOT", "CONDA_PREFIX_1", "CONDA_PREFIX"):
        value = os.environ.get(var)
        if value:
            candidates.append(Path(value))
    exe = os.environ.get("CONDA_EXE")
    if exe:
        # <root>/Scripts/conda.exe  or  <root>/condabin/conda.bat
        candidates.append(Path(exe).resolve().parent.parent)

    home = Path.home()
    candidates += [
        Path("C:/Miniconda"), Path("C:/Miniconda3"), Path("C:/Anaconda3"),
        Path("C:/ProgramData/Miniconda3"), Path("C:/ProgramData/Anaconda3"),
        home / "miniconda3", home / "anaconda3",
        home / "AppData/Local/miniconda3", home / "AppData/Local/Continuum/anaconda3",
    ]

    seen: set[Path] = set()
    for root in candidates:
        if root in seen:
            continue
        seen.add(root)
        hook = root / "shell" / "condabin" / "conda-hook.ps1"
        if hook.is_file():
            return hook
    return None


def build_ps_command(svc: dict, conda_hook: Path | None = None) -> str:
    """Build the -Command string run inside the new PowerShell window."""
    cwd = (ROOT / svc["cwd"]).resolve()
    parts = [f"Set-Location '{cwd}'"]
    if svc["conda_env"]:
        if conda_hook is not None:
            parts.append(f"& '{conda_hook}'")
        parts.append(f"conda activate {svc['conda_env']}")
    if svc.get("pythonpath"):
        # Absolute, because `cwd` is the service directory and a relative root
        # would resolve against it rather than the repo. os.pathsep so this
        # stays correct if the launcher is ever ported off PowerShell.
        roots = os.pathsep.join(str((ROOT / p).resolve()) for p in svc["pythonpath"])
        # Set AFTER `conda activate`: activation replays the env's own
        # configured vars and would overwrite an assignment made before it.
        parts.append(f"$env:PYTHONPATH = '{roots}'")
    if svc.get("env"):
        # Same ordering reason as PYTHONPATH above: `conda activate` replays
        # the env's own configured vars and would clobber an assignment made
        # before it, so per-service env vars are set after activation too.
        for key, value in svc["env"].items():
            parts.append(f"$env:{key} = '{value}'")
    parts.append(svc["command"])
    return "; ".join(parts)


def launch(name: str, svc: dict, conda_hook: Path | None = None) -> None:
    title = f"ECA · {name}"
    inner = build_ps_command(svc, conda_hook)
    # Set window title, then run the service command. -NoExit keeps the window
    # open so logs/errors stay visible after the process ends.
    ps = f"$host.UI.RawUI.WindowTitle = '{title}'; {inner}"
    subprocess.Popen(
        ["powershell", "-NoExit", "-Command", ps],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    print(f"  ✓ launched {name:<9} → {svc['command']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", metavar="SVC", help="start only these services")
    ap.add_argument("--skip", nargs="+", metavar="SVC", help="start all except these")
    ap.add_argument("--list", action="store_true", help="list services and exit")
    args = ap.parse_args()

    if args.list:
        print("Available services:\n")
        for name in ALL_SERVICES:
            default = " " if name in DEFAULT_ORDER else "*"
            print(f"  {default} {name:<10} {SERVICES[name]['note']}")
        print("\n  * not started by default — use --only")
        return 0

    if sys.platform != "win32":
        print("This launcher uses PowerShell windows — Windows only.", file=sys.stderr)
        return 1

    # Validate BEFORE filtering: a typo in --only used to fall through to the
    # empty-selection branch below and report "Nothing to start", which names
    # neither the bad argument nor the valid ones.
    unknown = set((args.only or []) + (args.skip or [])) - set(SERVICES)
    if unknown:
        print(f"Unknown service(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        print(f"Valid: {', '.join(ALL_SERVICES)}", file=sys.stderr)
        return 1

    selected = list(DEFAULT_ORDER)
    if args.only:
        selected = [s for s in ALL_SERVICES if s in args.only]
    if args.skip:
        selected = [s for s in selected if s not in args.skip]

    if not selected:
        print("Nothing to start.", file=sys.stderr)
        return 1

    # Fail here rather than in four separate windows. Each service runs in its
    # own console, so a missing prerequisite used to surface as a window that
    # flashed an error and sat there — after this launcher had already printed
    # "launched" for it.
    needs_conda = [n for n in selected if SERVICES[n]["conda_env"]]
    conda_hook = find_conda_hook() if needs_conda else None
    if needs_conda and conda_hook is None:
        print(
            f"Could not find conda. These need it: {', '.join(needs_conda)}",
            file=sys.stderr,
        )
        print(
            "Set CONDA_ROOT to your install (the folder holding "
            "shell/condabin/conda-hook.ps1), or start the rest with "
            f"--skip {' '.join(needs_conda)}",
            file=sys.stderr,
        )
        return 1

    print(f"Starting {len(selected)} service(s), each in its own PowerShell window:\n")
    for name in selected:
        launch(name, SERVICES[name], conda_hook)

    print("\nDone. Close a window to stop that service.")
    print("Health check once backend is up:  curl http://localhost:8000/health/detailed")
    print("UI:                               http://localhost:5173")
    return 0


if __name__ == "__main__":
    sys.exit(main())
