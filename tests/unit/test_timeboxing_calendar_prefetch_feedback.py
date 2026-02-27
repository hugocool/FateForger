"""Unit tests for calendar prefetch feedback messaging."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent


@pytest.mark.asyncio
async def test_prefetch_calendar_adds_note_when_client_unavailable() -> None:
    """Prefetch should surface a user-visible note when calendar client is unavailable."""
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._ensure_calendar_client = lambda: None
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )

    await TimeboxingFlowAgent._prefetch_calendar_immovables(
        agent, session, "2026-02-14"
    )

    assert (
        "Calendar integration is unavailable right now; share fixed events manually."
        in session.background_updates
    )


@pytest.mark.asyncio
async def test_prefetch_calendar_error_note_is_deduplicated() -> None:
    """Prefetch failure note should be appended once across repeated failures."""

    class _FailingCalendarClient:
        async def list_day_immovables(
            self,
            *,
            calendar_id: str,
            day: date,
            tz,
            diagnostics: dict | None = None,
        ) -> list[dict[str, str]]:
            _ = (calendar_id, day, tz, diagnostics)
            raise RuntimeError("calendar MCP unavailable")

    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._ensure_calendar_client = lambda: _FailingCalendarClient()
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )

    await TimeboxingFlowAgent._prefetch_calendar_immovables(
        agent, session, "2026-02-14"
    )
    await TimeboxingFlowAgent._prefetch_calendar_immovables(
        agent, session, "2026-02-14"
    )

    note = "Couldn't load calendar events yet; share fixed anchors manually or click Redo."
    assert session.background_updates.count(note) == 1


@pytest.mark.asyncio
async def test_prefetch_calendar_force_refresh_bypasses_cached_snapshot() -> None:
    """force_refresh should ignore day-cache and fetch live snapshot again."""

    class _Snapshot:
        def __init__(self, suffix: str) -> None:
            self.immovables = [{"title": f"Lunch {suffix}", "start": "13:00", "end": "14:00"}]
            self.response = {
                "events": [
                    {
                        "summary": f"Lunch {suffix}",
                        "start": {
                            "dateTime": "2026-02-14T13:00:00+01:00",
                            "timeZone": "Europe/Amsterdam",
                        },
                        "end": {
                            "dateTime": "2026-02-14T14:00:00+01:00",
                            "timeZone": "Europe/Amsterdam",
                        },
                    }
                ]
            }

    class _CalendarClient:
        def __init__(self) -> None:
            self.calls = 0

        async def list_day_snapshot(
            self,
            *,
            calendar_id: str,
            day: date,
            tz,
            diagnostics: dict | None = None,
        ) -> _Snapshot:
            _ = (calendar_id, day, tz, diagnostics)
            self.calls += 1
            return _Snapshot(suffix=f"R{self.calls}")

    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    client = _CalendarClient()
    agent._ensure_calendar_client = lambda: client
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )
    session.prefetched_immovables_by_date["2026-02-14"] = [
        {"title": "Lunch stale", "start": "13:00", "end": "14:00"}
    ]
    session.prefetched_remote_snapshots_by_date["2026-02-14"] = object()

    await TimeboxingFlowAgent._prefetch_calendar_immovables(
        agent,
        session,
        "2026-02-14",
        force_refresh=True,
    )

    assert client.calls == 1
    assert session.prefetched_immovables_by_date["2026-02-14"][0]["title"] == "Lunch R1"


@pytest.mark.asyncio
async def test_ensure_calendar_timeout_keeps_prefetch_running_in_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout in ensure should not cancel the in-flight calendar prefetch task."""

    async def _slow_prefetch(
        self: TimeboxingFlowAgent, session: Session, planned_date: str
    ) -> None:
        _ = self
        await asyncio.sleep(0.05)
        session.prefetched_immovables_by_date[planned_date] = [
            {"title": "Meeting", "start": "10:00", "end": "11:00"}
        ]

    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    session = Session(
        thread_ts="t1",
        channel_id="c1",
        user_id="u1",
        planned_date="2026-02-14",
        tz_name="Europe/Amsterdam",
    )
    monkeypatch.setattr(
        TimeboxingFlowAgent, "_prefetch_calendar_immovables", _slow_prefetch
    )

    await TimeboxingFlowAgent._ensure_calendar_immovables(agent, session, timeout_s=0.01)

    assert (
        "Calendar fetch timed out; share fixed anchors manually or click Redo."
        in session.background_updates
    )
    await asyncio.sleep(0.08)
    TimeboxingFlowAgent._apply_prefetched_calendar_immovables(agent, session)
    assert session.frame_facts.get("immovables")
