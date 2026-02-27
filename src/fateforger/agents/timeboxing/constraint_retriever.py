"""Gap-driven durable constraint retrieval for timeboxing.

This module provides a deterministic retriever that queries the configured
durable-memory store using structured filters and routing metadata.

It is "gap-driven" in the sense that it derives a small query plan from the
current planning context (stage + presence of gaps/blocks/immovables) and uses
`constraint_query_types` to select the most relevant constraint type_ids before
querying constraints.

This is not "NLU": it does not interpret free-form user text. Natural language
interpretation remains LLM-driven (see `nlu.py`).
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, List, Optional, Sequence

from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.constants import TIMEBOXING_LIMITS
from fateforger.agents.timeboxing.contracts import BlockPlan, Immovable, SleepTarget, WorkWindow
from fateforger.agents.timeboxing.durable_constraint_store import DurableConstraintStore
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage

STARTUP_PREFETCH_TAG = "startup_prefetch"


class ConstraintEventType(str, Enum):
    """Constraint event types used by durable-memory filtering."""

    MEETING = "M"
    COMMUTE = "C"
    DEEP_WORK = "DW"
    SHALLOW_WORK = "SW"
    HABIT = "H"
    REST = "R"
    BUFFER = "BU"
    BREAK = "BG"
    PREP = "PR"


class ConstraintTypeInfo(BaseModel):
    """Type record returned by `constraint_query_types`."""

    type_id: Optional[str] = None
    name: Optional[str] = None
    rule_shape: Optional[str] = None
    count: int = 0
    requires_windows: bool = False
    requires_scalars: List[str] = Field(default_factory=list)


class ConstraintQueryPlan(BaseModel):
    """Deterministic query plan for durable constraint retrieval."""

    stage: TimeboxingStage
    planned_date: str
    event_types_any: List[str] = Field(default_factory=list)
    type_ids: List[str] = Field(default_factory=list)
    limit: int


class ConstraintRetriever:
    """Gap-driven retriever for durable constraints."""

    def __init__(
        self,
        *,
        max_type_ids: int = TIMEBOXING_LIMITS.durable_constraint_type_ids_limit,
        query_limit: int = TIMEBOXING_LIMITS.durable_constraint_query_limit,
    ) -> None:
        """Create a retriever with size limits.

        Args:
            max_type_ids: Maximum number of constraint type IDs to request.
            query_limit: Maximum constraints to request from durable memory.
        """
        self._max_type_ids = max_type_ids
        self._query_limit = query_limit

    def build_plan(
        self,
        *,
        stage: TimeboxingStage,
        planned_date: str,
        work_window: WorkWindow | None,
        sleep_target: SleepTarget | None,
        immovables: Sequence[Immovable],
        block_plan: BlockPlan | None,
        frame_facts: dict[str, Any],
    ) -> ConstraintQueryPlan:
        """Build a deterministic query plan from planning context."""
        event_types = self._derive_event_types(
            stage=stage,
            work_window=work_window,
            sleep_target=sleep_target,
            immovables=immovables,
            block_plan=block_plan,
            frame_facts=frame_facts,
        )
        return ConstraintQueryPlan(
            stage=stage,
            planned_date=planned_date,
            event_types_any=event_types,
            type_ids=[],
            limit=self._query_limit,
        )

    async def retrieve(
        self,
        *,
        client: DurableConstraintStore,
        stage: TimeboxingStage,
        planned_day: date,
        work_window: WorkWindow | None,
        sleep_target: SleepTarget | None,
        immovables: Sequence[Immovable],
        block_plan: BlockPlan | None,
        frame_facts: dict[str, Any],
    ) -> tuple[ConstraintQueryPlan, list[dict[str, Any]]]:
        """Retrieve durable constraints for a stage, returning (plan, raw records)."""
        planned_date = planned_day.isoformat()
        plan = self.build_plan(
            stage=stage,
            planned_date=planned_date,
            work_window=work_window,
            sleep_target=sleep_target,
            immovables=immovables,
            block_plan=block_plan,
            frame_facts=frame_facts,
        )
        query_event_types = list(plan.event_types_any or [])
        if stage == TimeboxingStage.COLLECT_CONSTRAINTS:
            # Stage 1 prefetch is deterministic and startup-focused; event-type routing
            # is too restrictive for defaults like sleep/work-window.
            query_event_types = []
        if stage == TimeboxingStage.COLLECT_CONSTRAINTS:
            # Stage 1 startup prefetch is deterministic and startup-tag driven; avoid
            # extra type lookup RPCs on the critical path.
            type_ids = []
        else:
            type_ids = await self._select_type_ids(
                client=client,
                stage=stage,
                event_types=query_event_types,
                max_type_ids=self._max_type_ids,
            )
        plan.type_ids = type_ids
        filters = {
            "as_of": planned_date,
            "stage": stage.value,
            "event_types_any": query_event_types,
            "statuses_any": ["locked", "proposed"],
            "require_active": True,
        }
        if stage == TimeboxingStage.COLLECT_CONSTRAINTS:
            filters["scopes_any"] = ["profile", "datespan"]
            startup_records = await client.query_constraints(
                filters=filters,
                type_ids=plan.type_ids,
                tags=[STARTUP_PREFETCH_TAG],
                sort=[["Status", "descending"]],
                limit=plan.limit,
            )
            if startup_records:
                return plan, self._dedupe_rows_by_uid(startup_records)
        records = await client.query_constraints(
            filters=filters,
            type_ids=plan.type_ids,
            tags=None,
            sort=[["Status", "descending"]],
            limit=plan.limit,
        )
        return plan, records

    @staticmethod
    def _dedupe_rows_by_uid(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate row dicts by uid while preserving first-seen order."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("uid") or "").strip()
            if uid:
                if uid in seen:
                    continue
                seen.add(uid)
            out.append(row)
        return out

    async def _select_type_ids(
        self,
        *,
        client: DurableConstraintStore,
        stage: TimeboxingStage,
        event_types: Sequence[str],
        max_type_ids: int,
    ) -> list[str]:
        """Select relevant type_ids via `constraint_query_types`."""
        raw = await client.query_types(stage=stage.value, event_types=list(event_types or []))
        parsed = [ConstraintTypeInfo.model_validate(item) for item in (raw or [])]
        type_ids = [t.type_id for t in parsed if isinstance(t.type_id, str) and t.type_id]
        # Keep order as returned (already ranked by count).
        return list(type_ids)[: max(0, max_type_ids)]

    def _derive_event_types(
        self,
        *,
        stage: TimeboxingStage,
        work_window: WorkWindow | None,
        sleep_target: SleepTarget | None,
        immovables: Sequence[Immovable],
        block_plan: BlockPlan | None,
        frame_facts: dict[str, Any],
    ) -> list[str]:
        """Derive a small set of Notion event type codes relevant for the stage."""
        has_immovables = bool(list(immovables or []))
        has_blocks = bool(
            (block_plan and ((block_plan.deep_blocks or 0) > 0 or (block_plan.shallow_blocks or 0) > 0))
        )
        has_commutes = bool(frame_facts.get("commutes") or [])
        has_habits = bool(frame_facts.get("habits") or [])
        has_sleep = sleep_target is not None
        has_work_window = work_window is not None
        has_gaps = has_work_window and (has_immovables or has_blocks)

        base: set[str] = set()
        if stage in (TimeboxingStage.CAPTURE_INPUTS, TimeboxingStage.SKELETON, TimeboxingStage.REFINE, TimeboxingStage.REVIEW_COMMIT):
            base.update([ConstraintEventType.DEEP_WORK.value, ConstraintEventType.SHALLOW_WORK.value])
        if stage in (TimeboxingStage.COLLECT_CONSTRAINTS, TimeboxingStage.SKELETON, TimeboxingStage.REFINE, TimeboxingStage.REVIEW_COMMIT):
            if has_immovables:
                base.add(ConstraintEventType.MEETING.value)
            if has_commutes:
                base.add(ConstraintEventType.COMMUTE.value)
            if has_sleep:
                base.add(ConstraintEventType.REST.value)
            if has_habits:
                base.add(ConstraintEventType.HABIT.value)
        if stage in (TimeboxingStage.SKELETON, TimeboxingStage.REFINE, TimeboxingStage.REVIEW_COMMIT) and has_gaps:
            base.update([ConstraintEventType.BUFFER.value, ConstraintEventType.BREAK.value])

        # Always allow "prep" constraints to be retrieved for scheduling stages.
        if stage in (TimeboxingStage.SKELETON, TimeboxingStage.REFINE, TimeboxingStage.REVIEW_COMMIT):
            base.add(ConstraintEventType.PREP.value)

        return sorted(base)


__all__ = [
    "ConstraintEventType",
    "ConstraintQueryPlan",
    "ConstraintRetriever",
]
