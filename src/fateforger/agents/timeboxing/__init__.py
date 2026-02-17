"""Timeboxing agent package."""

from .actions import TimeboxAction
from .messages import TimeboxingUpdate, TimeboxPatchRecord
from .notebook_entrypoints import (
    GraphTurnTrace,
    MethodLocation,
    Stage3DraftTrace,
    create_agent,
    create_session,
    run_graph_turn,
    run_stage3_draft,
    stage3_framework_report,
    stage3_method_locations,
    stage3_source_snippets,
)
from .patching import TimeboxPatcher
from .preferences import (
    Constraint,
    ConstraintBase,
    ConstraintBatch,
    ConstraintStore,
    ensure_constraint_schema,
)
from .submitter import CalendarSubmitter
from .sync_engine import (
    SyncOp,
    SyncOpType,
    SyncTransaction,
    execute_sync,
    gcal_response_to_tb_plan,
    gcal_response_to_tb_plan_with_identity,
    plan_sync,
    undo_sync,
)
from .tb_models import ET, TBEvent, TBPlan
from .tb_ops import TBPatch, apply_tb_ops
from .timebox import Timebox, tb_plan_to_timebox, timebox_to_tb_plan

__all__ = [
    "CalendarSubmitter",
    "Constraint",
    "ConstraintBase",
    "ConstraintBatch",
    "ConstraintStore",
    "ET",
    "GraphTurnTrace",
    "MethodLocation",
    "Stage3DraftTrace",
    "SyncOp",
    "SyncOpType",
    "SyncTransaction",
    "TBEvent",
    "TBPatch",
    "TBPlan",
    "Timebox",
    "TimeboxAction",
    "TimeboxPatcher",
    "TimeboxPatchRecord",
    "TimeboxingUpdate",
    "apply_tb_ops",
    "create_agent",
    "create_session",
    "ensure_constraint_schema",
    "execute_sync",
    "gcal_response_to_tb_plan",
    "gcal_response_to_tb_plan_with_identity",
    "plan_sync",
    "run_graph_turn",
    "run_stage3_draft",
    "stage3_framework_report",
    "stage3_method_locations",
    "stage3_source_snippets",
    "tb_plan_to_timebox",
    "timebox_to_tb_plan",
    "undo_sync",
]
