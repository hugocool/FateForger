"""The day a session proposes is never one already on the calendar (#346).

The scheduled opener has always asked `standing_for` before opening; the
`/timebox` and typed-text door derived its day from the host clock alone. On
2026-09-05 Hugo committed Saturday at 03:40 and a `/timebox` card at 18:47
proposed Saturday again, which he flipped to Sunday by hand.

Whether a day already carries a committed session is a query over rows this
system minted, so it stays here as arithmetic with a test rather than becoming
a judgement. Which conversation a message belongs to is the judgement, and it
is not this function's question.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    TimeboxingStanding,
    TurnRequest,
)
from fateforger.agents.timeboxing.session_contracts import StartSession
from fateforger.slack_bot import timeboxing_host as host_module
from fateforger.slack_bot.timeboxing_host import HostPlanningContext

TZ = "Europe/Amsterdam"

#: The live case. Saturday 5 September 2026 was committed at 03:40.
SATURDAY = date(2026, 9, 5)
SUNDAY = date(2026, 9, 6)
MONDAY = date(2026, 9, 7)


class _Ledger:
    """Answers `standing_for` from a set of committed days, and records the asks."""

    def __init__(self, committed: set[date], *, fails: bool = False) -> None:
        self._committed = committed
        self._fails = fails
        self.asked: list[date] = []

    async def standing_for(
        self,
        *,
        owner_user_id: str,
        open_since: datetime,
        planned_from: date,
        planned_to: date,
    ) -> TimeboxingStanding:
        if self._fails:
            raise RuntimeError("session store unavailable")
        self.asked.append(planned_from)
        hit = next(
            (d for d in sorted(self._committed) if planned_from <= d <= planned_to),
            None,
        )
        return TimeboxingStanding(
            committed_session_key=f"C0AA6HC1RJL:{hit}" if hit else None
        )


class _Runtime:
    def __init__(self, ledger: object | None) -> None:
        if ledger is not None:
            self.timeboxing_session_store = ledger


def _now() -> datetime:
    """18:47 CEST on Saturday 5 September 2026 -- the `/timebox` press."""

    return datetime(2026, 9, 5, 16, 47, tzinfo=timezone.utc)  # noqa: UP017


def _request() -> TurnRequest:
    return TurnRequest(
        session_key="C0AA6HC1RJL:1788626829.487149",
        interaction_id="i1",
        actor_user_id="U095637NL8P",
        intent=StartSession(),
    )


@pytest.fixture(autouse=True)
def _pin_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_module, "planning_timezone", lambda: TZ)


@pytest.mark.asyncio
async def test_today_is_proposed_when_no_day_is_committed() -> None:
    ledger = _Ledger(set())
    host = HostPlanningContext(_Runtime(ledger), now=_now)

    day = await host.propose_planning_day(_request())

    assert day.date == SATURDAY
    # One query in the common case: the scan stops at the first free day.
    assert ledger.asked == [SATURDAY]


@pytest.mark.asyncio
async def test_the_proposal_skips_a_day_that_already_has_a_committed_session() -> None:
    ledger = _Ledger({SATURDAY})
    host = HostPlanningContext(_Runtime(ledger), now=_now)

    day = await host.propose_planning_day(_request())

    assert day.date == SUNDAY


@pytest.mark.asyncio
async def test_the_proposal_walks_past_every_committed_day_in_a_run() -> None:
    ledger = _Ledger({SATURDAY, SUNDAY})
    host = HostPlanningContext(_Runtime(ledger), now=_now)

    day = await host.propose_planning_day(_request())

    assert day.date == MONDAY
    # The classification follows the day proposed, not the day it started from:
    # Saturday is a weekend, Monday is not.
    assert day.iso_weekday == 1
    assert day.day_type.value == "working"


@pytest.mark.asyncio
async def test_the_day_is_asked_for_that_day_alone() -> None:
    ledger = _Ledger({SATURDAY})
    host = HostPlanningContext(_Runtime(ledger), now=_now)

    await host.propose_planning_day(_request())

    # A range wider than one day would report Saturday's commit against Sunday
    # and walk forever.
    assert ledger.asked == [SATURDAY, SUNDAY]


@pytest.mark.asyncio
async def test_a_ledger_that_cannot_be_read_still_proposes_today() -> None:
    """The proposal is an offer the user can flip, so a failed read is not fatal."""

    ledger = _Ledger(set(), fails=True)
    host = HostPlanningContext(_Runtime(ledger), now=_now)

    day = await host.propose_planning_day(_request())

    assert day.date == SATURDAY


@pytest.mark.asyncio
async def test_a_runtime_with_no_session_store_still_proposes_today() -> None:
    host = HostPlanningContext(_Runtime(None), now=_now)

    day = await host.propose_planning_day(_request())

    assert day.date == SATURDAY
