"""A planner that keeps asking for another turn is stopped, and told so.

Legacy commit 9eb333e capped consecutive no-change refine passes at three;
the harness's NeedsAnotherTurn had no cap. The streak is read from the
snapshot's handled_interactions, which already record every outcome kind.
"""

from __future__ import annotations

from fateforger.agents.timeboxing import adaptive_timeboxing as kernel_module
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
)
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    NeedsAnotherTurn,
    PlannerContinuation,
    PlanningResult,
    TurnFailed,
)
from tests.doubles.timeboxing import (
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)

_REASON = "lunch still collides with the daily; shortening it next pass"
LIMIT = kernel_module.MAX_CONSECUTIVE_CONTINUATIONS


def _continuing_planner():
    return RecordedPlanner(PlanningResult(continuation=PlannerContinuation(reason=_REASON)))


async def _turns(kernel, count: int, *, first_revision: int = 3):
    outcomes = []
    for i in range(count):
        outcomes.append(
            await kernel.turn(
                _advance_request(
                    expected_revision=first_revision + i,
                    interaction_id=f"1772.{i + 2}",
                ),
                progress=RecordingProgressSink(),
            )
        )
    return outcomes


def test_the_limit_allows_an_ordinary_continuation_but_not_a_loop():
    assert 2 < LIMIT < 9
    assert kernel_module.MAX_CONSECUTIVE_CONTINUATIONS == 3


# 3 is the contract (legacy's _REFINE_NO_CHANGE_LIMIT), not a reading of the
# constant -- a test that loops LIMIT times cannot fail when LIMIT moves.
async def test_continuations_accumulate_then_fail_on_the_limit():
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    kernel = _kernel(repo, _continuing_planner())

    outcomes = await _turns(kernel, 3)

    assert isinstance(outcomes[0], NeedsAnotherTurn)
    assert isinstance(outcomes[1], NeedsAnotherTurn)
    last = outcomes[2]
    assert isinstance(last, TurnFailed)
    assert last.code == "no_progress"
    assert _REASON in last.message


async def test_the_failure_says_how_many_passes_made_no_progress():
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    (*_, last) = await _turns(_kernel(repo, _continuing_planner()), 3)
    assert "3" in last.message


async def test_a_productive_turn_resets_the_streak():
    """Two continuations, then an artifact, then two more: no failure."""
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    skeleton = ArtifactDraft(
        kind=ArtifactKind.SKELETON,
        payload={"markdown": "## Saturday\n- 10:00 Deep work"},
        dependency_revisions={"planning_day": 1},
    )
    scripted = [
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(artifact_updates=[skeleton]),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
        PlanningResult(continuation=PlannerContinuation(reason=_REASON)),
    ]

    class _Scripted(RecordedPlanner):
        async def produce(self, brief, progress):
            self.briefs.append(brief)
            return scripted[len(self.briefs) - 1]

    outcomes = await _turns(_kernel(repo, _Scripted(scripted[0])), len(scripted))

    assert not any(isinstance(o, TurnFailed) for o in outcomes), outcomes
