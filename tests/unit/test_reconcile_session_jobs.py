"""A planning event ahead schedules its own start; a passed one is missing again."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


class _DayAwareLedger:
    """A ledger that is committed for exactly one day, to prove which day
    `evaluate` actually asked about."""

    def __init__(self, committed_day: date | None):
        self._committed_day = committed_day
        self.asked: list[dict] = []

    async def standing_for(self, **kwargs):
        self.asked.append(kwargs)
        committed = kwargs["planned_from"] == self._committed_day
        return TimeboxingStanding(committed_session_key="C1:1.0" if committed else None)


@pytest.mark.asyncio
async def test_a_stale_anchor_with_a_committed_old_day_still_nudges_today() -> None:
    """A stable per-user anchor id can outlive the day it named.

    `planning_event_id_for_user` mints one id reused every day; `get_event`
    keeps resolving it long after the day it planned has passed. If that
    stale day happened to be committed, the reconciler must not read that as
    "today is planned" -- it must fall through exactly as it would for an
    absent anchor.
    """
    scheduler = FakeScheduler()
    stale_start = datetime(2026, 9, 1, 9, 0, tzinfo=ZoneInfo(AMS))
    ledger = _Ledger(committed_key="C1:1.0")
    reconciler = _reconciler(scheduler, event=_event(stale_start), ledger=ledger)
    now = datetime(2026, 9, 4, 8, 0, tzinfo=ZoneInfo(AMS)).astimezone(timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    kinds = set(_jobs_by_kind(scheduler))
    assert SESSION_START_KIND not in kinds
    assert SESSION_EXPIRE_KIND not in kinds
    assert any(kind.startswith("nudge") for kind in kinds)
    # The stale day's committed-ness was never even worth asking about.
    assert ledger.asked == []


@pytest.mark.asyncio
async def test_a_tzless_offset_event_uses_its_own_offset_for_the_cutoff() -> None:
    """No `timeZone` name must not mean "treat it as UTC" -- that silently
    shifts which side of the 14:00 cutoff the event falls on. 15:00+02:00 is
    past the cutoff (plans the next day); 13:00 UTC (the same instant,
    wrongly read in UTC) is not.
    """
    scheduler = FakeScheduler()
    event = {
        "id": "ffplanning1",
        "summary": "Daily planning session",
        "start": {"dateTime": "2026-09-03T15:00:00+02:00"},
        "end": {"dateTime": "2026-09-03T15:30:00+02:00"},
    }
    now = datetime(2026, 9, 4, 20, 0, tzinfo=timezone.utc)

    # The event plans 2026-09-04 (past the 14:00 cutoff at its own offset).
    # A ledger committed for that correct day silences the reconciler.
    committed_correct_day = _DayAwareLedger(committed_day=date(2026, 9, 4))
    reconciler = _reconciler(scheduler, event=event, ledger=committed_correct_day)
    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )
    assert committed_correct_day.asked[0]["planned_from"] == date(2026, 9, 4)
    assert scheduler.get_jobs() == []

    # A ledger committed only for the wrong day (what a UTC-forcing bug would
    # have asked about) must not silence anything -- the nudge ladder fires.
    scheduler2 = FakeScheduler()
    committed_wrong_day = _DayAwareLedger(committed_day=date(2026, 9, 3))
    reconciler2 = _reconciler(scheduler2, event=event, ledger=committed_wrong_day)
    await reconciler2.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )
    assert committed_wrong_day.asked[0]["planned_from"] == date(2026, 9, 4)
    kinds = set(_jobs_by_kind(scheduler2))
    assert SESSION_START_KIND not in kinds
    assert any(kind.startswith("nudge") for kind in kinds)


@pytest.mark.asyncio
async def test_a_committed_day_west_of_utc_is_not_dropped_by_a_utc_date_bound() -> None:
    """The planned-day bound must compare `now` against `day` in the
    anchor's own zone, not UTC. West of UTC the two dates disagree before
    local midnight does: 18:00 in Los Angeles is already 01:00 UTC the next
    day, so a UTC-dated bound reads "today" as tomorrow and would re-nudge a
    day that was already planned and committed, every evening from local
    17:00 to midnight.
    """
    scheduler = FakeScheduler()
    la = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 9, 4, 9, 0, tzinfo=la)
    event = {
        "id": "ffplanning1",
        "summary": "Daily planning session",
        "start": {"dateTime": start.isoformat(), "timeZone": "America/Los_Angeles"},
        "end": {
            "dateTime": (start + timedelta(minutes=30)).isoformat(),
            "timeZone": "America/Los_Angeles",
        },
    }
    ledger = _DayAwareLedger(committed_day=date(2026, 9, 4))
    reconciler = _reconciler(scheduler, event=event, ledger=ledger)
    now_local = datetime(2026, 9, 4, 18, 0, tzinfo=la)
    now_utc = now_local.astimezone(timezone.utc)

    # Sanity: this is exactly the crossing the bug depends on -- UTC has
    # already rolled over to the next day while it is still today in LA.
    assert now_utc.date() == date(2026, 9, 5)

    jobs = await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now_utc
    )

    assert jobs == []
    assert ledger.asked and ledger.asked[0]["planned_from"] == date(2026, 9, 4)
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_expire_after_is_honoured() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    client = DummyCalendarClient(events=[])
    client.event_lookup[("primary", "ffplanning1")] = _event(start)
    rule = PlanningSessionRule(
        calendar_client=client,
        config=PlanningRuleConfig(expire_after=timedelta(minutes=2)),
    )

    async def dispatch(reminder):
        return None

    reconciler = PlanningReconciler(scheduler, calendar_client=client, dispatcher=dispatch, rule=rule)
    now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    jobs = _jobs_by_kind(scheduler)
    event_end = start + timedelta(minutes=30)
    assert jobs[SESSION_EXPIRE_KIND].trigger.run_date == event_end + timedelta(minutes=2)


@pytest.mark.asyncio
async def test_a_reconcile_between_the_end_and_the_expiry_keeps_the_expiry_job() -> None:
    # The hour between the event's end and its expiry belongs to the anchor:
    # an idle- or message-triggered reconcile in it must not sweep the
    # session_expire job that is the only thing left to close the session.
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start), ledger=_Ledger())
    event_end = start + timedelta(minutes=30)
    expiry = event_end + timedelta(minutes=60)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1",
        now=(start - timedelta(hours=1)).astimezone(timezone.utc),
    )
    assert _jobs_by_kind(scheduler)[SESSION_EXPIRE_KIND].trigger.run_date == expiry

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1",
        now=(event_end + timedelta(minutes=5)).astimezone(timezone.utc),
    )

    jobs = _jobs_by_kind(scheduler)
    assert set(jobs) == {SESSION_EXPIRE_KIND}
    assert jobs[SESSION_EXPIRE_KIND].trigger.run_date == expiry


@pytest.mark.asyncio
async def test_past_the_expiry_an_unplanned_day_is_missing_again() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start), ledger=_Ledger(committed_key=None))
    expiry = start + timedelta(minutes=30) + timedelta(minutes=60)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1",
        now=(expiry + timedelta(minutes=1)).astimezone(timezone.utc),
    )

    kinds = set(_jobs_by_kind(scheduler))
    assert SESSION_START_KIND not in kinds and SESSION_EXPIRE_KIND not in kinds
    assert any(kind.startswith("nudge") for kind in kinds)
