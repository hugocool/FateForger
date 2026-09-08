"""A skeleton the card cannot draw is refused where the planner can still fix it.

On 2026-09-02 the planner submitted a skeleton with no `markdown`, the host
stored it, and the review card showed an empty shape of the day (#267). The
payload shape is now a contract: validated at submit so the model retries in
the same turn, and stated in the obligation so it does not have to guess.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningBrief,
    PlanningDay,
    SkeletonPayload,
)
from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.planning_result_mcp import (
    PLANNING_RESULT_FILE_ENV,
    PlanningResultRefused,
    submit_planning_result,
)


@pytest.fixture()
def result_file(tmp_path, monkeypatch):
    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))
    return destination


def test_a_skeleton_without_markdown_is_refused_by_field_name(result_file) -> None:
    with pytest.raises(PlanningResultRefused) as caught:
        submit_planning_result(
            target_artifact="skeleton",
            artifact={"blocks": [{"start": "09:00", "title": "Deep work"}]},
            assumptions=[],
            blockers=[],
        )
    # The refusal names the field the model has to supply and the one it
    # invented, so the retry does not need the host's source to find them.
    assert "markdown" in str(caught.value)
    assert "blocks" in str(caught.value)
    assert result_file.read_text(encoding="utf-8") == ""


def test_a_skeleton_with_markdown_and_reasoning_is_accepted(result_file) -> None:
    submit_planning_result(
        target_artifact="skeleton",
        artifact={"markdown": "# Morning\n- Deep work", "reasoning": "deep work first"},
        assumptions=[],
        blockers=[],
    )
    assert result_file.read_text(encoding="utf-8") != ""


def test_reasoning_is_optional() -> None:
    payload = SkeletonPayload.model_validate({"markdown": "# Day"})
    assert payload.reasoning == ""


def _brief(target: ArtifactKind) -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
        locked_day=PlanningDay.lock_default(
            value=date(2026, 9, 3),
            timezone="Europe/Amsterdam",
            lock_revision=1,
        ),
        facts=[],
        assumptions=[],
        current_artifacts=[],
        approvals=[],
        applicable_constraints=[],
        calendar_snapshot={},
        target_artifact=target,
        readiness={},
        allowed_outputs={target},
    )


def test_the_skeleton_obligation_names_every_payload_field() -> None:
    """Drift guard: a field added to the contract must reach the prompt."""
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.SKELETON))
    for field in SkeletonPayload.model_fields:
        assert f"`{field}`" in text


def test_the_candidate_obligation_does_not_describe_a_skeleton() -> None:
    text = harness_bridge._planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE)
    )
    assert "`markdown`" not in text
