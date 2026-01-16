from datetime import datetime, timedelta, timezone

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

    assert len(jobs) == 4
    assert len(scheduler.get_jobs()) == 4

    client._events = [{"summary": "Planning session", "colorId": "10"}]
    jobs = await reconciler.reconcile_missing_planning(
        scope="C1:1",
        user_id="U1",
        channel_id="C1",
        now=now + timedelta(hours=1),
    )

    assert jobs == []
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_reconcile_uses_anchor_event_id_when_provided():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    client.event_lookup[("primary", "ff-planning-u1")] = {"id": "ff-planning-u1"}

    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        planning_event_id="ff-planning-u1",
        now=now,
    )

    assert jobs == []
    assert scheduler.get_jobs() == []
