import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent
from fateforger.agents.timeboxing.constraint_retriever import ConstraintRetriever
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


class _FakeConstraintClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def query_types(self, *, stage: str | None = None, event_types: list[str] | None = None):
        self.calls.append(("query_types", {"stage": stage, "event_types": event_types}))
        return [{"type_id": "t1", "count": 10}]

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
        return []


@pytest.mark.asyncio
async def test_fetch_durable_constraints_uses_type_routing() -> None:
    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._constraint_retriever = ConstraintRetriever(max_type_ids=3, query_limit=10)
    fake_client = _FakeConstraintClient()
    agent._ensure_constraint_memory_client = lambda: fake_client  # type: ignore[assignment]

    session = Session(thread_ts="t1", channel_id="c1", user_id="u1", planned_date="2026-01-21")
    session.frame_facts = {"work_window": {"start": "09:00", "end": "18:00"}, "immovables": []}
    session.input_facts = {"block_plan": {"deep_blocks": 1, "shallow_blocks": 0, "block_minutes": 60}}

    out = await agent._fetch_durable_constraints(session, stage=TimeboxingStage.SKELETON)
    assert out == []
    assert [c[0] for c in fake_client.calls] == ["query_types", "query_constraints"]
    assert fake_client.calls[1][1]["type_ids"] == ["t1"]
    assert fake_client.calls[1][1]["filters"]["stage"] == TimeboxingStage.SKELETON.value

