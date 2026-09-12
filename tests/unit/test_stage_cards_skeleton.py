"""The skeleton card carries typed groups, with per-line provenance composed
once here rather than left for the renderer to guess at.

Style A: trailing, italic. Nothing is marked when it came from the user, so
the only markers a person sees are things they did not say -- and a rule's
marker names the rule, never the category (#267).
"""

from __future__ import annotations

from datetime import date

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    PlannerAssumption,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.stage_cards import StageCard, map_outcome
from fateforger.slack_bot.timebox_candidate import PendingTimeboxCandidates


def _locked_day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 6), timezone="Europe/Amsterdam", lock_revision=1
    )


def _card(
    items: list[tuple[str, str, str | None]],
    *,
    rules: list[dict[str, str]] = (),
    assumptions: list[PlannerAssumption] = (),
) -> StageCard:
    """Drive `map_outcome` for one skeleton group named 'Morning'."""

    payload = {
        "day_label": "Sunday 6 September",
        "groups": [
            {
                "name": "Morning",
                "items": [
                    {
                        "text": t,
                        "source": src,
                        **({"rule_uid": uid} if uid is not None else {}),
                    }
                    for t, src, uid in items
                ],
            }
        ],
        "reasoning": "",
    }
    artifact = PlanningArtifact.create(
        artifact_id="a",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload=payload,
        dependency_revisions={},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=_locked_day(),
        applicable_constraints=list(rules),
        assumptions=list(assumptions),
    )
    card = map_outcome(
        AwaitingApproval(artifact=artifact),
        snapshot,
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    assert card is not None
    return card


def test_a_user_item_gets_no_marker() -> None:
    card = _card([("Hockey at 12:15", "user", None)])
    assert card.artifact_groups[0].lines == ["• Hockey at 12:15"]


def test_a_rule_item_is_labelled_with_the_stored_name() -> None:
    """Style A: trailing, italic. The name comes from the snapshot, not the model."""
    card = _card(
        [("Asleep by 23:00", "rule", "a1")],
        rules=[{"uid": "a1", "name": "Sleep schedule"}],
    )
    assert card.artifact_groups[0].lines == ["• Asleep by 23:00  _(Sleep schedule)_"]


def test_an_assumed_item_says_it_is_a_guess() -> None:
    card = _card([("Taxes at 09:15", "assumed", None)])
    assert card.artifact_groups[0].lines == ["• Taxes at 09:15  _(my guess)_"]


def test_decided_lists_no_assumption() -> None:
    """The guess is marked where the guess is; Decided is only what you said."""
    card = _card(
        [("Taxes at 09:15", "assumed", None)],
        assumptions=[
            PlannerAssumption(
                assumption_id="skeleton.ordinary_placement:1",
                requirement_id="skeleton.ordinary_placement",
                value="09:15",
                why_needed="nothing said when",
            )
        ],
    )
    assert all(item.kind == "fact" for item in card.decided)


def test_a_calendar_item_says_where_it_came_from() -> None:
    card = _card([("Dentist at 10:00", "calendar", None)])
    assert card.artifact_groups[0].lines == ["• Dentist at 10:00  _(on your calendar)_"]


def test_a_rule_item_citing_an_unnamed_uid_raises() -> None:
    """The kernel already verified this uid against the day's rules (Task 2);
    a miss here means the artifact and the snapshot disagree, which must be
    loud rather than drawing a rule with no name."""
    with pytest.raises(ValueError):
        _card([("Asleep by 23:00", "rule", "a1")], rules=[])


def test_group_names_and_item_text_are_escaped_like_a_schedule_summary() -> None:
    """The reserved three -- `&`, `<`, `>` -- are escaped exactly as
    `render_schedule` escapes a block summary. `*` and `_` are left alone on
    purpose: Slack mrkdwn has no escape for them, so a rule literally named
    "Deep *work*" would render half-bold either way."""
    card = _card([("Tom & Jerry <marathon>", "user", None)], )
    assert card.artifact_groups[0].lines == ["• Tom &amp; Jerry &lt;marathon&gt;"]


def test_a_group_name_is_escaped_too() -> None:
    payload = {
        "day_label": "Sunday 6 September",
        "groups": [
            {
                "name": "Work & Play",
                "items": [{"text": "memo", "source": "user"}],
            }
        ],
        "reasoning": "",
    }
    artifact = PlanningArtifact.create(
        artifact_id="a",
        kind=ArtifactKind.SKELETON,
        revision=1,
        payload=payload,
        dependency_revisions={},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=_locked_day(),
    )
    card = map_outcome(
        AwaitingApproval(artifact=artifact),
        snapshot,
        pending=PendingTimeboxCandidates(),
        actor_user_id="U1",
        session_key="C1:1.0",
        channel_id="C1",
        thread_ts="1.0",
    )
    assert card is not None
    assert card.artifact_groups[0].name == "Work &amp; Play"


def test_the_card_carries_the_day_label() -> None:
    card = _card([("Hockey at 12:15", "user", None)])
    assert card.artifact_day == "Sunday 6 September"
