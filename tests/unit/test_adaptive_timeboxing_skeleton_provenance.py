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
