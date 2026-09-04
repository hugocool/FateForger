from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from autogen_core import AgentId

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    OpenSessionRow,
    TimeboxingStanding,
)
from fateforger.agents.timeboxing.session_contracts import CancelSession, ConfirmPlanningDay
from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import LADDER_OFFSETS, SESSION_EXPIRE_KIND, SESSION_START_KIND
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.planning import DEFAULT_TIMEZONE
from fateforger.slack_bot.session_start import SessionStarter

AMS = "Europe/Amsterdam"
START = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))


def _reminder(kind: str = SESSION_START_KIND) -> PlanningReminder:
    return PlanningReminder(
        scope="U1", kind=kind, attempt=1, message="", user_id="U1", channel_id="D1",
        event_start=START.isoformat(), event_end=(START + timedelta(minutes=30)).isoformat(), event_tz=AMS,
    )


def _row(session_key: str, revision: int, *, minute: int = 0) -> OpenSessionRow:
    return OpenSessionRow(
        session_key=session_key,
        revision=revision,
        updated_at=datetime(2026, 9, 4, 9, minute),
    )


class _Ledger:
    def __init__(self, open_key=None, committed_key=None, *, rows=(), events=None, standing_error=None):
        self.standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)
        self.rows = list(rows)
        self.standing_error = standing_error
        self.asked_for_day = []
        self._events = events

    async def standing_for(self, **_):
        if self.standing_error is not None:
            raise self.standing_error
        return self.standing

    async def open_sessions_for_day(self, *, owner_user_id, planning_date):
        self.asked_for_day.append((owner_user_id, planning_date))
        return list(self.rows)

    async def load(self, key):
        return SimpleNamespace(revision=3, status="open")


class _Haunting:
    def __init__(self, events=None):
        self.scheduled = []
        self.cancelled = []
        self._events = events

    async def schedule_followup(self, **kwargs):
        if self._events is not None:
            self._events.append("schedule_followup")
        self.scheduled.append(kwargs)

    async def cancel_followups(self, **kwargs):
        self.cancelled.append(kwargs)


class _Runtime:
    def __init__(self):
        self.sent = []

    async def send_message(self, message, recipient):
        self.sent.append((message, recipient))


class _Guardian:
    def __init__(self):
        self.reconciled = []

    async def reconcile_user(self, *, user_id):
        self.reconciled.append(user_id)


class _Client:
    def __init__(self):
        self.posted, self.updates = [], []

    async def chat_postMessage(self, **p):
        self.posted.append(p)
        return {"channel": p["channel"], "ts": "root.1"}

    async def chat_update(self, **p):
        self.updates.append(p)
        return {"ok": True}

    async def chat_getPermalink(self, **_):
        return {"permalink": "https://slack/p"}

    async def conversations_invite(self, **_):
        return {"ok": True}


def _starter(monkeypatch, *, ledger, haunting=None, runtime=None, guardian=None, turns=None, events=None):
    turns = [] if turns is None else turns

    async def _deliver(**kwargs):
        if events is not None:
            events.append("turn")
        turns.append(kwargs)

    monkeypatch.setattr("fateforger.slack_bot.session_start._deliver_timebox_turn", _deliver)
    return SessionStarter(
        runtime=runtime or _Runtime(), client=_Client(),
        focus=FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"]),
        guardian=guardian or _Guardian(), ledger=ledger, haunting_service=haunting or _Haunting(),
        target_channel="C1", now=lambda: START.astimezone(timezone.utc),
    ), turns


@pytest.mark.asyncio
async def test_start_opens_confirms_the_day_dms_and_arms_the_ladder(monkeypatch):
    haunting, runtime = _Haunting(), _Runtime()
    starter, turns = _starter(monkeypatch, ledger=_Ledger(), haunting=haunting, runtime=runtime)

    await starter.start(_reminder())

    assert len(turns) == 1
    envelope = turns[0]["action"]
    assert envelope.session_key == "C1:root.1" and envelope.expected_revision == 0
    assert isinstance(envelope.intent, ConfirmPlanningDay)
    assert envelope.intent.planning_day.date.isoformat() == "2026-09-04"
    assert envelope.intent.planning_day.timezone == AMS
    # The interaction id is the turn's replay key: two dispatches of one
    # reminder must land on the same one, not open the day twice.
    assert turns[0]["interaction_id"] == "session_start:C1:root.1"
    dm, recipient = runtime.sent[0]
    assert "https://slack/p" in dm.content and dm.user_id == "U1"
    assert recipient == AgentId("user_channel", key="U1")
    armed = haunting.scheduled[0]
    assert armed["topic_id"] == "C1:root.1"
    assert armed["message_id"] == "planning_session:C1:root.1"
    assert armed["spec"].offsets == LADDER_OFFSETS and armed["spec"].cancel_on_user_reply is True
    assert len(armed["spec"].lines) == len(LADDER_OFFSETS) and "https://slack/p" in armed["spec"].lines[0]


@pytest.mark.asyncio
async def test_the_ladder_is_armed_after_the_turn_not_before(monkeypatch):
    # The turn records activity on the session key, and activity cancels a
    # pending ladder: armed first, the ladder disarms itself.
    events: list[str] = []
    haunting = _Haunting(events=events)
    starter, _ = _starter(monkeypatch, ledger=_Ledger(), haunting=haunting, events=events)

    await starter.start(_reminder())

    assert events == ["turn", "schedule_followup"]


@pytest.mark.asyncio
async def test_an_evening_event_plans_the_next_day(monkeypatch):
    starter, turns = _starter(monkeypatch, ledger=_Ledger())
    evening = START.replace(hour=18)
    reminder = PlanningReminder(scope="U1", kind=SESSION_START_KIND, attempt=1, message="", user_id="U1", channel_id="D1",
                                event_start=evening.isoformat(), event_end=(evening + timedelta(minutes=30)).isoformat(), event_tz=AMS)

    await starter.start(reminder)

    assert turns[0]["action"].intent.planning_day.date.isoformat() == "2026-09-05"


@pytest.mark.asyncio
async def test_an_event_without_a_named_timezone_keeps_its_own_offset(monkeypatch):
    # No IANA name: the offset inside event_start is the timezone there is, and
    # the locked day is labelled with the default the planning card writes in,
    # never UTC.
    starter, turns = _starter(monkeypatch, ledger=_Ledger())
    reminder = PlanningReminder(
        scope="U1", kind=SESSION_START_KIND, attempt=1, message="", user_id="U1", channel_id="D1",
        event_start="2026-09-04T09:00:00+02:00", event_end="2026-09-04T09:30:00+02:00", event_tz=None,
    )

    await starter.start(reminder)

    planning_day = turns[0]["action"].intent.planning_day
    assert planning_day.date.isoformat() == "2026-09-04"
    assert planning_day.timezone == DEFAULT_TIMEZONE


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [_Ledger(open_key="C1:old"), _Ledger(committed_key="C1:done")])
async def test_an_open_or_committed_session_blocks_the_start(monkeypatch, standing):
    haunting = _Haunting()
    starter, turns = _starter(monkeypatch, ledger=standing, haunting=haunting)

    await starter.start(_reminder())

    assert turns == [] and haunting.scheduled == []


@pytest.mark.asyncio
async def test_a_store_failure_starts_nothing_and_is_metered(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "fateforger.slack_bot.session_start.record_error",
        lambda **kwargs: errors.append(kwargs),
    )
    haunting = _Haunting()
    starter, turns = _starter(
        monkeypatch, ledger=_Ledger(standing_error=RuntimeError("db down")), haunting=haunting
    )

    await starter.start(_reminder())

    assert starter._client.posted == [], "no surface may be opened on an unreadable guard"
    assert turns == [] and haunting.scheduled == []
    assert {"component": "session_start", "error_type": "guard_failure"} in errors


@pytest.mark.asyncio
async def test_a_failed_turn_relabels_the_root_and_arms_nothing(monkeypatch):
    haunting = _Haunting()

    async def _boom(**_):
        raise RuntimeError("kernel down")

    monkeypatch.setattr("fateforger.slack_bot.session_start._deliver_timebox_turn", _boom)
    starter = SessionStarter(runtime=_Runtime(), client=_Client(), focus=FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"]),
                             guardian=_Guardian(), ledger=_Ledger(), haunting_service=haunting, target_channel="C1",
                             now=lambda: START.astimezone(timezone.utc))

    await starter.start(_reminder())

    assert haunting.scheduled == []
    assert any("canceled" in (u.get("text") or "") for u in starter._client.updates)


@pytest.mark.asyncio
async def test_expire_closes_the_session_nobody_touched_and_hands_over(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(rows=[_row("C1:root.1", 1)])
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert ledger.asked_for_day == [("U1", START.date())]
    assert haunting.cancelled == [{"topic_id": "C1:root.1"}]
    assert isinstance(turns[0]["action"].intent, CancelSession) and turns[0]["action"].expected_revision == 1
    relabel = starter._client.updates[-1]
    assert relabel["ts"] == "root.1" and "missed" in relabel["text"]
    line = starter._client.posted[-1]
    assert line["thread_ts"] == "root.1" and "Missed" in line["text"]
    assert any("Missed" in m.content for m, _ in runtime.sent)
    assert guardian.reconciled == ["U1"]


@pytest.mark.asyncio
async def test_expire_leaves_a_session_the_user_is_working_in(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    starter, turns = _starter(
        monkeypatch, ledger=_Ledger(rows=[_row("C1:live", 3)]), haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert runtime.sent == [] and guardian.reconciled == []


@pytest.mark.asyncio
async def test_expire_closes_only_the_untouched_one_when_both_stand_for_the_day(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    rows = [_row("C1:live", 4, minute=30), _row("C1:root.1", 1)]
    starter, turns = _starter(
        monkeypatch, ledger=_Ledger(rows=rows), haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert haunting.cancelled == [{"topic_id": "C1:root.1"}]
    assert [turn["session_key"] for turn in turns] == ["C1:root.1"]
    assert runtime.sent == [], "no missed DM while the user is still planning"
    assert guardian.reconciled == []


@pytest.mark.asyncio
async def test_expire_with_nothing_open_still_dms_and_hands_over(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    starter, turns = _starter(
        monkeypatch, ledger=_Ledger(), haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert any("Missed" in m.content for m, _ in runtime.sent)
    assert guardian.reconciled == ["U1"]


@pytest.mark.asyncio
async def test_expire_after_a_commit_does_nothing(monkeypatch):
    haunting, guardian = _Haunting(), _Guardian()
    ledger = _Ledger(committed_key="C1:root.1", rows=[_row("C1:root.1", 1)])
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == [] and guardian.reconciled == []
    assert ledger.asked_for_day == []
