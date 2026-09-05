# tests/unit/test_haunting_offsets.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.agents import HauntingAgent
from fateforger.haunt.messages import FollowUpDue, FollowUpSpec
from fateforger.haunt.service import HauntingService, PendingFollowUp


class _FakeScheduler:
    """Records date jobs; the test fires them by hand."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[datetime, dict]] = {}

    def add_job(self, func, trigger, run_date, id, kwargs, replace_existing, **extra):  # noqa: A002
        self.jobs[id] = (run_date, {"func": func, **kwargs, "_extra": extra})

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def _clock(start: datetime):
    state = {"now": start}

    def now() -> datetime:
        return state["now"]

    return now, state


@pytest.mark.asyncio
async def test_offsets_schedule_each_rung_from_the_arming_time() -> None:
    t0 = datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc)
    now, state = _clock(t0)
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    fired: list[int] = []

    async def dispatcher(due: FollowUpDue) -> None:
        fired.append(due.attempt)

    service.set_dispatcher(dispatcher)
    offsets = (timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=10))
    await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=offsets, cancel_on_user_reply=True),
    )

    run_at, job = next(iter(scheduler.jobs.values()))
    assert run_at == t0 + timedelta(minutes=2)

    for expected_minutes in (5, 10):
        state["now"] = run_at
        await job["func"](message_id=job["message_id"])
        run_at, job = next(iter(scheduler.jobs.values()))
        assert run_at == t0 + timedelta(minutes=expected_minutes)

    state["now"] = run_at
    await job["func"](message_id=job["message_id"])
    assert fired == [0, 1, 2]
    assert scheduler.jobs == {}  # the ladder ended after the last offset


@pytest.mark.asyncio
async def test_activity_on_the_topic_cancels_an_offsets_ladder() -> None:
    now, _ = _clock(datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    service.set_dispatcher(lambda due: None)
    await service.schedule_followup(
        message_id="planning_session:C1:1.0", topic_id="C1:1.0", task_id=None,
        user_id="U1", channel_id="D1", content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2),), cancel_on_user_reply=True),
    )

    cancelled = await service.record_user_activity(topic_id="C1:1.0", task_id=None, user_id="U1")

    assert cancelled == 1
    assert scheduler.jobs == {}


@pytest.mark.asyncio
async def test_a_reply_anywhere_by_the_same_user_cancels_the_ladder() -> None:
    # The nudges arrive in the DM, and a reply there routes to a DM-keyed
    # session -- a different topic than the one the ladder is armed on. The
    # user is the same, and that is what the ladder was following.
    now, _ = _clock(datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    service.set_dispatcher(lambda due: None)
    await service.schedule_followup(
        message_id="planning_session:C1:1.0", topic_id="C1:1.0", task_id=None,
        user_id="U1", channel_id="D1", content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2),), cancel_on_user_reply=True),
    )

    cancelled = await service.record_user_activity(topic_id="D9:dm", task_id=None, user_id="U1")

    assert cancelled == 1
    assert scheduler.jobs == {}


@pytest.mark.asyncio
async def test_another_users_activity_leaves_the_ladder_armed() -> None:
    now, _ = _clock(datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    service.set_dispatcher(lambda due: None)
    await service.schedule_followup(
        message_id="planning_session:C1:1.0", topic_id="C1:1.0", task_id=None,
        user_id="U1", channel_id="D1", content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2),), cancel_on_user_reply=True),
    )

    cancelled = await service.record_user_activity(topic_id="D9:dm", task_id=None, user_id="U2")

    assert cancelled == 0
    assert len(scheduler.jobs) == 1


@pytest.mark.asyncio
async def test_follow_up_run_dates_are_timezone_aware() -> None:
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler)
    offsets = (timedelta(minutes=2),)

    record = await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=offsets, cancel_on_user_reply=True),
    )

    run_at, _job = next(iter(scheduler.jobs.values()))
    assert run_at.tzinfo is not None
    assert run_at.utcoffset() == timedelta(0)
    assert run_at == record.created_at + offsets[0]


@pytest.mark.asyncio
async def test_a_naive_clock_is_read_as_utc() -> None:
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=datetime.utcnow)
    offsets = (timedelta(minutes=2),)

    record = await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=offsets, cancel_on_user_reply=True),
    )

    run_at, _job = next(iter(scheduler.jobs.values()))
    assert run_at.tzinfo is not None
    expected_naive = record.created_at + offsets[0]
    assert run_at == expected_naive.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_follow_up_jobs_carry_a_misfire_grace() -> None:
    now, _ = _clock(datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)

    await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(
            should_schedule=True, offsets=(timedelta(minutes=2),), cancel_on_user_reply=True
        ),
    )

    _run_at, job = next(iter(scheduler.jobs.values()))
    assert job["_extra"].get("misfire_grace_time") == 60


class _FakeSettingsStore:
    """A settings store the schedule call always finds enabled."""

    def __init__(self, *, default_delay_minutes: int = 30) -> None:
        from fateforger.haunt.settings_store import AdmonishmentSettingsPayload

        self._payload = AdmonishmentSettingsPayload(
            user_id="U1", enabled=True, default_delay_minutes=default_delay_minutes
        )

    async def get_settings(self, *, user_id, channel_id=None):
        return self._payload

    async def upsert_settings(self, *, user_id, channel_id=None, patch):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_an_offsets_spec_ignores_the_settings_delay_default() -> None:
    # `after` is a single-delay spec's field; an offsets ladder times itself
    # from its own list, and nothing downstream reads `after` once `offsets`
    # is set. Filling it from the settings default anyway is a landmine.
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, settings_store=_FakeSettingsStore())
    offsets = (timedelta(minutes=2), timedelta(minutes=5))

    record = await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=offsets, cancel_on_user_reply=True),
    )

    assert record is not None
    assert record.spec.after is None
    assert record.spec.offsets == offsets


@pytest.mark.asyncio
async def test_a_single_delay_spec_still_gets_the_settings_default() -> None:
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, settings_store=_FakeSettingsStore(default_delay_minutes=30))

    record = await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, cancel_on_user_reply=True),
    )

    assert record is not None
    assert record.spec.after == timedelta(minutes=30)


def test_lines_select_by_attempt_and_clamp_at_the_end() -> None:
    record = PendingFollowUp(
        message_id="m", topic_id="t", task_id=None, user_id="U1", channel_id="D1",
        content="ignored when lines are set",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2), timedelta(minutes=5)), lines=("first", "second")),
        attempt=0, created_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
    )
    due = lambda attempt: FollowUpDue(message_id="m", topic_id="t", task_id=None, attempt=attempt, escalation="gentle", user_id="U1")  # noqa: E731

    assert HauntingAgent._format_followup(record, due(0)) == "first"
    assert HauntingAgent._format_followup(record, due(1)) == "second"
    assert HauntingAgent._format_followup(record, due(7)) == "second"


def test_without_lines_the_old_prefixes_still_apply() -> None:
    record = PendingFollowUp(
        message_id="m", topic_id="t", task_id=None, user_id="U1", channel_id="D1",
        content="Follow up here", spec=FollowUpSpec(should_schedule=True, after=timedelta(minutes=10)),
        attempt=0, created_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
    )
    due = FollowUpDue(message_id="m", topic_id="t", task_id=None, attempt=0, escalation="firm", user_id="U1")
    assert HauntingAgent._format_followup(record, due) == "Reminder: Follow up here"
