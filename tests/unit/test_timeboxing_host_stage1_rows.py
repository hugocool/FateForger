"""resolve(SKELETON) used to fetch the active rules for the day-frame judgement
and return them to nobody; Stage 1 rendered nothing because it received
nothing (#262)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_host import HostPlanningContext
from tests.fixtures.stage1.elicit_stub import install_stub_elicit


@pytest.fixture(autouse=True)
def stub_elicit(monkeypatch):
    return install_stub_elicit(monkeypatch)


ROWS = [{"uid": "c1", "name": "Oats before gym", "necessity": "must", "anchors": []}]


class _Store:
    async def query_constraints(self, *, filters, limit):
        return ROWS

    async def count_suspended(self, planned_day, day_type):
        return 7


class _Sink:
    async def emit(self, event):
        return None


def _snapshot(*facts) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1, day_type=DayType.WORKING
        ),
        facts=list(facts),
    )


def test_skeleton_context_carries_the_rows_when_the_frame_is_already_stated(stub_elicit) -> None:
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")

    context = asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))

    assert context.applicable_constraints == ROWS
    assert context.suspended_constraint_count == 7
    assert [f.kind for f in context.facts] == [FactKind.COVERAGE_MATRIX]
    assert [p.cell_id for p in context.probes] == ["elicit.body.unclear"]
    assert stub_elicit.calls[0][1] == ROWS


def test_a_frame_the_judge_states_is_in_the_snapshot_elicit_sees(stub_elicit, monkeypatch) -> None:
    class _Frame:
        def __init__(self, client) -> None:
            pass

        async def frame_on_record(self, *, day, constraints, session_key):
            return PlanningFact(fact_id=f"frame:{day.date.isoformat()}", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00", "basis": ["c1"]}, source="constraint_memory")

    monkeypatch.setattr("fateforger.agents.timeboxing.day_frame.DayFrameJudge", _Frame)
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))

    context = asyncio.run(host.resolve(_snapshot(), target=ArtifactKind.SKELETON, progress=_Sink()))

    assert [f.kind for f in context.facts] == [FactKind.DAY_FRAME, FactKind.COVERAGE_MATRIX]
    seen_snapshot, _ = stub_elicit.calls[0]
    assert any(f.kind is FactKind.DAY_FRAME for f in seen_snapshot.facts)


def test_no_model_client_is_a_dependency_failure_even_with_a_frame_stated() -> None:
    from fateforger.slack_bot.timeboxing_host import AdaptiveDependencyUnavailable

    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=None)
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")
    with pytest.raises(AdaptiveDependencyUnavailable):
        asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))


def test_a_closed_stage_one_resolves_without_running_the_judgements(stub_elicit) -> None:
    """After consent the matrix nobody reads is not worth three fan-outs.

    `resolve(SKELETON)` runs on every skeleton turn -- the planner's, and any
    revise after it -- and Stage 1 is over by then. The rules and the count
    still come back; the judgements do not run.
    """
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")
    snapshot = _snapshot(frame).model_copy(update={"stage1": "closed"})

    context = asyncio.run(host.resolve(snapshot, target=ArtifactKind.SKELETON, progress=_Sink()))

    assert stub_elicit.calls == []
    assert context.facts == []
    assert context.probes == []
    assert context.applicable_constraints == ROWS
    assert context.suspended_constraint_count == 7
