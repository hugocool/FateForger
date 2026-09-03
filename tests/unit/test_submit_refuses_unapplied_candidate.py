"""Instructing the planner to apply first did not make it apply first.

Measured over 11 real candidate turns: 7 applied, 4 did not. The four all
short-circuit to the submit call, and two never even read the day:

    FAIL  memory_get_session_constraints -> submit_planning_result
    FAIL  ... -> report_skeleton_understanding -> submit_planning_result
    FAIL  ... plan_read -> report_skeleton_understanding -> submit_planning_result
    PASS  ... plan_read -> ... -> plan_apply -> submit_planning_result

Two instructions were added first -- the turn preamble (1a4cedb) and the tool
description (051f4eb) -- and the rate stayed at two thirds. `submit_planning_result`
is callable at any moment, so ending the turn early always satisfies the letter
of "end this turn by calling it once".

This is the profile's own argument about `toolFilter`, applied here: "calling
the wrong one is not instructed against, it is impossible." A candidate with no
captured patch cannot be committed, so submitting one is refused rather than
discouraged -- and refused *during* the turn, while the planner still has steps
left to call plan_apply and try again, instead of failing the whole turn after
it ends.
"""

import pytest

from fateforger.slack_bot.planning_result_mcp import (
    PlanningResultRefused,
    submit_planning_result,
)
from fateforger.slack_bot.validated_timebox_draft import CANDIDATE_OUTPUT_FILE_ENV
from fateforger.slack_bot.planning_result_mcp import PLANNING_RESULT_FILE_ENV


@pytest.fixture
def turn(tmp_path, monkeypatch):
    result = tmp_path / "planning-result.json"
    result.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(result))
    monkeypatch.setenv(CANDIDATE_OUTPUT_FILE_ENV, str(tmp_path / "candidate.json"))
    return tmp_path


def _submit(**kw):
    base = dict(target_artifact="validated_candidate", artifact={"blocks": []},
                assumptions=[], blockers=[])
    base.update(kw)
    return submit_planning_result(**base)


def test_a_candidate_with_no_captured_patch_is_refused(turn) -> None:
    with pytest.raises(PlanningResultRefused) as caught:
        _submit()
    assert "plan_apply" in str(caught.value)


def test_a_candidate_with_a_captured_patch_is_accepted(turn) -> None:
    (turn / "candidate.json").write_text('{"version": 1}', encoding="utf-8")
    _submit()


def test_a_skeleton_needs_no_patch(turn) -> None:
    """Only a candidate is committed, so only a candidate needs one."""

    _submit(target_artifact="skeleton", artifact={"markdown": "# Day"})


def test_a_blocker_needs_no_patch(turn) -> None:
    """A turn that asks the user a question produces no artifact to commit."""

    _submit(artifact=None, blockers=[{
        "requirement_id": "candidate.concrete_placements",
        "why_needed": "no free window is left for the gym",
    }])


def test_a_host_that_records_no_patch_is_a_failure_of_the_host(turn, monkeypatch) -> None:
    """Unset means refuse, not allow -- the same stance `_destination` takes.

    A host that provisions no candidate file cannot capture the patch a commit
    replays, so every candidate it produces would be uncommittable. Failing
    open would make that host indistinguishable from a working one right up
    until the user approved a plan that could never land.
    """

    monkeypatch.delenv(CANDIDATE_OUTPUT_FILE_ENV, raising=False)
    with pytest.raises(PlanningResultRefused):
        _submit()
