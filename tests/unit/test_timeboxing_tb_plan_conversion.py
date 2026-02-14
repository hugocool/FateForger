"""Regression tests for TBPlan conversion and CalendarEvent typing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan
from fateforger.agents.timeboxing.timebox import Timebox, tb_plan_to_timebox, timebox_to_tb_plan


def test_calendar_event_color_id_accepts_string_event_type() -> None:
    """CalendarEvent should coerce string event_type values into EventType enums."""
    event = CalendarEvent(
        summary="Focus",
        event_type=EventType.DEEP_WORK.value,
        start_time=time(9, 0),
        end_time=time(11, 0),
    )

    assert event.event_type == EventType.DEEP_WORK
    assert event.colorId == EventType.DEEP_WORK.color_id


def test_tb_plan_to_timebox_converts_fixed_windows_without_validation_error() -> None:
    """TBPlan fixed-window events should convert to Timebox cleanly."""
    plan = TBPlan(
        date=date(2026, 2, 14),
        tz="Europe/Amsterdam",
        events=[
            TBEvent(
                n="Deep Work",
                t=ET.DW,
                p=FixedWindow(st=time(10, 0), et=time(12, 0)),
            )
        ],
    )

    timebox = tb_plan_to_timebox(plan)
    assert len(timebox.events) == 1
    assert timebox.events[0].summary == "Deep Work"
    assert timebox.events[0].start_time == time(10, 0)
    assert timebox.events[0].end_time == time(12, 0)


def test_timebox_to_tb_plan_recovers_missing_event_summary() -> None:
    """Conversion should not crash when legacy/malformed events omit summary."""
    malformed_event = CalendarEvent.model_construct(
        summary=None,
        event_type=EventType.MEETING,
        start_time=time(9, 0),
        end_time=time(10, 0),
        eventId="evt_123",
    )
    timebox = Timebox.model_construct(
        events=[malformed_event],
        date=date(2026, 2, 14),
        timezone="Europe/Amsterdam",
    )

    plan = timebox_to_tb_plan(timebox)

    assert len(plan.events) == 1
    assert plan.events[0].n == "evt_123"


def test_timebox_to_tb_plan_uses_datetime_anchors_as_fixed_window() -> None:
    """Conversion should map datetime start/end anchors into FixedWindow timing."""
    anchored_event = CalendarEvent.model_construct(
        summary="Calendar Busy",
        event_type=EventType.MEETING,
        start=datetime(2026, 2, 14, 9, 0),
        end=datetime(2026, 2, 14, 10, 0),
        start_time=None,
        end_time=None,
        duration=None,
    )
    timebox = Timebox.model_construct(
        events=[anchored_event],
        date=date(2026, 2, 14),
        timezone="Europe/Amsterdam",
    )

    plan = timebox_to_tb_plan(timebox)
    assert len(plan.events) == 1
    assert isinstance(plan.events[0].p, FixedWindow)
    assert plan.events[0].p.st == time(9, 0)
    assert plan.events[0].p.et == time(10, 0)


def test_timebox_to_tb_plan_validate_false_keeps_unanchored_seed() -> None:
    """`validate=False` should preserve an editable seed for Stage 4 repair."""
    unanchored = CalendarEvent.model_construct(
        summary="Unanchored",
        event_type=EventType.MEETING,
        start_time=None,
        end_time=None,
        duration=timedelta(minutes=45),
    )
    timebox = Timebox.model_construct(
        events=[unanchored],
        date=date(2026, 2, 14),
        timezone="Europe/Amsterdam",
    )

    with pytest.raises(ValueError, match="fixed_start or fixed_window anchor"):
        timebox_to_tb_plan(timebox)

    seed = timebox_to_tb_plan(timebox, validate=False)
    assert len(seed.events) == 1
    assert seed.events[0].p.a == "ap"
