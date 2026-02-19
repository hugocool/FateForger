from __future__ import annotations

from datetime import date

import pytest

from fateforger.agents.timeboxing.constraint_retriever import (
    STARTUP_PREFETCH_TAG,
    ConstraintRetriever,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


class _DummyClient:
    def __init__(self, *, startup_rows: list[dict], broad_rows: list[dict]) -> None:
        self.startup_rows = startup_rows
        self.broad_rows = broad_rows
        self.query_types_calls: list[dict] = []
        self.query_constraints_calls: list[dict] = []

    async def query_types(self, *, stage: str | None, event_types: list[str] | None):
        self.query_types_calls.append(
            {
                "stage": stage,
                "event_types": list(event_types or []),
            }
        )
        return [{"type_id": "sleep", "name": "Sleep", "count": 10}]

    async def query_constraints(
        self,
        *,
        filters: dict,
        type_ids: list[str] | None,
        tags: list[str] | None,
        sort,
        limit: int,
    ):
        self.query_constraints_calls.append(
            {
                "filters": dict(filters),
                "type_ids": list(type_ids or []),
                "tags": list(tags or []),
                "limit": limit,
                "sort": sort,
            }
        )
        if tags == [STARTUP_PREFETCH_TAG]:
            return list(self.startup_rows)
        return list(self.broad_rows)


@pytest.mark.asyncio
async def test_collect_retriever_prefers_startup_prefetch_tagged_rows() -> None:
    retriever = ConstraintRetriever(max_type_ids=5, query_limit=25)
    client = _DummyClient(
        startup_rows=[{"uid": "u1", "name": "Sleep default"}],
        broad_rows=[{"uid": "u2", "name": "Fallback row"}],
    )

    _plan, rows = await retriever.retrieve(
        client=client,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        planned_day=date(2026, 2, 18),
        work_window=None,
        sleep_target=None,
        immovables=[],
        block_plan=None,
        frame_facts={},
    )

    assert rows == [{"uid": "u1", "name": "Sleep default"}]
    assert client.query_types_calls == []
    assert len(client.query_constraints_calls) == 1
    call = client.query_constraints_calls[0]
    assert call["tags"] == [STARTUP_PREFETCH_TAG]
    assert call["filters"]["event_types_any"] == []
    assert call["filters"]["scopes_any"] == ["profile", "datespan"]


@pytest.mark.asyncio
async def test_collect_retriever_falls_back_to_broad_when_no_startup_rows() -> None:
    retriever = ConstraintRetriever(max_type_ids=5, query_limit=25)
    client = _DummyClient(
        startup_rows=[],
        broad_rows=[{"uid": "u2", "name": "Fallback row"}],
    )

    _plan, rows = await retriever.retrieve(
        client=client,
        stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        planned_day=date(2026, 2, 18),
        work_window=None,
        sleep_target=None,
        immovables=[],
        block_plan=None,
        frame_facts={},
    )

    assert rows == [{"uid": "u2", "name": "Fallback row"}]
    assert client.query_types_calls == []
    assert len(client.query_constraints_calls) == 2
    assert client.query_constraints_calls[0]["tags"] == [STARTUP_PREFETCH_TAG]
    assert client.query_constraints_calls[1]["tags"] == []


@pytest.mark.asyncio
async def test_non_collect_retriever_does_not_use_startup_prefetch_tag() -> None:
    retriever = ConstraintRetriever(max_type_ids=5, query_limit=25)
    client = _DummyClient(
        startup_rows=[{"uid": "u1", "name": "Should not be used"}],
        broad_rows=[{"uid": "u2", "name": "Refine row"}],
    )

    _plan, rows = await retriever.retrieve(
        client=client,
        stage=TimeboxingStage.REFINE,
        planned_day=date(2026, 2, 18),
        work_window=None,
        sleep_target=None,
        immovables=[],
        block_plan=None,
        frame_facts={},
    )

    assert rows == [{"uid": "u2", "name": "Refine row"}]
    assert len(client.query_types_calls) == 1
    assert len(client.query_constraints_calls) == 1
    call = client.query_constraints_calls[0]
    assert call["tags"] == []
    assert "scopes_any" not in call["filters"]
