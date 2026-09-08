"""Unit tests for CalendarSubmitter sync execution settings."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

import fateforger.agents.timeboxing.submitter as submitter_module
from fateforger.agents.timeboxing.sync_engine import SyncOp, SyncOpType, SyncTransaction
from fateforger.agents.timeboxing.tb_models import ET, FixedStart, TBEvent, TBPlan


def _plan() -> TBPlan:
    return TBPlan(
        events=[
            TBEvent(
                n="Deep work",
                d="",
                t=ET.DW,
                p=FixedStart(st=time(9, 0), dur=timedelta(hours=1)),
            )
        ],
        date=date(2025, 6, 15),
        tz="Europe/Amsterdam",
    )


@pytest.mark.asyncio
async def test_submit_plan_halts_on_first_sync_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submitter should execute sync with halt_on_error enabled."""
    captured: dict[str, bool] = {}

    def _fake_plan_sync(*args, **kwargs):
        _ = (args, kwargs)
        return [
            SyncOp(
                op_type=SyncOpType.UPDATE,
                gcal_event_id="fftb1",
                after_payload={"calendarId": "primary", "eventId": "fftb1"},
            )
        ]

    async def _fake_execute_sync(
        ops, workbench, *, halt_on_error: bool = False
    ) -> SyncTransaction:
        _ = (ops, workbench)
        captured["halt_on_error"] = halt_on_error
        return SyncTransaction(status="committed")

    monkeypatch.setattr(submitter_module, "plan_sync", _fake_plan_sync)
    monkeypatch.setattr(submitter_module, "execute_sync", _fake_execute_sync)
    submitter = submitter_module.CalendarSubmitter(server_url="http://localhost:3000")
    monkeypatch.setattr(submitter, "_get_workbench", lambda: object())

    tx = await submitter.submit_plan(
        desired=_plan(),
        remote=_plan(),
        event_id_map={},
    )

    assert tx.status == "committed"
    assert captured["halt_on_error"] is True
