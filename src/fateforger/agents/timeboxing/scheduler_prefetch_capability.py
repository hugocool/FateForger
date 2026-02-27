"""Scheduler prefetch orchestration capability for timeboxing sessions."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Protocol

from .constants import TIMEBOXING_TIMEOUTS
from .stage_gating import TimeboxingStage


class SessionPrefetchState(Protocol):
    """Minimal session state contract for prefetch orchestration."""

    stage: TimeboxingStage
    planned_date: str | None


QueueConstraintPrefetchFn = Callable[[SessionPrefetchState], None]
AwaitDurablePrefetchFn = Callable[..., Awaitable[None]]
EnsureCalendarImmovablesFn = Callable[..., Awaitable[None]]
PrefetchCalendarImmovablesFn = Callable[
    [SessionPrefetchState, str], Awaitable[None]
]
IsCollectStageLoadedFn = Callable[[SessionPrefetchState], bool]


class SchedulerPrefetchCapability:
    """Coordinates calendar + durable prefetch entrypoints for stages."""

    def __init__(
        self,
        *,
        queue_constraint_prefetch: QueueConstraintPrefetchFn,
        await_pending_durable_prefetch: AwaitDurablePrefetchFn,
        ensure_calendar_immovables: EnsureCalendarImmovablesFn,
        prefetch_calendar_immovables: PrefetchCalendarImmovablesFn,
        is_collect_stage_loaded: IsCollectStageLoadedFn,
    ) -> None:
        self._queue_constraint_prefetch = queue_constraint_prefetch
        self._await_pending_durable_prefetch = await_pending_durable_prefetch
        self._ensure_calendar_immovables = ensure_calendar_immovables
        self._prefetch_calendar_immovables = prefetch_calendar_immovables
        self._is_collect_stage_loaded = is_collect_stage_loaded

    def queue_initial_prefetch(
        self,
        *,
        session: SessionPrefetchState,
        planned_date: str,
    ) -> None:
        """Kick off non-blocking prefetch while waiting for session commit."""
        asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
        self._queue_constraint_prefetch(session)

    async def prime_committed_collect_context(
        self,
        *,
        session: SessionPrefetchState,
        blocking: bool = False,
    ) -> None:
        """Prime durable + calendar context for committed collect stage."""
        self._queue_constraint_prefetch(session)
        if not blocking:
            planned_date = (session.planned_date or "").strip()
            if planned_date:
                asyncio.create_task(
                    self._prefetch_calendar_immovables(session, planned_date)
                )
            return
        awaitables: list[Awaitable[None]] = [
            self._await_pending_durable_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            )
        ]
        planned_date = (session.planned_date or "").strip()
        if planned_date:
            awaitables.append(self._prefetch_calendar_immovables(session, planned_date))
        else:
            awaitables.append(
                self._ensure_calendar_immovables(
                    session,
                    timeout_s=TIMEBOXING_TIMEOUTS.calendar_prefetch_wait_s,
                )
            )
        await asyncio.gather(*awaitables)

    async def ensure_collect_stage_ready(
        self,
        *,
        session: SessionPrefetchState,
    ) -> None:
        """Block briefly when collect-stage durable constraints are still loading."""
        if (
            session.stage == TimeboxingStage.COLLECT_CONSTRAINTS
            and not self._is_collect_stage_loaded(session)
        ):
            await self._await_pending_durable_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
                timeout_s=TIMEBOXING_TIMEOUTS.pending_constraints_wait_s,
                fail_on_timeout=False,
            )


__all__ = [
    "SessionPrefetchState",
    "SchedulerPrefetchCapability",
]
