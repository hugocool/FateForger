from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import logging

import pytest

from fateforger.haunt.reconcile import (
    McpCalendarClient,
    PlanningReconciler,
    PlanningReminder,
    PlanningRuleConfig,
)


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
        # Shaped like a real APScheduler Job: the scheduled time lives on the
        # trigger (`DateTrigger.run_date`), which is what production reads. A
        # double that exposed only a flat `run_date` would let a change pass
        # here and fail live -- which is how the len()-over-a-count bug shipped.
        job_trigger = type("DateTrigger", (), {"run_date": run_date})()
        self._jobs[id] = type(
            "Job",
            (),
            {"id": id, "run_date": run_date, "trigger": job_trigger, "kwargs": kwargs},
        )

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
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )


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


class _FakeTextItem:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeToolResult:
    def __init__(self, content: str) -> None:
        self.result = [_FakeTextItem(content)]


class _FakeWorkbench:
    def __init__(self, content: str) -> None:
        self._content = content

    async def call_tool(self, name: str, arguments: dict):
        assert name == "list-events"
        return _FakeToolResult(self._content)


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
            # The minted id, not the title. `planning_event_id_for_user` stamps
            # `ffplanning...` on every planning event this system creates, and
            # that is the only thing the reconciler now recognises.
            "id": "ffplanningu1abc",
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
async def test_reconcile_list_events_window_omits_microseconds() -> None:
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, 0, 654321, tzinfo=timezone.utc)
    await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert client.calls
    _, time_min, time_max = client.calls[-1]
    assert "." not in time_min
    assert "." not in time_max


@pytest.mark.asyncio
async def test_haunt_calendar_client_logs_mcp_tool_error_payload(caplog) -> None:
    client = object.__new__(McpCalendarClient)
    client._workbench = _FakeWorkbench(
        'MCP error -32602: Invalid arguments for tool list-events'
    )

    with caplog.at_level(logging.WARNING, logger="fateforger.haunt.reconcile"):
        events = await client.list_events(
            calendar_id="primary",
            time_min="2025-01-01T09:00:00+00:00",
            time_max="2025-01-02T09:00:00+00:00",
        )

    assert events == []
    assert "list-events returned tool error payload" in caplog.text


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
async def test_reconcile_logs_outcome_for_anchor_match(caplog):
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    client.event_lookup[("primary", "ff-planning-u1")] = {
        "id": "ff-planning-u1",
        "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
        "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
    }

    with caplog.at_level(logging.INFO, logger="fateforger.haunt.reconcile"):
        jobs = await reconciler.reconcile_missing_planning(
            scope="U1",
            user_id="U1",
            channel_id="C1",
            planning_event_id="ff-planning-u1",
            now=now,
        )

    assert jobs == []
    assert "planning_reconcile evaluate outcome=anchor_match" in caplog.text


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
async def test_an_unmarked_event_is_not_adopted_however_it_is_titled():
    """This test asserted the opposite until 2026-09-01, and it was wrong.

    An event titled "timeboxing" used to score 70 and be adopted as the user's
    planning session. That is a judgement about what a title someone wrote
    means, made by a keyword table -- the thing CLAUDE.md sends to a model and
    never to a pattern. It also had to be defended by hand: "Planning with wife"
    needed a -40 guardrail, "poker" needed its own, and that list has no end
    because it is a list of every way a person might phrase something.

    So an event carrying no mark this system minted is not adopted, whatever it
    is called. The user is nudged, the nudge books a session, and the booked one
    carries the mark. Nudging about a session that exists is visible and
    correctable in one reply; silently adopting a poker night is neither.
    """
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
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="C1", now=now
    )

    assert len(jobs) == 6, "an unmarked event was adopted as the planning session"


@pytest.mark.asyncio
async def test_a_marked_event_is_adopted_whatever_it_is_titled():
    """The other half: identity decides, so the title is irrelevant both ways.

    A planning event the user renamed to something unrecognisable is still
    theirs, and would have scored 0 under the old table.
    """
    scheduler = FakeScheduler()
    client = DummyCalendarClient(
        events=[
            {
                "id": "ffplanningu1xyz",
                "summary": "zzz",
                "start": {"dateTime": "2025-01-01T10:00:00+00:00"},
                "end": {"dateTime": "2025-01-01T10:30:00+00:00"},
            }
        ]
    )
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="C1", now=now
    )

    assert jobs == [], "a marked event was not recognised"

@pytest.mark.asyncio
async def test_reconcile_logs_outcome_for_nudges_scheduled(caplog):
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    reconciler = PlanningReconciler(scheduler, calendar_client=client)

    now = datetime(2025, 1, 1, 9, 0, tzinfo=timezone.utc)
    with caplog.at_level(logging.INFO, logger="fateforger.haunt.reconcile"):
        jobs = await reconciler.reconcile_missing_planning(
            scope="U1",
            user_id="U1",
            channel_id="C1",
            planning_event_id="ff-planning-u1",
            now=now,
        )

    assert len(jobs) == 6
    assert "planning_reconcile evaluate outcome=nudges_scheduled" in caplog.text


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


@pytest.mark.asyncio
async def test_reconcile_trusts_recent_local_stored_session_when_calendar_read_lags():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    store = FakePlanningSessionStore(
        sessions=[
            _StoredSession(
                user_id="U1",
                planned_date=date(2025, 1, 1),
                calendar_id="primary",
                event_id="ffplanningu1",
                status="planned",
                source="admonisher_planning_card",
                updated_at=datetime(2025, 1, 1, 9, 0, 0),
            )
        ]
    )
    reconciler = PlanningReconciler(
        scheduler, calendar_client=client, planning_session_store=store
    )

    now = datetime(2025, 1, 1, 9, 1, tzinfo=timezone.utc)
    jobs = await reconciler.reconcile_missing_planning(
        scope="U1",
        user_id="U1",
        channel_id="C1",
        now=now,
    )

    assert jobs == []
    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_reconcile_does_not_trust_stale_local_stored_session_without_calendar_event():
    scheduler = FakeScheduler()
    client = DummyCalendarClient(events=[])
    store = FakePlanningSessionStore(
        sessions=[
            _StoredSession(
                user_id="U1",
                planned_date=date(2025, 1, 1),
                calendar_id="primary",
                event_id="ffplanningu1",
                status="planned",
                source="admonisher_planning_card",
                updated_at=datetime(2025, 1, 1, 8, 40, 0),
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

    assert len(jobs) == 6
    assert scheduler.get_jobs()
