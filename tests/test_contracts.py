"""
Test Contracts - Unit tests for Ticket #1 deliverables.

Tests the PlanDiff, CalendarOp, and CalendarEvent models to ensure
they work correctly with AutoGen's json_output parameter.
"""

from datetime import date, datetime

import pytest

from src.contracts import (
    CalendarEvent,
    CalendarOp,
    EventDateTime,
    OpType,
    PlanDiff,
)


class TestCalendarEvent:
    """Test CalendarEvent model validation and structure."""

    def test_calendar_event_creation(self):
        """Test basic CalendarEvent creation."""
        event = CalendarEvent(
            summary="Test Event",
            description="A test event",
            start=EventDateTime(date_time=datetime(2025, 7, 20, 14, 0)),
            end=EventDateTime(date_time=datetime(2025, 7, 20, 15, 0)),
        )
        assert event.summary == "Test Event"
        assert event.description == "A test event"

    def test_calendar_event_json_serialization(self):
        """Test that CalendarEvent can be serialized to JSON."""
        event = CalendarEvent(id="event123", summary="Test Event", status="confirmed")
        json_data = event.model_dump(by_alias=True)
        assert json_data["summary"] == "Test Event"
        assert json_data["id"] == "event123"

    def test_calendar_event_parsing(self):
        """Test that CalendarEvent can be parsed from JSON."""
        json_data = {"id": "event456", "summary": "Parsed Event", "status": "confirmed"}
        event = CalendarEvent.model_validate(json_data)
        assert event.id == "event456"
        assert event.summary == "Parsed Event"


class TestCalendarOp:
    """Test CalendarOp model and operation validation."""

    def test_create_operation_validation(self):
        """Test CREATE operation requires event field."""
        event = CalendarEvent(summary="New Event")
        op = CalendarOp(op=OpType.CREATE, event=event)
        op.validate_operation()  # Should not raise

        # Missing event should raise
        op_invalid = CalendarOp(op=OpType.CREATE)
        with pytest.raises(ValueError, match="CREATE operation requires 'event' field"):
            op_invalid.validate_operation()

    def test_update_operation_validation(self):
        """Test UPDATE operation requires event_id field."""
        op = CalendarOp(
            op=OpType.UPDATE, event_id="event123", diff={"summary": "Updated Title"}
        )
        op.validate_operation()  # Should not raise

        # Missing event_id should raise
        op_invalid = CalendarOp(op=OpType.UPDATE, diff={"summary": "New"})
        with pytest.raises(
            ValueError, match="UPDATE operation requires 'event_id' field"
        ):
            op_invalid.validate_operation()

    def test_delete_operation_validation(self):
        """Test DELETE operation requires event_id field."""
        op = CalendarOp(op=OpType.DELETE, event_id="event123")
        op.validate_operation()  # Should not raise

        # Missing event_id should raise
        op_invalid = CalendarOp(op=OpType.DELETE)
        with pytest.raises(
            ValueError, match="DELETE operation requires 'event_id' field"
        ):
            op_invalid.validate_operation()


class TestPlanDiff:
    """Test PlanDiff model and collection behavior."""

    def test_plan_diff_creation(self):
        """Test basic PlanDiff creation."""
        ops = [
            CalendarOp(op=OpType.CREATE, event=CalendarEvent(summary="Event 1")),
            CalendarOp(op=OpType.DELETE, event_id="event123"),
        ]
        plan = PlanDiff(operations=ops)
        assert len(plan.operations) == 2

    def test_plan_diff_validation(self):
        """Test plan validation catches invalid operations."""
        ops = [
            CalendarOp(op=OpType.CREATE, event=CalendarEvent(summary="Valid")),
            CalendarOp(op=OpType.DELETE),  # Missing event_id - invalid
        ]
        plan = PlanDiff(operations=ops)

        with pytest.raises(
            ValueError, match="DELETE operation requires 'event_id' field"
        ):
            plan.validate_all_operations()

    def test_operation_count(self):
        """Test operation counting functionality."""
        ops = [
            CalendarOp(op=OpType.CREATE, event=CalendarEvent(summary="Event 1")),
            CalendarOp(op=OpType.CREATE, event=CalendarEvent(summary="Event 2")),
            CalendarOp(
                op=OpType.UPDATE, event_id="event123", diff={"summary": "Updated"}
            ),
            CalendarOp(op=OpType.DELETE, event_id="event456"),
        ]
        plan = PlanDiff(operations=ops)
        counts = plan.operation_count

        assert counts["create"] == 2
        assert counts["update"] == 1
        assert counts["delete"] == 1

    def test_plan_diff_str_representation(self):
        """Test string representation of PlanDiff."""
        ops = [
            CalendarOp(op=OpType.CREATE, event=CalendarEvent(summary="Event 1")),
            CalendarOp(op=OpType.DELETE, event_id="event123"),
        ]
        plan = PlanDiff(operations=ops)
        str_repr = str(plan)
        assert "1 create" in str_repr
        assert "1 delete" in str_repr
        assert "0 update" in str_repr

    def test_json_output_compatibility(self):
        """Test that PlanDiff works with model_validate for json_output compatibility."""
        sample_json = {
            "operations": [
                {
                    "op": "create",
                    "event": {
                        "summary": "New Meeting",
                        "description": "Team sync",
                        "start": {"dateTime": "2025-07-20T14:00:00"},
                        "end": {"dateTime": "2025-07-20T15:00:00"},
                    },
                },
                {
                    "op": "update",
                    "event_id": "event123",
                    "diff": {"summary": "Updated Meeting Title"},
                },
                {"op": "delete", "event_id": "event456"},
            ]
        }

        # This is the key test - PlanDiff.model_validate should work for json_output
        plan = PlanDiff.model_validate(sample_json)
        assert len(plan.operations) == 3
        assert plan.operations[0].op == OpType.CREATE
        assert plan.operations[1].op == OpType.UPDATE
        assert plan.operations[2].op == OpType.DELETE

        # Validate all operations are properly formed
        plan.validate_all_operations()  # Should not raise
