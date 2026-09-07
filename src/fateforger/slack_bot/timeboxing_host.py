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

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.adaptive_timeboxing import (
    PlanningContext,
    TurnRequest,
)
from fateforger.agents.timeboxing.elicitation_judges import build_judges, elicit
from fateforger.agents.timeboxing.required_blocks import required_blocks_value
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

logger = logging.getLogger(__name__)

#: How far past today the day proposal will walk looking for a day that is not
#: already on the calendar. A week covers what produced this (one committed
#: day, occasionally two); past it the proposal falls back to the starting day
#: and the user flips it on the card, which is what the card is for.
_PROPOSAL_HORIZON_DAYS = 7

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
        value = await self._first_unplanned_day(
            today, owner_user_id=request.actor_user_id
        )
        return PlanningDay.lock_default(
            value=value, timezone=tz_name, lock_revision=1
        )

    async def _first_unplanned_day(self, start: date, *, owner_user_id: str) -> date:
        """`start`, or the first day after it carrying no committed session.

        The scheduled opener has always asked this before opening a session
        (`session_start.SessionStarter._blocked`). The `/timebox` and typed-text
        door never did, so on 2026-09-05 a card at 18:47 proposed the Saturday
        that had been committed at 03:40 and Hugo moved it to Sunday by hand.

        This is not a judgement and must not become one: `standing_for` answers
        it from rows this system minted, and it was answering correctly every
        ten minutes that evening while the card ignored it. What a message
        *means* -- a new day, or a revision of the standing one -- is the
        judgement, and it is decided elsewhere.

        Failing to read the store falls back to `start`. The day is a proposal
        the user can flip, so a lookup that cannot be made is worth a line in
        the log and not a session that refuses to open.
        """

        ledger = getattr(self._runtime, "timeboxing_session_store", None)
        if ledger is None:
            return start
        # Only `committed_session_key` is read below, so this bound merely has
        # to be a real datetime; the open clause it governs is another
        # question -- whether the user is busy -- and not this one.
        asked_at = self._now()
        for offset in range(_PROPOSAL_HORIZON_DAYS):
            day = start + timedelta(days=offset)
            try:
                standing = await ledger.standing_for(
                    owner_user_id=owner_user_id,
                    open_since=asked_at,
                    planned_from=day,
                    planned_to=day,
                )
            except Exception:
                logger.warning(
                    "propose_planning_day: could not read the session store for %s; "
                    "proposing %s unchecked",
                    owner_user_id,
                    start,
                    exc_info=True,
                )
                return start
            if standing.committed_session_key is None:
                return day
            logger.info(
                "propose_planning_day: %s is already committed by %s; looking past it",
                day,
                standing.committed_session_key,
            )
        logger.warning(
            "propose_planning_day: %s days from %s are all committed for %s; "
            "proposing %s and letting the user choose",
            _PROPOSAL_HORIZON_DAYS,
            start,
            owner_user_id,
            start,
        )
        return start

    async def resolve(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        target: ArtifactKind,
        progress,
    ) -> PlanningContext:
        if target is ArtifactKind.SKELETON:
            return await self._frame_from_corpus(snapshot)
        if target is not ArtifactKind.VALIDATED_CANDIDATE:
            # Stage 3 presents the skeleton. Reading the remote baseline here
            # would make the presentation stage touch the calendar, which is
            # exactly the boundary this route exists to hold.
            return PlanningContext()

        planning_day = self._locked_day(snapshot)
        day = planning_day.date.isoformat()

        calendar_id = (
            getattr(self._runtime, "timeboxing_calendar_id", "") or ""
        ).strip()
        if not calendar_id:
            # An invented calendar id is how a plan lands on a calendar nobody
            # reads. Absence stays absence.
            raise AdaptiveDependencyUnavailable("no calendar is configured")

        from .tmbx_client import TmbxClient

        calendar_snapshot = await TmbxClient().read(calendar_id, day)
        if not _read_succeeded(calendar_snapshot):
            # `TmbxClient.read` answers `ok: false` for most refusals rather
            # than raising, so this was filed as a CALENDAR_SNAPSHOT fact and
            # the readiness gate -- a presence test -- reported the requirement
            # satisfied. A gate that exists to guarantee the plan accounts for
            # the real calendar was satisfied by the ABSENCE of the real
            # calendar, and only the planner noticed, one layer later (#226).
            #
            # Same rule as the constraint-store probe on #206: absence and
            # failure must not read as data. Named rather than generic, because
            # "the calendar could not be read" and "no calendar is configured"
            # are different problems for whoever has to fix one.
            reason = str((calendar_snapshot or {}).get("reason") or "").strip()
            raise AdaptiveDependencyUnavailable(
                f"the calendar {calendar_id} could not be read for {day}"
                + (f" ({reason})" if reason else "")
            )
        constraints = await self._active_constraints(planning_day)
        return PlanningContext(
            facts=planning_facts(
                day=day, calendar_snapshot=calendar_snapshot, constraints=constraints
            ),
            applicable_constraints=constraints,
            calendar_snapshot=calendar_snapshot,
        )

    async def _frame_from_corpus(
        self, snapshot: PlanningSessionSnapshot
    ) -> PlanningContext:
        """What memory says about the day, and what Stage 1 still needs to ask.

        The rules are returned in every case (#262). The frame judgement is
        skipped when the user typed a frame this session. Then `elicit` runs
        the three Stage 1 judgements against the rules and the snapshot --
        with the frame just judged merged in, so the `bounded` row sees it --
        and returns the matrix fact and the probes. A host that cannot judge
        fails the turn rather than proposing to close a stage it never opened.
        Once the stage is closed the judgements are skipped: the rules and the
        count still come back, the matrix does not.
        """
        planning_day = self._locked_day(snapshot)
        constraints = await self._active_constraints(planning_day)
        suspended = await self._suspended_count(planning_day)
        # The judgements read the judge client and nothing else: no fallback to
        # the planner's intent client, because a silent fallback would put a
        # 45-cell classify batch back on the pro pin at high effort.
        model_client = getattr(self._runtime, "timeboxing_judge_model_client", None)
        if model_client is None:
            raise AdaptiveDependencyUnavailable(
                "no model client for the Stage 1 judgements"
            )

        frame: PlanningFact | None = None
        if not any(fact.kind is FactKind.DAY_FRAME for fact in snapshot.facts):
            from fateforger.agents.timeboxing.day_frame import DayFrameJudge

            frame = await DayFrameJudge(model_client).frame_on_record(
                day=planning_day,
                constraints=constraints,
                session_key=snapshot.session_key,
            )
        if snapshot.stage1 == "closed":
            # Every skeleton turn after consent -- the planner's, and any
            # revise after that -- resolves here too, and Stage 1 is over by
            # then: the three judge fan-outs would rewrite a matrix nobody
            # reads any more. `stage1` is a field the kernel minted, so this
            # is arithmetic on state, not a judgement about anything said.
            return PlanningContext(
                facts=[frame] if frame is not None else [],
                applicable_constraints=constraints,
                suspended_constraint_count=suspended,
            )

        seen = (
            snapshot
            if frame is None
            else snapshot.model_copy(update={"facts": [*snapshot.facts, frame]})
        )
        result = await elicit(
            seen, constraints, build_judges(model_client), session_key=snapshot.session_key
        )
        return PlanningContext(
            facts=([frame] if frame is not None else []) + [result.matrix_fact],
            applicable_constraints=constraints,
            suspended_constraint_count=suspended,
            probes=result.probes,
        )

    async def _suspended_count(self, planning_day: PlanningDay) -> int:
        store = getattr(self._runtime, "timeboxing_constraint_store", None)
        if store is None:
            raise AdaptiveDependencyUnavailable("constraint memory is unavailable")
        return int(
            await store.count_suspended(
                planning_day.date.isoformat(), planning_day.day_type.value
            )
        )

    @staticmethod
    def _locked_day(snapshot: PlanningSessionSnapshot) -> PlanningDay:
        planning_day = snapshot.planning_day
        if planning_day is None:
            raise AdaptiveDependencyUnavailable("the planning day is not locked")
        return planning_day

    async def _active_constraints(self, planning_day: PlanningDay) -> Any:
        store = getattr(self._runtime, "timeboxing_constraint_store", None)
        if store is None:
            raise AdaptiveDependencyUnavailable("constraint memory is unavailable")
        return await store.query_constraints(
            filters={
                "planned_day": planning_day.date.isoformat(),
                "day_type": planning_day.day_type.value,
                "require_active": True,
            },
            limit=200,
        )


def _read_succeeded(calendar_snapshot: Any) -> bool:
    """Whether a `plan_read` payload actually carries a calendar.

    `ok` is a field tmbx mints, so reading it is a field lookup and not a
    judgement about anything a person wrote.

    A missing `ok` counts as success: tmbx has answered without one, and
    treating an unrecognised-but-present payload as a failure would refuse
    every turn on a shape change rather than on a real problem. An explicitly
    false `ok` is the case this exists for.
    """
    if not isinstance(calendar_snapshot, dict):
        return False
    return calendar_snapshot.get("ok", True) is not False


def _block_count(calendar_snapshot: Any) -> int | None:
    """However many blocks the read reported, without assuming its shape.

    tmbx states a count; a sequence would also be a reasonable thing for a
    calendar port to return. Anything else is reported as unknown rather than
    guessed at -- this fact exists to say a fetch happened, and no readiness
    check reads the number, so being wrong about it is worse than being silent.
    """

    blocks = (calendar_snapshot or {}).get("blocks") if calendar_snapshot else None
    if isinstance(blocks, bool):
        return None
    if isinstance(blocks, int):
        return blocks
    try:
        return len(blocks)
    except TypeError:
        return None


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

    The third fact, `REQUIRED_BLOCKS`, is the one exception to "presence only":
    readiness, the brief and both missing-block checks read its slugs. It is
    filed on every successful resolve, carrying an empty slug list when no rule
    requires a kind -- `_merge_facts` merges by fact_id and never deletes, so
    only an empty value under the same id clears a requirement the day no
    longer has. Filing it conditionally would leave a suspended rule refusing
    every later candidate for a block nothing asks for any more.
    """

    facts: list[PlanningFact] = []
    if not _read_succeeded(calendar_snapshot):
        # Defence in depth behind `resolve`'s refusal. `satisfied_by` is a
        # presence test, so filing this fact for a failed read is the whole
        # defect -- and a second caller arriving later must not reintroduce it
        # by calling this function directly.
        return facts
    facts = [
        PlanningFact(
            fact_id=f"calendar:{day}",
            kind=FactKind.CALENDAR_SNAPSHOT,
            # `blocks` is already a count in what tmbx returns, not a list.
            # The first version of this called len() on it, which raised
            # TypeError on the first real candidate turn -- the unit test had
            # stubbed a list of block dicts, a shape nothing produces. Pass
            # the payload's own summary through rather than recomputing it
            # from a shape this function does not own.
            value={"fetched": True, "blocks": _block_count(calendar_snapshot)},
            source="calendar",
        ),
        PlanningFact(
            fact_id=f"constraints:{day}",
            kind=FactKind.ACTIVE_CONSTRAINTS,
            value={"fetched": True, "count": len(constraints or [])},
            source="constraint_memory",
        ),
    ]
    facts.append(
        PlanningFact(
            fact_id=f"required-blocks:{day}",
            kind=FactKind.REQUIRED_BLOCKS,
            value=required_blocks_value(constraints),
            source="constraint_memory",
        )
    )
    return facts


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
