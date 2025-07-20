"""
Calendar Contract - Data models for AutoGen Sequential Workflow (Ticket #1)

Defines the structured data contracts for multi-agent calendar pipeline:
- CalendarEvent: Google Calendar event structure with constrained generation support
- CalendarOp: Individual calendar operation (CREATE/UPDATE/DELETE)
- PlanDiff: Collection of calendar operations for LLM structured output

These models enable json_output= parameter in AutoGen for validated LLM responses.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
from datetime import date as Date
from pydantic import BaseModel, Field, ConfigDict


class EventDateTime(BaseModel):
    """Google Calendar EventDateTime structure."""

    date: Optional[Date] = Field(None, description="All-day date (yyyy-mm-dd)")
    date_time: Optional[datetime] = Field(
        None, alias="dateTime", description="RFC3339 timestamp"
    )
    time_zone: Optional[str] = Field(
        None, alias="timeZone", description="IANA TZ name (e.g. Europe/Amsterdam)"
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CreatorOrganizer(BaseModel):
    """Google Calendar creator/organizer structure."""

    id: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = Field(None, alias="displayName")
    self_: Optional[bool] = Field(None, alias="self")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RemindersOverride(BaseModel):
    """Individual reminder override."""

    method: Optional[str] = None  # "email" | "popup"
    minutes: Optional[int] = None


class Reminders(BaseModel):
    """Google Calendar reminders structure."""

    use_default: Optional[bool] = Field(None, alias="useDefault")
    overrides: Optional[List[RemindersOverride]] = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ExtendedProperties(BaseModel):
    """Google Calendar extended properties."""

    private: Optional[Dict[str, str]] = None
    shared: Optional[Dict[str, str]] = None


class CalendarEvent(BaseModel):
    """
    Google Calendar event structure optimized for constrained generation.

    Compatible with json_output= parameter in AutoGen for validated LLM responses.
    Matches Google Calendar API v3 event resource structure.
    """

    id: Optional[str] = Field(None, description="Calendar event ID")
    status: Optional[str] = Field(
        None, description="Event status: confirmed | tentative | cancelled"
    )
    summary: Optional[str] = Field(None, description="Event title/summary")
    description: Optional[str] = Field(None, description="Event description")
    location: Optional[str] = Field(None, description="Event location")
    color_id: Optional[str] = Field(
        None, alias="colorId", description="Event color ID (1-11)"
    )
    creator: Optional[CreatorOrganizer] = Field(None, description="Event creator")
    start: Optional[EventDateTime] = Field(None, description="Event start time")
    end: Optional[EventDateTime] = Field(None, description="Event end time")
    source: Optional[Dict[str, str]] = Field(
        None,
        description="Source info: {'url': '<your-notion-url>', 'title': 'Open in Notion'}",
    )
    transparency: Optional[str] = Field(
        None, description="Free/busy status: opaque | transparent"
    )
    extended_properties: Optional[ExtendedProperties] = Field(
        None, alias="extendedProperties", description="Extended properties"
    )
    reminders: Optional[Reminders] = Field(None, description="Reminder settings")
    event_type: Optional[str] = Field(
        None,
        alias="eventType",
        description="Event type: default | workingLocation | outOfOffice | focusTime | birthday",
    )

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class OpType(str, Enum):
    """Calendar operation types for Sequential Workflow."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class CalendarOp(BaseModel):
    """
    Individual calendar operation for Sequential Workflow processing.

    Designed for AutoGen ClosureAgent consumption with clear operation semantics.
    """

    op: OpType = Field(..., description="Operation type: create, update, or delete")
    event: Optional[CalendarEvent] = Field(
        None, description="Full event data for CREATE operations"
    )
    event_id: Optional[str] = Field(
        None, description="Target event ID for UPDATE/DELETE operations"
    )
    diff: Optional[Dict[str, Any]] = Field(
        None,
        description="Sparse field updates for UPDATE operations (e.g., {'summary': 'New Title'})",
    )

    def validate_operation(self) -> None:
        """Validate operation has required fields for its type."""
        if self.op == OpType.CREATE:
            if not self.event:
                raise ValueError("CREATE operation requires 'event' field")
        elif self.op == OpType.UPDATE:
            if not self.event_id:
                raise ValueError("UPDATE operation requires 'event_id' field")
        elif self.op == OpType.DELETE:
            if not self.event_id:
                raise ValueError("DELETE operation requires 'event_id' field")


class PlanDiff(BaseModel):
    """
    Collection of calendar operations for AutoGen Sequential Workflow.

    This is the primary structure for LLM structured output using json_output=PlanDiff.
    Enables deterministic multi-agent processing through AutoGen runtime topics.
    """

    operations: List[CalendarOp] = Field(
        default_factory=list, description="List of calendar operations to execute"
    )

    def validate_all_operations(self) -> None:
        """Validate all operations in the plan."""
        for op in self.operations:
            op.validate_operation()

    @property
    def operation_count(self) -> Dict[str, int]:
        """Get count of operations by type."""
        counts = {"create": 0, "update": 0, "delete": 0}
        for op in self.operations:
            counts[op.op.value] += 1
        return counts

    def __str__(self) -> str:
        counts = self.operation_count
        return f"PlanDiff({counts['create']} create, {counts['update']} update, {counts['delete']} delete)"
