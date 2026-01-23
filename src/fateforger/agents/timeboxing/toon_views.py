"""TOON prompt views for timeboxing.

This module defines *minimal* column sets for injecting structured lists into LLM prompts.
The goal is to avoid dumping full Pydantic/SQLModel JSON into prompts while still preserving
the information the stage agent needs.
"""

from __future__ import annotations

from typing import Any

from fateforger.agents.schedular.models.calendar import CalendarEvent
from fateforger.agents.timeboxing.contracts import Immovable, TaskCandidate
from fateforger.agents.timeboxing.preferences import Constraint


def immovables_rows(items: list[Immovable]) -> list[dict[str, Any]]:
    """Return minimal TOON rows for immovables."""
    return [{"title": i.title, "start": i.start, "end": i.end} for i in items]


def tasks_rows(items: list[TaskCandidate]) -> list[dict[str, Any]]:
    """Return minimal TOON rows for task candidates."""
    return [
        {
            "title": t.title,
            "block_count": t.block_count,
            "duration_min": t.duration_min,
            "due": t.due,
            "importance": t.importance,
        }
        for t in items
    ]


def constraints_rows(items: list[Constraint]) -> list[dict[str, Any]]:
    """Return minimal TOON rows for constraints.

    We intentionally avoid DB-only fields and large nested dicts (selector/hints).
    """
    return [
        {
            "name": c.name,
            "necessity": getattr(c.necessity, "value", c.necessity),
            "scope": getattr(c.scope, "value", c.scope),
            "status": getattr(c.status, "value", c.status),
            "source": getattr(c.source, "value", c.source),
            "description": c.description,
        }
        for c in items
    ]


def timebox_events_rows(items: list[CalendarEvent]) -> list[dict[str, Any]]:
    """Return compact TOON rows for timebox events (for summary/review stages)."""
    rows: list[dict[str, Any]] = []
    for ev in items:
        rows.append(
            {
                "type": getattr(ev.event_type, "value", None),
                "summary": ev.summary,
                "ST": ev.start_time.strftime("%H:%M") if ev.start_time else "",
                "ET": ev.end_time.strftime("%H:%M") if ev.end_time else "",
                "DT": ev.duration.total_seconds() if ev.duration else "",
                "AP": "true" if getattr(ev, "anchor_prev", True) else "false",
                "location": ev.location or "",
            }
        )
    return rows


__all__ = [
    "constraints_rows",
    "immovables_rows",
    "tasks_rows",
    "timebox_events_rows",
]

