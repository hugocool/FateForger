from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import TimeboxingStanding
from fateforger.agents.timeboxing.session_contracts import CancelSession, ConfirmPlanningDay
from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import LADDER_OFFSETS, SESSION_EXPIRE_KIND, SESSION_START_KIND
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.session_start import SessionStarter

AMS = "Europe/Amsterdam"
START = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))


def _reminder(kind: str = SESSION_START_KIND) -> PlanningReminder:
    return PlanningReminder(
        scope="U1", kind=kind, attempt=1, message="", user_id="U1", channel_id="D1",
        event_start=START.isoformat(), event_end=(START + timedelta(minutes=30)).isoformat(), event_tz=AMS,
    )


class _Ledger:
    def __init__(self, open_key=None, committed_key=None):
        self.standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)

    async def standing_for(self, **_):
        return self.standing

    async def load(self, key):
        return SimpleNamespace(revision=3, status="open")


class _Haunting:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    async def schedule_followup(self, **kwargs):
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


def _starter(monkeypatch, *, ledger, haunting=None, runtime=None, guardian=None, turns=None):
    turns = [] if turns is None else turns

    async def _deliver(**kwargs):
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
    dm, recipient = runtime.sent[0]
    assert "https://slack/p" in dm.content and dm.user_id == "U1"
    armed = haunting.scheduled[0]
    assert armed["topic_id"] == "C1:root.1"
    assert armed["message_id"] == "planning_session:C1:root.1"
    assert armed["spec"].offsets == LADDER_OFFSETS and armed["spec"].cancel_on_user_reply is True
    assert len(armed["spec"].lines) == len(LADDER_OFFSETS) and "https://slack/p" in armed["spec"].lines[0]


@pytest.mark.asyncio
async def test_an_evening_event_plans_the_next_day(monkeypatch):
    starter, turns = _starter(monkeypatch, ledger=_Ledger())
    evening = START.replace(hour=18)
    reminder = PlanningReminder(scope="U1", kind=SESSION_START_KIND, attempt=1, message="", user_id="U1", channel_id="D1",
                                event_start=evening.isoformat(), event_end=(evening + timedelta(minutes=30)).isoformat(), event_tz=AMS)

    await starter.start(reminder)

    assert turns[0]["action"].intent.planning_day.date.isoformat() == "2026-09-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [_Ledger(open_key="C1:old"), _Ledger(committed_key="C1:done")])
async def test_an_open_or_committed_session_blocks_the_start(monkeypatch, standing):
    haunting = _Haunting()
    starter, turns = _starter(monkeypatch, ledger=standing, haunting=haunting)

    await starter.start(_reminder())

    assert turns == [] and haunting.scheduled == []


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
async def test_expire_without_a_commit_cancels_marks_missed_and_hands_over(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(open_key="C1:root.1")
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert haunting.cancelled == [{"topic_id": "C1:root.1"}]
    assert isinstance(turns[0]["action"].intent, CancelSession) and turns[0]["action"].expected_revision == 3
    assert any("Missed" in m.content for m, _ in runtime.sent)
    assert guardian.reconciled == ["U1"]


@pytest.mark.asyncio
async def test_expire_after_a_commit_does_nothing(monkeypatch):
    haunting, guardian = _Haunting(), _Guardian()
    starter, turns = _starter(monkeypatch, ledger=_Ledger(committed_key="C1:root.1"), haunting=haunting, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == [] and guardian.reconciled == []
