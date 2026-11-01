"""Tests for B4 (boundary: shared code never imports loop/) and C1
(run_loop_probes.py dry-run row schema)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from langgraph_agents.local_tests.agent_loop.run_loop_probes import main as run_probes_main


# B4: with AGENT_MODE unset, importing api.main must not load loop/.
def test_main_import_does_not_load_loop():
    code = (
        "import os, sys\n"
        "sys.path.insert(0, 'agenticRAG')\n"
        "os.environ.pop('AGENT_MODE', None)\n"
        "try:\n"
        "    import langgraph_agents.api.main  # noqa: F401\n"
        "except ImportError:\n"
        "    pass  # vva_motion not available in this env\n"
        "print('loop_loaded', 'langgraph_agents.loop' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd="D:\\Swin documents\\Virtual-Verbal-Assistant",
    )
    assert result.returncode == 0, result.stderr
    assert "loop_loaded False" in result.stdout


# C1: run_loop_probes.py in --dry-run writes rows matching the run_probes.py
# schema, with FakeAgentModel (no network).
def test_run_loop_probes_dry_run_row_schema(monkeypatch):
    from pathlib import Path
    HERE = Path(__file__).resolve().parents[3] / "agenticRAG" / "langgraph_agents" / "local_tests" / "agent_loop"
    out = HERE / "results" / "dry-test.jsonl"
    # Clean slate.
    if out.exists():
        out.unlink()
    try:
        # Run the CLI in dry-run mode for P1 and P2, one repeat each.
        exit_code = run_probes_main([
            "--probes", "P1,P2",
            "--repeats", "1",
            "--label", "dry-test",
            "--dry-run",
        ])
        assert exit_code == 0

        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert len(rows) == 2  # P1 and P2
        for row in rows:
            assert row["probe"] in ("P1", "P2")
            assert row["mode"] == "dry-run"
            assert row["effort"] == "low"
            assert isinstance(row["pass"], bool)
            assert isinstance(row["rounds"], int)
            assert isinstance(row["tool_calls"], int)
            assert isinstance(row["final_answer"], str)
            assert isinstance(row["elapsed_s"], (int, float))
            assert "call_log" in row and isinstance(row["call_log"], list)
            assert "failed_check" in row
            assert "stop_reason" in row
    finally:
        if out.exists():
            out.unlink()
