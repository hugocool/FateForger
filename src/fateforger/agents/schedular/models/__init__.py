"""
FateForger Contracts - Pydantic schemas & messages for AutoGen Sequential Workflow.

This module contains all the data structures used for inter-agent communication
and structured LLM output in the FateForger calendar automation system.
"""

from .calendar_diff import CalendarOp, OpType, PlanDiff
from .calendar_event import (
    CalendarEvent,
    CreatorOrganizer,
    EventDateTime,
    ExtendedProperties,
    Reminders,
)

__all__ = [
    "CalendarEvent",
    "EventDateTime",
    "CreatorOrganizer",
    "Reminders",
    "ExtendedProperties",
    "CalendarOp",
    "PlanDiff",
    "OpType",
]
