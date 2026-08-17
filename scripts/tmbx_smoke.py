#!/usr/bin/env python3
"""Manual smoke test for ``GoogleCalendarAdapter`` — not a test, not run in CI.

Reads one real day from one real calendar through a real Google Calendar
MCP server and prints the plan exactly as ``plan_read`` would render it.
This is the thing a human runs once, by hand, to confirm the streamable-
HTTP wiring actually works end to end before a live session — it never
writes anything.

Usage:
    poetry run python scripts/tmbx_smoke.py [--calendar-id primary] \\
        [--day 2026-08-17] [--tz Europe/Amsterdam]

Reads ``MCP_CALENDAR_SERVER_URL`` from the environment (defaults to
``http://localhost:3000``, same as ``tmbx.calendar.gcal``'s own default) —
the server has to already be running and authorised against a real Google
account; this script does none of that setup itself.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

from tmbx.calendar.gcal import (
    DEFAULT_SERVER_URL,
    SERVER_URL_ENV_VAR,
    GoogleCalendarAdapter,
)
from tmbx.core.models import Plan
from tmbx.journal.store import JournalStore, init_journal
from tmbx.service import PlanService, ReadResult


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calendar-id",
        default="primary",
        help='Calendar to read (default: "primary"). A JSON array string '
        'such as \'["work","personal"]\' reads multiple calendars at once.',
    )
    parser.add_argument(
        "--day",
        default=(date.today() + timedelta(days=1)).isoformat(),  # noqa: DTZ011
        help="YYYY-MM-DD (default: tomorrow).",
    )
    parser.add_argument(
        "--tz",
        default=Plan.model_fields["tz"].default,
        help=f'IANA tz (default: {Plan.model_fields["tz"].default!r}).',
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    server_url = os.environ.get(SERVER_URL_ENV_VAR, DEFAULT_SERVER_URL)
    day = date.fromisoformat(args.day)

    print(f"MCP server:  {server_url}")
    print(f"calendar_id: {args.calendar_id}")
    print(f"day:         {day.isoformat()}")
    print(f"tz:          {args.tz}")
    print("-" * 60)

    adapter = GoogleCalendarAdapter(tz=args.tz, server_url=server_url)

    # An in-memory, throwaway journal — read_rendered() never writes to it,
    # but PlanService requires one, and a real (if ephemeral) JournalStore
    # is more honest than a stub that would only ever raise if touched.
    store = JournalStore(await init_journal(":memory:"))
    service = PlanService(adapter, store)
    result: ReadResult = await service.read_rendered(args.calendar_id, day, args.tz)

    print(result.rendered)
    print("-" * 60)
    print(f"{result.blocks} block(s). snapshot token: {result.snapshot.token}")
    return 0


def main() -> None:
    args = _parse_args()
    try:
        exit_code = asyncio.run(_run(args))
    except Exception as exc:
        print(f"tmbx_smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
