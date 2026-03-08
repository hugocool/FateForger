from datetime import datetime, timedelta, timezone
import logging
from dataclasses import dataclass
from datetime import date

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


class _BrokenCalendarClient(_DummyCalendarClient):
    async def list_events(self, *, calendar_id: str, time_min: str, time_max: str):  # noqa: ARG002
        raise RuntimeError("calendar list failed")


class _DummyPlanningStore:
    def __init__(self, sessions=None):
        self._sessions = list(sessions or [])

    async def list_for_user_between(self, **kwargs):  # noqa: ARG002
        user_id = kwargs.get("user_id")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        statuses = kwargs.get("statuses") or ()
        allowed = {
            str(getattr(item, "value", item)).strip().lower() for item in statuses
        }
        rows = []
        for session in self._sessions:
            if user_id and session.user_id != user_id:
                continue
            if start_date and session.planned_date < start_date:
                continue
            if end_date and session.planned_date > end_date:
                continue
            if allowed and session.status.lower() not in allowed:
                continue
            rows.append(session)
        return rows

    async def upsert(self, **kwargs):  # noqa: ARG002
        return None


class _DummyReconciler:
    def __init__(self, calendar_client):
        self.calendar_client = calendar_client


class _DummyAnchorStore:
    def __init__(self):
        self.upserts = []

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        return kwargs


@dataclass
class _SessionRef:
    user_id: str
    planned_date: date
    calendar_id: str
    event_id: str
    status: str = "planned"
    updated_at: datetime = datetime(2026, 3, 6, 21, 0, tzinfo=timezone.utc)


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


@pytest.mark.asyncio
async def test_planning_still_missing_logs_revalidation_exception_context(caplog):
    runtime = type(
        "Runtime",
        (),
        {
            "event_draft_store": object(),
            "planning_guardian": None,
            "planning_session_store": _DummyPlanningStore(),
            "planning_reconciler": _DummyReconciler(_BrokenCalendarClient(events=[])),
        },
    )()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    reminder = PlanningReminder(
        scope="U1",
        kind="nudge2",
        attempt=2,
        message="still missing",
        user_id="U1",
        channel_id="D1",
    )

    with caplog.at_level(logging.INFO):
        still_missing = await coordinator._planning_still_missing(
            reminder=reminder,
            planning_event_id="ffplanning-stale",
            calendar_id="primary",
        )

    assert still_missing is True
    assert "planning revalidation failed" in caplog.text
    assert "scope=U1" in caplog.text
    assert "kind=nudge2" in caplog.text
    assert "planning_event_id=ffplanning-stale" in caplog.text


@pytest.mark.asyncio
async def test_planning_still_missing_fail_soft_when_local_upcoming_ref_exists():
    runtime = type(
        "Runtime",
        (),
        {
            "event_draft_store": object(),
            "planning_guardian": None,
            "planning_session_store": _DummyPlanningStore(
                sessions=[
                    _SessionRef(
                        user_id="U1",
                        planned_date=date.today(),
                        calendar_id="primary",
                        event_id="canonical-event-123",
                    )
                ]
            ),
            "planning_reconciler": _DummyReconciler(_BrokenCalendarClient(events=[])),
            "planning_anchor_store": _DummyAnchorStore(),
        },
    )()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    reminder = PlanningReminder(
        scope="U1",
        kind="nudge2",
        attempt=2,
        message="still missing",
        user_id="U1",
        channel_id="D1",
    )

    still_missing = await coordinator._planning_still_missing(
        reminder=reminder,
        planning_event_id="stale-event-999",
        calendar_id="primary",
    )

    assert still_missing is False
    assert runtime.planning_anchor_store.upserts
    assert runtime.planning_anchor_store.upserts[-1]["event_id"] == "canonical-event-123"


@pytest.mark.asyncio
async def test_planning_still_missing_refreshes_anchor_on_success_path():
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    end = start + timedelta(minutes=30)
    runtime = type(
        "Runtime",
        (),
        {
            "event_draft_store": object(),
            "planning_guardian": None,
            "planning_session_store": _DummyPlanningStore(
                sessions=[
                    _SessionRef(
                        user_id="U1",
                        planned_date=start.date(),
                        calendar_id="primary",
                        event_id="canonical-event-abc",
                    )
                ]
            ),
            "planning_reconciler": _DummyReconciler(
                _DummyCalendarClient(
                    events=[
                        {
                            "id": "canonical-event-abc",
                            "summary": "Daily planning session",
                            "start": {"dateTime": start.isoformat()},
                            "end": {"dateTime": end.isoformat()},
                        }
                    ]
                )
            ),
            "planning_anchor_store": _DummyAnchorStore(),
        },
    )()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    reminder = PlanningReminder(
        scope="U1",
        kind="nudge1",
        attempt=1,
        message="nudge",
        user_id="U1",
        channel_id="D1",
    )

    still_missing = await coordinator._planning_still_missing(
        reminder=reminder,
        planning_event_id="stale-event-999",
        calendar_id="primary",
    )

    assert still_missing is False
    assert runtime.planning_anchor_store.upserts
    assert runtime.planning_anchor_store.upserts[-1]["event_id"] == "canonical-event-abc"
