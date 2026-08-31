"""Catch a misfiled assumption while the planner can still fix it.

`9018f41` stopped a misfiled requirement id costing the whole turn: the kernel
now drops the assumption and keeps the artifact. That confines the damage and
loses the record — the planner made a judgement, and the note saying which
requirement it settled is thrown away.

The check itself runs in the kernel, after the harness process has exited, so
there is nobody left to tell. Every other check in this system refuses *during*
the turn and the model corrects: `plan_apply` returns violations and the planner
re-patches; `submit_planning_result` refuses a candidate with no captured patch
and the planner applies and resubmits.

So this moves the same check inside that window, and names the ids that would
have been accepted — a refusal the model cannot act on is the failure mode the
whole session has been about.
"""

import json

import pytest

from fateforger.slack_bot.planning_result_mcp import (
    OPEN_REQUIREMENTS_FILE_ENV,
    PLANNING_RESULT_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)
from fateforger.slack_bot.validated_timebox_draft import CANDIDATE_OUTPUT_FILE_ENV


@pytest.fixture
def turn(tmp_path, monkeypatch):
    result = tmp_path / "planning-result.json"
    result.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(result))
    captured = tmp_path / "candidate.json"
    captured.write_text('{"version": 1}', encoding="utf-8")
    monkeypatch.setenv(CANDIDATE_OUTPUT_FILE_ENV, str(captured))
    return tmp_path


def _open_requirements(turn, ids):
    path = turn / "open-requirements.json"
    path.write_text(json.dumps(ids), encoding="utf-8")
    return path


def _submit(**kw):
    base = dict(
        target_artifact="validated_candidate",
        artifact={"blocks": []},
        assumptions=[],
        blockers=[],
    )
    base.update(kw)
    return submit_planning_result(**base)


def _assumption(requirement_id):
    return [{
        "requirement_id": requirement_id,
        "value": "17:00",
        "why_needed": "the block needed a time",
    }]


def test_an_id_that_is_not_open_this_turn_is_refused(turn, monkeypatch) -> None:
    """The exact shape measured live: a real id, from the previous stage."""

    monkeypatch.setenv(
        OPEN_REQUIREMENTS_FILE_ENV,
        str(_open_requirements(turn, ["candidate.concrete_placements"])),
    )
    with pytest.raises(PlanningResultRefused) as caught:
        _submit(assumptions=_assumption("skeleton.ordinary_placement"))
    assert "skeleton.ordinary_placement" in str(caught.value)


def test_the_refusal_names_what_would_have_worked(turn, monkeypatch) -> None:
    """A refusal the model cannot act on just burns a step."""

    monkeypatch.setenv(
        OPEN_REQUIREMENTS_FILE_ENV,
        str(_open_requirements(turn, ["candidate.concrete_placements"])),
    )
    with pytest.raises(PlanningResultRefused) as caught:
        _submit(assumptions=_assumption("skeleton.ordinary_placement"))
    assert "candidate.concrete_placements" in str(caught.value)


def test_an_open_id_is_accepted(turn, monkeypatch) -> None:
    monkeypatch.setenv(
        OPEN_REQUIREMENTS_FILE_ENV,
        str(_open_requirements(turn, ["candidate.concrete_placements"])),
    )
    _submit(assumptions=_assumption("candidate.concrete_placements"))


def test_a_host_that_declares_nothing_does_not_block_the_turn(turn, monkeypatch) -> None:
    """Fails open, unlike the captured-patch guard, and deliberately.

    A host that provisions no candidate file cannot commit anything, so refusing
    is right there. Here the kernel still validates after the turn, so an
    unvalidatable submission is merely unchecked early -- refusing would break
    every submission on a host that simply does not publish its requirements.
    """

    monkeypatch.delenv(OPEN_REQUIREMENTS_FILE_ENV, raising=False)
    _submit(assumptions=_assumption("anything.at.all"))


def _brief_with(readiness):
    from datetime import UTC, date, datetime

    from fateforger.agents.timeboxing.session_contracts import (
        ArtifactKind,
        DayType,
        PlanningBrief,
        PlanningDay,
    )

    return PlanningBrief(
        session_key="C1:1.0", base_revision=1,
        observed_at=datetime(2026, 8, 31, tzinfo=UTC),
        locked_day=PlanningDay(
            date=date(2026, 8, 31), timezone="Europe/Amsterdam", iso_weekday=1,
            day_type=DayType.WORKING, classification_basis="calendar",
            lock_revision=1,
        ),
        facts=[], assumptions=[], current_artifacts=[], approvals=[],
        applicable_constraints=[], calendar_snapshot={},
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        readiness=readiness, allowed_outputs=set(),
    )


def test_the_bridge_publishes_the_open_planner_gaps() -> None:
    """The half that would make the whole check inert if it published nothing.

    A guard fed an empty list never fires, and every test above would still
    pass -- the shape of vacuous check this session has hit repeatedly.
    """

    from fateforger.slack_bot.harness_bridge import _planner_owned_open

    published = _planner_owned_open(_brief_with({
        "target_artifact": "validated_candidate",
        "gaps": [
            {"requirement_id": "candidate.concrete_placements",
             "owner": "planner", "satisfied": False},
            {"requirement_id": "candidate.calendar_snapshot",
             "owner": "system", "satisfied": False},
            {"requirement_id": "candidate.already_done",
             "owner": "planner", "satisfied": True},
        ],
    }))
    assert published == ["candidate.concrete_placements"]


def test_a_readiness_shape_it_does_not_recognise_publishes_nothing() -> None:
    """Empty means "not published", and the server then skips rather than
    refusing everything -- the kernel still validates after the turn."""

    from fateforger.slack_bot.harness_bridge import _planner_owned_open

    assert _planner_owned_open(_brief_with({})) == []
    assert _planner_owned_open(_brief_with({"gaps": "not a list"})) == []
