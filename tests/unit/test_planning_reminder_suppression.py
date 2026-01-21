from datetime import timedelta

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.timeboxing_activity import TimeboxingActivityTracker
from fateforger.slack_bot import planning as planning_mod
from fateforger.slack_bot.planning import PlanningCoordinator


class DummyClient:
    def __init__(self):
        self.posted = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ok": True, "channel": payload.get("channel"), "ts": "m1"}


@pytest.mark.asyncio
async def test_dispatch_skips_when_timeboxing_active(monkeypatch):
    tracker = TimeboxingActivityTracker(idle_timeout=timedelta(hours=1))
    monkeypatch.setattr(planning_mod, "timeboxing_activity", tracker)

    runtime = type(
        "Runtime",
        (),
        {"event_draft_store": object(), "planning_guardian": None, "planning_reconciler": None},
    )()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    tracker.mark_active(user_id="U1", channel_id="C1", thread_ts="T1")

    await coordinator.dispatch_planning_reminder(
        PlanningReminder(
            scope="U1",
            kind="nudge1",
            attempt=1,
            message="nudge",
            user_id="U1",
            channel_id="C1",
        )
    )

    tracker.mark_inactive(user_id="U1")

    assert client.posted == []
