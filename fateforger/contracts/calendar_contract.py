"""
Calendar Contract - Data models for AutoGen Sequential Workflow (Ticket #1).

Defines the structured data contracts for multi-agent calendar pipeline:
- CalendarOp: Individual calendar operation (CREATE/UPDATE/DELETE)
- PlanDiff: Collection of calendar operations for LLM structured output

These models enable json_output= parameter in AutoGen for validated LLM responses
and deterministic multi-agent processing through runtime topics.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .calendar_event import CalendarEvent


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
