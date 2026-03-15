#!/usr/bin/env python3
"""Create March 13 timebox events in Google Calendar via MCP."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

TZ = "Europe/Amsterdam"
DATE = "2026-03-13"

EVENTS = [
    {
        "summary": "Morning ritual",
        "start": {"dateTime": f"{DATE}T07:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T08:30:00+01:00", "timeZone": TZ},
        "description": "Wake, shower, breakfast. Hard stop at 08:30.",
        "colorId": "2",
    },
    {
        "summary": "Commute",
        "start": {"dateTime": f"{DATE}T08:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T09:00:00+01:00", "timeZone": TZ},
        "description": "30-min commute to office.",
        "colorId": "8",
    },
    {
        "summary": "DW1: Demo Validation Prep",
        "start": {"dateTime": f"{DATE}T09:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T10:45:00+01:00", "timeZone": TZ},
        "description": (
            "Deep Work – 105 min\n"
            "Task: Validate standalone demo before 11:00 Lab42 meeting.\n"
            "Entry state: repo open, demo script ready.\n"
            "Exit criteria: demo runs end-to-end without errors (screenshot/recording saved).\n"
            "Hard stop: 10:45 – travel to Lab42."
        ),
        "colorId": "9",
    },
    {
        "summary": "Travel buffer -> Lab42 Cafe",
        "start": {"dateTime": f"{DATE}T10:45:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T11:00:00+01:00", "timeZone": TZ},
        "description": "Walk to Lab42 Cafe, 15-min buffer.",
        "colorId": "8",
    },
    {
        "summary": "Meeting – Lab42 Cafe",
        "start": {"dateTime": f"{DATE}T11:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T11:30:00+01:00", "timeZone": TZ},
        "location": "Lab42 Cafe",
        "description": "30-min stakeholder meeting. Compliance overview + demo.",
        "colorId": "11",
    },
    {
        "summary": "SW1: Compliance Overview Email",
        "start": {"dateTime": f"{DATE}T11:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T12:30:00+01:00", "timeZone": TZ},
        "description": (
            "Shallow Work – 60 min\n"
            "Task: Write and send compliance overview email to stakeholders.\n"
            "Exit criteria: email sent, CC list complete.\n"
            "Hard stop: 12:30."
        ),
        "colorId": "5",
    },
    {
        "summary": "Lunch",
        "start": {"dateTime": f"{DATE}T12:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T13:00:00+01:00", "timeZone": TZ},
        "description": "Break. Step away from screen.",
        "colorId": "2",
    },
    {
        "summary": "DW2: Automated Facet Extraction",
        "start": {"dateTime": f"{DATE}T13:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T15:00:00+01:00", "timeZone": TZ},
        "description": (
            "Deep Work – 120 min\n"
            "Task: Implement automated facet extraction pipeline.\n"
            "Entry state: feature branch open, dataset ready.\n"
            "Exit criteria: extraction script committed + sample output JSON saved.\n"
            "Hard stop: 15:00."
        ),
        "colorId": "9",
    },
    {
        "summary": "SW2: C2F Integration – Define Artifacts",
        "start": {"dateTime": f"{DATE}T15:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T16:00:00+01:00", "timeZone": TZ},
        "description": (
            "Shallow Work – 60 min\n"
            "Task: Define deployment plan for C2F indexer on Scaleway. Outline EHR API feed architecture.\n"
            "Exit criteria: deployment checklist doc saved (named artifact).\n"
            "Hard stop: 16:00 (oats)."
        ),
        "colorId": "5",
    },
    {
        "summary": "Oats",
        "start": {"dateTime": f"{DATE}T16:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T16:15:00+01:00", "timeZone": TZ},
        "description": "Pre-gym oats. Leave desk.",
        "colorId": "2",
    },
    {
        "summary": "DW3: C2F Platform Integration Kickoff",
        "start": {"dateTime": f"{DATE}T16:15:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T17:00:00+01:00", "timeZone": TZ},
        "description": (
            "Deep Work – 45 min (hard-stopped by daily planning at 17:00)\n"
            "Task: C2F indexer deployment – docker-compose.yml + Scaleway config.\n"
            "Entry state: deployment checklist from SW2 open.\n"
            "Exit criteria: docker-compose.yml updated with indexer service OR blocker note saved.\n"
            "Hard stop: 17:00."
        ),
        "colorId": "9",
    },
    {
        "summary": "End-of-day closure",
        "start": {"dateTime": f"{DATE}T17:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T18:00:00+01:00", "timeZone": TZ},
        "description": (
            "Closure – 30 min\n"
            "Update artifact links and board status.\n"
            "Flag any block with no artifact -> schedule smallest artifact first tomorrow.\n"
            "Hard stop: 18:00 – gym."
        ),
        "colorId": "2",
    },
    {
        "summary": "Gym",
        "start": {"dateTime": f"{DATE}T18:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T19:00:00+01:00", "timeZone": TZ},
        "description": "Gym session.",
        "colorId": "4",
    },
    {
        "summary": "Commute home",
        "start": {"dateTime": f"{DATE}T19:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T19:30:00+01:00", "timeZone": TZ},
        "description": "30-min commute home.",
        "colorId": "8",
    },
    {
        "summary": "Evening – Chill & Music",
        "start": {"dateTime": f"{DATE}T20:00:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T22:30:00+01:00", "timeZone": TZ},
        "description": "No work. Chill + make music.",
        "colorId": "6",
    },
    {
        "summary": "Wind down",
        "start": {"dateTime": f"{DATE}T22:30:00+01:00", "timeZone": TZ},
        "end": {"dateTime": f"{DATE}T23:30:00+01:00", "timeZone": TZ},
        "description": "Wind down. Hard stop: lights out 23:30.",
        "colorId": "2",
    },
]


async def main() -> None:
    """Create all timebox events."""
    params = StreamableHttpServerParams(url="http://localhost:3000/mcp", timeout=15.0)
    wb = McpWorkbench(params)
    created = []
    failed = []
    for ev in EVENTS:
        try:
            await wb.call_tool("create-event", arguments={"calendarId": "primary", **ev})
            created.append(ev["summary"])
            start = ev["start"]["dateTime"][11:16]
            end = ev["end"]["dateTime"][11:16]
            print(f"OK  {start}-{end}  {ev['summary']}")
        except Exception as exc:
            failed.append(ev["summary"])
            print(f"FAIL  {ev['summary']}: {exc}")
    print(f"\nCreated: {len(created)}, Failed: {len(failed)}")
    if failed:
        print("Failed:", failed)


if __name__ == "__main__":
    asyncio.run(main())
