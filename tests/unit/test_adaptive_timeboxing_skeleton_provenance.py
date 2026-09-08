"""A rule the model invented must never be drawn as provenance.

#330 was a judge mistyping a `rule_uid` by one character. The card that draws
from an unverified uid would name a rule that does not exist, and the user
would have no way to tell -- so the kernel checks every cited `rule_uid`
against the uids memory actually returned for this day before the skeleton is
ever stored or offered for approval.
"""

from __future__ import annotations

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    AwaitingApproval,
    TurnFailed,
)
from tests.unit._kernel_fixtures import _ROWS, RowsContextPort, _skeleton_citing
from tests.unit.test_adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)


@pytest.mark.asyncio
async def test_a_rule_uid_outside_the_days_constraints_fails_the_turn() -> None:
    """A rule the model invented must never be drawn as provenance."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_citing("not-a-real-uid")),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "unknown_rule_uid"
    assert "not-a-real-uid" in outcome.message


@pytest.mark.asyncio
async def test_a_rule_uid_among_the_days_constraints_is_accepted() -> None:
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_citing("a1")),
        context=RowsContextPort(_ROWS),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, AwaitingApproval)
    assert outcome.artifact.kind is ArtifactKind.SKELETON


@pytest.mark.asyncio
async def test_a_rule_row_with_no_name_is_not_a_uid_a_skeleton_may_cite() -> None:
    """Verification and rendering must agree, or the turn dies after the write.

    `_known_rule_uids` used to admit any row carrying a `uid`, while
    `stage_cards._rule_names` needs a string `name` to draw the citation with.
    A row with one and not the other therefore passed the check, the artifact
    was stored, and `_artifact_groups` then raised `ValueError` while drawing
    it -- after the commit, so there was nothing to redraw. The two read the
    same rows the same way now.
    """

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    outcome = await _kernel(
        repo, RecordedPlanner(_skeleton_citing("a1")),
        context=RowsContextPort([{"uid": "a1"}]),
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert isinstance(outcome, TurnFailed), outcome
    assert outcome.code == "unknown_rule_uid"


@pytest.mark.asyncio
async def test_every_verified_uid_can_be_drawn() -> None:
    """The property behind the test above, stated directly.

    Whatever `_known_rule_uids` admits, `_rule_names` must be able to name --
    otherwise a skeleton can pass the gate and fail the card.
    """

    from fateforger.slack_bot.stage_cards import _rule_names
    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        AdaptiveTimeboxing,
        PlanningContext,
    )
    from fateforger.agents.timeboxing.session_contracts import (
        PlanningSessionSnapshot,
    )

    rows = [
        {"uid": "a1", "name": "Sleep schedule"},  # both
        {"uid": "a2"},                            # no name
        {"name": "Nameless"},                     # no uid
        {"uid": "a4", "name": 7},                 # name is not a string
        {"uid": 5, "name": "Numeric uid"},        # uid is not a string
    ]
    known = AdaptiveTimeboxing._known_rule_uids(
        None, PlanningContext(applicable_constraints=rows)
    )
    nameable = set(
        _rule_names(
            PlanningSessionSnapshot(
                session_key="C1:1.0",
                revision=0,
                owner_user_id="U1",
                applicable_constraints=rows,
            )
        )
    )

    assert known == {"a1"}
    assert known <= nameable
