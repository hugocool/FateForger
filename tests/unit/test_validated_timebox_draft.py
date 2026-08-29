"""A commit may use only the exact candidate that tmbx last validated."""

from __future__ import annotations

import json

from fateforger.slack_bot.validated_timebox_draft import (
    CANDIDATE_OUTPUT_FILE_ENV,
    claim_plan_apply_attempt,
    current_attempt,
    read_validated_candidate,
    record_validation_result,
    validated_commit_matches,
)


def _input(*, day: str = "2026-08-27") -> dict:
    return {
        "snapshot": {
            "calendar_id": "hugo.evers@gmail.com",
            "day": day,
            "tz": "Europe/Amsterdam",
            "etags": {},
            "event_ids": [],
        },
        "patch": {
            "ops": [
                {
                    "op": "add",
                    "h": "deep-work",
                    "n": "Deep work",
                    "t": "DW",
                    "p": {"a": "fs", "st": "09:00:00", "dur": "PT2H"},
                }
            ]
        },
    }


def _apply_event(*, committable: bool, day: str = "2026-08-27") -> dict:
    return {
        "tool_name": "mcp__tmbx__plan_apply",
        "hook_event_name": "PostToolUse",
        "tool_input": _input(day=day),
        "tool_response": json.dumps(
            {
                "ok": True,
                "committable": committable,
                "violations": [],
                "rendered": "09:00-11:00 Deep work",
            }
        ),
    }


def _commit_event(*, day: str = "2026-08-27") -> dict:
    return {"tool_name": "mcp__tmbx__plan_commit", "tool_input": _input(day=day)}


def test_exact_last_validated_candidate_matches(tmp_path):
    state = tmp_path / "validated-draft.json"

    record_validation_result(_apply_event(committable=True), str(state))

    assert validated_commit_matches(_commit_event(), str(state))


def test_model_mutating_the_snapshot_day_after_validation_fails_closed(tmp_path):
    state = tmp_path / "validated-draft.json"
    record_validation_result(_apply_event(committable=True), str(state))

    assert not validated_commit_matches(
        _commit_event(day="2026-08-28"), str(state)
    )


def test_failed_later_preview_clears_an_earlier_valid_candidate(tmp_path):
    state = tmp_path / "validated-draft.json"
    record_validation_result(_apply_event(committable=True), str(state))
    record_validation_result(_apply_event(committable=False), str(state))

    assert not validated_commit_matches(_commit_event(), str(state))


def test_reading_again_does_not_reset_the_per_turn_apply_budget(tmp_path):
    state = tmp_path / "validated-draft.json"
    claim_plan_apply_attempt(str(state))
    claim_plan_apply_attempt(str(state))

    record_validation_result(
        {
            "tool_name": "mcp__tmbx__plan_read",
            "hook_event_name": "PostToolUse",
        },
        str(state),
    )

    assert current_attempt(str(state)) == 2


def test_committable_preview_exports_the_exact_validated_candidate(tmp_path):
    state = tmp_path / "validated-draft.json"
    candidate = tmp_path / "candidate.json"

    record_validation_result(
        _apply_event(committable=True), str(state), str(candidate)
    )

    exported = read_validated_candidate(candidate)
    assert exported is not None
    assert exported.snapshot == _input()["snapshot"]
    assert exported.patch == _input()["patch"]
    assert len(exported.digest) == 64


def test_failed_later_preview_invalidates_exported_candidate(tmp_path):
    state = tmp_path / "validated-draft.json"
    candidate = tmp_path / "candidate.json"
    record_validation_result(
        _apply_event(committable=True), str(state), str(candidate)
    )

    record_validation_result(
        _apply_event(committable=False), str(state), str(candidate)
    )

    assert read_validated_candidate(candidate) is None


def test_candidate_output_env_name_is_stable_for_the_profile():
    assert CANDIDATE_OUTPUT_FILE_ENV == "FF_DSH_CANDIDATE_OUTPUT_FILE"
