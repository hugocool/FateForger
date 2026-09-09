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

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fateforger.agents.tasks.board import TaskBoard
from fateforger.agents.tasks.task_source import (
    BoardTaskSource,
    TaskCandidate,
    TaskCandidates,
    TaskSource,
)
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
        # Both after the calendar read, deliberately: that read is what proves
        # tmbx is up, and the material store the handles are written into lives
        # in that same process. But neither of these needs the other's answer,
        # and running them in sequence put the whole of `_work_refs` -- board
        # 20s, then the judgement 45s, then the material writes 20s -- in front
        # of the planner on top of the constraint query. The three inside
        # `_work_refs` genuinely chain (there is nothing to judge before the
        # board answers, and nothing to store before the judgement does); these
        # two do not. CLAUDE.md's parallelise rule, on the one path where the
        # user is watching a card and waiting.
        #
        # `return_exceptions=True` and re-raise, the same shape as
        # `_store_materials` below and for the same reason: a bare gather
        # propagates the first failure and leaves its sibling running detached,
        # which here would be a half-finished lookup writing materials into a
        # turn that has already failed. Collecting means both are awaited and
        # the failure keeps its own traceback and its own type -- the caller
        # catches `AdaptiveDependencyUnavailable`, not an ExceptionGroup.
        #
        # The constraint failure is raised first when both fail, which is the
        # order a caller saw when these ran in sequence.
        constraints, work = await asyncio.gather(
            self._active_constraints(planning_day),
            self._work_refs(snapshot, day),
            return_exceptions=True,
        )
        for settled in (constraints, work):
            if isinstance(settled, BaseException):
                raise settled
        return PlanningContext(
            facts=[
                *planning_facts(
                    day=day,
                    calendar_snapshot=calendar_snapshot,
                    constraints=constraints,
                ),
                *work.facts,
            ],
            applicable_constraints=constraints,
            calendar_snapshot=calendar_snapshot,
            work_refs_unresolved=work.unresolved,
            candidates=work.candidates,
        )

    async def _work_refs(
        self, snapshot: PlanningSessionSnapshot, day: str
    ) -> WorkRefs:
        """Which of the current sprint's Ready tickets this session asked for.

        Runs on every candidate resolve, so a request made after the first
        candidate reaches the next one. A session that asked for no work at all
        costs nothing: `requested_work_text` is empty, and `work_refs_for_turn`
        returns before it reads a board or asks a model.

        The judge client is the one the Stage 1 judgements use -- the flash pin
        at `minimal` effort, which with `json_output=True` is the request shape
        the work-lookup eval measured. A host without one fails the turn rather
        than quietly planning every day unlinked: that is a misconfiguration,
        not an answer.
        """
        message = requested_work_text(snapshot)
        if not message.strip():
            return WorkRefs(facts=[], unresolved=False)

        try:
            board = TaskBoard.from_settings()
        except Exception as exc:  # noqa: BLE001 - no board is one outcome
            # `from_settings` raises on a missing token, before any request --
            # and before a judge is asked for, since there would be nothing to
            # ask about.
            return work_board_unavailable(day, exc)

        # The scope is `BoardTaskSource`'s measured default -- the current
        # sprint's Ready rows, which is the list the work-lookup eval's rates
        # were taken over. Widening it is its own ticket with its own eval run
        # (#401), not a keyword changed here.
        source = BoardTaskSource(board)

        model_client = getattr(self._runtime, "timeboxing_judge_model_client", None)
        if model_client is None:
            raise AdaptiveDependencyUnavailable("no model client for the work lookup")

        from .tmbx_client import TmbxClient

        return await work_refs_for_turn(
            day=day,
            message=message,
            source=source,
            ask=judge_ask(model_client),
            put_material=TmbxClient().material_put,
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


#: One page of the current sprint's Ready list. Notion caps a query at 100, and
#: a sprint holding more Ready tickets than that is a different problem from
#: this one: paging would widen a scope that is deliberately narrow, and the
#: narrowness is what fixes what "the next one" means.
WORK_ROW_LIMIT = 100

#: The model transport `resolve_work` takes: one prompt in, one answer out.
Ask = Callable[[str], Awaitable[str]]
#: `MaterialStore.put` as the host reaches it -- across a process boundary in
#: production, directly in a test. Returns the handle.
PutMaterial = Callable[..., Awaitable[str]]


#: How long the board read and the judgement each get. Both sit inside a
#: planning turn the user is waiting on, so an unbounded hang is worse than
#: either failing: a turn that never answers is a session nobody can continue,
#: while a timeout takes the same non-blocking path as any other failure and
#: the day is planned unlinked.
BOARD_TIMEOUT_S = 20.0
LOOKUP_TIMEOUT_S = 45.0
#: The material writes cross the same MCP mount the calendar read crosses, so
#: they can hang the same way. Bounded for the same reason as the other two: a
#: hung write is still a dead turn, and a dead turn is the one outcome this
#: whole path is arranged to prevent.
MATERIAL_TIMEOUT_S = 20.0


@dataclass(frozen=True)
class WorkRefs:
    """What one turn's work lookup produced, and whether it got an answer.

    The first two fields are not redundant, and neither is implied by the
    other. An empty fact says "this turn resolved no handles"; the flag says
    why -- the lookup could not be completed, rather than a message that named
    no ticket -- and only the second changes how the planner should read the
    brief.

    `candidates` is the third thing neither of those says: what the board
    *offered* before any of it was judged. It travels here so the card and the
    judgement cannot disagree about the day's list -- one read, handed to both
    (#401).

    **`None` is "no board was read", and it never stands in for an empty
    board.** Two turns produce it: one where nobody asked for any work, so
    there was nothing to look up, and one where the read itself failed --
    which `unresolved` already tells apart. A board that answered and offered
    nothing is a `TaskCandidates` with an empty `rows`, and a surface must be
    able to say "your sprint has no ready tickets" without saying "the board
    could not be read".

    A turn whose read succeeded and whose *judgement* then failed keeps its
    candidates: the list was on offer and the person may still be shown it,
    with nothing marked as taken from it.
    """

    facts: list[PlanningFact]
    unresolved: bool
    candidates: TaskCandidates | None = None


def work_lookup_failed(
    day: str,
    event: str,
    exc: BaseException,
    *,
    candidates: TaskCandidates | None = None,
) -> WorkRefs:
    """A step of the lookup did not complete: say so at error, and go on.

    **The fact is filed, with an empty value.** `_merge_facts` merges by
    fact_id and never deletes, so only an empty value under the same id clears
    the day's refs -- the same house pattern `REQUIRED_BLOCKS` uses, and for
    the same reason. Filing nothing would leave the previous turn's handles
    standing on the brief beside the sentence saying the work could not be
    resolved, so the planner could attach a ticket while the card told the
    reader the day was planned without one. That is the failure this whole
    line exists to catch, inverted.

    Clearing loses nothing. The fact records what the current message named,
    not durable state: a link the planner already attached lives on the block
    in the plan and in the material store, and neither is touched here. All
    this stops is a new turn acting on an older message's intent, which is the
    more dangerous direction.

    **Every failure here is non-blocking**, decided 2026-09-08: the user asked
    to plan a day, and whether the board timed out, the model named a ticket
    nobody showed it, or the material store refused the write, the outcome for
    them is the same -- nobody knows which ticket they meant, so the day is
    planned unlinked and someone attaches one later, which is a decision this
    plan already took. Nothing acts on a bad answer and the brief still says
    the work could not be resolved, so the loudness lives in this log line and
    on the brief rather than in a dead turn.

    `event` distinguishes the four for whoever has to fix one --
    `work_board_unavailable`, `work_lookup_hallucinated_id`,
    `work_lookup_failed`, `work_material_unstorable` -- because they have four
    different remedies even though the planner's next move is identical.

    `candidates` is whatever the board did offer before the step that failed.
    Three of the four events happen *after* a successful read and keep it, so
    the surface can still show the day's list with nothing marked taken from
    it; only `work_board_unavailable` has nothing to carry, and passes None.
    """
    # The type that actually broke, not the wrapper around it.
    # `BoardTaskSource` turns every board failure into one
    # `TaskSourceUnavailable` carrying the original as `__cause__`, so reading
    # the wrapper here would file a Notion 503, a missing token and a malformed
    # page under one label -- while the sibling catch in `_work_refs` logs the
    # real type, and `work_board_unavailable` would then carry two type
    # vocabularies under one event name. Whoever greps for one of these greps
    # for the failure, not for the layer that renamed it.
    error_type = type(exc.__cause__ or exc).__name__
    logger.error(
        "%s: %s: %s",
        event,
        error_type,
        exc,
        # The traceback, because the catch is broad. A bare
        # "work_lookup_failed: TypeError: 'NoneType' object is not
        # subscriptable" names neither the frame nor the layer it came from,
        # and the thing that raised may be `resolve_work`, the MCP client or a
        # row mapper. Loudness that survives the widening.
        exc_info=True,
        extra={"event": event, "error_type": error_type},
    )
    from fateforger.agents.timeboxing.work_refs import work_refs_fact_id

    return WorkRefs(
        facts=[
            PlanningFact(
                fact_id=work_refs_fact_id(day),
                kind=FactKind.WORK_REFS,
                value=[],
                source="system",
            )
        ],
        unresolved=True,
        candidates=candidates,
    )


def work_board_unavailable(day: str, exc: BaseException) -> WorkRefs:
    """The board could not be built or read, under its own event name.

    The one failure with no candidates to carry: nothing was offered, which is
    a different sentence for the reader than a board that offered nothing.
    """
    return work_lookup_failed(day, "work_board_unavailable", exc)


def requested_work_text(snapshot: Any) -> str:
    """What the user asked this day to hold, in their own words.

    `requested_activity` facts are what the intent interpreter filed from what
    they typed -- one per thing they want the day to carry. They are joined and
    handed to the judgement whole; nothing here reads them, and no other fact
    kind is mixed in, because a bedtime is not a request for work.

    An empty string means nobody asked for anything, and the caller skips the
    lookup entirely: no board read, no model call, nothing on the brief.
    """
    wanted = [
        fact.value
        for fact in getattr(snapshot, "facts", [])
        if fact.kind is FactKind.REQUESTED_ACTIVITY and isinstance(fact.value, str)
    ]
    return "\n".join(text for text in wanted if text.strip())


def judge_ask(model_client: Any) -> Ask:
    """`resolve_work`'s transport, in the request shape the eval measured.

    The whole prompt goes in one user turn and the answer comes back as a JSON
    object -- `json_output=True`, not a schema. That is what
    `tests/integration/test_eval_work_lookup.py` sampled, and it says so: a
    system/user split or a transport without `response_format` invalidates its
    rates. The model and the reasoning effort are the client's, and the judge
    client is built as the flash pin at `minimal` (`llm/factory.py`), which is
    the other half of that shape.
    """

    async def ask(prompt: str) -> str:
        from autogen_core.models import UserMessage

        result = await model_client.create(
            [UserMessage(content=prompt, source="host")],
            json_output=True,
        )
        content = result.content
        if not isinstance(content, str):
            raise AdaptiveDependencyUnavailable(
                f"the work lookup answered with {type(content).__name__}, not text"
            )
        return content

    return ask


async def _store_materials(
    rows: list[TaskCandidate], put_material: PutMaterial
) -> list[str]:
    """The handle for each row, all written at once and all waited for.

    Concurrent because the writes are independent and this sits inside the
    latency of a turn somebody is watching a progress card for.

    `return_exceptions=True` because a bare gather propagates the first failure
    and leaves its siblings running detached: a second failure then surfaces as
    an unretrieved-exception warning with nothing to trace it to. Harmless for
    correctness -- the puts are idempotent and these refs are discarded -- but
    noise nobody owns. Collecting them means every write is awaited, and the
    first failure is re-raised with its own traceback intact.

    One failed write refuses the whole set rather than returning the handles
    that did land: a partial list reads as "this is the work", and the ticket
    that fell out is the one nobody would notice missing.

    Bounded, because these cross the same MCP mount the calendar read crosses
    and can hang the same way.
    """
    settled = await asyncio.wait_for(
        asyncio.gather(
            *(
                # The candidate's own `source`, not a constant: the port
                # exists so a second backend can land behind it, and a handle
                # minted under the wrong system's name points at nothing. It
                # is `"notion"` for every row the board adapter produces, so
                # nothing about today's behaviour changes.
                put_material(
                    source=row.source,
                    external_id=row.external_id,
                    url=row.url,
                    label=row.label,
                )
                for row in rows
            ),
            return_exceptions=True,
        ),
        timeout=MATERIAL_TIMEOUT_S,
    )
    refused = next(
        (result for result in settled if isinstance(result, BaseException)), None
    )
    if refused is not None:
        raise refused
    return list(settled)


async def work_refs_for_turn(
    *,
    day: str,
    message: str,
    source: TaskSource,
    ask: Ask,
    put_material: PutMaterial,
) -> WorkRefs:
    """Turn what the user asked for into handles the planner can attach.

    Four steps, in this order and host-side: read the day's candidate tickets
    through the `TaskSource` port, ask which of them the message names, record
    each answer in the material store, and file one fact carrying the handles.

    **The board is read exactly once, and that one listing goes two places.**
    The judgement below is handed it, and it is returned on `WorkRefs` for the
    surface that shows the person what was on offer. A card fetching its own
    list could differ from the one the judgement saw -- a row shown that was
    never judged over, or the reverse -- and nothing would notice; one read is
    what makes "the list you were shown is the list it judged over" true by
    construction rather than by coincidence (#401).

    **The scope is decided by the source, never by the model.**
    `current_sprint_ready` is what fixes the meaning of "the next one" -- the
    spike that produced this plan watched a subagent choose its own scope and
    pass over an overdue in-sprint tax filing. The rows go to `resolve_work`
    in the order the port returned them, unsorted and unfiltered, because the
    prompt tells the model that order is the person's ranking (see
    `build_prompt`).

    **`WORK_ROW_LIMIT` is passed explicitly**, and the reason is a trap rather
    than a preference: `TaskSource.candidates` defaults to a readable page of
    twelve. A caller here taking that default would show the judgement twelve
    of a hundred ready rows and get a plausible answer back over a silently
    narrowed list -- "the next finance ticket" resolving to the next one *of
    the first twelve*, with nothing to notice it by.

    **No failure here blocks the turn.** Each step is caught broadly and logged
    under its own event name (see `work_lookup_failed`), and the caller puts
    one sentence on the brief. Broadly, because the failures that matter are
    not the typed ones: the port raises `TaskSourceUnavailable` for everything
    the board can do wrong, but a wait that times out here raises
    `TimeoutError` instead, and that is not a `TaskSourceUnavailable`. Both
    waits are bounded, because a planning turn that hangs is worse than one
    planned unlinked.
    """
    if not message.strip():
        # Nobody asked for anything, so there is nothing to point at: no board
        # read, no model call, and nothing on the brief either way. No
        # candidates either -- the board was never asked, which is not the
        # same answer as a board that had nothing.
        return WorkRefs(facts=[], unresolved=False)

    from fateforger.agents.timeboxing.work_lookup import UnknownWorkId, resolve_work
    from fateforger.agents.timeboxing.work_refs import work_refs_fact_id

    try:
        listing = await asyncio.wait_for(
            # `date.fromisoformat` on a day string this system minted and
            # writes into its own fact ids -- an identifier, not anybody's
            # prose. Inside the guard so a malformed one takes the same named,
            # non-blocking path as an unreachable board rather than killing
            # the turn as a bare ValueError.
            source.candidates(date.fromisoformat(day), limit=WORK_ROW_LIMIT),
            timeout=BOARD_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - every board failure is one outcome
        return work_board_unavailable(day, exc)

    try:
        rows = await asyncio.wait_for(
            resolve_work(message, list(listing.rows), ask=ask),
            timeout=LOOKUP_TIMEOUT_S,
        )
    except UnknownWorkId as exc:
        # Its own name: an id nobody showed the model is a prompt or a model
        # problem, not an outage, and it is the one failure here that says
        # something about the judgement rather than about the plumbing.
        return work_lookup_failed(
            day, "work_lookup_hallucinated_id", exc, candidates=listing
        )
    except Exception as exc:  # noqa: BLE001 - transport, timeout, unparseable
        return work_lookup_failed(day, "work_lookup_failed", exc, candidates=listing)

    if not rows:
        # The ordinary case, and it stays silent: a message naming a topic
        # rather than an item plans the day with no ticket attached. No fact,
        # deliberately unlike the failure paths above: this is an answer, and
        # the message it answered still holds every earlier request, so a ref
        # the model named on an earlier draw and passes over on this one is a
        # disagreement between two samples, not a retraction. Clearing on it
        # would let sampling noise drop a ticket the user did ask for.
        return WorkRefs(facts=[], unresolved=False, candidates=listing)

    try:
        handles = await _store_materials(rows, put_material)
    except Exception as exc:  # noqa: BLE001 - a handle nothing stored is no handle
        return work_lookup_failed(
            day, "work_material_unstorable", exc, candidates=listing
        )

    refs = [
        {"link": handle, "label": row.label, "task": row.number}
        for handle, row in zip(handles, rows)
    ]
    return WorkRefs(
        facts=[
            PlanningFact(
                fact_id=work_refs_fact_id(day),
                kind=FactKind.WORK_REFS,
                value=refs,
                source="system",
            )
        ],
        unresolved=False,
        candidates=listing,
    )


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
