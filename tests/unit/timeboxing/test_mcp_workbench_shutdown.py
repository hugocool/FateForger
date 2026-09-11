from __future__ import annotations

from fateforger.haunt.reconcile import McpCalendarClient as HauntCalendarClient


class _StopWorkbench:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.close_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1


async def test_haunt_calendar_client_prefers_stop() -> None:
    client = object.__new__(HauntCalendarClient)
    wb = _StopWorkbench()
    client._workbench = wb

    await client.close()

    assert wb.stop_calls == 1
    assert wb.close_calls == 0
