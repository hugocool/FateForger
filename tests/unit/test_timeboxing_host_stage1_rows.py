"""resolve(SKELETON) used to fetch the active rules for the day-frame judgement
and return them to nobody; Stage 1 rendered nothing because it received
nothing (#262)."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    DayType,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_host import HostPlanningContext

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


def test_skeleton_context_carries_the_rows_when_the_frame_is_already_stated() -> None:
    runtime = SimpleNamespace(timeboxing_constraint_store=_Store(), timeboxing_intent_model_client=object())
    host = HostPlanningContext(runtime, now=lambda: datetime.now(timezone.utc))
    frame = PlanningFact(fact_id="frame-1", kind=FactKind.DAY_FRAME, value={"wake": "07:00", "sleep": "23:00"}, source="user")

    context = asyncio.run(host.resolve(_snapshot(frame), target=ArtifactKind.SKELETON, progress=_Sink()))

    assert context.applicable_constraints == ROWS
    assert context.suspended_constraint_count == 7
    assert context.facts == []
