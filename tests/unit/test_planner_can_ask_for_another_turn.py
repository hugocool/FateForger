"""A planner that ran out of attempts had no way to say so.

Measured on 2026-08-31. The planner spent its patch-retry budget and reported,
accurately, that it was one five-minute change from converging -- lunch
overlapping a pre-existing meeting. It asked for a fresh turn.

There was no typed way to ask, so it used a blocker, the only channel available.
A blocker is how you ask the *user* something, and the requirement was
planner-owned, so the kernel refused it as `illegal_user_blocker` and discarded
the turn: the artifacts, the diagnosis, all of it.

The refusal was correct by the rules. The gap is that a planner with something
true to say had no way to say it, which is the same shape as #236 -- there an
assumption naming another stage's requirement, here a blocker on a requirement
it owns. Both reach for the nearest wrong channel.

So: a typed continuation. The turn does not fail, whatever it produced is kept,
and the reason travels to the next turn rather than being thrown away.
"""

import logging

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactDraft,
    ArtifactKind,
    NeedsAnotherTurn,
    PlannerContinuation,
    PlanningResult,
    TurnFailed,
)

from .test_adaptive_timeboxing import (
    InMemoryPlanningSessionRepository,
    RecordedPlanner,
    RecordingProgressSink,
    _advance_request,
    _incident_snapshot,
    _kernel,
)

_REASON = (
    "patch-retry budget exhausted mid-fix; lunch 13:15-13:45 collides with the "
    "foreign Daily planning session 13:40-14:10 and shortening it to 25m clears it"
)


def _planner(**kw):
    return RecordedPlanner(
        PlanningResult(continuation=PlannerContinuation(reason=_REASON), **kw)
    )


@pytest.mark.asyncio
async def test_asking_for_another_turn_does_not_fail_the_turn() -> None:
    outcome = await _kernel(
        InMemoryPlanningSessionRepository([_incident_snapshot()]), _planner()
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert not isinstance(outcome, TurnFailed)
    assert isinstance(outcome, NeedsAnotherTurn)


@pytest.mark.asyncio
async def test_the_reason_survives_for_the_next_turn() -> None:
    """Losing the diagnosis is what made the old behaviour expensive: the
    planner had already worked out the answer."""

    outcome = await _kernel(
        InMemoryPlanningSessionRepository([_incident_snapshot()]), _planner()
    ).turn(_advance_request(), progress=RecordingProgressSink())

    assert _REASON in outcome.reason


@pytest.mark.asyncio
async def test_work_already_done_is_kept() -> None:
    """The whole point. The old path discarded a valid artifact alongside the
    unusable blocker."""

    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    planner = _planner(
        artifact_updates=[
            ArtifactDraft(
                kind=ArtifactKind.SKELETON,
                payload={"markdown": "## Monday\n- 10:00 Deep work"},
                dependency_revisions={"planning_day": 1},
            )
        ]
    )

    await _kernel(repo, planner).turn(
        _advance_request(), progress=RecordingProgressSink()
    )

    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert any(a.kind is ArtifactKind.SKELETON for a in stored.artifacts)


@pytest.mark.asyncio
async def test_the_session_stays_open() -> None:
    repo = InMemoryPlanningSessionRepository([_incident_snapshot()])
    await _kernel(repo, _planner()).turn(
        _advance_request(), progress=RecordingProgressSink()
    )
    stored = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    assert stored.status == "open"


@pytest.mark.asyncio
async def test_it_is_logged_so_a_loop_is_visible(caplog) -> None:
    """A planner that asks for another turn every turn is a bug, and a silent
    continuation would look exactly like slow progress."""

    with caplog.at_level(logging.WARNING):
        await _kernel(
            InMemoryPlanningSessionRepository([_incident_snapshot()]), _planner()
        ).turn(_advance_request(), progress=RecordingProgressSink())

    assert any("another turn" in r.getMessage() for r in caplog.records)


def test_slack_says_it_is_continuing_not_that_it_failed() -> None:
    """The fallback renderer calls every unknown outcome a failure.

    "I could not carry that planning step through, and nothing reached your
    calendar" is exactly wrong here: the work succeeded and is kept.
    """

    from fateforger.agents.timeboxing.session_contracts import PlanningSessionSnapshot
    from fateforger.slack_bot.timeboxing_cards import (
        PendingTimeboxCandidates,
        render_outcome,
    )

    msg = render_outcome(
        NeedsAnotherTurn(reason=_REASON),
        pending=PendingTimeboxCandidates(),
        snapshot=PlanningSessionSnapshot(
            session_key="C:1", revision=1, owner_user_id="U1"
        ),
        session_key="C:1", actor_user_id="U1", channel_id="C", thread_ts="1",
        logger=logging.getLogger(__name__),
    )
    assert "could not" not in msg.text
    assert "nothing reached your calendar" not in msg.text


def test_the_submit_tool_accepts_a_continuation(tmp_path, monkeypatch) -> None:
    """Unreachable from the planner, the whole contract is dead code."""

    import json

    from fateforger.slack_bot.planning_result_mcp import (
        PLANNING_RESULT_FILE_ENV,
        submit_planning_result,
    )

    destination = tmp_path / "planning-result.json"
    destination.touch()
    monkeypatch.setenv(PLANNING_RESULT_FILE_ENV, str(destination))

    submit_planning_result(
        target_artifact="skeleton", artifact=None, assumptions=[], blockers=[],
        continuation={"reason": _REASON},
    )
    recorded = json.loads(destination.read_text(encoding="utf-8"))
    assert recorded["continuation"]["reason"] == _REASON


def test_the_obligation_tells_the_planner_it_exists() -> None:
    """An escape hatch nobody is told about is one nobody uses."""

    from .test_candidate_obligation_names_apply import _brief
    from fateforger.agents.timeboxing.session_contracts import ArtifactKind
    from fateforger.slack_bot.harness_bridge import _planning_obligation

    text = _planning_obligation(_brief(ArtifactKind.VALIDATED_CANDIDATE))
    assert "continuation" in text
