"""Tests for the `timebox_log_query.py llm` command."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run_llm_query(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "scripts/dev/timebox_log_query.py", "llm", *args]
    return subprocess.run(
        cmd,
        cwd=_repo_root(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_llm_query_filters_by_session_and_call_label(tmp_path: Path) -> None:
    """LLM subcommand should return only matching rows."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    llm_path = log_dir / "llm_io_20260227_120000_12345.jsonl"
    rows = [
        {
            "session_key": "s1",
            "thread_ts": "100.1",
            "call_label": "planning_date",
            "model": "google/gemini-3",
            "status": "ok",
        },
        {
            "session_key": "s2",
            "thread_ts": "100.2",
            "call_label": "patcher_attempt_1",
            "model": "google/gemini-3",
            "status": "error",
        },
    ]
    llm_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["OBS_LLM_AUDIT_LOG_DIR"] = str(log_dir)
    env["OBS_LLM_AUDIT_INDEX_FILE"] = "llm_io_index.jsonl"

    result = _run_llm_query(
        "--session-key",
        "s1",
        "--call-label",
        "planning_date",
        "--limit",
        "10",
        env=env,
    )

    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["session_key"] == "s1"
    assert parsed["call_label"] == "planning_date"


def test_llm_query_returns_nonzero_when_no_logs(tmp_path: Path) -> None:
    """LLM subcommand should fail fast when no log files exist."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["OBS_LLM_AUDIT_LOG_DIR"] = str(log_dir)
    env["OBS_LLM_AUDIT_INDEX_FILE"] = "llm_io_index.jsonl"

    result = _run_llm_query("--session-key", "missing", env=env)

    assert result.returncode == 1
