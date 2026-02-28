"""AutoGen Memory component for durable timeboxing constraints.

This component enriches model context with relevant durable constraints derived
from planning state (stage/date/event types) before tool/LLM reasoning.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from autogen_core import CancellationToken
from autogen_core.memory import Memory, MemoryContent, MemoryQueryResult, UpdateContextResult
from autogen_core.model_context import ChatCompletionContext
from autogen_core.models import SystemMessage
from pydantic import ValidationError

from .durable_constraint_store import DurableConstraintStore


class ConstraintPlanningMemory(Memory):
    """Inject durable constraints into model context using planning-state filters."""

    component_type = "memory"

    def __init__(
        self,
        *,
        store_provider: Callable[[], DurableConstraintStore | None],
        max_items: int = 12,
    ) -> None:
        self._store_provider = store_provider
        self._max_items = max(1, int(max_items))
        self._planning_state: dict[str, Any] = {}

    def set_planning_state(self, state: dict[str, Any] | None) -> None:
        """Update the planning state used for next retrieval."""
        self._planning_state = dict(state or {})

    async def update_context(
        self,
        model_context: ChatCompletionContext,
    ) -> UpdateContextResult:
        store = self._store_provider()
        if store is None:
            return UpdateContextResult(memories=MemoryQueryResult(results=[]))

        today = datetime.now(timezone.utc).date().isoformat()
        stage = str(self._planning_state.get("stage") or "").strip() or None
        event_types_raw = self._planning_state.get("event_types") or []
        event_types = [str(item).strip() for item in event_types_raw if str(item).strip()]
        as_of = str(self._planning_state.get("planned_date") or today).strip() or today

        filters: dict[str, Any] = {
            "as_of": as_of,
            "require_active": True,
            "statuses_any": ["locked", "proposed"],
        }
        if stage:
            filters["stage"] = stage
        if event_types:
            filters["event_types_any"] = event_types

        rows = await store.query_constraints(
            filters=filters,
            type_ids=None,
            tags=None,
            sort=[["Status", "descending"], ["Name", "ascending"]],
            limit=self._max_items,
        )
        memories: list[MemoryContent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            memories.append(
                MemoryContent(
                    content=json.dumps(row, ensure_ascii=False),
                    mime_type="application/json",
                    metadata={"uid": row.get("uid"), "kind": "timeboxing_constraint"},
                )
            )
        if memories:
            lines: list[str] = []
            for idx, row in enumerate(rows[: self._max_items], start=1):
                name = str(row.get("name") or "Constraint").strip()
                description = str(row.get("description") or "").strip()
                if description:
                    lines.append(f"{idx}. {name}: {description}")
                else:
                    lines.append(f"{idx}. {name}")
            if lines:
                await model_context.add_message(
                    SystemMessage(
                        content=(
                            "Relevant durable constraints for this stage:\n"
                            + "\n".join(lines)
                        )
                    )
                )
        return UpdateContextResult(memories=MemoryQueryResult(results=memories))

    async def query(
        self,
        query: str | MemoryContent,
        cancellation_token: CancellationToken | None = None,
        **kwargs: Any,
    ) -> MemoryQueryResult:
        _ = cancellation_token, kwargs
        store = self._store_provider()
        if store is None:
            return MemoryQueryResult(results=[])
        text_query = query if isinstance(query, str) else str(query.content)
        rows = await store.query_constraints(
            filters={"text_query": text_query, "require_active": False},
            type_ids=None,
            tags=None,
            sort=[["Status", "descending"]],
            limit=self._max_items,
        )
        return MemoryQueryResult(
            results=[
                MemoryContent(
                    content=json.dumps(row, ensure_ascii=False),
                    mime_type="application/json",
                    metadata={"uid": row.get("uid"), "kind": "timeboxing_constraint"},
                )
                for row in rows
                if isinstance(row, dict)
            ]
        )

    async def add(
        self, content: MemoryContent, cancellation_token: CancellationToken | None = None
    ) -> None:
        _ = cancellation_token
        store = self._store_provider()
        if store is None:
            return
        if content.mime_type == "application/json":
            try:
                parsed = (
                    content.content
                    if isinstance(content.content, dict)
                    else json.loads(str(content.content))
                )
            except (TypeError, json.JSONDecodeError, ValidationError, ValueError):
                parsed = None
            if isinstance(parsed, dict) and (
                "constraint_record" in parsed or "name" in parsed
            ):
                record = (
                    parsed
                    if "constraint_record" in parsed
                    else {"constraint_record": parsed}
                )
                await store.upsert_constraint(
                    record=record,
                    event={"action": "memory_component_add"},
                )

    async def clear(self) -> None:
        self._planning_state = {}

    async def close(self) -> None:
        return


__all__ = ["ConstraintPlanningMemory"]
