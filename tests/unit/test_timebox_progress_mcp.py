"""The model-authored reporter is separate from patching and tightly bounded."""

from __future__ import annotations

from fateforger.slack_bot.progress_events import (
    ProgressFocus,
    ProgressPhase,
    ProgressSelection,
    ProgressSource,
    ProgressStatus,
    ProgressTradeoff,
    TimeboxProgressEvent,
)
from fateforger.slack_bot.timebox_progress_mcp import (
    report_scheduling_decision,
    report_skeleton_understanding,
)


def test_skeleton_report_writes_one_typed_event_for_the_slack_host(
    tmp_path, monkeypatch
):
    destination = tmp_path / "progress.jsonl"
    monkeypatch.setenv("FF_DSH_PROGRESS_FILE", str(destination))
    monkeypatch.setenv("FF_DSH_SESSION_KEY", "C1:1772.0")

    answer = report_skeleton_understanding(
        focus="approved_outline",
        preserved_count=2,
        remaining_count=3,
    )

    event = TimeboxProgressEvent.from_json(destination.read_text().strip())
    assert answer == "Progress recorded. Continue the timebox work."
    assert event.source is ProgressSource.AGENT
    assert event.phase is ProgressPhase.UNDERSTANDING_SKELETON
    assert event.status is ProgressStatus.SUCCEEDED
    assert event.focus is ProgressFocus.APPROVED_OUTLINE
    assert event.preserved_count == 2
    assert event.remaining_count == 3


def test_decision_report_is_a_small_state_machine(tmp_path, monkeypatch):
    destination = tmp_path / "progress.jsonl"
    monkeypatch.setenv("FF_DSH_PROGRESS_FILE", str(destination))
    monkeypatch.setenv("FF_DSH_SESSION_KEY", "C1:1772.0")

    report_scheduling_decision(
        decision_state="opened",
        focus="exercise",
        option_count=2,
        selection=None,
        tradeoff="protect_duration",
    )
    report_scheduling_decision(
        decision_state="selected",
        focus="exercise",
        option_count=2,
        selection="place_earlier",
        tradeoff="protect_duration",
    )

    events = [
        TimeboxProgressEvent.from_json(line)
        for line in destination.read_text().splitlines()
    ]
    assert [event.decision_state for event in events] == ["opened", "selected"]
    assert all(event.phase is ProgressPhase.WEIGHING_OPTIONS for event in events)
    assert events[-1].selection is ProgressSelection.PLACE_EARLIER
    assert events[-1].tradeoff is ProgressTradeoff.PROTECT_DURATION


def test_reporting_without_a_slack_progress_file_is_a_noop(monkeypatch):
    monkeypatch.delenv("FF_DSH_PROGRESS_FILE", raising=False)
    monkeypatch.delenv("FF_DSH_SESSION_KEY", raising=False)

    assert (
        report_skeleton_understanding(
            focus="approved_outline",
            preserved_count=1,
            remaining_count=4,
        )
        == "Progress recorded. Continue the timebox work."
    )


def test_malformed_optional_report_is_ignored_without_blocking_the_run(
    tmp_path, monkeypatch
):
    destination = tmp_path / "progress.jsonl"
    monkeypatch.setenv("FF_DSH_PROGRESS_FILE", str(destination))
    monkeypatch.setenv("FF_DSH_SESSION_KEY", "C1:1772.0")

    answer = report_scheduling_decision(
        decision_state="selected",
        focus="exercise",
        option_count=2,
        selection=None,
        tradeoff="not_a_supported_code",
    )

    assert answer == "Progress ignored. Continue the timebox work."
    assert not destination.exists()
