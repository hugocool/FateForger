"""
FateForger Runtime - Orchestration code for AutoGen Sequential Workflow.

This module contains the runtime orchestration components that coordinate
multi-agent calendar operations using AutoGen's runtime topic system.
"""

from .sync_stub import (
    DiffMessage,
    OpMessage,
    create_workflow_runtime,
    sync_plan_to_calendar,
)

__all__ = [
    "sync_plan_to_calendar",
    "create_workflow_runtime",
    "DiffMessage",
    "OpMessage",
]
