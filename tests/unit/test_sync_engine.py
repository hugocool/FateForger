"""Unit tests for ``fateforger.agents.timeboxing.sync_engine``.

Covers:
- ``base32hex_id`` determinism and GCal safety
- ``is_owned_event`` prefix check
- ``gcal_response_to_tb_plan`` conversion
- ``plan_sync`` DeepDiff-based diffing (creates, updates, deletes, no-ops)
- ``execute_sync`` with mocked MCP workbench
- ``undo_sync`` compensating ops
- Foreign event protection (no mutations on non-fftb events)
"""

from __future__ import annotations

import re
from datetime import date, time, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fateforger.adapters.calendar.models import (
    GCalEvent,
    GCalEventDateTime,
    GCalEventsResponse,
)
from fateforger.agents.timeboxing.sync_engine import (
    FFTB_PREFIX,
    SyncOp,
    SyncOpType,
    SyncTransaction,
    base32hex_id,
    execute_sync,
    gcal_response_to_tb_plan,
    gcal_response_to_tb_plan_with_identity,
    is_owned_event,
    plan_sync,
    undo_sync,
)
from fateforger.agents.timeboxing.tb_models import (
    ET,
    AfterPrev,
    FixedStart,
    FixedWindow,
    TBEvent,
    TBPlan,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_gcal_response(
    events: list[tuple[str, str, str, str | None]],
) -> GCalEventsResponse:
    """Build a GCalEventsResponse from (id, summary, start_iso, end_iso) tuples.

    Args:
        events: List of (id, summary, start_dateTime, end_dateTime) tuples.
            The optional 4th element is colorId.

    Returns:
        A GCalEventsResponse.
    """
    gcal_events = []
    for ev in events:
        eid, summary, start, end = ev[0], ev[1], ev[2], ev[3]
        gcal_events.append(
            GCalEvent(
                id=eid,
                summary=summary,
                start=GCalEventDateTime(dateTime=start, timeZone="Europe/Amsterdam"),
                end=GCalEventDateTime(dateTime=end, timeZone="Europe/Amsterdam"),
                status="confirmed",
            )
        )
    return GCalEventsResponse(events=gcal_events, totalCount=len(gcal_events))


PLAN_DATE = date(2025, 6, 15)
TZ = "Europe/Amsterdam"


# ── base32hex_id ─────────────────────────────────────────────────────────


class TestBase32hexId:
    """Test deterministic event ID generation."""

    def test_deterministic(self) -> None:
        a = base32hex_id("2025-06-15|Morning|08:00|0")
        b = base32hex_id("2025-06-15|Morning|08:00|0")
        assert a == b

    def test_different_seeds_differ(self) -> None:
        a = base32hex_id("seed1")
        b = base32hex_id("seed2")
        assert a != b

    def test_starts_with_prefix(self) -> None:
        eid = base32hex_id("test")
        assert eid.startswith(FFTB_PREFIX)

    def test_gcal_safe_characters(self) -> None:
        """GCal event IDs must only contain a-v and 0-9."""
        eid = base32hex_id("test-with-special-chars!@#$%")
        allowed = set("abcdefghijklmnopqrstuv0123456789")
        assert all(c in allowed for c in eid), f"Invalid chars in {eid}"

    def test_max_length_respected(self) -> None:
        eid = base32hex_id("long-seed", max_len=20)
        assert len(eid) <= 20


# ── is_owned_event ───────────────────────────────────────────────────────


class TestIsOwnedEvent:
    """Test agent ownership detection."""

    def test_owned(self) -> None:
        assert is_owned_event("fftbabc123") is True

    def test_foreign(self) -> None:
        assert is_owned_event("abc123def") is False

    def test_empty(self) -> None:
        assert is_owned_event("") is False


# ── gcal_response_to_tb_plan ────────────────────────────────────────────


class TestGcalResponseToTbPlan:
    """Test GCal → TBPlan conversion."""

    def test_basic_conversion(self) -> None:
        resp = _make_gcal_response(
            [
                (
                    "evt1",
                    "Standup",
                    "2025-06-15T09:00:00+02:00",
                    "2025-06-15T09:15:00+02:00",
                ),
                (
                    "evt2",
                    "Lunch",
                    "2025-06-15T12:00:00+02:00",
                    "2025-06-15T13:00:00+02:00",
                ),
            ]
        )
        plan, id_map = gcal_response_to_tb_plan(resp, plan_date=PLAN_DATE, tz_name=TZ)

        assert len(plan.events) == 2
        assert plan.events[0].n == "Standup"
        assert plan.events[0].p.a == "fw"  # all GCal events become fixed windows
        assert plan.date == PLAN_DATE

        # Check event_id_map
        assert "Standup|09:00:00" in id_map
        assert id_map["Standup|09:00:00"] == "evt1"

    def test_skips_all_day_events(self) -> None:
        resp = GCalEventsResponse(
            events=[
                GCalEvent(
                    id="allday",
                    summary="Holiday",
                    start=GCalEventDateTime(date="2025-06-15"),
                    end=GCalEventDateTime(date="2025-06-16"),
                )
            ],
            totalCount=1,
        )
        plan, id_map = gcal_response_to_tb_plan(resp, plan_date=PLAN_DATE, tz_name=TZ)
        assert len(plan.events) == 0

    def test_skips_cancelled(self) -> None:
        resp = GCalEventsResponse(
            events=[
                GCalEvent(
                    id="x",
                    summary="Cancelled",
                    start=GCalEventDateTime(dateTime="2025-06-15T10:00:00+02:00"),
                    end=GCalEventDateTime(dateTime="2025-06-15T11:00:00+02:00"),
                    status="cancelled",
                )
            ],
            totalCount=1,
        )
        plan, _ = gcal_response_to_tb_plan(resp, plan_date=PLAN_DATE, tz_name=TZ)
        assert len(plan.events) == 0

    def test_skips_wrong_date(self) -> None:
        resp = _make_gcal_response(
            [
                (
                    "evt1",
                    "Tomorrow",
                    "2025-06-16T09:00:00+02:00",
                    "2025-06-16T10:00:00+02:00",
                ),
            ]
        )
        plan, _ = gcal_response_to_tb_plan(resp, plan_date=PLAN_DATE, tz_name=TZ)
        assert len(plan.events) == 0

    def test_sorts_by_start_time(self) -> None:
        resp = _make_gcal_response(
            [
                (
                    "b",
                    "Later",
                    "2025-06-15T14:00:00+02:00",
                    "2025-06-15T15:00:00+02:00",
                ),
                (
                    "a",
                    "Earlier",
                    "2025-06-15T09:00:00+02:00",
                    "2025-06-15T10:00:00+02:00",
                ),
            ]
        )
        plan, _ = gcal_response_to_tb_plan(resp, plan_date=PLAN_DATE, tz_name=TZ)
        assert plan.events[0].n == "Earlier"
        assert plan.events[1].n == "Later"

    def test_identity_variant_returns_ordered_ids(self) -> None:
        resp = _make_gcal_response(
            [
                (
                    "evt-b",
                    "Later",
                    "2025-06-15T14:00:00+02:00",
                    "2025-06-15T15:00:00+02:00",
                ),
                (
                    "evt-a",
                    "Earlier",
                    "2025-06-15T09:00:00+02:00",
                    "2025-06-15T10:00:00+02:00",
                ),
            ]
        )
        plan, _id_map, ids = gcal_response_to_tb_plan_with_identity(
            resp, plan_date=PLAN_DATE, tz_name=TZ
        )
        assert [event.n for event in plan.events] == ["Earlier", "Later"]
        assert ids == ["evt-a", "evt-b"]


# ── plan_sync ────────────────────────────────────────────────────────────


class TestPlanSync:
    """Test DeepDiff-based sync planning."""

    def test_identical_plans_no_ops(self) -> None:
        """No changes → no sync ops."""
        plan = TBPlan(
            events=[
                TBEvent(
                    n="A",
                    d="",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=1)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        ops = plan_sync(plan, plan, {}, calendar_id="primary")
        assert len(ops) == 0

    def test_new_events_create_ops(self) -> None:
        """Events in desired but not remote → creates."""
        remote = TBPlan(events=[], date=PLAN_DATE, tz=TZ)
        desired = TBPlan(
            events=[
                TBEvent(
                    n="New",
                    d="desc",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=1)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        ops = plan_sync(remote, desired, {})
        assert len(ops) == 1
        assert ops[0].op_type == SyncOpType.CREATE
        assert ops[0].gcal_event_id.startswith(FFTB_PREFIX)
        assert ops[0].after_payload["summary"] == "New"
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ops[0].after_payload["start"]
        )
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ops[0].after_payload["end"]
        )
        assert "+" not in ops[0].after_payload["start"]
        assert "+" not in ops[0].after_payload["end"]

    def test_removed_owned_events_delete_ops(self) -> None:
        """Owned events in remote but not desired → deletes."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Old",
                    d="",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=1)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(events=[], date=PLAN_DATE, tz=TZ)

        # Map the event to an owned ID
        event_id_map = {"Old|09:00:00": "fftbabc123"}
        ops = plan_sync(remote, desired, event_id_map)
        assert len(ops) == 1
        assert ops[0].op_type == SyncOpType.DELETE
        assert ops[0].gcal_event_id == "fftbabc123"

    def test_foreign_events_not_deleted(self) -> None:
        """Foreign events (non-fftb ID) should not be deleted."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Meeting", d="", t="M", p=FixedWindow(st=time(10), et=time(11))
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(events=[], date=PLAN_DATE, tz=TZ)

        event_id_map = {"Meeting|10:00:00": "foreign_gcal_id"}
        ops = plan_sync(remote, desired, event_id_map)
        assert len(ops) == 0  # foreign event not deleted

    def test_changed_event_update_ops(self) -> None:
        """Changed fields on owned events → updates."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Work",
                    d="old desc",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=2)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(
            events=[
                TBEvent(
                    n="Work",
                    d="new desc",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=2)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )

        event_id_map = {"Work|09:00:00": "fftbwork123"}
        ops = plan_sync(remote, desired, event_id_map)
        assert len(ops) == 1
        assert ops[0].op_type == SyncOpType.UPDATE
        assert ops[0].after_payload["description"] == "new desc"
        assert ops[0].before_payload is not None
        assert ops[0].before_payload["description"] == "old desc"
        assert "root['description']" in ops[0].diff_paths
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ops[0].after_payload["start"]
        )
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ops[0].after_payload["end"]
        )
        assert "+" not in ops[0].after_payload["start"]
        assert "+" not in ops[0].after_payload["end"]

    def test_creates_before_deletes_ordering(self) -> None:
        """Creates should come before deletes in the ops list."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Old",
                    d="",
                    t="DW",
                    p=FixedStart(st=time(9), dur=timedelta(hours=1)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(
            events=[
                TBEvent(
                    n="New",
                    d="",
                    t="DW",
                    p=FixedStart(st=time(10), dur=timedelta(hours=1)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )

        event_id_map = {"Old|09:00:00": "fftbold123"}
        ops = plan_sync(remote, desired, event_id_map)
        op_types = [op.op_type for op in ops]
        # Creates should appear before deletes
        if SyncOpType.CREATE in op_types and SyncOpType.DELETE in op_types:
            assert op_types.index(SyncOpType.CREATE) < op_types.index(SyncOpType.DELETE)

    def test_remote_identity_enables_update_without_key_hint(self) -> None:
        """When key-based map is missing, ordered remote IDs should still prevent duplicates."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Deep Work",
                    d="",
                    t="DW",
                    p=FixedWindow(st=time(9, 0), et=time(10, 0)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(
            events=[
                TBEvent(
                    n="Deep Work",
                    d="",
                    t="DW",
                    p=FixedWindow(st=time(9, 15), et=time(10, 15)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        ops = plan_sync(
            remote,
            desired,
            {},
            remote_event_ids_by_index=["fftb-owned-1"],
        )
        assert [op.op_type for op in ops] == [SyncOpType.UPDATE]
        assert ops[0].gcal_event_id == "fftb-owned-1"

    def test_foreign_match_is_noop_not_create(self) -> None:
        """Foreign events should never mutate or duplicate when reconciled."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Lunch",
                    d="",
                    t="M",
                    p=FixedWindow(st=time(12, 0), et=time(13, 0)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(
            events=[
                TBEvent(
                    n="Lunch",
                    d="changed but foreign",
                    t="M",
                    p=FixedWindow(st=time(12, 5), et=time(13, 5)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        ops = plan_sync(
            remote,
            desired,
            {},
            remote_event_ids_by_index=["foreign-event-id-1"],
        )
        assert ops == []

    def test_foreign_overlap_summary_mismatch_is_noop_not_create(self) -> None:
        """Foreign overlaps with renamed summaries should still avoid duplicate creates."""
        remote = TBPlan(
            events=[
                TBEvent(
                    n="Lunch",
                    d="",
                    t="M",
                    p=FixedWindow(st=time(13, 0), et=time(14, 0)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        desired = TBPlan(
            events=[
                TBEvent(
                    n="Lunch Break",
                    d="planner wording changed",
                    t="M",
                    p=FixedWindow(st=time(13, 0), et=time(14, 0)),
                )
            ],
            date=PLAN_DATE,
            tz=TZ,
        )
        ops = plan_sync(
            remote,
            desired,
            {},
            remote_event_ids_by_index=["foreign-event-id-2"],
        )
        assert ops == []


# ── execute_sync ─────────────────────────────────────────────────────────


class TestExecuteSync:
    """Test sync execution with mocked MCP workbench."""

    @pytest.fixture()
    def mock_workbench(self) -> AsyncMock:
        wb = AsyncMock()
        result = MagicMock()
        result.is_error = False
        result.result = [MagicMock(text='{"ok": true}')]
        wb.call_tool.return_value = result
        return wb

    @pytest.mark.asyncio
    async def test_successful_execution(self, mock_workbench: AsyncMock) -> None:
        ops = [
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="fftbtest123",
                after_payload={"calendarId": "primary", "summary": "Test"},
            ),
        ]
        tx = await execute_sync(ops, mock_workbench)
        assert tx.status == "committed"
        assert len(tx.results) == 1
        assert tx.results[0]["ok"] is True
        mock_workbench.call_tool.assert_called_once_with(
            "create-event",
            arguments={"calendarId": "primary", "summary": "Test"},
        )

    @pytest.mark.asyncio
    async def test_failed_op_marks_partial(self, mock_workbench: AsyncMock) -> None:
        error_result = MagicMock()
        error_result.is_error = True
        error_result.result = [MagicMock(text="Event not found")]
        mock_workbench.call_tool.return_value = error_result

        ops = [
            SyncOp(
                op_type=SyncOpType.DELETE,
                gcal_event_id="fftbgone",
                after_payload={"calendarId": "primary", "eventId": "fftbgone"},
            )
        ]
        tx = await execute_sync(ops, mock_workbench)
        assert tx.status == "partial"
        assert tx.results[0]["ok"] is False

    @pytest.mark.asyncio
    async def test_exception_marks_partial(self, mock_workbench: AsyncMock) -> None:
        mock_workbench.call_tool.side_effect = RuntimeError("Connection failed")

        ops = [
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="fftbtest",
                after_payload={"calendarId": "primary", "summary": "X"},
            )
        ]
        tx = await execute_sync(ops, mock_workbench)
        assert tx.status == "partial"
        assert "error" in tx.results[0]

    @pytest.mark.asyncio
    async def test_multiple_ops_executed_sequentially(
        self, mock_workbench: AsyncMock
    ) -> None:
        ops = [
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="a",
                after_payload={"summary": "A"},
            ),
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id="b",
                after_payload={"summary": "B"},
            ),
        ]
        tx = await execute_sync(ops, mock_workbench)
        assert tx.status == "committed"
        assert mock_workbench.call_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_halt_on_error_stops_remaining_ops(self, mock_workbench: AsyncMock) -> None:
        error_result = MagicMock()
        error_result.is_error = True
        error_result.result = [MagicMock(text="bad request")]
        mock_workbench.call_tool.return_value = error_result

        ops = [
            SyncOp(
                op_type=SyncOpType.UPDATE,
                gcal_event_id="fftb-first",
                after_payload={"summary": "First"},
            ),
            SyncOp(
                op_type=SyncOpType.DELETE,
                gcal_event_id="fftb-second",
                after_payload={"eventId": "fftb-second"},
            ),
        ]
        tx = await execute_sync(ops, mock_workbench, halt_on_error=True)
        assert tx.status == "partial_halted"
        assert len(tx.results) == 1
        assert mock_workbench.call_tool.call_count == 1


# ── undo_sync ────────────────────────────────────────────────────────────


class TestUndoSync:
    """Test compensating undo operations."""

    @pytest.fixture()
    def mock_workbench(self) -> AsyncMock:
        wb = AsyncMock()
        result = MagicMock()
        result.is_error = False
        result.result = [MagicMock(text='{"ok": true}')]
        wb.call_tool.return_value = result
        return wb

    @pytest.mark.asyncio
    async def test_undo_create_becomes_delete(self, mock_workbench: AsyncMock) -> None:
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftbcreated",
                    after_payload={
                        "calendarId": "primary",
                        "eventId": "fftbcreated",
                        "summary": "New",
                    },
                ),
            ],
            results=[{"ok": True, "event_id": "fftbcreated"}],
        )
        undo_tx = await undo_sync(tx, mock_workbench)
        assert undo_tx.status == "undone"
        # Should have called delete-event
        call_args = mock_workbench.call_tool.call_args
        assert call_args[0][0] == "delete-event"

    @pytest.mark.asyncio
    async def test_undo_update_restores_before(self, mock_workbench: AsyncMock) -> None:
        before = {"calendarId": "primary", "eventId": "fftbx", "summary": "Old"}
        after = {"calendarId": "primary", "eventId": "fftbx", "summary": "New"}
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.UPDATE,
                    gcal_event_id="fftbx",
                    after_payload=after,
                    before_payload=before,
                ),
            ],
            results=[{"ok": True, "event_id": "fftbx"}],
        )
        undo_tx = await undo_sync(tx, mock_workbench)
        assert undo_tx.status == "undone"
        call_args = mock_workbench.call_tool.call_args
        assert call_args[0][0] == "update-event"
        # Should restore with before payload
        assert call_args[1]["arguments"]["summary"] == "Old"

    @pytest.mark.asyncio
    async def test_undo_delete_recreates(self, mock_workbench: AsyncMock) -> None:
        before = {"calendarId": "primary", "eventId": "fftbdel", "summary": "Deleted"}
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.DELETE,
                    gcal_event_id="fftbdel",
                    after_payload={"calendarId": "primary", "eventId": "fftbdel"},
                    before_payload=before,
                ),
            ],
            results=[{"ok": True, "event_id": "fftbdel"}],
        )
        undo_tx = await undo_sync(tx, mock_workbench)
        assert undo_tx.status == "undone"
        call_args = mock_workbench.call_tool.call_args
        assert call_args[0][0] == "create-event"
        assert call_args[1]["arguments"]["summary"] == "Deleted"

    @pytest.mark.asyncio
    async def test_undo_reverses_order(self, mock_workbench: AsyncMock) -> None:
        """Undo should process ops in reverse order."""
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftba",
                    after_payload={"calendarId": "primary", "eventId": "fftba"},
                ),
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftbb",
                    after_payload={"calendarId": "primary", "eventId": "fftbb"},
                ),
            ],
            results=[
                {"ok": True, "event_id": "fftba"},
                {"ok": True, "event_id": "fftbb"},
            ],
        )
        undo_tx = await undo_sync(tx, mock_workbench)
        # First undo call should be for fftbb (last created)
        calls = mock_workbench.call_tool.call_args_list
        assert calls[0][1]["arguments"]["eventId"] == "fftbb"
        assert calls[1][1]["arguments"]["eventId"] == "fftba"

    @pytest.mark.asyncio
    async def test_undo_skips_forward_ops_that_failed(
        self, mock_workbench: AsyncMock
    ) -> None:
        """Undo should compensate only forward ops that completed successfully."""
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftb-failed",
                    after_payload={"calendarId": "primary", "eventId": "fftb-failed"},
                ),
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftb-ok",
                    after_payload={"calendarId": "primary", "eventId": "fftb-ok"},
                ),
            ],
            results=[
                {"ok": False, "event_id": "fftb-failed"},
                {"ok": True, "event_id": "fftb-ok"},
            ],
            status="partial",
        )

        undo_tx = await undo_sync(tx, mock_workbench)
        assert undo_tx.status == "undone"
        calls = mock_workbench.call_tool.call_args_list
        assert len(calls) == 1
        assert calls[0][0][0] == "delete-event"
        assert calls[0][1]["arguments"]["eventId"] == "fftb-ok"

    @pytest.mark.asyncio
    async def test_undo_raises_when_transaction_results_missing(
        self, mock_workbench: AsyncMock
    ) -> None:
        """Undo should fail loudly when deterministic execution results are absent."""
        tx = SyncTransaction(
            ops=[
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id="fftb-created",
                    after_payload={"calendarId": "primary", "eventId": "fftb-created"},
                )
            ],
            status="committed",
        )

        with pytest.raises(ValueError, match="complete per-op execution results"):
            await undo_sync(tx, mock_workbench)


# ── SyncTransaction ─────────────────────────────────────────────────────


class TestSyncTransaction:
    """Test SyncTransaction dataclass."""

    def test_default_status(self) -> None:
        tx = SyncTransaction()
        assert tx.status == "pending"
        assert tx.ops == []
        assert tx.results == []

    def test_timestamp_set(self) -> None:
        tx = SyncTransaction()
        assert tx.timestamp  # should have a default timestamp
