"""The one refusal branch that names nothing.

Four rules answer with a refusal here and three of them log which requirement
tripped. `illegal_user_blocker` did not, so a live turn refused on 2026-08-31
produced exactly one line:

    adaptive timeboxing turn refused code=illegal_user_blocker session_key=...

and the requirement had to be recovered by decompressing the harness session
log and reading the submitted tool call. The information existed; the diagnostic
threw it away.

This is the same shape as the seventeen `extra=` fields the formatter discarded
(#231) and the four-rules gap already closed for `invalid_planner_result`: a
failure path that cannot say what it saw.
"""

import logging

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    PlanningResult,
    TurnFailed,
    UserBlockerDraft,
)

from .test_adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)


@pytest.mark.asyncio
async def test_it_says_which_requirement_was_delegated(caplog) -> None:
    """`skeleton.ordinary_placement` is planner-owned: asking the user about it
    is the planner handing back a decision that was its own to make."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = RecordedPlanner(
        PlanningResult(
            blockers=[
                UserBlockerDraft(
                    requirement_id="skeleton.ordinary_placement",
                    why_needed="ran out of patch attempts, need another turn",
                )
            ]
        )
    )

    with caplog.at_level(logging.ERROR):
        outcome = await _kernel(repo, planner).turn(
            _advance_request(), progress=RecordingProgressSink()
        )

    assert isinstance(outcome, TurnFailed)
    assert outcome.code == "illegal_user_blocker"

    refusals = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert refusals, "a refusal that logs nothing cannot be diagnosed"
    assert "skeleton.ordinary_placement" in refusals[0]
    assert "planner_owned_blocker" in refusals[0]
