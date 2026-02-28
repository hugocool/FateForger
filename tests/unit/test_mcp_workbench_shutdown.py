from __future__ import annotations

from fateforger.agents.timeboxing.mcp_clients import (
    McpCalendarClient as TimeboxingCalendarClient,
)
from fateforger.haunt.reconcile import McpCalendarClient as HauntCalendarClient


class _StopWorkbench:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.close_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1


class _CloseOnlyWorkbench:
    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


async def test_haunt_calendar_client_prefers_stop() -> None:
    client = object.__new__(HauntCalendarClient)
    wb = _StopWorkbench()
    client._workbench = wb

    await client.close()

    assert wb.stop_calls == 1
    assert wb.close_calls == 0


async def test_timeboxing_calendar_client_falls_back_to_close() -> None:
    client = object.__new__(TimeboxingCalendarClient)
    wb = _CloseOnlyWorkbench()
    client._workbench = wb

    await client.close()

    assert wb.close_calls == 1
