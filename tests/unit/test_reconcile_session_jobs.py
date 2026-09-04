"""A planning event ahead schedules its own start; a passed one is missing again."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import TimeboxingStanding
from fateforger.haunt.reconcile import PlanningReconciler, PlanningRuleConfig, PlanningSessionRule
from fateforger.haunt.session_start import SESSION_EXPIRE_KIND, SESSION_START_KIND

from .test_reconcile import DummyCalendarClient, FakeScheduler

AMS = "Europe/Amsterdam"


def _event(start: datetime, minutes: int = 30) -> dict:
    return {
        "id": "ffplanning1",
        "summary": "Daily planning session",
        "start": {"dateTime": start.isoformat(), "timeZone": AMS},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": AMS},
    }


class _Ledger:
    def __init__(self, committed_key: str | None = None, open_key: str | None = None):
        self._standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)
        self.asked: list[dict] = []

    async def standing_for(self, **kwargs):
        self.asked.append(kwargs)
        return self._standing


def _reconciler(scheduler, *, event: dict | None, ledger=None):
    client = DummyCalendarClient(events=[])
    if event is not None:
        client.event_lookup[("primary", "ffplanning1")] = event
    rule = PlanningSessionRule(calendar_client=client, config=PlanningRuleConfig(), timeboxing_ledger=ledger)

    async def dispatch(reminder):
        return None

    return PlanningReconciler(scheduler, calendar_client=client, dispatcher=dispatch, rule=rule)


def _jobs_by_kind(scheduler):
    return {job.kwargs["reminder"].kind: job for job in scheduler.get_jobs()}


@pytest.mark.asyncio
async def test_an_event_ahead_schedules_its_start_and_expiry_and_no_nudges() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start))
    now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    jobs = _jobs_by_kind(scheduler)
    assert set(jobs) == {SESSION_START_KIND, SESSION_EXPIRE_KIND}
    assert jobs[SESSION_START_KIND].trigger.run_date == start
    assert jobs[SESSION_EXPIRE_KIND].trigger.run_date == start + timedelta(minutes=30) + timedelta(minutes=60)
    reminder = jobs[SESSION_START_KIND].kwargs["reminder"]
    assert reminder.event_start == start.isoformat()
    assert reminder.event_tz == AMS
    assert reminder.user_id == "U1" and reminder.channel_id == "D1"


@pytest.mark.asyncio
async def test_a_restart_inside_the_window_starts_now_not_in_the_past() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start))
    now = start + timedelta(minutes=10)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now.astimezone(timezone.utc)
    )

    jobs = _jobs_by_kind(scheduler)
    assert jobs[SESSION_START_KIND].trigger.run_date >= now.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_a_passed_event_with_no_committed_session_is_missing_again() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    ledger = _Ledger(committed_key=None)
    reconciler = _reconciler(scheduler, event=_event(start), ledger=ledger)
    now = (start + timedelta(hours=2)).astimezone(timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    kinds = set(_jobs_by_kind(scheduler))
    assert SESSION_START_KIND not in kinds
    assert any(kind.startswith("nudge") for kind in kinds)
    assert ledger.asked and ledger.asked[0]["planned_from"] == start.date()


@pytest.mark.asyncio
async def test_a_passed_event_with_a_committed_session_schedules_nothing() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start), ledger=_Ledger(committed_key="C1:1.0"))
    now = (start + timedelta(hours=2)).astimezone(timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_a_moved_event_moves_its_jobs_under_the_same_keys() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    client = DummyCalendarClient(events=[])
    client.event_lookup[("primary", "ffplanning1")] = _event(start)
    rule = PlanningSessionRule(calendar_client=client, config=PlanningRuleConfig())

    async def dispatch(reminder):
        return None

    reconciler = PlanningReconciler(scheduler, calendar_client=client, dispatcher=dispatch, rule=rule)
    now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now)
    first_ids = {job.id for job in scheduler.get_jobs()}

    client.event_lookup[("primary", "ffplanning1")] = _event(start + timedelta(hours=1))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now)

    assert {job.id for job in scheduler.get_jobs()} == first_ids
    assert _jobs_by_kind(scheduler)[SESSION_START_KIND].trigger.run_date == start + timedelta(hours=1)
