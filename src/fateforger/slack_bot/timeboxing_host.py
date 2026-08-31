"""What the adaptive planning kernel needs from the Slack host, and nothing else.

The kernel decides what a planning turn does. It cannot know what day it is in
the user's timezone, which calendar to read, where the constraint store lives,
or how to tell a person that a step is running -- so it takes those as ports and
the host supplies them. This module is that supply.

None of it is Slack routing, which is why it no longer sits in the file that
answers every Slack event: the router only has to build these and hand them
over. What stays behind there is the wiring -- which repository, which planner,
which requirement catalog -- because that is a fact about this deployment rather
than about planning.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    PlanningContext,
    TurnRequest,
)
from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    ArtifactKind,
    FactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    StartSession,
    TimeboxIntent,
)
from fateforger.core.config import settings

from .progress import HarnessProgressCard
from .progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)
from .progress_events import (
    ProgressSource,
    TimeboxProgressEvent,
)
from .progress_events import (
    ProgressStatus as TimeboxProgressStatus,
)
from .timebox_candidate import PendingTimeboxCandidates, ValidatedTimeboxCandidate

#: Kernel lifecycle phases worth showing. The kernel names its own phases, so
#: this maps identifiers this system minted -- anything outside the map is
#: dropped rather than guessed at, because the card is a claim about what
#: happened and not a log tail.
_KERNEL_PROGRESS_PHASES = {
    "resolving_context": TimeboxProgressPhase.LOADING_CONSTRAINTS,
    "planning": TimeboxProgressPhase.WEIGHING_OPTIONS,
}
_KERNEL_PROGRESS_STATUSES = {
    "started": TimeboxProgressStatus.STARTED,
    "succeeded": TimeboxProgressStatus.SUCCEEDED,
    "failed": TimeboxProgressStatus.FAILED,
}

class AdaptiveDependencyUnavailable(RuntimeError):
    """A host-owned read model this turn needs could not be reached."""


def planning_timezone() -> str:
    """The timezone a planning day is locked in.

    Reads the setting rather than a literal. This used to be a getattr with a
    hardcoded fallback, against a Settings that never defined the field -- so
    the fallback was the only branch that ever ran and PLANNING_TIMEZONE was
    silently inert. An empty setting raises instead of quietly picking a
    country for the user.
    """

    name = (settings.planning_timezone or "").strip()
    if not name:
        raise RuntimeError(
            "planning_timezone is empty; set PLANNING_TIMEZONE to an IANA name"
        )
    return name


class KernelProgressSink:
    """Project kernel lifecycle facts onto the existing timeboxing card.

    The kernel reports phase/status pairs of its own; this turns them into the
    same versioned `TimeboxProgressEvent` every other producer emits, so the
    card keeps one contract instead of growing a second, looser one beside it.
    """

    def __init__(self, card: HarnessProgressCard, *, session_key: str) -> None:
        self._card = card
        self._session_key = session_key
        self._sequence = 0

    async def emit(self, event: object) -> None:
        if isinstance(event, (TimeboxProgressEvent, str)):
            await self._card.handle(event)
            return
        if not isinstance(event, dict):
            return
        phase = _KERNEL_PROGRESS_PHASES.get(str(event.get("phase")))
        status = _KERNEL_PROGRESS_STATUSES.get(str(event.get("status")))
        if phase is None or status is None:
            return
        self._sequence += 1
        await self._card.handle(
            TimeboxProgressEvent(
                session_key=self._session_key,
                sequence=self._sequence,
                source=ProgressSource.RUNTIME,
                phase=phase,
                status=status,
            )
        )


class HostPlanningContext:
    """The planning day and the external read models, both host-owned.

    The weekday is arithmetic on the host clock. Asking a model which day it is
    is what turned Saturday 2026-08-29 into a Friday working day, and no amount
    of prompt wording repairs a question that should never have been asked.

    The clock arrives as an argument rather than being read here, because a day
    derived from `datetime.now` cannot be pinned: a suite that cannot say which
    Saturday it is testing asserts nothing about weekends.
    """

    def __init__(self, runtime, *, now: Callable[[], datetime]) -> None:
        self._runtime = runtime
        self._now = now

    async def propose_planning_day(self, request: TurnRequest) -> PlanningDay:
        tz_name = planning_timezone()
        today = self._now().astimezone(ZoneInfo(tz_name)).date()
        return PlanningDay.lock_default(
            value=today, timezone=tz_name, lock_revision=1
        )

    async def resolve(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        target: ArtifactKind,
        progress,
    ) -> PlanningContext:
        if target is not ArtifactKind.VALIDATED_CANDIDATE:
            # Stage 3 presents the skeleton. Reading the remote baseline here
            # would make the presentation stage touch the calendar, which is
            # exactly the boundary this route exists to hold.
            return PlanningContext()

        planning_day = snapshot.planning_day
        if planning_day is None:
            raise AdaptiveDependencyUnavailable("the planning day is not locked")
        day = planning_day.date.isoformat()

        calendar_id = (
            getattr(self._runtime, "timeboxing_calendar_id", "") or ""
        ).strip()
        if not calendar_id:
            # An invented calendar id is how a plan lands on a calendar nobody
            # reads. Absence stays absence.
            raise AdaptiveDependencyUnavailable("no calendar is configured")
        store = getattr(self._runtime, "timeboxing_constraint_store", None)
        if store is None:
            raise AdaptiveDependencyUnavailable("constraint memory is unavailable")

        from .tmbx_client import TmbxClient

        calendar_snapshot = await TmbxClient().read(calendar_id, day)
        constraints = await store.query_constraints(
            filters={
                "planned_day": day,
                "day_type": planning_day.day_type.value,
                "require_active": True,
            },
            limit=200,
        )
        return PlanningContext(
            facts=planning_facts(
                day=day, calendar_snapshot=calendar_snapshot, constraints=constraints
            ),
            applicable_constraints=constraints,
            calendar_snapshot=calendar_snapshot,
        )


def planning_facts(
    *, day: str, calendar_snapshot: Any, constraints: Any
) -> list[PlanningFact]:
    """Record that the two system fetches happened, without repeating them.

    These facts exist to satisfy readiness requirements, and `satisfied_by` is
    a presence test -- nothing anywhere reads their value. They used to carry
    the fetched payload itself, which the brief already carries in
    `applicable_constraints` and `calendar_snapshot`.

    That cost more than it looks. The whole brief is re-sent on every tool
    round-trip, so a duplicate is not paid once: measured on a real session,
    the constraints were 4,492 tokens in the field and the same 4,492 in the
    fact -- identical uid sets, identical bytes -- and at nine calls a session
    that is roughly 40k tokens of the same list, for nothing.

    So the fact now says *that* the fetch happened and how much it returned,
    which is what a requirement check needs, and the typed field that documents
    the shape stays the one place the data lives.
    """

    return [
        PlanningFact(
            fact_id=f"calendar:{day}",
            kind=FactKind.CALENDAR_SNAPSHOT,
            value={
                "fetched": True,
                "blocks": len((calendar_snapshot or {}).get("blocks") or []),
            },
            source="calendar",
        ),
        PlanningFact(
            fact_id=f"constraints:{day}",
            kind=FactKind.ACTIVE_CONSTRAINTS,
            value={"fetched": True, "count": len(constraints or [])},
            source="constraint_memory",
        ),
    ]


class PendingCandidateCommitPort:
    """Commit the exact candidate Slack displayed, through the existing gate.

    The kernel decides *when* a commit may happen; tmbx keeps the write and its
    idempotency digest. Nothing new reaches the calendar through this class.
    """

    def __init__(
        self,
        *,
        pending: PendingTimeboxCandidates,
        session_key: str,
        actor_user_id: str,
        candidate_id: str | None = None,
    ) -> None:
        self._pending = pending
        self._session_key = session_key
        self._actor_user_id = actor_user_id
        self._candidate_id = candidate_id

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact:
        from .tmbx_client import TmbxClient

        basis = ValidatedTimeboxCandidate.from_artifact_payload(candidate.payload)
        pending = self._pending.peek(self._session_key)
        if pending is None or pending.digest != basis.digest:
            raise AdaptiveDependencyUnavailable(
                "the approved candidate is no longer the current one"
            )
        # The opaque id the button carried, when a button carried one. It is the
        # ownership token the existing gate already checks, so a press drawn on
        # a candidate that has since been replaced is spent against nothing.
        spent = self._pending.consume(
            self._session_key,
            self._candidate_id or pending.candidate_id,
            actor_user_id=self._actor_user_id,
        )
        if spent is None:
            raise AdaptiveDependencyUnavailable(
                "this candidate is not this user's to commit"
            )
        result = await TmbxClient().commit(
            spent.snapshot, spent.patch, idempotency_key=spent.digest
        )
        return PlanningArtifact.create(
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=1,
            payload={
                "committed": result.get("committed") is True,
                "tx_id": result.get("tx_id"),
                "reason": result.get("reason"),
                "candidate_digest": digest,
                # Which calendar the write reached, carried from tmbx rather
                # than guessed here: the env var that picks the backend
                # belongs to the tmbx process, not to this one. Absent on an
                # older server, and absent must not read as durable.
                "calendar_backend": result.get("calendar_backend") or "unknown",
                "durable": result.get("durable") is True,
            },
            dependency_revisions={"validated_candidate": candidate.revision},
        )

async def derive_timebox_intent(
    runtime,
    snapshot: PlanningSessionSnapshot,
    *,
    user_text: str,
) -> TimeboxIntent:
    """Turn one Slack reply into a typed intent, never by reading the words.

    Until a day has even been proposed there is nothing to decide about, so no
    model is asked: the session starts and the host puts its own date on screen.
    From the moment that card exists the reply is interpreted -- including the
    reply that confirms it. Skipping the interpreter there was what left the
    date card answerable only by a press, and a session an agent drives by
    typing could not get past it.

    The schema-bound interpreter names the decision; the host binds the date,
    the artifact identity and the question being answered from state it already
    trusts.
    """
    if snapshot.planning_day is None and not any(
        artifact.kind is ArtifactKind.PLANNING_DAY for artifact in snapshot.artifacts
    ):
        return StartSession()
    if not user_text.strip():
        return Advance()
    interpreter = getattr(runtime, "timeboxing_intent_interpreter", None)
    if interpreter is None:
        # Falling back to a guess would give this route two behaviours, and the
        # wrong one would be the silent one.
        raise AdaptiveDependencyUnavailable("no intent interpreter is configured")
    return await interpreter.interpret(user_text, snapshot)
