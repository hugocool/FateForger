from __future__ import annotations

from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent, drift, make_snapshot

DAY = date(2026, 8, 17)
TZ = "Europe/Amsterdam"


def _event(eid, etag="v1", uid=None, handle=None):
    return CalendarEvent(
        event_id=eid,
        summary="Block",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
        etag=etag,
        uid=uid,
        handle=handle,
    )


@pytest.fixture
def calendar():
    return FakeCalendar({"primary": [_event("e1"), _event("e2")]})


async def test_list_day_returns_events(calendar):
    assert len(await calendar.list_day("primary", DAY, TZ)) == 2


async def test_snapshot_captures_etags(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    assert snapshot.etags == {"e1": "v1", "e2": "v1"}
    assert snapshot.token


async def test_snapshot_carries_tz(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    assert snapshot.tz == TZ


async def test_no_drift_when_nothing_changed(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    assert drift(snapshot, await calendar.list_day("primary", DAY, TZ)) == []


async def test_drift_detects_a_concurrent_edit(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    calendar.mutate("primary", "e1")
    assert drift(snapshot, await calendar.list_day("primary", DAY, TZ)) == ["e1"]


async def test_drift_detects_a_vanished_event(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    await calendar.delete("primary", "e2")
    assert drift(snapshot, await calendar.list_day("primary", DAY, TZ)) == ["e2"]


async def test_drift_detects_a_new_event(calendar):
    snapshot = make_snapshot(
        "primary", DAY, TZ, await calendar.list_day("primary", DAY, TZ)
    )
    await calendar.create("primary", _event("e3"))
    assert drift(snapshot, await calendar.list_day("primary", DAY, TZ)) == ["e3"]


async def test_uid_and_handle_roundtrip(calendar):
    await calendar.create("primary", _event("e9", uid="u9", handle="DW1"))
    stored = next(
        e for e in await calendar.list_day("primary", DAY, TZ) if e.event_id == "e9"
    )
    assert (stored.uid, stored.handle) == ("u9", "DW1")


async def test_mutate_requires_calendar_id_and_only_touches_that_calendar():
    """Two calendars can share an event_id string. mutate() must not bump
    the wrong calendar's event just because the id matches somewhere."""
    two_cals = FakeCalendar(
        {
            "primary": [_event("shared", etag="v1")],
            "work": [_event("shared", etag="v1")],
        }
    )
    two_cals.mutate("work", "shared")
    primary_event = (await two_cals.list_day("primary", DAY, TZ))[0]
    work_event = (await two_cals.list_day("work", DAY, TZ))[0]
    assert primary_event.etag == "v1"
    assert work_event.etag != "v1"


async def test_mutate_unknown_calendar_raises(calendar):
    with pytest.raises(KeyError):
        calendar.mutate("nonexistent", "e1")


async def test_mutate_unknown_event_in_known_calendar_raises(calendar):
    with pytest.raises(KeyError):
        calendar.mutate("primary", "nonexistent")


async def test_create_rejects_a_duplicate_event_id(calendar):
    """A real provider returns 409 for a duplicate id. The fake must refuse
    too, so a test cannot pass here and fail against Google."""
    with pytest.raises(ValueError):
        await calendar.create("primary", _event("e1"))


async def test_make_snapshot_rejects_a_duplicate_uid():
    """uid uniqueness is an invariant maintained elsewhere in the domain;
    silently overwriting event_ids on a collision would corrupt the
    create-vs-update decision Task 14 makes from it."""
    events = [_event("e1", uid="dup"), _event("e2", uid="dup")]
    with pytest.raises(ValueError):
        make_snapshot("primary", DAY, TZ, events)
