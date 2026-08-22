"""Progress from the harness must survive both transports and never block a run.

Two earlier attempts failed silently and are the reason this path exists: the
MCP servers' stderr went blind when they moved from stdio to `streamable-http`,
and a tail written inside `for line in proc.stdout` could never fire because
that loop blocks and a warm run emits nothing until the answer. A hook writing a
file, polled from its own thread, depends on neither.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.dsh_progress_hook import PROGRESS_FILE_ENV, main, step_line


# -- the hook ------------------------------------------------------------


def test_it_reports_the_tool_the_harness_named():
    assert step_line({"tool_name": "plan_read"}) == "plan_read"


def test_an_event_naming_no_tool_produces_no_step():
    """A blank name would render an empty checklist row that says nothing."""
    assert step_line({"tool_name": "   "}) is None
    assert step_line({}) is None


def test_it_writes_the_step_where_the_bridge_will_look(tmp_path, monkeypatch, capsys):
    destination = tmp_path / "steps"
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(destination))
    monkeypatch.setattr(
        "sys.stdin", _stdin(json.dumps({"tool_name": "memory_get_active_constraints"}))
    )

    assert main() == 0
    assert destination.read_text().splitlines() == ["memory_get_active_constraints"]
    # Exit 2 blocks the tool call and stdout is parsed as a decision, so a
    # reporter that wrote either would change what the agent is allowed to do.
    assert capsys.readouterr().out == ""


def test_it_appends_rather_than_replacing(tmp_path, monkeypatch):
    """One process runs per tool call, so a truncating write keeps only the last."""
    destination = tmp_path / "steps"
    destination.write_text("plan_read\n")
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(destination))
    monkeypatch.setattr("sys.stdin", _stdin(json.dumps({"tool_name": "plan_write"})))

    main()
    assert destination.read_text().splitlines() == ["plan_read", "plan_write"]


def test_without_the_env_var_it_does_nothing_at_all(tmp_path, monkeypatch, capsys):
    """A headless run has no listener; it must not pay for a feature it lacks."""
    monkeypatch.delenv(PROGRESS_FILE_ENV, raising=False)
    monkeypatch.setattr("sys.stdin", _stdin("not json at all"))
    assert main() == 0
    assert capsys.readouterr().out == ""


def test_a_malformed_event_is_loud_but_never_blocks(tmp_path, monkeypatch, capsys):
    """Exit 2 would fail the tool call. The complaint belongs on stderr."""
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(tmp_path / "steps"))
    monkeypatch.setattr("sys.stdin", _stdin("{not json"))

    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "could not parse" in captured.err


def test_an_unwritable_destination_does_not_fail_the_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(tmp_path / "nope" / "steps"))
    monkeypatch.setattr("sys.stdin", _stdin(json.dumps({"tool_name": "plan_read"})))

    assert main() == 0
    assert "could not write progress" in capsys.readouterr().err


class _stdin:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> str:
        return self._payload


# -- the tail ------------------------------------------------------------


def _drain(path: Path, seen: list[str]) -> int:
    """Run the tail to completion over a file that is already written."""
    stop = threading.Event()
    stop.set()
    return harness_bridge._tail_progress(path, seen.append, stop)


def test_the_tail_reports_every_completed_step(tmp_path):
    path = tmp_path / "steps"
    path.write_text("plan_read\nmemory_get_active_constraints\n")
    seen: list[str] = []
    assert _drain(path, seen) == 2
    assert seen == ["plan_read", "memory_get_active_constraints"]


def test_a_half_written_line_is_held_back_until_its_newline(tmp_path):
    """Concurrent hooks append while the tail reads.

    Without holding the fragment back, one tool name is reported as two steps —
    a checklist that invents progress that did not happen.
    """
    path = tmp_path / "steps"
    path.write_text("plan_read\nmemory_get_act")
    seen: list[str] = []
    assert _drain(path, seen) == 1
    assert seen == ["plan_read"]


def test_it_reports_a_step_before_the_run_has_finished(tmp_path, monkeypatch):
    """The whole point: progress must precede completion, not arrive with it.

    Also pins that the read offset advances — a tail that restarted at zero
    would report `plan_read` twice and show progress that never happened.
    """
    monkeypatch.setattr(harness_bridge, "_POLL_INTERVAL_S", 0.01)
    path = tmp_path / "steps"
    path.touch()
    seen: list[str] = []
    stop = threading.Event()
    worker = threading.Thread(
        target=lambda: harness_bridge._tail_progress(path, seen.append, stop),
        daemon=True,
    )
    worker.start()
    try:
        with path.open("a") as handle:
            handle.write("plan_read\n")
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        # Observed while the tail is still running and stop is still clear.
        assert seen == ["plan_read"]
        assert not stop.is_set()

        with path.open("a") as handle:
            handle.write("plan_write\n")
    finally:
        stop.set()
        worker.join(timeout=5)
    assert seen == ["plan_read", "plan_write"]


def test_a_failing_consumer_does_not_take_down_the_turn(tmp_path):
    """The progress channel describes the work; it must not be able to end it."""
    path = tmp_path / "steps"
    path.write_text("plan_read\nplan_write\n")
    calls: list[str] = []

    def explode(step: str) -> None:
        calls.append(step)
        raise RuntimeError("slack is down")

    stop = threading.Event()
    stop.set()
    assert harness_bridge._tail_progress(path, explode, stop) == 2
    assert calls == ["plan_read", "plan_write"]


def test_a_missing_file_is_survivable(tmp_path):
    """The hook may never fire — a turn using no tools is not a failure."""
    seen: list[str] = []
    assert _drain(tmp_path / "never-created", seen) == 0
    assert seen == []


# -- the bridge ----------------------------------------------------------


def test_ask_points_the_hook_at_a_file_and_reports_what_lands(monkeypatch):
    """End to end across the seam, with the harness itself stubbed."""
    captured: dict[str, object] = {}

    class _Done:
        returncode = 0
        stdout = "the plan"
        stderr = ""

    def fake_run(args, **kwargs):
        env = kwargs["env"]
        captured["path"] = env[PROGRESS_FILE_ENV]
        # Stand in for the PostToolUse hook firing twice mid-run.
        Path(env[PROGRESS_FILE_ENV]).write_text("plan_read\nplan_write\n")
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)
    seen: list[str] = []
    reply = harness_bridge.ask("plan tuesday", on_event=seen.append)

    assert seen == ["plan_read", "plan_write"]
    assert reply.text == "the plan"
    assert (reply.timings or {})["tool_calls"] == 2


def test_without_a_listener_the_hook_is_told_nothing(monkeypatch):
    """No consumer means the env var is unset and every hook process no-ops."""

    class _Done:
        returncode = 0
        stdout = "the plan"
        stderr = ""

    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured["env"] = kwargs["env"]
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)
    harness_bridge.ask("plan tuesday")
    assert PROGRESS_FILE_ENV not in captured["env"]


def test_a_failing_harness_still_raises_with_the_listener_attached(monkeypatch):
    class _Done:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda *a, **k: _Done())
    with pytest.raises(harness_bridge.HarnessError):
        harness_bridge.ask("plan tuesday", on_event=lambda step: None)
