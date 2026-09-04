#!/usr/bin/env python3
"""#210: do tmbx's private extended properties survive a Google Calendar UI edit?

Two phases, run by hand, against a far-future empty day on Hugo's own calendar:

  write   create one probe event carrying tmbx.uid / tmbx.slug / tmbx.type,
          print its id, then STOP. Hugo now edits it in the Google Calendar UI:
          drag to a new time, rename, resize, and "duplicate" it.
  check   re-read the day through the adapter and print, per event, which of
          the three private keys came back. Then delete every probe event.

Decision 4 of docs/superpowers/specs/2026-09-04-required-blocks-design.md rests
on the answer being "all three survive drag, rename and resize". Duplicate is
reported for information: a duplicate that copies the slug is a second block of
the same kind, which the watcher must count, not resolve.

Usage:
    PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py write \
        --calendar-id hugo.evers@gmail.com --day 2030-01-07
    ... edit in the UI ...
    PYTHONPATH=src uv run python scripts/spikes/private_props_survive_ui_edit.py check \
        --calendar-id hugo.evers@gmail.com --day 2030-01-07 [--keep]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date as date_type
from datetime import datetime

from tmbx.calendar.gcal import GoogleCalendarAdapter
from tmbx.calendar.port import CalendarEvent

PROBE_SUMMARY = "tmbx #210 probe (edit me, then run check)"
PROBE_UID = "spike210uid0000000000000000000001"
PROBE_SLUG = "planning"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["write", "check"])
    p.add_argument("--calendar-id", required=True)
    p.add_argument("--day", default="2030-01-07")
    p.add_argument("--tz", default="Europe/Amsterdam")
    p.add_argument("--keep", action="store_true", help="check: do not delete probes")
    return p.parse_args()


async def write(adapter: GoogleCalendarAdapter, calendar_id: str, day: date_type) -> None:
    created = await adapter.create(
        calendar_id,
        CalendarEvent(
            event_id="",
            summary=PROBE_SUMMARY,
            start=datetime(day.year, day.month, day.day, 10, 0),
            end=datetime(day.year, day.month, day.day, 10, 30),
            uid=PROBE_UID,
            slug=PROBE_SLUG,
            block_type="PR",
        ),
    )
    print(f"created id={created.event_id!r} on {day}; slug={PROBE_SLUG!r} uid={PROBE_UID!r}")
    print("Now, in the Google Calendar UI: drag it to 14:00, rename it, resize it to 45m,")
    print("and use 'Duplicate'. Then run the check phase.")


async def check(
    adapter: GoogleCalendarAdapter, calendar_id: str, day: date_type, tz: str, keep: bool
) -> None:
    events = await adapter.list_day(calendar_id, day, tz)
    probes = [e for e in events if e.uid == PROBE_UID or e.slug == PROBE_SLUG or e.summary]
    print(f"{len(events)} event(s) on {day}:")
    for e in events:
        print(
            f"  id={e.event_id!r} {e.start.time()}–{e.end.time()} summary={e.summary!r}\n"
            f"     tmbx.uid={e.uid!r} tmbx.slug={e.slug!r} tmbx.type={e.block_type!r}"
        )
    survived = [e for e in events if e.slug == PROBE_SLUG]
    print(f"\nevents still carrying slug={PROBE_SLUG!r}: {len(survived)}")
    print("Record in the spec: which edits kept uid/slug/type, and whether the duplicate did.")
    if not keep:
        for e in events:
            if e.uid == PROBE_UID or e.slug == PROBE_SLUG:
                await adapter.delete(calendar_id, e.event_id)
                print(f"deleted {e.event_id!r}")


async def main() -> None:
    a = _args()
    adapter = GoogleCalendarAdapter(tz=a.tz)
    day = date_type.fromisoformat(a.day)
    if a.phase == "write":
        await write(adapter, a.calendar_id, day)
    else:
        await check(adapter, a.calendar_id, day, a.tz, a.keep)


if __name__ == "__main__":
    asyncio.run(main())
