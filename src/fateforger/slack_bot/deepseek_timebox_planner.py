"""Host-owned context boundary for adaptive DeepSeek planning turns.

The kernel supplies durable typed session state. This adapter refreshes the
two external read models for the exact locked day and hands one complete brief
to a fresh harness run. Slack transcripts and prior assistant messages have no
parameter and therefore cannot become planner state through this seam.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from fateforger.agents.timeboxing.adaptive_timeboxing import ProgressSink
from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningBrief,
    PlanningResult,
)
from fateforger.slack_bot import harness_bridge
from fateforger.slack_bot.timebox_candidate import ValidatedTimeboxCandidate
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


class HarnessBridgeRunner:
    """Run one planning turn as a fresh harness process behind the result file.

    The brief is the whole context. This class deliberately has no parameter
    for a Slack transcript, an assistant message, or an utterance, so none of
    those can reach the model through this seam -- and none is needed, because
    what Hugo said already reached the kernel as typed facts.

    ``harness_bridge.ask`` is synchronous and owns a child process, so it runs
    on a worker thread; progress arrives on that thread and is handed back to
    the loop the turn is running on.
    """

    def __init__(
        self,
        *,
        model: str = harness_bridge.PLANNING_MODEL,
        reasoning: str = harness_bridge.PLANNING_REASONING,
        ask: Callable[..., harness_bridge.HarnessReply] = harness_bridge.ask,
    ) -> None:
        self._model = model
        self._reasoning = reasoning
        self._ask = ask

    async def run(self, brief: PlanningBrief, progress: ProgressSink) -> PlanningResult:
        loop = asyncio.get_running_loop()

        def forward(event: object) -> None:
            try:
                asyncio.run_coroutine_threadsafe(progress.emit(event), loop)
            except RuntimeError:
                # The turn has already finished with this run's outcome.
                # Progress observes it and must never decide it.
                logger.warning("planner progress arrived after its turn closed")

        reply = await asyncio.to_thread(
            self._ask,
            "",
            session_id=brief.session_key,
            planning_brief=brief,
            model=self._model,
            reasoning=self._reasoning,
            on_event=forward,
        )
        if reply.planning_result is None:
            # ``ask`` raises before this on a brief with no result. Kept so a
            # substituted ``ask`` cannot return an empty turn as a successful
            # one, which is the failure the whole seam exists to make loud.
            raise DependencyUnavailable("planner produced no typed result")
        return _with_commit_basis(reply.planning_result, reply.validated_candidate)


def _with_commit_basis(
    result: PlanningResult, candidate: ValidatedTimeboxCandidate | None
) -> PlanningResult:
    """Attach the tmbx patch the host watched go through, to the candidate.

    A candidate is approved as prose and committed as a patch, and only the
    model produced the prose. The patch came back from the ``plan_apply`` the
    planner just made, and the host captured it by watching tmbx rather than by
    asking -- so the model writes what a human reads, the host attaches what a
    machine replays, and neither half can forge the other.

    Without this the four commit-basis keys are simply absent, the card that
    offers the plan arms an empty candidate, and ``plan_commit({}, {})`` is
    refused as ``malformed_input``: a plan that could be shown and approved and
    never committed. The keys are named once, by ``as_commit_basis``, so this
    writer and the two readers cannot disagree about them.

    A candidate draft with nothing captured is left exactly as it came. The
    commit port already refuses a candidate it cannot match, and inventing an
    empty basis here would turn a missing patch into a forged one.
    """

    if candidate is None:
        return result
    updates = []
    for draft in result.artifact_updates:
        if draft.kind is not ArtifactKind.VALIDATED_CANDIDATE or not isinstance(
            draft.payload, dict
        ):
            updates.append(draft)
            continue
        updates.append(
            draft.model_copy(
                update={
                    "payload": {**draft.payload, **candidate.as_commit_basis()}
                }
            )
        )
    return result.model_copy(update={"artifact_updates": updates})


__all__ = [
    "ConstraintReader",
    "DeepSeekTimeboxPlanner",
    "DependencyUnavailable",
    "HarnessBridgeRunner",
    "HarnessRunner",
    "UnavailableConstraintReader",
]
