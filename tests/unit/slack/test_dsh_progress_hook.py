"""Progress from the harness must survive both transports and never block a run.

Two earlier attempts failed silently and are the reason this path exists: the
MCP servers' stderr went blind when they moved from stdio to `streamable-http`,
and a tail written inside `for line in proc.stdout` could never fire because
that loop blocks and a warm run emits nothing until the answer. A hook writing a
file, polled from its own thread, depends on neither.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.dsh_progress_hook import (
    DONE,
    PROGRESS_FILE_ENV,
    START,
    ProgressEvent,
    ProgressPhase,
    ProgressStatus,
    label_for,
    main,
    progress_event,
    step_line,
)
from fateforger.slack_bot.harness_bridge import compose_task
from fateforger.slack_bot.progress_events import (
    ProgressFocus,
    TimeboxProgressEvent,
)
from fateforger.slack_bot.progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)
from fateforger.slack_bot.progress_events import (
    ProgressSource as TimeboxProgressSource,
)
from fateforger.slack_bot.progress_events import (
    ProgressStatus as TimeboxProgressStatus,
)
from fateforger.slack_bot.validated_timebox_draft import (
    CANDIDATE_OUTPUT_FILE_ENV,
    DRAFT_STATE_FILE_ENV,
    claim_plan_apply_attempt,
    record_validation_result,
)

# -- the hook ------------------------------------------------------------


def test_it_reports_the_tool_under_a_name_a_person_would_use():
    """`mcp__memory__memory_get_suspended_constraints` is not a progress update.

    The checklist is read by someone waiting on their calendar, and a wall of
    mangled identifiers tells them only that something is happening — which
    they already knew, because they are waiting.
    """
    assert (
        step_line({"tool_name": "mcp__tmbx__plan_read"}) == f"{DONE}\tReading the day"
    )
    assert label_for("mcp__memory__memory_observe") == "Remembering what you said"


def test_a_tool_nobody_labelled_still_reports_itself():
    """Dropping it would make a slow run look like an idle one.

    An unlabelled step is ugly; a missing step is a lie about what the system
    is doing, and the whole point here is to stop silence being ambiguous.
    """
    assert step_line({"tool_name": "some_new_tool"}) == f"{DONE}\tsome_new_tool"


def test_a_call_that_started_is_distinguishable_from_one_that_finished():
    """Both hook points run this script, and the phase decides what Slack shows.

    Without it the first step appears only once the first tool has returned —
    measured at 5.6s into a turn, with nothing in the thread before it.
    """
    started = step_line(
        {"tool_name": "mcp__tmbx__plan_read", "hook_event_name": "PreToolUse"}
    )
    finished = step_line(
        {"tool_name": "mcp__tmbx__plan_read", "hook_event_name": "PostToolUse"}
    )
    assert started == f"{START}\tReading the day"
    assert finished == f"{DONE}\tReading the day"
    assert started != finished


def test_an_event_with_no_phase_is_read_as_finished():
    """A step opened and never resolved spins forever in the checklist."""
    assert step_line({"tool_name": "mcp__tmbx__plan_read"}).startswith(f"{DONE}\t")


def test_an_event_naming_no_tool_produces_no_step():
    """A blank name would render an empty checklist row that says nothing."""
    assert step_line({"tool_name": "   "}) is None
    assert step_line({}) is None


def test_plan_results_become_typed_safe_progress_without_payload_prose():
    read = progress_event(
        {
            "tool_name": "mcp__tmbx__plan_read",
            "hook_event_name": "PostToolUse",
            "tool_response": json.dumps(
                {
                    "ok": True,
                    "blocks": [{"name": "private"}, {"name": "also private"}],
                    "rendered": "SECRET CALENDAR CONTENT",
                }
            ),
        }
    )
    refused = progress_event(
        {
            "tool_name": "mcp__tmbx__plan_apply",
            "hook_event_name": "PostToolUse",
            "tool_response": json.dumps(
                {
                    "ok": False,
                    "reason": "invalid_patch",
                    "message": "SECRET MODEL-SHAPED PATCH DETAILS",
                }
            ),
        }
    )

    assert read is not None
    assert read.phase is ProgressPhase.READING_PLAN
    assert read.status is ProgressStatus.SUCCEEDED
    assert read.safe_detail == {"block_count": 2}

    assert refused is not None
    assert refused.phase is ProgressPhase.REVISING_PATCH
    assert refused.status is ProgressStatus.FAILED
    assert refused.safe_detail == {"refusal_reason": "invalid_patch"}

    serialized = read.to_line() + refused.to_line()
    assert "SECRET" not in serialized
    assert "private" not in serialized


def test_clean_preview_progress_carries_runtime_derived_block_count():
    event = progress_event(
        {
            "tool_name": "mcp__tmbx__plan_apply",
            "hook_event_name": "PostToolUse",
            "tool_response": json.dumps(
                {
                    "ok": True,
                    "committable": True,
                    "block_count": 10,
                    "violations": [],
                    "overspecified": [],
                    "rendered": "SECRET PLAN",
                }
            ),
        },
        attempt=2,
    )

    assert event is not None
    assert event.safe_detail == {
        "attempt": 2,
        "block_count": 10,
        "overspecified_count": 0,
    }
    assert "SECRET" not in event.to_line()


def test_the_reporter_tool_does_not_create_duplicate_lifecycle_noise():
    assert (
        progress_event(
            {
                "tool_name": "mcp__progress__report_skeleton_understanding",
                "hook_event_name": "PreToolUse",
            }
        )
        is None
    )


def test_it_writes_a_typed_event_where_the_bridge_will_look(
    tmp_path, monkeypatch, capsys
):
    destination = tmp_path / "steps"
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(destination))
    monkeypatch.setattr(
        "sys.stdin",
        _stdin(json.dumps({"tool_name": "mcp__memory__memory_get_active_constraints"})),
    )

    assert main() == 0
    lines = destination.read_text().splitlines()
    assert len(lines) == 1
    assert ProgressEvent.from_line(lines[0]) == ProgressEvent(
        phase=ProgressPhase.LOADING_CONSTRAINTS,
        status=ProgressStatus.SUCCEEDED,
    )
    # Exit 2 blocks the tool call and stdout is parsed as a decision, so a
    # reporter that wrote either would change what the agent is allowed to do.
    assert capsys.readouterr().out == ""


def test_plan_apply_progress_uses_the_runtime_owned_attempt_number(
    tmp_path, monkeypatch
):
    destination = tmp_path / "steps"
    state = tmp_path / "draft-state.json"
    claim_plan_apply_attempt(str(state))
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(destination))
    monkeypatch.setenv(DRAFT_STATE_FILE_ENV, str(state))
    monkeypatch.setattr(
        "sys.stdin",
        _stdin(
            json.dumps(
                {
                    "tool_name": "mcp__tmbx__plan_apply",
                    "hook_event_name": "PreToolUse",
                }
            )
        ),
    )

    assert main() == 0
    event = ProgressEvent.from_line(destination.read_text().strip())
    assert event.safe_detail["attempt"] == 1


def test_it_appends_rather_than_replacing(tmp_path, monkeypatch):
    """One process runs per tool call, so a truncating write keeps only the last."""
    destination = tmp_path / "steps"
    destination.write_text(f"{DONE}\tReading the day\n")
    monkeypatch.setenv(PROGRESS_FILE_ENV, str(destination))
    monkeypatch.setattr(
        "sys.stdin", _stdin(json.dumps({"tool_name": "mcp__tmbx__plan_apply"}))
    )

    main()
    lines = destination.read_text().splitlines()
    assert lines[0] == f"{DONE}\tReading the day"
    assert ProgressEvent.from_line(lines[1]) == ProgressEvent(
        phase=ProgressPhase.DRAFTING_PATCH,
        status=ProgressStatus.SUCCEEDED,
    )


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


def _drain(path: Path, seen: list, *, session_key: str = "test:unscoped") -> int:
    """Run the tail to completion over a file that is already written."""
    stop = threading.Event()
    stop.set()
    return harness_bridge._tail_progress(
        path, seen.append, stop, session_key=session_key
    )


def test_the_tail_discards_untyped_or_malformed_lines_fail_closed(tmp_path, caplog):
    path = tmp_path / "steps"
    path.write_text("plan_read\n<@U123> secret token xoxb-leak\n")
    seen: list[TimeboxProgressEvent] = []

    assert _drain(path, seen) == 0
    assert seen == []
    assert "discarded malformed progress" in caplog.text
    assert "xoxb-leak" not in caplog.text


def test_the_tail_delivers_typed_progress_events(tmp_path):
    event = ProgressEvent(
        phase=ProgressPhase.REVISING_PATCH,
        status=ProgressStatus.FAILED,
        safe_detail={"violation_count": 2, "violation_kinds": ["overlap"]},
    )
    path = tmp_path / "steps"
    path.write_text(event.to_line() + "\n")
    seen: list[TimeboxProgressEvent] = []

    assert _drain(path, seen, session_key="C1:1772.0") == 1
    assert len(seen) == 1
    delivered = seen[0]
    assert delivered.session_key == "C1:1772.0"
    assert delivered.sequence == 1
    assert delivered.source is TimeboxProgressSource.HARNESS_HOOK
    assert delivered.phase.value == event.phase.value
    assert delivered.status.value == event.status.value
    assert delivered.violation_count == 2
    assert delivered.violation_kinds == ("overlap",)


def test_started_and_finished_events_render_but_count_as_one_tool_call(tmp_path):
    started = ProgressEvent(
        phase=ProgressPhase.DRAFTING_PATCH,
        status=ProgressStatus.STARTED,
    )
    finished = ProgressEvent(
        phase=ProgressPhase.VALIDATING_PATCH,
        status=ProgressStatus.SUCCEEDED,
    )
    path = tmp_path / "steps"
    path.write_text(started.to_line() + "\n" + finished.to_line() + "\n")
    seen: list[TimeboxProgressEvent] = []

    completed_calls = _drain(path, seen, session_key="C1:1772.0")

    assert len(seen) == 2
    assert completed_calls == 1


def test_the_tail_resequences_direct_agent_progress_with_host_identity(tmp_path):
    event = TimeboxProgressEvent(
        session_key="model-supplied",
        sequence=0,
        source=TimeboxProgressSource.AGENT,
        phase=TimeboxProgressPhase.UNDERSTANDING_SKELETON,
        status=TimeboxProgressStatus.SUCCEEDED,
        focus=ProgressFocus.DEEP_WORK,
        preserved_count=2,
        remaining_count=3,
    )
    path = tmp_path / "steps"
    path.write_text(event.to_json() + "\n")
    seen: list[TimeboxProgressEvent] = []

    assert _drain(path, seen, session_key="C1:1772.0") == 0
    assert seen[0].session_key == "C1:1772.0"
    assert seen[0].sequence == 1
    assert seen[0].source is TimeboxProgressSource.AGENT


def test_a_half_written_line_is_held_back_until_its_newline(tmp_path):
    """Concurrent hooks append while the tail reads.

    Without holding the fragment back, one tool name is reported as two steps —
    a checklist that invents progress that did not happen.
    """
    path = tmp_path / "steps"
    complete = ProgressEvent(
        phase=ProgressPhase.READING_PLAN,
        status=ProgressStatus.SUCCEEDED,
    ).to_line()
    path.write_text(complete + "\n" + '{"version":1,"phase":"reading')
    seen: list[TimeboxProgressEvent] = []
    assert _drain(path, seen) == 1
    assert len(seen) == 1


def test_it_reports_a_step_before_the_run_has_finished(tmp_path, monkeypatch):
    """The whole point: progress must precede completion, not arrive with it.

    Also pins that the read offset advances — a tail that restarted at zero
    would report `plan_read` twice and show progress that never happened.
    """
    monkeypatch.setattr(harness_bridge, "_POLL_INTERVAL_S", 0.01)
    path = tmp_path / "steps"
    path.touch()
    seen: list[TimeboxProgressEvent] = []
    stop = threading.Event()
    worker = threading.Thread(
        target=lambda: harness_bridge._tail_progress(path, seen.append, stop),
        daemon=True,
    )
    worker.start()
    try:
        with path.open("a") as handle:
            handle.write(
                ProgressEvent(
                    phase=ProgressPhase.READING_PLAN,
                    status=ProgressStatus.STARTED,
                ).to_line()
                + "\n"
            )
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.01)
        # Observed while the tail is still running and stop is still clear.
        assert len(seen) == 1
        assert not stop.is_set()

        with path.open("a") as handle:
            handle.write(
                ProgressEvent(
                    phase=ProgressPhase.READING_PLAN,
                    status=ProgressStatus.SUCCEEDED,
                ).to_line()
                + "\n"
            )
    finally:
        stop.set()
        worker.join(timeout=5)
    assert len(seen) == 2


def test_a_failing_consumer_does_not_take_down_the_turn(tmp_path):
    """The progress channel describes the work; it must not be able to end it."""
    path = tmp_path / "steps"
    event = ProgressEvent(
        phase=ProgressPhase.READING_PLAN,
        status=ProgressStatus.SUCCEEDED,
    ).to_line()
    path.write_text(event + "\n" + event + "\n")
    calls: list[TimeboxProgressEvent] = []

    def explode(step: TimeboxProgressEvent) -> None:
        calls.append(step)
        raise RuntimeError("slack is down")

    stop = threading.Event()
    stop.set()
    assert harness_bridge._tail_progress(path, explode, stop) == 2
    assert len(calls) == 2


def test_a_missing_file_is_survivable(tmp_path):
    """The hook may never fire — a turn using no tools is not a failure."""
    seen: list[str] = []
    assert _drain(tmp_path / "never-created", seen) == 0
    assert seen == []


def test_cancellable_harness_starts_an_isolated_process_group(monkeypatch):
    captured: dict[str, object] = {}

    class _Process:
        returncode = 0

        def communicate(self, timeout=None):
            return "answer", ""

    def fake_popen(args, **kwargs):
        captured.update(kwargs)
        return _Process()

    monkeypatch.setattr(harness_bridge.subprocess, "Popen", fake_popen)

    done = harness_bridge._run_cancellable(
        ["node", "cli"],
        cwd="/tmp",
        env={},
        cancel_event=threading.Event(),
        timeout_s=1,
    )

    assert done.returncode == 0
    assert captured["start_new_session"] is True


def test_termination_signals_the_whole_process_group_before_reaping(monkeypatch):
    signals: list[tuple[int, int]] = []

    class _Process:
        pid = 4242

        def poll(self):
            return None

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(
        harness_bridge.os,
        "killpg",
        lambda pid, sig: signals.append((pid, sig)),
    )

    harness_bridge._terminate_process(_Process())

    assert signals == [(4242, harness_bridge.signal.SIGTERM)]


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
        captured["session_key"] = env["FF_DSH_SESSION_KEY"]
        captured["reasoning"] = env["FF_HARNESS_REASONING"]
        captured["fateforger_root"] = env["FF_FATEFORGER_ROOT"]
        # Stand in for the PostToolUse hook firing twice mid-run.
        event = ProgressEvent(
            phase=ProgressPhase.READING_PLAN,
            status=ProgressStatus.SUCCEEDED,
        ).to_line()
        Path(env[PROGRESS_FILE_ENV]).write_text(event + "\n" + event + "\n")
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)
    seen: list[TimeboxProgressEvent] = []
    reply = harness_bridge.ask(
        "plan tuesday",
        session_id="C1:1772.0",
        model=harness_bridge.PLANNING_MODEL,
        reasoning=harness_bridge.PLANNING_REASONING,
        on_event=seen.append,
    )

    assert len(seen) == 2
    assert captured["session_key"] == "C1:1772.0"
    # The caller's effort, not a constant. This asserted "low", which the
    # bridge hardcoded for every turn that named a model -- so the value the
    # planner chose could never reach the child and FF_HARNESS_REASONING was
    # inert on the one path that always sets a model.
    assert captured["reasoning"] == harness_bridge.PLANNING_REASONING
    assert captured["fateforger_root"] == str(
        Path(harness_bridge.__file__).resolve().parents[3]
    )
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


def test_ask_returns_the_exact_private_candidate_exported_by_the_hook(monkeypatch):
    candidate_input = {
        "snapshot": {"calendar_id": "primary", "day": "2026-08-29"},
        "patch": {"ops": [{"op": "add", "h": "DW1"}]},
    }

    class _Done:
        returncode = 0
        stdout = "the displayed plan"
        stderr = ""

    def fake_run(args, **kwargs):
        state = kwargs["env"][DRAFT_STATE_FILE_ENV]
        output = kwargs["env"][CANDIDATE_OUTPUT_FILE_ENV]
        record_validation_result(
            {
                "tool_name": "mcp__tmbx__plan_apply",
                "hook_event_name": "PostToolUse",
                "tool_input": candidate_input,
                "tool_response": json.dumps(
                    {
                        "ok": True,
                        "committable": True,
                        "rendered": "09:00-11:00 Canonical deep work",
                    }
                ),
            },
            state,
            output,
        )
        return _Done()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)

    reply = harness_bridge.ask("plan the day")

    assert reply.validated_candidate is not None
    assert reply.validated_candidate.snapshot == candidate_input["snapshot"]
    assert reply.validated_candidate.patch == candidate_input["patch"]
    assert reply.text == "09:00-11:00 Canonical deep work"


def test_a_failing_harness_still_raises_with_the_listener_attached(monkeypatch):
    class _Done:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(harness_bridge.subprocess, "run", lambda *a, **k: _Done())
    with pytest.raises(harness_bridge.HarnessError):
        harness_bridge.ask("plan tuesday", on_event=lambda step: None)


def test_cancelling_a_turn_terminates_the_owned_child_process(monkeypatch):
    """Cancelling asyncio alone left ``subprocess.run`` alive for minutes.

    Use a real sleeping child so this test proves process ownership at the OS
    boundary.  The only fake is the command selection; no harness, model, or
    Slack dependency is involved.
    """

    monkeypatch.setattr(
        harness_bridge,
        "_cli_args",
        lambda _task, _profile: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ],
    )
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(harness_bridge.HarnessCancelled):
            harness_bridge.ask("plan tuesday", cancel_event=cancel)
    finally:
        timer.cancel()

    assert time.monotonic() - started < 2.0


# -- the commit gate's approval file --------------------------------------


class _Ok:
    returncode = 0
    stdout = "done"
    stderr = ""


def _capture_env(monkeypatch) -> dict:
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs["env"])
        return _Ok()

    monkeypatch.setattr(harness_bridge.subprocess, "run", fake_run)
    return captured


def test_the_gate_is_told_where_approval_will_be_written(tmp_path, monkeypatch):
    env = _capture_env(monkeypatch)
    target = tmp_path / "approval-token"
    harness_bridge.ask("plan tuesday", approval_file=target)
    assert env[harness_bridge.APPROVAL_FILE_ENV] == str(target.resolve())


def test_the_path_reaches_the_hook_absolute(tmp_path, monkeypatch):
    """The hook runs with the session workspace as its cwd.

    A relative path would resolve somewhere neither side agreed on, which the
    gate reads as "no approval" — denying a commit the user actually granted.
    """
    monkeypatch.chdir(tmp_path)
    env = _capture_env(monkeypatch)
    harness_bridge.ask("plan tuesday", approval_file="approval-token")
    assert Path(env[harness_bridge.APPROVAL_FILE_ENV]).is_absolute()


def test_without_an_approval_path_the_variable_is_absent(monkeypatch):
    """Absence is not neutral — the gate denies without it, which is the safe
    direction. What must not happen is this module inventing a path the Slack
    button handler cannot find, leaving the gate looking configured while
    denying everything."""
    env = _capture_env(monkeypatch)
    harness_bridge.ask("plan tuesday")
    assert harness_bridge.APPROVAL_FILE_ENV not in env


# -- the thread's identity ------------------------------------------------


def test_a_turn_carries_the_session_id_it_was_given():
    """The harness spawns a fresh process per turn and remembers nothing.

    What survives is the memory server, keyed by this id. Without it in the
    task the model cannot read back what the thread established, and the
    memory policy's instruction that both tools share "the *same* value" names
    a value nothing supplies.
    """
    task = compose_task("plan tomorrow", session_id="C123:1772.9")
    assert "C123:1772.9" in task
    assert "plan tomorrow" in task


def test_it_tells_the_model_to_read_the_session_back_not_only_to_write_it():
    """Recording without reading is a store nobody queries."""
    task = compose_task("plan tomorrow", session_id="C1:1.0")
    assert "memory_get_session_constraints" in task
    assert "memory_observe" in task


def test_without_a_session_id_the_task_is_the_bare_text():
    """Headless callers have no thread, and must not be handed a fake one.

    An invented id would write into a session nothing ever reads — which is
    exactly the failure this parameter exists to fix, reintroduced one level
    up.
    """
    assert compose_task("plan tomorrow") == "plan tomorrow"


def test_history_and_session_id_compose_rather_than_replace():
    """A caller that genuinely holds a transcript keeps working."""
    task = compose_task(
        "and the gym?",
        history=[("Hugo", "plan tomorrow"), ("agent", "which day?")],
        session_id="C1:1.0",
    )
    assert "C1:1.0" in task and "which day?" in task and "and the gym?" in task


def test_proposed_timebox_is_explicit_draft_input_not_calendar_state():
    """A follow-up edits the displayed proposal even when it was never committed."""

    task = compose_task(
        "move Focus Audit to 10:00",
        proposed_timebox="FA1 Focus Audit 09:00-10:00",
        proposed_calendar_id="primary",
        proposed_day="2026-08-29",
        session_id="C1:1.0",
    )

    assert "Current proposed timebox" in task
    assert "not evidence that it is already on the calendar" in task
    assert "FA1 Focus Audit 09:00-10:00" in task
    assert "calendar `primary` for day `2026-08-29`" in task
