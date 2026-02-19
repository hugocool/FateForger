from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import PlanningReconciler, PlanningReminder, PlanningRuleConfig


class DummyCalendarClient:
    def __init__(self, events):
        self._events = events
        self.calls = []
        self.event_lookup = {}

    async def list_events(self, *, calendar_id: str, time_min: str, time_max: str):
        self.calls.append((calendar_id, time_min, time_max))
        return list(self._events)

    async def get_event(self, *, calendar_id: str, event_id: str):
        return self.event_lookup.get((calendar_id, event_id))


class FakeScheduler:
    def __init__(self):
        self._jobs = {}

    def get_jobs(self):
        return list(self._jobs.values())

    def add_job(self, func, trigger, run_date, id, kwargs, replace_existing, **_):
        self._jobs[id] = type("Job", (), {"id": id, "run_date": run_date, "kwargs": kwargs})

    def remove_job(self, job_id):
        self._jobs.pop(job_id, None)


@dataclass
class _StoredSession:
    user_id: str
    planned_date: date
    calendar_id: str
    event_id: str
    status: str = "planned"
    title: str | None = None
    event_url: str | None = None
    source: str | None = None
    channel_id: str | None = None
    thread_ts: str | None = None


class FakePlanningSessionStore:
    def __init__(self, sessions=None):
        self._sessions = list(sessions or [])
        self.upserts = []

    async def list_for_user_between(
        self, *, user_id: str, start_date: date, end_date: date, statuses
    ):
        allowed = set(statuses)
        return [
            s
            for s in self._sessions
            if s.user_id == user_id
            and start_date <= s.planned_date <= end_date
            and s.status in allowed
        ]

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        row = _StoredSession(**kwargs)
        self._sessions = [
            s
            for s in self._sessions
            if not (s.user_id == row.user_id and s.planned_date == row.planned_date)
        ]
        self._sessions.append(row)
        return row


@pytest.mark.asyncio
async def test_reconcile_adds_and_clears_jobs():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    dispatched = []

    async def dispatch(reminder: PlanningReminder):
        dispatched.append(reminder)

    reconciler = PlanningReconciler(
        scheduler,
        calendar_client=client,
        dispatcher=dispatch,
    )

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="C1:1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    # Default rule schedules multiple nudges (exponential backoff) + an expiry.
    assert len(jobs) == 6
    assert len(scheduler.get_jobs()) == 6
    assert [j.payload.kind for j in jobs[:5]] == ["nudge1", "nudge2", "nudge3", "nudge4", "nudge5"]
    assert jobs[-1].payload.kind == "expire"

    client._events = [
        {
            "id": "evt-planning",
            "summary": "Planning session",
            "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
            "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
        }
    ]
    jobs = await reconciler.reconcile_missing_planning(
        scope="C1:1",
        user_id="U1",
        channel_id="C1",
        now=now + timedelta(hours=1),
    )

    assert jobs == []
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_reconcile_does_not_use_color_id_to_detect_planning():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[{"summary": "Focus time", "colorId": "10"}])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="C1:1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert jobs


@pytest.mark.asyncio
async def test_reconcile_nudges_use_exponential_backoff_by_default():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="C1:1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )
    nudges = [j for j in jobs if j.payload.kind.startswith("nudge")]
    assert len(nudges) == 5
    offsets = [n.run_at - now for n in nudges]
    assert offsets == [
        timedelta(minutes=10),
        timedelta(minutes=20),
        timedelta(minutes=40),
        timedelta(minutes=80),
        timedelta(minutes=160),
    ]


@pytest.mark.asyncio
async def test_reconcile_uses_anchor_event_id_when_provided():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    client.event_lookup[("primary", "ff-planning-u1")] = {
        "id": "ff-planning-u1",
        "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
        "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
    }

    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        planning_event_id="ff-planning-u1",
        now=now,
    )

    assert jobs == []
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_reconcile_ignores_anchor_event_outside_window():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    client.event_lookup[("primary", "ff-planning-u1")] = {
        "id": "ff-planning-u1",
        "start": {"dateTime": "2024-12-30T09:00:00+00:00"},
        "end": {"dateTime": "2024-12-30T09:30:00+00:00"},
    }

    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        planning_event_id="ff-planning-u1",
        now=now,
    )

    assert len(jobs) == 6
    assert scheduler.get_jobs()
    assert client.calls


@pytest.mark.asyncio
async def test_reconcile_does_not_confuse_social_planning_events():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(
        events=[
            {
                "id": "evt-wife",
                "summary": "Planning with wife",
                "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T11:00:00+00:00"},
            }
        ]
    )
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert len(jobs) == 6


@pytest.mark.asyncio
async def test_reconcile_accepts_timeboxing_fallback_event():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(
        events=[
            {
                "id": "evt-timeboxing",
                "summary": "timeboxing",
                "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
            }
        ]
    )
    store = FakePlanningSessionStore()
    reconciler = PlanningReconciler(
        scheduler, calendar_client=client, planning_session_store=store
    )

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert jobs == []
    assert store.upserts
    assert store.upserts[-1]["event_id"] == "evt-timeboxing"


@pytest.mark.asyncio
async def test_reconcile_uses_stored_session_event_id_before_title_scan():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    client.event_lookup[("primary", "ffplanningu1")] = {
        "id": "ffplanningu1",
        "summary": "Daily planning session",
        "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
        "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
    }
    store = FakePlanningSessionStore(
        sessions=[
            _StoredSession(
                user_id="U1",
                planned_date=date(2025, 1, 1),
                calendar_id="primary",
                event_id="ffplanningu1",
                status="planned",
            )
        ]
    )
    reconciler = PlanningReconciler(
        scheduler, calendar_client=client, planning_session_store=store
    )

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert jobs == []
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_reconcile_fallback_keeps_nudges_on_ambiguous_titles():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(
        events=[
            {
                "id": "evt-planning",
                "summary": "Planning session",
                "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
            },
            {
                "id": "evt-timebox",
                "summary": "Timeboxing session",
                "start": {"dateTime": "2025-01-01T11:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T11:30:00+00:00"},
            },
        ]
    )
    store = FakePlanningSessionStore()
    reconciler = PlanningReconciler(
        scheduler, calendar_client=client, planning_session_store=store
    )

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert len(jobs) == 6
    assert not store.upserts


@pytest.mark.asyncio
async def test_reconcile_fallback_prefers_deterministic_ffplanning_id():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(
        events=[
            {
                "id": "ffplanning-u1",
                "summary": "Planning session",
                "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
            },
            {
                "id": "evt-timebox",
                "summary": "Timeboxing session",
                "start": {"dateTime": "2025-01-01T11:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T11:30:00+00:00"},
            },
        ]
    )
    store = FakePlanningSessionStore()
    reconciler = PlanningReconciler(
        scheduler, calendar_client=client, planning_session_store=store
    )

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert jobs == []
    assert store.upserts
    assert store.upserts[-1]["event_id"] == "ffplanning-u1"
