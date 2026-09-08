"""Whether the constraint corpus already states the day's sleep window (#251).

The frame is the user's to state. Before the session asks, the host checks
what is already on record -- and whether a rule states when the user wakes or
sleeps is a judgement about what the rule means, so it goes to a model. These
tests stub the model and assert the plumbing: what was sent, and what the
answer became.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from fateforger.agents.timeboxing.day_frame import (
    DayFrameJudge,
    day_frame_on_record,
)
from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningDay


class _SchemaOutputClient:
    def __init__(self, *responses: dict[str, object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[object, object]] = []

    async def create(self, messages, *, json_output):  # noqa: ANN001
        self.calls.append((messages, json_output))
        return SimpleNamespace(content=json.dumps(self._responses.pop(0)))


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 2), timezone="Europe/Amsterdam", lock_revision=1
    )


BEDTIME = {
    "uid": "c-bedtime",
    "name": "Bedtime",
    "description": "In bed by 00:30 on weekdays, up at 08:30.",
    "necessity": "must",
    "frame_slot": None,
}
OATS = {
    "uid": "c-oats",
    "name": "Oats before gym",
    "description": "Eat oats two hours before the gym.",
    "necessity": "should",
    "frame_slot": None,
}


@pytest.mark.asyncio
async def test_a_bedtime_rule_on_record_becomes_a_frame_fact_from_memory() -> None:
    client = _SchemaOutputClient(
        {"stated": True, "wake": "08:30", "sleep": "00:30", "basis_uids": ["c-bedtime"]}
    )

    fact = await DayFrameJudge(client).frame_on_record(
        day=_day(), constraints=[OATS, BEDTIME], session_key="C1:1.0"
    )

    assert fact is not None
    assert fact.kind is FactKind.DAY_FRAME
    assert fact.source == "constraint_memory"
    assert fact.value == {"wake": "08:30", "sleep": "00:30", "basis": ["c-bedtime"]}
    assert fact.fact_id == "frame:2026-09-02"


@pytest.mark.asyncio
async def test_a_corpus_without_a_bedtime_rule_yields_no_frame() -> None:
    """No fact means the requirement stays open and the user is asked."""

    client = _SchemaOutputClient(
        {"stated": False, "wake": None, "sleep": None, "basis_uids": []}
    )

    fact = await DayFrameJudge(client).frame_on_record(
        day=_day(), constraints=[OATS], session_key="C1:1.0"
    )

    assert fact is None


@pytest.mark.asyncio
async def test_one_stated_boundary_is_still_a_frame() -> None:
    """A bedtime with no wake time is half a frame, recorded as such, not guessed."""

    client = _SchemaOutputClient(
        {"stated": True, "wake": None, "sleep": "23:30", "basis_uids": ["c-bedtime"]}
    )

    fact = await DayFrameJudge(client).frame_on_record(
        day=_day(), constraints=[BEDTIME], session_key="C1:1.0"
    )

    assert fact is not None
    assert fact.value == {"wake": None, "sleep": "23:30", "basis": ["c-bedtime"]}


@pytest.mark.asyncio
async def test_stated_with_no_boundary_at_all_is_refused_not_recorded() -> None:
    """A frame that names no time would satisfy the requirement while saying nothing."""

    client = _SchemaOutputClient(
        {"stated": True, "wake": None, "sleep": None, "basis_uids": ["c-bedtime"]}
    )

    with pytest.raises(ValueError):
        await DayFrameJudge(client).frame_on_record(
            day=_day(), constraints=[BEDTIME], session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_a_basis_the_corpus_never_offered_is_refused() -> None:
    """The uids are host-minted; the model may only point at what it was shown."""

    client = _SchemaOutputClient(
        {"stated": True, "wake": "08:00", "sleep": None, "basis_uids": ["c-invented"]}
    )

    with pytest.raises(ValueError):
        await DayFrameJudge(client).frame_on_record(
            day=_day(), constraints=[BEDTIME], session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_a_time_the_schema_cannot_read_is_refused() -> None:
    client = _SchemaOutputClient(
        {"stated": True, "wake": "half eight", "sleep": None, "basis_uids": ["c-bedtime"]}
    )

    with pytest.raises(ValueError):
        await DayFrameJudge(client).frame_on_record(
            day=_day(), constraints=[BEDTIME], session_key="C1:1.0"
        )


@pytest.mark.asyncio
async def test_the_model_is_shown_the_day_and_the_rules_and_nothing_else_decides() -> None:
    """The judgement is the model's: no rule is filtered out before it is asked."""

    client = _SchemaOutputClient(
        {"stated": False, "wake": None, "sleep": None, "basis_uids": []}
    )

    await DayFrameJudge(client).frame_on_record(
        day=_day(), constraints=[OATS, BEDTIME], session_key="C1:1.0"
    )

    (messages, _schema), = client.calls
    sent = json.loads(messages[-1].content)
    assert sent["day"] == {"date": "2026-09-02", "weekday": "Wednesday", "day_type": "working"}
    assert [rule["uid"] for rule in sent["rules"]] == ["c-oats", "c-bedtime"]


@pytest.mark.asyncio
async def test_an_empty_corpus_asks_no_model() -> None:
    """Nothing on record is not a judgement; there is nothing to judge."""

    client = _SchemaOutputClient()

    fact = await DayFrameJudge(client).frame_on_record(
        day=_day(), constraints=[], session_key="C1:1.0"
    )

    assert fact is None
    assert client.calls == []


def test_the_fact_kind_is_the_one_the_requirement_names() -> None:
    fact = day_frame_on_record(
        day=_day(), wake="08:30", sleep="00:30", basis=["c-bedtime"]
    )
    assert fact.kind is FactKind.DAY_FRAME
