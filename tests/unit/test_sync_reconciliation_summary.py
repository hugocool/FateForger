"""Unit tests for shared reconciliation summary contract."""

from __future__ import annotations

from datetime import date, time

from fateforger.agents.timeboxing.tb_models import ET, FixedWindow, TBEvent, TBPlan
from fateforger.sync_core.reconciliation_summary import summarize_reconciliation


def _plan(events: list[TBEvent]) -> TBPlan:
    return TBPlan(events=events, date=date(2026, 3, 11), tz="Europe/Amsterdam")


def test_summarize_reconciliation_counts() -> None:
    remote = _plan(
        [
            TBEvent(
                n="Focus",
                d="",
                t=ET.DW,
                p=FixedWindow(st=time(9, 0), et=time(10, 0)),
            ),
            TBEvent(
                n="Lunch",
                d="",
                t=ET.BU,
                p=FixedWindow(st=time(12, 0), et=time(13, 0)),
            ),
        ]
    )
    desired = _plan(
        [
            TBEvent(
                n="Focus",
                d="",
                t=ET.DW,
                p=FixedWindow(st=time(9, 30), et=time(10, 30)),
            ),
            TBEvent(
                n="Lunch",
                d="",
                t=ET.BU,
                p=FixedWindow(st=time(12, 0), et=time(13, 0)),
            ),
            TBEvent(
                n="Deep Work",
                d="",
                t=ET.DW,
                p=FixedWindow(st=time(14, 0), et=time(15, 0)),
            ),
        ]
    )
    event_id_map = {
        "Focus|09:00:00": "fftb_focus_1",
        "Lunch|12:00:00": "external_lunch_1",
    }
    remote_event_ids_by_index = ["fftb_focus_1", "external_lunch_1"]

    summary = summarize_reconciliation(
        remote=remote,
        desired=desired,
        event_id_map=event_id_map,
        remote_event_ids_by_index=remote_event_ids_by_index,
    )

    assert summary.remote_fetched == 2
    assert summary.matched == 2
    assert summary.create == 1
    assert summary.update == 1
    assert summary.noop == 1
    assert summary.delete == 0


def test_planned_mutations_is_create_update_delete_sum() -> None:
    remote = _plan([])
    desired = _plan(
        [
            TBEvent(
                n="One",
                d="",
                t=ET.DW,
                p=FixedWindow(st=time(11, 0), et=time(12, 0)),
            )
        ]
    )

    summary = summarize_reconciliation(
        remote=remote,
        desired=desired,
        event_id_map={},
        remote_event_ids_by_index=[],
    )

    assert summary.create == 1
    assert summary.update == 0
    assert summary.delete == 0
    assert summary.planned_mutations == 1


def test_summarize_reconciliation_reports_owned_delete() -> None:
    remote = _plan(
        [
            TBEvent(
                n="Owned",
                d="",
                t=ET.DW,
                p=FixedWindow(st=time(8, 0), et=time(9, 0)),
            )
        ]
    )
    desired = _plan([])

    summary = summarize_reconciliation(
        remote=remote,
        desired=desired,
        event_id_map={"Owned|08:00:00": "fftb_owned_1"},
        remote_event_ids_by_index=["fftb_owned_1"],
    )

    assert summary.remote_fetched == 1
    assert summary.create == 0
    assert summary.update == 0
    assert summary.noop == 0
    assert summary.delete == 1
    assert summary.planned_mutations == 1


def test_summarize_reconciliation_foreign_overlap_is_noop() -> None:
    remote = _plan(
        [
            TBEvent(
                n="Lunch",
                d="",
                t=ET.BU,
                p=FixedWindow(st=time(12, 0), et=time(13, 0)),
            )
        ]
    )
    desired = _plan(
        [
            TBEvent(
                n="Lunch",
                d="",
                t=ET.BU,
                p=FixedWindow(st=time(12, 0), et=time(13, 0)),
            )
        ]
    )

    summary = summarize_reconciliation(
        remote=remote,
        desired=desired,
        event_id_map={"Lunch|12:00:00": "foreign_lunch_1"},
        remote_event_ids_by_index=["foreign_lunch_1"],
    )

    assert summary.remote_fetched == 1
    assert summary.matched == 1
    assert summary.create == 0
    assert summary.update == 0
    assert summary.noop == 1
    assert summary.delete == 0
    assert summary.planned_mutations == 0
