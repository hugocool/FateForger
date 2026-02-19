"""Regression tests for Stage 3 remote snapshot plan building."""

from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.tb_models import TBEvent, TBPlan, FixedWindow
from fateforger.agents.timeboxing.timebox import Timebox


def test_build_remote_snapshot_plan_bypasses_timebox_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 3 baseline snapshot should not depend on Timebox validator execution."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
        frame_facts={
            "immovables": [
                {
                    "summary": "Brunch",
                    "event_type": "M",
                    "start_time": "11:30",
                    "end_time": "13:00",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                }
            ]
        },
    )

    def _boom(self: Timebox) -> Timebox:
        _ = self
        raise RuntimeError("validator should not run for remote snapshot baseline")

    monkeypatch.setattr(Timebox, "schedule_and_validate", _boom)

    plan = TimeboxingFlowAgent._build_remote_snapshot_plan(agent, session)

    assert plan.date == date(2026, 2, 14)
    assert len(plan.events) == 1
    assert plan.events[0].n == "Brunch"


def test_build_remote_snapshot_plan_uses_prefetched_identity() -> None:
    """Refine baseline should hydrate event-id mapping from prefetched remote snapshot."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    prefetched_plan = TBPlan(
        date=date(2026, 2, 14),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Standup",
                t="M",
                p=FixedWindow(st="09:00", et="09:30"),
            )
        ],
    )
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
        prefetched_remote_snapshots_by_date={"2026-02-14": prefetched_plan},
        prefetched_event_id_maps_by_date={"2026-02-14": {"Standup|09:00:00": "fftb-1"}},
        prefetched_remote_event_ids_by_date={"2026-02-14": ["fftb-1"]},
    )

    plan = TimeboxingFlowAgent._build_remote_snapshot_plan(agent, session)

    assert plan.events[0].n == "Standup"
    assert session.event_id_map["Standup|09:00:00"] == "fftb-1"
    assert session.remote_event_ids_by_index == ["fftb-1"]
