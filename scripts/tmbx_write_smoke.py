#!/usr/bin/env python3
"""Manual smoke test for the WRITE half of ``GoogleCalendarAdapter``.

Companion to ``tmbx_smoke.py``, which proves only the read half. This one
exercises ``create``, ``update`` and ``delete`` — implemented at
gcal.py:214–247 and, as of 2026-08-31, never executed once. Every commit this
system has ever made went to ``FakeCalendar``, an in-memory dict, because
``TMBX_CALENDAR_BACKEND`` defaults to ``"fake"``.

That default is right and should stay (a real calendar is one ``plan_commit``
away from a real write). But it means flipping to ``google`` without running
this makes the first real calendar write also the first test of the code doing
it — on a real day, with a real plan.

Not a test, not run in CI, and it writes. Deliberately:

* to a **far-future, empty day**, so a leftover from a failed run is obvious
  rather than tangled into a real schedule;
* **self-cleaning** — nothing survives a passing run;
* to one named calendar only, never a shared one.

Every step is read back through the adapter. Verify independently through a
different client too: a write that only this adapter can see is not a write.

Usage:
    poetry run python scripts/tmbx_write_smoke.py [--calendar-id ...] \
        [--day 2030-01-01] [--tz Europe/Amsterdam]

Reads ``MCP_CALENDAR_SERVER_URL`` from the environment (default
``http://localhost:3000``), same as ``tmbx.calendar.gcal``. The server has to be
running and authorised already; this script does none of that setup.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date as date_type
from datetime import datetime

from tmbx.calendar.gcal import GoogleCalendarAdapter
from tmbx.calendar.port import CalendarEvent

_PROBE_SUMMARY = "tmbx write probe (delete me)"
_PROBE_SUMMARY_UPDATED = "tmbx write probe (updated)"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-id", default="primary")
    parser.add_argument("--day", default="2030-01-01")
    parser.add_argument("--tz", default="Europe/Amsterdam")
    return parser.parse_args()


async def run(*, calendar_id: str, day: date_type, tz: str) -> bool:
    adapter = GoogleCalendarAdapter(tz=tz)
    ok = True

    baseline = await adapter.list_day(calendar_id, day, tz)
    print(f"1. baseline {day}: {len(baseline)} event(s)")
    if baseline:
        print("   NOTE: day is not empty; pick an empty one to keep this legible")

    created = await adapter.create(
        calendar_id,
        CalendarEvent(
            event_id="",
            summary=_PROBE_SUMMARY,
            start=datetime(day.year, day.month, day.day, 10, 0),
            end=datetime(day.year, day.month, day.day, 11, 0),
        ),
    )
    print(f"2. create -> id={created.event_id!r} {created.start}-{created.end}")
    if not created.event_id:
        print("   FAIL: create returned no event id, so nothing can be cleaned up")
        return False

    try:
        seen = await adapter.list_day(calendar_id, day, tz)
        present = [e for e in seen if e.event_id == created.event_id]
        print(f"3. read back: {len(seen)} event(s), probe present={bool(present)}")
        ok &= bool(present)

        updated = await adapter.update(
            calendar_id,
            created.model_copy(
                update={
                    "summary": _PROBE_SUMMARY_UPDATED,
                    "start": datetime(day.year, day.month, day.day, 14, 0),
                    "end": datetime(day.year, day.month, day.day, 15, 30),
                }
            ),
        )
        print(f"4. update -> {updated.start}-{updated.end} {updated.summary!r}")
        ok &= updated.summary == _PROBE_SUMMARY_UPDATED
        ok &= updated.start.hour == 14

        after = await adapter.list_day(calendar_id, day, tz)
        same = [e for e in after if e.event_id == created.event_id]
        # An update that creates a second event instead of moving the first is
        # the failure mode that would quietly duplicate a user's whole day.
        print(
            f"5. read back after update: {len(after)} event(s), "
            f"exactly one probe={len(same) == 1}"
        )
        ok &= len(same) == 1 and len(after) == len(baseline) + 1
    finally:
        await adapter.delete(calendar_id, created.event_id)

    final = await adapter.list_day(calendar_id, day, tz)
    gone = not [e for e in final if e.event_id == created.event_id]
    print(f"6. delete -> {len(final)} event(s), probe gone={gone}")
    ok &= gone and len(final) == len(baseline)

    print(f"\n{'ALL WRITE OPERATIONS PASSED' if ok else 'SOMETHING FAILED'}")
    print(f"event id, for independent verification: {created.event_id}")
    return ok


def main() -> int:
    args = _parse_args()
    day = date_type.fromisoformat(args.day)
    print(f"calendar_id: {args.calendar_id}\nday:         {day}\ntz:          {args.tz}")
    print("-" * 60)
    return 0 if asyncio.run(
        run(calendar_id=args.calendar_id, day=day, tz=args.tz)
    ) else 1


if __name__ == "__main__":
    sys.exit(main())
