"""The one requirement the host enforced without ever stating it.

A validated candidate is committable only if a tmbx patch sits behind it, and
the host gets that patch by watching the `plan_apply` the planner made -- it
will not accept one the model writes, because a model-written basis is a forged
one. So applying is mandatory.

The obligation never said so. It was the same sentence for every stage:
"produce exactly one <target> and call submit_planning_result once". A planner
that reasoned its way to a good day and submitted it without applying had
followed every instruction it was given, and produced something that could be
shown, approved, and never committed (#217).
"""

from datetime import UTC, date, datetime

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    PlanningBrief,
    PlanningDay,
)
from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.harness_bridge import _planning_obligation


def _brief(target: ArtifactKind) -> PlanningBrief:
    return PlanningBrief(
        session_key="C1:1.0",
        base_revision=1,
        observed_at=datetime(2026, 8, 31, tzinfo=UTC),
        locked_day=PlanningDay(
            date=date(2026, 8, 31),
            timezone="Europe/Amsterdam",
            iso_weekday=1,
            day_type=DayType.WORKING,
            classification_basis="calendar",
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
        allowed_outputs=set(),
    )


def test_a_candidate_turn_is_told_to_apply_first() -> None:
    text = _planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    assert "plan_apply" in text


def test_it_says_why_so_the_step_is_not_read_as_ceremony() -> None:
    """An instruction without a reason is the one a model drops under load."""

    text = _planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    assert "commit" in text


def test_a_skeleton_turn_is_not() -> None:
    """Only a candidate is committed, so only a candidate must be applied."""

    assert "plan_apply" not in _planning_obligation(_brief(ArtifactKind.SKELETON))


def test_the_candidate_obligation_names_each_required_kind_with_its_rule() -> None:
    """#214: existence is (from memory: …), time is an assumption -- two
    claims about one block, stated as two, or the model picks one."""
    from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact

    brief = _brief(ArtifactKind.VALIDATED_CANDIDATE)
    brief = brief.model_copy(update={"facts": [
        *brief.facts,
        PlanningFact(
            fact_id="required-blocks:2026-09-07", kind=FactKind.REQUIRED_BLOCKS,
            value={"slugs": ["planning"], "by_rule": {"planning": {"uid": "c1", "name": "Daily timeboxing planning session"}}},
            source="constraint_memory",
        ),
    ]})
    text = harness_bridge._planning_obligation(brief)
    assert "A `planning` block is required today (from memory: Daily timeboxing planning session)." in text
    assert "Set `slug: planning` on it verbatim" in text
    assert "candidate.required_blocks" in text


def test_a_brief_without_required_kinds_says_nothing_about_them() -> None:
    text = harness_bridge._planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    assert "is required today" not in text
