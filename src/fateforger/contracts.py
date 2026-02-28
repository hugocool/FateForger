from __future__ import annotations

from datetime import date as _DateOnly
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator
from pydantic.config import ConfigDict


class OpType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class EventDateTime(BaseModel):
    """Represents a Google Calendar / MCP event start or end time.

    Handles both timed events (``dateTime``) and all-day events (``date``).
    Use :meth:`to_datetime` to resolve a timezone-aware :class:`~datetime.datetime`
    from either variant without manual dict probing or try/except.
    """

    date_time: Optional[datetime] = Field(default=None, alias="dateTime")
    date: Optional[_DateOnly] = Field(default=None)
    time_zone: Optional[str] = Field(default=None, alias="timeZone")

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @field_validator("date_time", mode="before")
    @classmethod
    def _parse_date_time(cls, v: object) -> object:
        """Accept ISO strings in addition to datetime objects."""
        if isinstance(v, str) and v:
            from dateutil import parser as date_parser  # noqa: PLC0415

            return date_parser.isoparse(v)
        return v

    @field_validator("date", mode="before")
    @classmethod
    def _parse_date(cls, v: object) -> object:
        """Accept ISO date strings in addition to _DateOnly objects."""
        if isinstance(v, str) and v:
            from dateutil import parser as date_parser  # noqa: PLC0415

            return date_parser.isoparse(v).date()
        return v

    def to_datetime(self, tz: Union[ZoneInfo, timezone]) -> Optional[datetime]:
        """Resolve a timezone-aware datetime from either ``dateTime`` or ``date``.

        - **Timed events**: converts ``dateTime`` to *tz*; if naive, attaches *tz*.
        - **All-day events**: combines ``date`` with midnight in *tz*.
        - Returns ``None`` when both fields are absent.
        """
        if self.date_time is not None:
            if self.date_time.tzinfo is None:
                return self.date_time.replace(tzinfo=tz)
            return self.date_time.astimezone(tz)
        if self.date is not None:
            return datetime.combine(self.date, datetime.min.time(), tz)
        return None


class CalendarEvent(BaseModel):
    id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    status: Optional[str] = None
    start: Optional[EventDateTime] = None
    end: Optional[EventDateTime] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class CalendarOp(BaseModel):
    op: OpType
    event: Optional[CalendarEvent] = None
    event_id: Optional[str] = Field(default=None, alias="event_id")
    diff: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    def validate_operation(self) -> None:
        if self.op is OpType.CREATE and self.event is None:
            raise ValueError("CREATE operation requires 'event' field")
        if self.op is OpType.UPDATE and not self.event_id:
            raise ValueError("UPDATE operation requires 'event_id' field")
        if self.op is OpType.DELETE and not self.event_id:
            raise ValueError("DELETE operation requires 'event_id' field")


class PlanDiff(BaseModel):
    operations: List[CalendarOp] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    def validate_all_operations(self) -> None:
        for op in self.operations:
            op.validate_operation()

    @property
    def operation_count(self) -> Dict[str, int]:
        counts = {"create": 0, "update": 0, "delete": 0}
        for op in self.operations:
            counts[op.op.value] += 1
        return counts

    def __str__(self) -> str:
        counts = self.operation_count
        return (
            f"PlanDiff({counts['create']} create, "
            f"{counts['update']} update, {counts['delete']} delete)"
        )


__all__ = [
    "CalendarEvent",
    "CalendarOp",
    "EventDateTime",
    "OpType",
    "PlanDiff",
]
