from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class OpType(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class EventDateTime(BaseModel):
    date_time: Optional[datetime] = Field(default=None, alias="dateTime")
    time_zone: Optional[str] = Field(default=None, alias="timeZone")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


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
