from __future__ import annotations

import asyncio

import pytest

from fateforger.agents.timeboxing.scheduler_prefetch_capability import (
    SchedulerPrefetchCapability,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage


class _Session:
    def __init__(
        self,
        *,
        stage: TimeboxingStage = TimeboxingStage.COLLECT_CONSTRAINTS,
        planned_date: str = "2026-02-27",
    ):
        self.stage = stage
        self.planned_date = planned_date


@pytest.mark.asyncio
async def test_prime_committed_collect_context_non_blocking_prefetches_in_background() -> None:
    calls: list[str] = []
    gate = asyncio.Event()

    async def _await_prefetch(session, *, stage, **_kwargs):  # noqa: ARG001
        calls.append(f"await:{stage.value}")

    async def _ensure_calendar(session):  # noqa: ARG001
        calls.append("ensure_calendar")

    async def _prefetch_calendar(session, planned_date):  # noqa: ARG001
        calls.append(f"prefetch:{planned_date}")
        gate.set()

    def _queue_constraint(session):  # noqa: ARG001
        calls.append("queue_constraint")

    capability = SchedulerPrefetchCapability(
        queue_constraint_prefetch=_queue_constraint,
        await_pending_durable_prefetch=_await_prefetch,
        ensure_calendar_immovables=_ensure_calendar,
        prefetch_calendar_immovables=_prefetch_calendar,
        is_collect_stage_loaded=lambda s: False,
    )
    await capability.prime_committed_collect_context(session=_Session(), blocking=False)
    await asyncio.wait_for(gate.wait(), timeout=0.2)
    assert calls == [
        "queue_constraint",
        "prefetch:2026-02-27",
    ]


@pytest.mark.asyncio
async def test_prime_committed_collect_context_blocking_uses_bounded_calendar_ensure() -> None:
    calls: list[str] = []

    async def _await_prefetch(session, *, stage, **_kwargs):  # noqa: ARG001
        calls.append(f"await:{stage.value}")

    async def _ensure_calendar(session, **_kwargs):  # noqa: ARG001
        calls.append("ensure_calendar")

    async def _prefetch_calendar(session, planned_date):  # noqa: ARG001
        calls.append(f"prefetch:{planned_date}")

    capability = SchedulerPrefetchCapability(
        queue_constraint_prefetch=lambda s: calls.append("queue_constraint"),  # noqa: ARG005
        await_pending_durable_prefetch=_await_prefetch,
        ensure_calendar_immovables=_ensure_calendar,
        prefetch_calendar_immovables=_prefetch_calendar,
        is_collect_stage_loaded=lambda s: False,
    )
    await capability.prime_committed_collect_context(session=_Session(), blocking=True)

    assert calls[0] == "queue_constraint"
    assert "await:CollectConstraints" in calls
    assert "ensure_calendar" in calls
    assert not any(call.startswith("prefetch:") for call in calls)


@pytest.mark.asyncio
async def test_ensure_collect_stage_ready_waits_only_when_needed() -> None:
    calls: list[str] = []

    async def _await_prefetch(session, *, stage, **kwargs):  # noqa: ARG001
        calls.append(f"{stage.value}:{kwargs.get('fail_on_timeout')}")

    capability = SchedulerPrefetchCapability(
        queue_constraint_prefetch=lambda s: None,
        await_pending_durable_prefetch=_await_prefetch,
        ensure_calendar_immovables=lambda s: None,  # type: ignore[arg-type]
        prefetch_calendar_immovables=lambda s, d: None,  # type: ignore[arg-type]
        is_collect_stage_loaded=lambda s: False,
    )
    await capability.ensure_collect_stage_ready(session=_Session())
    await capability.ensure_collect_stage_ready(
        session=_Session(stage=TimeboxingStage.CAPTURE_INPUTS)
    )
    capability_loaded = SchedulerPrefetchCapability(
        queue_constraint_prefetch=lambda s: None,
        await_pending_durable_prefetch=_await_prefetch,
        ensure_calendar_immovables=lambda s: None,  # type: ignore[arg-type]
        prefetch_calendar_immovables=lambda s, d: None,  # type: ignore[arg-type]
        is_collect_stage_loaded=lambda s: True,
    )
    await capability_loaded.ensure_collect_stage_ready(session=_Session())
    assert calls == ["CollectConstraints:False"]


@pytest.mark.asyncio
async def test_prime_committed_collect_context_blocking_without_date_uses_ensure() -> None:
    calls: list[str] = []

    async def _await_prefetch(session, *, stage, **_kwargs):  # noqa: ARG001
        calls.append(f"await:{stage.value}")

    async def _ensure_calendar(session, **_kwargs):  # noqa: ARG001
        calls.append("ensure_calendar")

    capability = SchedulerPrefetchCapability(
        queue_constraint_prefetch=lambda s: calls.append("queue_constraint"),  # noqa: ARG005
        await_pending_durable_prefetch=_await_prefetch,
        ensure_calendar_immovables=_ensure_calendar,
        prefetch_calendar_immovables=lambda s, d: None,  # type: ignore[arg-type]
        is_collect_stage_loaded=lambda s: False,
    )
    await capability.prime_committed_collect_context(
        session=_Session(planned_date=""),
        blocking=True,
    )
    assert "ensure_calendar" in calls
