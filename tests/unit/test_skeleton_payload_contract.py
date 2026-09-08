"""A skeleton the card cannot draw is refused where the planner can still fix it.

On 2026-09-02 the planner submitted a skeleton with no `markdown`, the host
stored it, and the review card showed an empty shape of the day (#267). The
payload shape is now a contract: validated at submit so the model retries in
the same turn, and stated in the obligation so it does not have to guess.

The markdown shape was itself replaced by typed groups (#267, #344): flat
markdown cannot carry provenance the system can verify, so a rule name on the
card would be unverified model text -- the exact failure this project has
already had once.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningBrief,
    PlanningDay,
    SkeletonGroup,
    SkeletonItem,
    SkeletonPayload,
    UserBlockerDraft,
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


def _valid_artifact() -> dict:
    return {
        "day_label": "Sunday 6 September",
        "groups": [
            {
                "name": "Evening",
                "items": [
                    {"text": "Asleep by 23:00", "source": "rule", "rule_uid": "a1"},
                    {"text": "TD party after hockey", "source": "user"},
                ],
            }
        ],
    }


def test_a_skeleton_without_groups_is_refused_by_field_name(result_file) -> None:
    with pytest.raises(PlanningResultRefused) as caught:
        submit_planning_result(
            target_artifact="skeleton",
            artifact={"blocks": [{"start": "09:00", "title": "Deep work"}]},
            assumptions=[],
            blockers=[],
        )
    # The refusal names the field the model has to supply and the one it
    # invented, so the retry does not need the host's source to find them.
    assert "day_label" in str(caught.value)
    assert "blocks" in str(caught.value)
    assert result_file.read_text(encoding="utf-8") == ""


def test_a_skeleton_with_groups_and_reasoning_is_accepted(result_file) -> None:
    submit_planning_result(
        target_artifact="skeleton",
        artifact={**_valid_artifact(), "reasoning": "bedtime after hockey"},
        assumptions=[],
        blockers=[],
    )
    assert result_file.read_text(encoding="utf-8") != ""


def test_reasoning_is_optional() -> None:
    payload = SkeletonPayload.model_validate(_valid_artifact())
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


def test_the_skeleton_obligation_names_every_item_field() -> None:
    """Drift guard: a field added to `SkeletonItem` must reach the prompt too.

    `payload_shape` states each item as a JSON literal rather than a backtick
    reference (`{"text": ..., "source": ..., "rule_uid": ...}`), so the check
    matches that form -- unlike the payload-level guard above, which matches
    the backtick form those fields get instead.
    """
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.SKELETON))
    for field in SkeletonItem.model_fields:
        assert f'"{field}"' in text


def test_the_skeleton_obligation_names_the_blocking_field() -> None:
    """Drift guard: a planner never told about `blocking` will never set it.

    #259 is what an unstated channel already cost once: the planner invented
    a field the kernel never read. `blocking` is a real channel now, and the
    skeleton turn -- the only one whose open gaps a user blocker can name --
    must say so.
    """
    assert "blocking" in UserBlockerDraft.model_fields
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.SKELETON))
    assert "`blocking`" in text


def test_the_candidate_obligation_does_not_invite_a_question() -> None:
    """The candidate turn has no gap a user blocker could legally name.

    Every user-owned requirement targets `SKELETON`, and `evaluate` returns
    only this turn's target's gaps -- so a blocker raised here is refused as
    `invalid_planner_result` and the finished candidate is discarded with the
    turn. Inviting one would be inviting that.
    """
    text = harness_bridge._planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE)
    )
    assert "`blocking`" not in text


def test_no_user_owned_requirement_targets_the_candidate() -> None:
    """The kernel fact the gate above rests on, asserted where it is used.

    If a user-owned requirement ever targets `VALIDATED_CANDIDATE`, the
    obligation may invite a question there -- and this test is the thing that
    says so, rather than the next reader re-deriving it from the catalog.
    """
    from fateforger.agents.timeboxing.readiness import _CATALOG, RequirementOwner

    assert {
        requirement.target_artifact
        for requirement in _CATALOG
        if requirement.owner is RequirementOwner.USER
    } == {ArtifactKind.SKELETON}


def test_the_candidate_obligation_does_not_describe_a_skeleton() -> None:
    text = harness_bridge._planning_obligation(
        _brief(ArtifactKind.VALIDATED_CANDIDATE)
    )
    assert "`day_label`" not in text


def test_a_rule_item_needs_a_rule_uid():
    with pytest.raises(ValidationError):
        SkeletonItem(text="Asleep by 23:00", source="rule")


def test_a_non_rule_item_may_not_carry_a_rule_uid():
    """A uid on a user-stated item would render a rule that placed nothing."""
    with pytest.raises(ValidationError):
        SkeletonItem(text="Hockey at 12:15", source="user", rule_uid="a1")


def test_a_valid_payload_round_trips():
    payload = SkeletonPayload(
        day_label="Sunday 6 September",
        groups=[SkeletonGroup(name="Evening", items=[
            SkeletonItem(text="Asleep by 23:00", source="rule", rule_uid="a1"),
            SkeletonItem(text="TD party after hockey", source="user"),
        ])],
    )
    assert payload.groups[0].items[0].rule_uid == "a1"


def test_the_old_markdown_shape_is_refused():
    """A payload from before this contract must not be drawn as an empty day."""
    with pytest.raises(ValidationError):
        SkeletonPayload.model_validate({"markdown": "# Day", "reasoning": ""})
