"""Unit tests for Timebox.schedule_and_validate semantics."""

from __future__ import annotations

from datetime import time

import pytest

from fateforger.agents.timeboxing.timebox import Timebox


def test_timebox_computes_end_time_from_start_plus_duration() -> None:
    """Compute missing end_time when start_time and duration are present."""
    tb = Timebox.model_validate(
        {
            "date": "2026-01-21",
            "timezone": "Europe/Amsterdam",
            "events": [
                {
                    "summary": "Deep Work",
                    "event_type": "DW",
                    "start_time": "09:00",
                    "duration": "PT90M",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                }
            ],
        }
    )
    assert tb.events[0].end_time == time(10, 30)


def test_timebox_computes_duration_from_start_and_end() -> None:
    """Compute missing duration when start_time and end_time are present."""
    tb = Timebox.model_validate(
        {
            "date": "2026-01-21",
            "timezone": "Europe/Amsterdam",
            "events": [
                {
                    "summary": "Meeting",
                    "event_type": "M",
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                }
            ],
        }
    )
    assert tb.events[0].duration is not None
    assert tb.events[0].duration.total_seconds() == 30 * 60


def test_timebox_anchors_duration_only_after_previous_when_anchor_prev_true() -> None:
    """Anchor a duration-only event after the previous event's end time."""
    tb = Timebox.model_validate(
        {
            "date": "2026-01-21",
            "timezone": "Europe/Amsterdam",
            "events": [
                {
                    "summary": "Meeting",
                    "event_type": "M",
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                },
                {
                    "summary": "Deep Work",
                    "event_type": "DW",
                    "duration": "PT90M",
                    "anchor_prev": True,
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                },
            ],
        }
    )
    assert tb.events[1].start_time == time(10, 30)
    assert tb.events[1].end_time == time(12, 0)


def test_timebox_anchors_duration_only_before_next_when_anchor_prev_false() -> None:
    """Anchor a duration-only event before the next event's start time."""
    tb = Timebox.model_validate(
        {
            "date": "2026-01-21",
            "timezone": "Europe/Amsterdam",
            "events": [
                {
                    "summary": "Meeting 1",
                    "event_type": "M",
                    "start_time": "10:00",
                    "end_time": "10:30",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                },
                {
                    "summary": "Buffer",
                    "event_type": "BU",
                    "duration": "PT30M",
                    "anchor_prev": False,
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                },
                {
                    "summary": "Meeting 2",
                    "event_type": "M",
                    "start_time": "11:00",
                    "end_time": "11:30",
                    "calendarId": "primary",
                    "timeZone": "Europe/Amsterdam",
                },
            ],
        }
    )
    assert tb.events[1].start_time == time(10, 30)
    assert tb.events[1].end_time == time(11, 0)


def test_timebox_rejects_overlaps() -> None:
    """Reject overlapping event schedules."""
    with pytest.raises(ValueError, match="Overlap"):
        Timebox.model_validate(
            {
                "date": "2026-01-21",
                "timezone": "Europe/Amsterdam",
                "events": [
                    {
                        "summary": "A",
                        "event_type": "M",
                        "start_time": "10:00",
                        "end_time": "10:30",
                        "calendarId": "primary",
                        "timeZone": "Europe/Amsterdam",
                    },
                    {
                        "summary": "B",
                        "event_type": "M",
                        "start_time": "10:15",
                        "end_time": "10:45",
                        "calendarId": "primary",
                        "timeZone": "Europe/Amsterdam",
                    },
                ],
            }
        )

