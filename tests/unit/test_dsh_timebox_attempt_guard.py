"""A slow model cannot loop forever on rejected timebox patches."""

from __future__ import annotations

from fateforger.slack_bot.dsh_timebox_attempt_guard_hook import (
    _max_attempts,
    guard_decision,
)
from fateforger.slack_bot.progress_events import (
    ProgressFocus,
    ProgressPhase,
    ProgressSource,
    ProgressStatus,
    TimeboxProgressEvent,
)


def _apply() -> dict:
    return {
        "tool_name": "mcp__tmbx__plan_apply",
        "hook_event_name": "PreToolUse",
    }


def _patch() -> dict:
    return {
        "tool_name": "timebox_patch",
        "hook_event_name": "PreToolUse",
    }


def test_first_five_attempts_are_allowed_and_sixth_is_denied(tmp_path):
    state = tmp_path / "draft-state.json"

    decisions = [
        guard_decision(_apply(), str(state), max_attempts=5) for _ in range(6)
    ]

    assert decisions[:5] == [None] * 5
    assert decisions[5]["decision"] == "deny"
    assert "retry budget" in decisions[5]["reason"]


def test_environment_cannot_raise_the_hard_limit(monkeypatch):
    """Catches turning the safety ceiling into a soft default.

    The ceiling moved from five to eight on 2026-08-31, on evidence: the first
    session against a real calendar spent all five attempts and reported it was
    one five-minute change from converging. Five had been tuned against an
    in-memory calendar with an empty day, where nothing is fitted around
    anything.

    It is still a ceiling and the intent of this test is unchanged. Attempts are
    expensive -- the turn that exhausted five took 604 seconds -- so an
    unbounded budget only trades a failed turn for one nobody will wait out.
    """

    monkeypatch.setenv("FF_TIMEBOX_PATCH_MAX_ATTEMPTS", "99")

    assert _max_attempts() == 8


def test_exhaustion_emits_a_typed_terminal_progress_fact(tmp_path, monkeypatch):
    state = tmp_path / "draft-state.json"
    progress = tmp_path / "progress.jsonl"
    monkeypatch.setenv("FF_DSH_PROGRESS_FILE", str(progress))
    monkeypatch.setenv("FF_DSH_SESSION_KEY", "C1:1772.0")

    for _ in range(6):
        decision = guard_decision(_apply(), str(state), max_attempts=5)

    event = TimeboxProgressEvent.from_json(progress.read_text().splitlines()[-1])
    assert decision["decision"] == "deny"
    assert event.phase is ProgressPhase.REVISING_PATCH
    assert event.source is ProgressSource.RUNTIME
    assert event.status is ProgressStatus.FAILED
    assert event.attempt == 6
    assert event.refusal_code == "retry_budget_exhausted"


def test_patch_is_denied_until_the_skeleton_understanding_was_reported(tmp_path):
    progress = tmp_path / "progress.jsonl"
    progress.touch()

    decision = guard_decision(
        _patch(),
        str(tmp_path / "state.json"),
        progress_path=str(progress),
        max_attempts=5,
    )

    assert decision["decision"] == "deny"
    assert "report_skeleton_understanding" in decision["reason"]


def test_patch_is_allowed_after_the_typed_skeleton_report(tmp_path):
    progress = tmp_path / "progress.jsonl"
    progress.write_text(
        TimeboxProgressEvent(
            session_key="C1:1772.0",
            sequence=0,
            source=ProgressSource.AGENT,
            phase=ProgressPhase.UNDERSTANDING_SKELETON,
            status=ProgressStatus.SUCCEEDED,
            focus=ProgressFocus.APPROVED_OUTLINE,
            preserved_count=0,
            remaining_count=2,
        ).to_json()
        + "\n"
    )

    assert (
        guard_decision(
            _patch(),
            str(tmp_path / "state.json"),
            progress_path=str(progress),
            max_attempts=5,
        )
        is None
    )
