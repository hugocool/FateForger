from __future__ import annotations

from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent, drift, make_snapshot

DAY = date(2026, 8, 17)


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
    assert len(await calendar.list_day("primary", DAY)) == 2


async def test_snapshot_captures_etags(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    assert snapshot.etags == {"e1": "v1", "e2": "v1"}
    assert snapshot.token


async def test_no_drift_when_nothing_changed(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == []


async def test_drift_detects_a_concurrent_edit(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    calendar.mutate("e1")
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e1"]


async def test_drift_detects_a_vanished_event(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    await calendar.delete("primary", "e2")
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e2"]


async def test_drift_detects_a_new_event(calendar):
    snapshot = make_snapshot("primary", DAY, await calendar.list_day("primary", DAY))
    await calendar.create("primary", _event("e3"))
    assert drift(snapshot, await calendar.list_day("primary", DAY)) == ["e3"]


async def test_uid_and_handle_roundtrip(calendar):
    await calendar.create("primary", _event("e9", uid="u9", handle="DW1"))
    stored = next(e for e in await calendar.list_day("primary", DAY) if e.event_id == "e9")
    assert (stored.uid, stored.handle) == ("u9", "DW1")
