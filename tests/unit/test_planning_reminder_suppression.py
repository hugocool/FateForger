from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.timeboxing_activity import TimeboxingActivityTracker
from fateforger.slack_bot import planning as planning_mod
from fateforger.slack_bot.planning import PlanningCoordinator


class DummyClient:
    def __init__(self):
        self.posted = []
        self.opened = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ok": True, "channel": payload.get("channel"), "ts": "m1"}

    async def conversations_open(self, *, users):
        self.opened.append(tuple(users))
        return {"ok": True, "channel": {"id": "D1"}}


class _DummyCalendarClient:
    def __init__(self, *, events):
        self._events = events

    async def get_event(self, *, calendar_id: str, event_id: str):  # noqa: ARG002
        return None

    async def list_events(self, *, calendar_id: str, time_min: str, time_max: str):  # noqa: ARG002
        return list(self._events)


class _DummyPlanningStore:
    async def list_for_user_between(self, **kwargs):  # noqa: ARG002
        return []

    async def upsert(self, **kwargs):  # noqa: ARG002
        return None


class _DummyReconciler:
    def __init__(self, calendar_client):
        self.calendar_client = calendar_client


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


@pytest.mark.asyncio
async def test_dispatch_skips_stale_reminder_when_planning_now_exists():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(minutes=30)

    runtime = type(
        "Runtime",
        (),
        {
            "event_draft_store": object(),
            "planning_guardian": None,
            "planning_session_store": _DummyPlanningStore(),
            "planning_reconciler": _DummyReconciler(
                _DummyCalendarClient(
                    events=[
                        {
                            "id": "evt-planning",
                            "summary": "Planning session",
                            "start": {"dateTime": start.isoformat()},
                            "end": {"dateTime": end.isoformat()},
                        }
                    ]
                )
            ),
        },
    )()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    await coordinator.dispatch_planning_reminder(
        PlanningReminder(
            scope="U1",
            kind="nudge3",
            attempt=3,
            message="still missing",
            user_id="U1",
            channel_id="D1",
        )
    )

    assert client.posted == []
