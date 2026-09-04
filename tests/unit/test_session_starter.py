from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from autogen_core import AgentId

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    OpenSessionRow,
    TimeboxingStanding,
)
from fateforger.agents.timeboxing.session_contracts import (
    CancelSession,
    ConfirmPlanningDay,
    HandledInteraction,
    PlanningSessionSnapshot,
)
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


#: The starter's clock, as the store writes `updated_at`: naive UTC.
NOW_NAIVE = START.astimezone(timezone.utc).replace(tzinfo=None)


def _row(session_key: str, revision: int, *, stale_minutes: int = 60) -> OpenSessionRow:
    return OpenSessionRow(
        session_key=session_key,
        revision=revision,
        updated_at=NOW_NAIVE - timedelta(minutes=stale_minutes),
    )


def _snapshot(session_key: str, *, auto_opened: bool) -> PlanningSessionSnapshot:
    """The snapshot behind a row; only `start` writes the auto-open id."""

    snapshot = PlanningSessionSnapshot.new(session_key=session_key, owner_user_id="U1")
    if not auto_opened:
        return snapshot
    return snapshot.model_copy(
        update={
            "handled_interactions": [
                HandledInteraction(
                    interaction_id=f"session_start:{session_key}",
                    outcome_kind="applied",
                    session_revision=1,
                )
            ]
        }
    )


class _Ledger:
    def __init__(
        self,
        open_key=None,
        committed_key=None,
        *,
        rows=(),
        events=None,
        standing_error=None,
        hand_opened=(),
        unreadable=(),
    ):
        self.standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)
        self.rows = list(rows)
        self.standing_error = standing_error
        self.asked_for_day = []
        self._events = events
        # Session keys whose snapshot carries no auto-open interaction id: the
        # user opened these by hand and expiry may never close them.
        self._hand_opened = set(hand_opened)
        # Session keys whose snapshot cannot be loaded at all -- a transient
        # store error, distinct from "loaded and not ours".
        self._unreadable = set(unreadable)

    async def standing_for(self, **_):
        if self.standing_error is not None:
            raise self.standing_error
        return self.standing

    async def open_sessions_for_day(self, *, owner_user_id, planning_date):
        self.asked_for_day.append((owner_user_id, planning_date))
        return list(self.rows)

    async def load(self, key):
        if key in self._unreadable:
            raise RuntimeError("store unavailable")
        return _snapshot(key, auto_opened=key not in self._hand_opened)


class _Haunting:
    def __init__(self, events=None, *, declined=False):
        self.scheduled = []
        self.cancelled = []
        self._events = events
        # The service returns None when the user's admonishment settings say
        # not to nudge -- indistinguishable from an armed ladder at the call
        # site unless it is read.
        self._declined = declined

    async def schedule_followup(self, **kwargs):
        if self._events is not None:
            self._events.append("schedule_followup")
        self.scheduled.append(kwargs)
        if self._declined:
            return None
        return object()

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
    def __init__(self, *, permalink="https://slack/p"):
        self.posted, self.updates = [], []
        self._permalink = permalink

    async def chat_postMessage(self, **p):
        self.posted.append(p)
        return {"channel": p["channel"], "ts": "root.1"}

    async def chat_update(self, **p):
        self.updates.append(p)
        return {"ok": True}

    async def chat_getPermalink(self, **_):
        return {"permalink": self._permalink}

    async def conversations_invite(self, **_):
        return {"ok": True}


def _starter(monkeypatch, *, ledger, haunting=None, runtime=None, guardian=None, turns=None, events=None, client=None):
    turns = [] if turns is None else turns

    async def _deliver(**kwargs):
        if events is not None:
            events.append("turn")
        turns.append(kwargs)

    monkeypatch.setattr("fateforger.slack_bot.session_start._deliver_timebox_turn", _deliver)
    return SessionStarter(
        runtime=runtime or _Runtime(), client=client or _Client(),
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
    rows = [_row("C1:live", 4, stale_minutes=30), _row("C1:root.1", 1)]
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


@pytest.mark.asyncio
async def test_expire_never_closes_a_session_the_user_opened_by_hand(monkeypatch):
    # Revision 1 is only evidence of "untouched" for a session this starter
    # opened: a hand-opened one sits at 1 until its first turn lands, and
    # closing it would shut the user out of the session they just opened.
    haunting, guardian = _Haunting(), _Guardian()
    ledger = _Ledger(rows=[_row("C1:hand", 1)], hand_opened=("C1:hand",))
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []


@pytest.mark.asyncio
async def test_expire_ignores_a_stale_manual_session_and_hands_back(monkeypatch):
    # A non-auto row's revision is no longer evidence of "live" -- only
    # recency is. A manual session saved hours ago is neither closed (not
    # ours) nor counted as live, so expire falls through to the missed DM
    # and hands back to the reconciler.
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(rows=[_row("C1:manual", 10, stale_minutes=240)], hand_opened=("C1:manual",))
    starter, turns = _starter(
        monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert any("Missed" in m.content for m, _ in runtime.sent)
    assert guardian.reconciled == ["U1"]


@pytest.mark.asyncio
async def test_a_hand_opened_session_touched_minutes_ago_counts_as_live(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(rows=[_row("C1:hand", 1, stale_minutes=2)], hand_opened=("C1:hand",))
    starter, turns = _starter(
        monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert runtime.sent == [] and guardian.reconciled == []


@pytest.mark.asyncio
async def test_an_auto_opened_session_past_the_untouched_revision_is_live(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(rows=[_row("C1:root.1", 2)])
    starter, turns = _starter(
        monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert runtime.sent == [] and guardian.reconciled == []


@pytest.mark.asyncio
async def test_expire_says_nothing_while_another_session_still_stands(monkeypatch):
    # A session with no planning_date yet is invisible to open_sessions_for_day
    # but stands in `standing`. DMing "Missed" at a user who is mid-session,
    # with no day locked, is the one message that must not go out.
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(open_key="C1:hand")
    starter, turns = _starter(
        monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian
    )

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == []
    assert runtime.sent == [] and guardian.reconciled == []


@pytest.mark.asyncio
async def test_our_own_open_session_blocks_the_start_at_any_age(monkeypatch):
    # `standing`'s recency window is an hour wide; a planning event longer than
    # that, or a restart past it, leaves the session it already opened invisible
    # there -- and the day gets a second one. This is what stops a double open
    # after a restart during a long event, so it must hold no matter how stale
    # the row looks.
    haunting = _Haunting()
    ledger = _Ledger(rows=[_row("C1:already", 1, stale_minutes=180)])
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting)

    await starter.start(_reminder())

    assert turns == [] and haunting.scheduled == []
    assert starter._client.posted == []


@pytest.mark.asyncio
async def test_a_stale_open_session_does_not_block_the_start(monkeypatch):
    # "open" is not "live": a session nobody but the user touched, hours ago,
    # is not ours to gate a fresh start on.
    haunting = _Haunting()
    ledger = _Ledger(rows=[_row("C1:manual", 2, stale_minutes=180)], hand_opened=("C1:manual",))
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting)

    await starter.start(_reminder())

    assert len(turns) == 1
    assert haunting.scheduled, "the ladder still arms for a fresh start"


@pytest.mark.asyncio
async def test_an_unreadable_row_blocks_the_start(monkeypatch):
    # Unreadable is not the same finding as "not ours": the guard cannot
    # prove the row isn't its own, and letting a fresh start through on that
    # uncertainty is how a restart double-opens a session. It fails closed,
    # the opposite of what `_sweep` does on the same unreadable row.
    errors = []
    monkeypatch.setattr(
        "fateforger.slack_bot.session_start.record_error",
        lambda **kwargs: errors.append(kwargs),
    )
    haunting = _Haunting()
    ledger = _Ledger(rows=[_row("C1:mystery", 1)], unreadable=("C1:mystery",))
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting)

    await starter.start(_reminder())

    assert turns == [] and haunting.scheduled == []
    assert starter._client.posted == [], "no surface may be opened on an unreadable row"
    assert {"component": "session_start", "error_type": "guard_failure"} in errors


@pytest.mark.asyncio
async def test_expire_closes_a_dm_keyed_session_without_a_thread_line(monkeypatch):
    haunting, runtime = _Haunting(), _Runtime()
    ledger = _Ledger(rows=[_row("D1:dm", 1)])
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert haunting.cancelled == [{"topic_id": "D1:dm"}]
    assert turns[0]["thread_ts"] == "dm" and turns[0]["channel_id"] == "D1"
    assert starter._client.updates == [], "there is no thread root to relabel in a DM"
    assert starter._client.posted == [], "and no thread to post the missed line into"
    assert any("Missed" in m.content for m, _ in runtime.sent)


def test_the_agent_type_matches_the_runtime_it_mirrors():
    # Repeated rather than imported to dodge an import cycle; a copy that
    # drifts routes the DM at an agent nobody registered.
    from fateforger.core import runtime as core_runtime

    from fateforger.slack_bot import session_start as slack_session_start

    assert slack_session_start.USER_CHANNEL_AGENT_TYPE == core_runtime.USER_CHANNEL_AGENT_TYPE


@pytest.mark.asyncio
async def test_the_missed_line_names_the_day_that_was_missed(monkeypatch):
    # An evening event plans tomorrow: "today's planning session" names the
    # wrong day for every session that does.
    runtime = _Runtime()
    ledger = _Ledger(rows=[_row("C1:root.1", 1)])
    starter, _ = _starter(monkeypatch, ledger=ledger, runtime=runtime)
    evening = START.replace(hour=18)
    reminder = PlanningReminder(
        scope="U1", kind=SESSION_EXPIRE_KIND, attempt=1, message="", user_id="U1", channel_id="D1",
        event_start=evening.isoformat(), event_end=(evening + timedelta(minutes=30)).isoformat(), event_tz=AMS,
    )

    await starter.expire(reminder)

    assert any("Sat 5 Sep" in m.content for m, _ in runtime.sent)
    assert "Sat 5 Sep" in starter._client.posted[-1]["text"]
    assert "Sat 5 Sep" in starter._client.updates[-1]["text"]


@pytest.mark.asyncio
async def test_a_ladder_the_settings_declined_says_so(monkeypatch, caplog):
    haunting = _Haunting(declined=True)
    starter, _ = _starter(monkeypatch, ledger=_Ledger(), haunting=haunting)

    with caplog.at_level(logging.INFO, logger="fateforger.slack_bot.session_start"):
        await starter.start(_reminder())

    assert "declined" in caplog.text and "U1" in caplog.text


@pytest.mark.asyncio
async def test_a_missing_permalink_is_loud_and_still_arms(monkeypatch):
    errors = []
    monkeypatch.setattr(
        "fateforger.slack_bot.session_start.record_error",
        lambda **kwargs: errors.append(kwargs),
    )
    haunting = _Haunting()
    starter, _ = _starter(
        monkeypatch, ledger=_Ledger(), haunting=haunting, client=_Client(permalink="")
    )

    await starter.start(_reminder())

    assert {"component": "session_start", "error_type": "permalink_failure"} in errors
    assert haunting.scheduled, "a linkless ladder still beats no ladder"
