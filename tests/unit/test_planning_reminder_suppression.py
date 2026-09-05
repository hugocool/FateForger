from datetime import datetime, timedelta, timezone
import logging
from dataclasses import dataclass
from datetime import date

import pytest

from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload
from fateforger.haunt.reconcile import PlanningReminder
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


class _RecordingDraftStore:
    """Enough of the draft store for a card to be built and posted."""

    def __init__(self):
        self.created = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        return EventDraftPayload(
            draft_id=kwargs["draft_id"],
            user_id=kwargs["user_id"],
            channel_id=kwargs["channel_id"],
            message_ts=None,
            calendar_id=kwargs["calendar_id"],
            event_id=kwargs["event_id"],
            title=kwargs["title"],
            description=kwargs["description"],
            timezone=kwargs["timezone"],
            start_at_utc=kwargs["start_at_utc"],
            duration_min=kwargs["duration_min"],
            status=DraftStatus.PENDING,
            event_url=None,
            last_error=None,
        )

    async def attach_message(self, **kwargs):  # noqa: ARG002
        return None


class _NoSlotRuntime:
    """A runtime whose planner never answers, so the dispatcher uses defaults."""

    event_draft_store = None
    planning_guardian = None
    planning_reconciler = None
    timeboxing_session_store = None

    def __init__(self, *, ledger):
        self.event_draft_store = _RecordingDraftStore()
        self.timeboxing_session_store = ledger

    async def send_message(self, *args, **kwargs):  # noqa: ARG002
        return None


def _session(session_key, *, status, day=None, owner="U1"):
    from fateforger.agents.timeboxing.session_contracts import (
        PlanningDay,
        PlanningSessionSnapshot,
    )

    snapshot = PlanningSessionSnapshot(
        session_key=session_key, revision=3, owner_user_id=owner, status=status
    )
    if day is not None:
        snapshot = snapshot.model_copy(
            update={
                # Derived from the date, the way the host derives it. A
                # hardcoded WORKING here failed every Saturday and Sunday,
                # because `day` is today (#305).
                "planning_day": PlanningDay.lock_default(
                    value=day, timezone="Europe/Amsterdam", lock_revision=1
                )
            }
        )
    return snapshot


def _reminder(user_id="U1"):
    return PlanningReminder(
        scope=user_id,
        kind="nudge1",
        attempt=1,
        message="nudge",
        user_id=user_id,
        channel_id="D1",
    )


async def _dispatch(ledger):
    runtime = _NoSlotRuntime(ledger=ledger)
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    await coordinator.dispatch_planning_reminder(_reminder())
    return client, runtime.event_draft_store


@pytest.mark.asyncio
async def test_a_user_with_no_session_is_nudged():
    """The control: without it every suppression test below is vacuous."""

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    client, drafts = await _dispatch(InMemoryPlanningSessionRepository())

    assert len(client.posted) == 1
    assert len(drafts.created) == 1


@pytest.mark.asyncio
async def test_dispatch_is_silent_while_a_timeboxing_session_is_open():
    """#256: the 00:33:26 card, posted to a user mid-session.

    Read from the session store, not from a flag the turn handler sets --
    the flag and the calendar disagreed twelve times in one evening.
    """

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    ledger = InMemoryPlanningSessionRepository([_session("C1:1.0", status="open")])

    client, drafts = await _dispatch(ledger)

    assert client.posted == []
    assert drafts.created == []


@pytest.mark.asyncio
async def test_dispatch_is_silent_after_the_day_was_committed():
    """#256: the 00:43:58 card, posted eleven minutes after a 19-block commit."""

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    today = datetime.now(timezone.utc).date()
    ledger = InMemoryPlanningSessionRepository(
        [_session("C1:1.0", status="committed", day=today)]
    )

    client, drafts = await _dispatch(ledger)

    assert client.posted == []
    assert drafts.created == []


@pytest.mark.asyncio
async def test_a_day_committed_last_week_does_not_silence_this_weeks_nudge():
    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    last_week = datetime.now(timezone.utc).date() - timedelta(days=7)
    ledger = InMemoryPlanningSessionRepository(
        [_session("C1:1.0", status="committed", day=last_week)]
    )

    client, _ = await _dispatch(ledger)

    assert len(client.posted) == 1


@pytest.mark.asyncio
async def test_an_open_session_abandoned_hours_ago_no_longer_silences():
    """Suppression that never lifted would be the opposite silent failure."""

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    stale = datetime.now(timezone.utc) - timedelta(hours=3)
    ledger = InMemoryPlanningSessionRepository(
        [_session("C1:1.0", status="open")], clock=lambda: stale
    )

    client, _ = await _dispatch(ledger)

    assert len(client.posted) == 1


@pytest.mark.asyncio
async def test_someone_elses_open_session_is_not_this_users():
    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    ledger = InMemoryPlanningSessionRepository(
        [_session("C9:1.0", status="open", owner="U2")]
    )

    client, _ = await _dispatch(ledger)

    assert len(client.posted) == 1


@pytest.mark.asyncio
async def test_a_session_opened_during_the_slow_part_still_wins():
    """The check runs again before the first side effect, not only at entry.

    Between entry and the draft the dispatcher awaits the calendar and a
    slot suggestion -- up to ten seconds. A turn that begins in that window
    is exactly the mid-session interruption the ticket describes.
    """

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    ledger = InMemoryPlanningSessionRepository()
    runtime = _NoSlotRuntime(ledger=ledger)

    async def _turn_begins_meanwhile(*args, **kwargs):  # noqa: ARG001
        await ledger.load_or_create("C1:1.0", owner_user_id="U1")
        return None

    runtime.send_message = _turn_begins_meanwhile  # type: ignore[method-assign]
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    await coordinator.dispatch_planning_reminder(_reminder())

    assert client.posted == []
    assert runtime.event_draft_store.created == []


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
                        event_id="ffplanningcanonicalabc",
                    )
                ]
            ),
            "planning_reconciler": _DummyReconciler(
                _DummyCalendarClient(
                    events=[
                        {
                            "id": "ffplanningcanonicalabc",
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
    assert runtime.planning_anchor_store.upserts[-1]["event_id"] == "ffplanningcanonicalabc"


@pytest.mark.asyncio
async def test_the_planning_anchor_lives_on_the_timeboxing_calendar():
    """#256 part three: one calendar id for the session and the nudger.

    The anchor went to "primary" while the session wrote to
    TIMEBOX_CALENDAR_ID, so the reconciler looked for planning on a calendar
    the session never touched.
    """

    from fateforger.agents.timeboxing.adaptive_timeboxing import (
        InMemoryPlanningSessionRepository,
    )

    runtime = _NoSlotRuntime(ledger=InMemoryPlanningSessionRepository())
    runtime.timeboxing_calendar_id = "hugo.evers@gmail.com"  # type: ignore[attr-defined]
    runtime.planning_anchor_store = _PayloadAnchorStore()  # type: ignore[attr-defined]
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]

    await coordinator.dispatch_planning_reminder(_reminder())

    upserts = runtime.planning_anchor_store.upserts  # type: ignore[attr-defined]
    assert upserts and upserts[-1]["calendar_id"] == "hugo.evers@gmail.com"
    assert runtime.event_draft_store.created[-1]["calendar_id"] == "hugo.evers@gmail.com"


class _PayloadAnchorStore(_DummyAnchorStore):
    async def get(self, *, user_id):  # noqa: ARG002
        return None

    async def upsert(self, **kwargs):
        from fateforger.haunt.planning_store import PlanningAnchorPayload

        self.upserts.append(kwargs)
        return PlanningAnchorPayload(**kwargs)


@pytest.mark.asyncio
async def test_revalidation_hands_the_rule_the_ledger_it_needs(monkeypatch):
    # Without the ledger the rule cannot ask whether the day was committed, so
    # a passed planning event whose day *is* planned still reads as missing and
    # the ladder starts over.
    built: list[dict] = []

    class _SpyRule:
        def __init__(self, **kwargs):
            built.append(kwargs)

        async def evaluate(self, **_):
            return []

    monkeypatch.setattr("fateforger.slack_bot.planning.PlanningSessionRule", _SpyRule)
    ledger = object()
    runtime = type(
        "Runtime",
        (),
        {
            "event_draft_store": object(),
            "planning_guardian": None,
            "planning_session_store": _DummyPlanningStore(),
            "planning_reconciler": _DummyReconciler(_DummyCalendarClient(events=[])),
            "timeboxing_session_store": ledger,
        },
    )()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=DummyClient())  # type: ignore[arg-type]

    await coordinator._planning_still_missing(
        reminder=PlanningReminder(
            scope="U1", kind="nudge1", attempt=1, message="", user_id="U1", channel_id="D1"
        ),
        planning_event_id="ffplanning1",
        calendar_id="primary",
    )

    assert built and built[0]["timeboxing_ledger"] is ledger
