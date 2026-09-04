"""At capture the host asks memory about the sleep window before the user is asked (#251)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_host import (
    AdaptiveDependencyUnavailable,
    HostPlanningContext,
)


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


class _Store:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[dict[str, Any]] = []
        self.suspended_calls: list[tuple[Any, Any]] = []

    async def query_constraints(self, *, filters, limit):  # noqa: ANN001
        self.queries.append({"filters": filters, "limit": limit})
        return self.rows

    async def count_suspended(self, planned_day, day_type):  # noqa: ANN001
        self.suspended_calls.append((planned_day, day_type))
        return 0


class _Runtime:
    """Deliberately no timeboxing_calendar_id: the skeleton stage must not want one."""

    def __init__(self, *, store, client) -> None:  # noqa: ANN001
        if store is not None:
            self.timeboxing_constraint_store = store
        if client is not None:
            self.timeboxing_intent_model_client = client


class _Progress:
    async def emit(self, _event: object) -> None:
        return None


def _now() -> datetime:
    return datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)  # noqa: UP017


def _snapshot(*facts: PlanningFact) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=3,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 2), timezone="Europe/Amsterdam", lock_revision=1
        ),
        facts=list(facts),
    )


BEDTIME = {"uid": "c-bedtime", "name": "Bedtime", "description": "Asleep by 00:30, up 08:30."}


@pytest.mark.asyncio
async def test_a_bedtime_rule_on_record_arrives_as_a_frame_fact_at_capture() -> None:
    store = _Store([BEDTIME])
    client = _SchemaOutputClient(
        {"stated": True, "wake": "08:30", "sleep": "00:30", "basis_uids": ["c-bedtime"]}
    )
    host = HostPlanningContext(_Runtime(store=store, client=client), now=_now)

    context = await host.resolve(
        _snapshot(), target=ArtifactKind.SKELETON, progress=_Progress()
    )

    assert [fact.kind for fact in context.facts] == [FactKind.DAY_FRAME]
    assert context.facts[0].source == "constraint_memory"
    assert store.queries == [
        {
            "filters": {
                "planned_day": "2026-09-02",
                "day_type": "working",
                "require_active": True,
            },
            "limit": 200,
        }
    ]


@pytest.mark.asyncio
async def test_a_corpus_that_says_nothing_about_sleep_leaves_the_question_open() -> None:
    store = _Store([{"uid": "c-oats", "name": "Oats", "description": "Oats before gym."}])
    client = _SchemaOutputClient(
        {"stated": False, "wake": None, "sleep": None, "basis_uids": []}
    )
    host = HostPlanningContext(_Runtime(store=store, client=client), now=_now)

    context = await host.resolve(
        _snapshot(), target=ArtifactKind.SKELETON, progress=_Progress()
    )

    assert context.facts == []


@pytest.mark.asyncio
async def test_a_frame_the_user_already_stated_asks_no_model_but_still_hands_back_the_rules() -> None:
    """The user's word on the frame stands; re-deriving it from the corpus could
    only contradict them, so no model call is made. The rules themselves are
    still fetched and returned -- Stage 1 needs them regardless of whether the
    frame question was already settled (#262)."""

    store = _Store([BEDTIME])
    client = _SchemaOutputClient()
    host = HostPlanningContext(_Runtime(store=store, client=client), now=_now)
    stated = PlanningFact(
        fact_id="f1",
        kind=FactKind.DAY_FRAME,
        value={"wake": "09:00", "sleep": "01:00"},
        source="user",
    )

    context = await host.resolve(
        _snapshot(stated), target=ArtifactKind.SKELETON, progress=_Progress()
    )

    assert context.facts == []
    assert context.applicable_constraints == [BEDTIME]
    assert context.suspended_constraint_count == 0
    assert len(store.queries) == 1
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_missing_model_client_is_loud_not_an_empty_corpus() -> None:
    """A host that cannot judge must not look like a corpus with no bedtime rule."""

    host = HostPlanningContext(_Runtime(store=_Store([BEDTIME]), client=None), now=_now)

    with pytest.raises(AdaptiveDependencyUnavailable):
        await host.resolve(_snapshot(), target=ArtifactKind.SKELETON, progress=_Progress())


@pytest.mark.asyncio
async def test_missing_constraint_memory_is_loud_at_capture_too() -> None:
    host = HostPlanningContext(
        _Runtime(store=None, client=_SchemaOutputClient()), now=_now
    )

    with pytest.raises(AdaptiveDependencyUnavailable):
        await host.resolve(_snapshot(), target=ArtifactKind.SKELETON, progress=_Progress())
