import pytest

pytest.importorskip("autogen_agentchat")

from datetime import date

from fateforger.agents.timeboxing.constraint_retriever import ConstraintRetriever
from fateforger.agents.timeboxing.contracts import BlockPlan, Immovable, SleepTarget, WorkWindow
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


class _FakeConstraintClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def query_types(self, *, stage: str | None = None, event_types: list[str] | None = None):
        self.calls.append(("query_types", {"stage": stage, "event_types": event_types}))
        return [{"type_id": f"type_{i}", "count": 100 - i} for i in range(50)]

    async def query_constraints(
        self,
        *,
        filters: dict,
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ):
        self.calls.append(
            (
                "query_constraints",
                {
                    "filters": filters,
                    "type_ids": type_ids,
                    "tags": tags,
                    "sort": sort,
                    "limit": limit,
                },
            )
        )
        return [{"uid": "u1", "name": "c1", "description": "d1"}]


@pytest.mark.asyncio
async def test_retriever_builds_type_id_narrowed_query():
    retriever = ConstraintRetriever(max_type_ids=5, query_limit=20)
    client = _FakeConstraintClient()

    plan, records = await retriever.retrieve(
        client=client,  # type: ignore[arg-type]
        stage=TimeboxingStage.SKELETON,
        planned_day=date(2026, 1, 21),
        work_window=WorkWindow(start="09:00", end="18:00"),
        sleep_target=SleepTarget(start=None, end=None, hours=None),
        immovables=[Immovable(title="Meeting", start="10:00", end="10:30")],
        block_plan=BlockPlan(deep_blocks=2, shallow_blocks=1, block_minutes=60, focus_theme=None),
        frame_facts={"commutes": [{"label": "Office", "duration_min": 30}]},
    )

    assert plan.stage == TimeboxingStage.SKELETON
    assert plan.limit == 20
    assert len(plan.type_ids) == 5
    assert records and records[0]["uid"] == "u1"

    assert client.calls[0][0] == "query_types"
    assert client.calls[1][0] == "query_constraints"
    qc = client.calls[1][1]
    assert qc["filters"]["stage"] == TimeboxingStage.SKELETON.value
    assert qc["limit"] == 20
    assert qc["type_ids"] == plan.type_ids

