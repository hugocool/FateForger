"""Host-owned context boundary for adaptive DeepSeek planning turns.

The kernel supplies durable typed session state. This adapter refreshes the
two external read models for the exact locked day and hands one complete brief
to a fresh harness run. Slack transcripts and prior assistant messages have no
parameter and therefore cannot become planner state through this seam.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from fateforger.agents.timeboxing.adaptive_timeboxing import ProgressSink
from fateforger.agents.timeboxing.session_contracts import PlanningBrief, PlanningResult
from fateforger.slack_bot.tmbx_client import TmbxClient

logger = logging.getLogger(__name__)


class DependencyUnavailable(RuntimeError):
    """A required read or planner dependency is unavailable for this turn."""


class ConstraintReader(Protocol):
    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]: ...


class HarnessRunner(Protocol):
    """Task 8's result-file bridge plugs into this single async call."""

    async def run(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult: ...


class CalendarReader(Protocol):
    async def read(self, calendar_id: str, day: str) -> dict[str, Any]: ...


class UnavailableConstraintReader:
    """Preserve missing runtime memory as a typed dependency failure."""

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        _ = (filters, limit)
        raise DependencyUnavailable("constraint dependency unavailable")


class DeepSeekTimeboxPlanner:
    """Enrich a kernel brief and produce one schema-bound planner result."""

    def __init__(
        self,
        *,
        tmbx_client: TmbxClient | CalendarReader,
        constraint_reader: ConstraintReader,
        calendar_id: str,
        clock: Callable[[], datetime],
        harness_runner: HarnessRunner,
    ) -> None:
        if not calendar_id.strip():
            raise ValueError("calendar_id must not be empty")
        self._tmbx_client = tmbx_client
        self._constraint_reader = constraint_reader
        self._calendar_id = calendar_id
        self._clock = clock
        self._harness_runner = harness_runner

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult:
        """Run from only typed session state and exact host-owned snapshots."""

        locked_day = brief.locked_day
        day = locked_day.date.isoformat()
        try:
            calendar_snapshot = await self._tmbx_client.read(self._calendar_id, day)
        except Exception as exc:
            logger.warning(
                "adaptive planner calendar read unavailable error_type=%s",
                type(exc).__name__,
            )
            raise DependencyUnavailable("calendar dependency unavailable") from exc
        if calendar_snapshot.get("ok") is not True:
            raise DependencyUnavailable("calendar dependency unavailable")

        try:
            constraints = await self._constraint_reader.query_constraints(
                filters={
                    "planned_day": day,
                    "day_type": locked_day.day_type.value,
                    "require_active": True,
                },
                limit=200,
            )
        except DependencyUnavailable:
            raise
        except Exception as exc:
            logger.warning(
                "adaptive planner constraint read unavailable error_type=%s",
                type(exc).__name__,
            )
            raise DependencyUnavailable("constraint dependency unavailable") from exc

        complete_brief = brief.model_copy(
            update={
                "observed_at": self._clock(),
                "applicable_constraints": constraints,
                "calendar_snapshot": calendar_snapshot,
            },
            deep=True,
        )
        try:
            return await self._harness_runner.run(complete_brief, progress)
        except DependencyUnavailable:
            raise
        except Exception as exc:
            logger.warning(
                "adaptive planner harness unavailable error_type=%s",
                type(exc).__name__,
            )
            raise DependencyUnavailable("planner dependency unavailable") from exc


__all__ = [
    "ConstraintReader",
    "DeepSeekTimeboxPlanner",
    "DependencyUnavailable",
    "HarnessRunner",
    "UnavailableConstraintReader",
]
