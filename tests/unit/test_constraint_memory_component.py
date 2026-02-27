from __future__ import annotations

import json

from autogen_core.memory import MemoryContent
from autogen_core.model_context import BufferedChatCompletionContext

from fateforger.agents.timeboxing.constraint_memory_component import ConstraintPlanningMemory


class _FakeStore:
    def __init__(self) -> None:
        self.rows = [
            {"uid": "tb1", "name": "Deep Work", "description": "Morning block", "status": "locked"}
        ]
        self.upserts: list[dict] = []

    async def query_constraints(self, *, filters, type_ids=None, tags=None, sort=None, limit=50):
        _ = (filters, type_ids, tags, sort, limit)
        return list(self.rows)

    async def upsert_constraint(self, *, record, event=None):
        self.upserts.append({"record": record, "event": event})
        return {"uid": "tb_new"}


async def test_constraint_planning_memory_updates_context() -> None:
    store = _FakeStore()
    memory = ConstraintPlanningMemory(store_provider=lambda: store, max_items=5)
    memory.set_planning_state({"stage": "Refine", "planned_date": "2026-02-26"})

    context = BufferedChatCompletionContext(buffer_size=10)
    result = await memory.update_context(context)
    messages = await context.get_messages()

    assert result.memories.results
    assert messages
    assert "Relevant durable constraints" in str(messages[-1].content)


async def test_constraint_planning_memory_query_and_add() -> None:
    store = _FakeStore()
    memory = ConstraintPlanningMemory(store_provider=lambda: store, max_items=5)

    queried = await memory.query("deep work")
    assert queried.results

    await memory.add(
        MemoryContent(
            content=json.dumps({"constraint_record": {"name": "New Rule"}}),
            mime_type="application/json",
            metadata={},
        )
    )
    assert store.upserts
