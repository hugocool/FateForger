"""A candidate without a required kind is refused while the planner can still
add it (#214, spec §2).

Same position as the captured-patch guard: `submit_planning_result` is callable
at any moment, so the refusal has to be inside the turn, and it names the slug
because a refusal the model cannot act on only burns a step. Presence is read
from the captured `plan_apply` (ops and rows), never from the artifact prose.
"""
import json

import pytest

from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    REQUIRED_BLOCKS_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)
from fateforger.slack_bot.validated_timebox_draft import CANDIDATE_OUTPUT_FILE_ENV


@pytest.fixture
def turn(tmp_path, monkeypatch):
    (tmp_path / "planning-result.json").touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(tmp_path / "planning-result.json"))
    monkeypatch.setenv(CANDIDATE_OUTPUT_FILE_ENV, str(tmp_path / "candidate.json"))
    monkeypatch.setenv(REQUIRED_BLOCKS_FILE_ENV, str(tmp_path / "required-blocks.json"))
    return tmp_path


def _captured(turn, ops, rows=()):
    (turn / "candidate.json").write_text(json.dumps({
        "version": 1, "snapshot": {"event_ids": {}}, "patch": {"ops": ops}, "rows": list(rows),
    }), encoding="utf-8")


def _require(turn, slugs):
    (turn / "required-blocks.json").write_text(json.dumps(slugs), encoding="utf-8")


def _submit():
    return submit_planning_result(
        target_artifact="validated_candidate", artifact={"blocks": []},
        assumptions=[], blockers=[],
    )


def test_a_candidate_missing_a_required_kind_is_refused_by_name(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "DW1", "slug": None}])
    with pytest.raises(PlanningResultRefused) as caught:
        _submit()
    assert "planning" in str(caught.value)
    assert "slug" in str(caught.value)


def test_a_candidate_with_the_kind_on_an_op_is_accepted(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "PLN1", "slug": "planning"}])
    _submit()


def test_a_kind_already_on_the_day_is_seen_through_the_rows(turn) -> None:
    _require(turn, ["planning"])
    _captured(turn, [{"op": "add", "h": "DW1"}], rows=[{"h": "PLN1", "slug": "planning"}])
    _submit()


def test_a_host_that_publishes_no_required_kinds_fails_open(turn, monkeypatch) -> None:
    monkeypatch.delenv(REQUIRED_BLOCKS_FILE_ENV)
    _captured(turn, [{"op": "add", "h": "DW1"}])
    _submit()


def test_an_empty_requirement_list_refuses_nothing(turn) -> None:
    _require(turn, [])
    _captured(turn, [{"op": "add", "h": "DW1"}])
    _submit()


def test_a_skeleton_is_never_checked_for_required_kinds(turn) -> None:
    _require(turn, ["planning"])
    submit_planning_result(
        target_artifact="skeleton", artifact={"markdown": "# Day", "reasoning": "r"},
        assumptions=[], blockers=[],
    )
