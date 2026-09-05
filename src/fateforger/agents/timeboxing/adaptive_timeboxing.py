"""Artifact-led planning-session orchestration with an in-memory repository."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .elicitation import stage1_gate
from .readiness import (
    ReadinessGap,
    ReadinessReport,
    RequirementOwner,
    TimeboxRequirements,
)
from .required_blocks import required_slugs, slugs_on_candidate
from .session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactApproval,
    ArtifactKind,
    ArtifactSnapshot,
    AwaitingApproval,
    CandidateNotApplied,
    NeedsAnotherTurn,
    AwaitingUser,
    BlockerOption,
    Cancelled,
    CancelSession,
    ChooseBlockerOption,
    Committed,
    ConfirmPlanningDay,
    DenyAssumption,
    FactKind,
    FileAssumption,
    Gate,
    GateMet,
    GoBack,
    HandledInteraction,
    PendingBlocker,
    PlannerAssumption,
    PlanningArtifact,
    PlanningBrief,
    PlanningDay,
    PlanningFact,
    PlanningResult,
    PlanningSessionSnapshot,
    ProvidePlanningFacts,
    RestoreConstraint,
    ReviseArtifact,
    StartSession,
    TimeboxIntent,
    TurnFailed,
    TurnOutcome,
    UserBlockerDraft,
    has_commit_receipt,
    suspension_fact_id,
)

logger = logging.getLogger(__name__)


class TurnRequest(BaseModel):
    """One idempotent, revision-aware planning-session interaction."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_key: str = Field(min_length=1)
    interaction_id: str = Field(min_length=1)
    actor_user_id: str = Field(min_length=1)
    expected_revision: int | None = Field(default=None, ge=0)
    intent: TimeboxIntent


class PlanningContext(BaseModel):
    """Host-resolved facts and snapshots for one planner invocation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)  # noqa: UP017
    )
    facts: list[PlanningFact] = Field(default_factory=list)
    applicable_constraints: JsonValue = Field(default_factory=dict)
    #: None is "this resolve did not look", which is not the same answer as
    #: zero. Only the Stage 1 resolve counts memory-side suspensions; a
    #: candidate-stage resolve leaving the default here used to overwrite the
    #: Stage 1 count with 0 on every turn, and the card stopped saying how many
    #: rules the day type had taken off.
    suspended_constraint_count: int | None = Field(default=None, ge=0)
    calendar_snapshot: JsonValue = Field(default_factory=dict)


class ProgressSink(Protocol):
    """Best-effort observer for bounded progress events."""

    async def emit(self, event: object) -> None: ...


class PlannerPort(Protocol):
    """Produce one schema-bound result from a complete planning brief."""

    async def produce(
        self, brief: PlanningBrief, progress: ProgressSink
    ) -> PlanningResult: ...


class PlanningContextPort(Protocol):
    """Resolve host-owned planning-day and downstream context."""

    async def propose_planning_day(self, request: TurnRequest) -> PlanningDay: ...

    async def resolve(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        target: ArtifactKind,
        progress: ProgressSink,
    ) -> PlanningContext: ...


class CommitPort(Protocol):
    """Commit one candidate using its digest as the remote idempotency key."""

    async def commit(
        self, candidate: PlanningArtifact, *, digest: str
    ) -> PlanningArtifact: ...


class PlanningSessionRepository(Protocol):
    """Atomic persistence and replay boundary for planning sessions."""

    def session_guard(
        self, session_key: str
    ) -> AbstractAsyncContextManager[None]:
        """Coalesce turns sharing this repository/runtime instance.

        SQL adapters implement this with an in-process per-session lock. They
        must not hold a database write transaction across planner, network, or
        commit calls. Cross-process coalescing requires a separate lease or
        outbox and is outside this protocol.
        """

        ...

    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        """The session if one exists, never creating one.

        The routing seam asks this for every thread reply, and a thread that is
        not a planning session must not become one by being asked about.
        """

        ...

    async def load_or_create(
        self, session_key: str, *, owner_user_id: str
    ) -> PlanningSessionSnapshot: ...

    async def load_outcome(
        self, session_key: str, *, interaction_id: str
    ) -> TurnOutcome | None:
        """Load the full stored outcome used for duplicate/restart replay."""

        ...

    async def save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        expected_revision: int,
        interaction_id: str,
        outcome: TurnOutcome,
    ) -> PlanningSessionSnapshot: ...


class TimeboxingStanding(BaseModel):
    """What the session store says about one user's planning, for the nudger.

    Both are session keys so the log can name the session that silenced a
    reminder. ``open_session_key`` is a session still under way; a stale open
    session -- abandoned mid-way -- stops counting once it has not been saved
    since ``open_since``, or the Admonisher would never speak again.
    ``committed_session_key`` is a day already planned inside the window asked
    about (#256).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    open_session_key: str | None = None
    committed_session_key: str | None = None

    @property
    def under_way(self) -> bool:
        return self.open_session_key is not None

    @property
    def planned(self) -> bool:
        return self.committed_session_key is not None


class OpenSessionRow(BaseModel):
    """One open session, from the indexed columns alone.

    ``revision`` is how a caller tells a session nobody touched from one the
    user is working in: the turn that opens a session writes revision 1, so
    anything above it is the user's own doing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_key: str
    revision: int
    updated_at: datetime


class TimeboxingSessionLedger(Protocol):
    """The reconciler's read of the session store: is this user busy or done?

    One question answered from one table, so the nudge suppressor and the
    dispatch decision read the same truth. On 2026-09-02 the suppressor read
    an in-process flag set by the turn handler while the reconciler read the
    calendar for a planning event, and the two disagreed twelve times in one
    session -- a card mid-session, then more after the day was committed.
    """

    async def standing_for(
        self,
        *,
        owner_user_id: str,
        open_since: datetime,
        planned_from: date,
        planned_to: date,
    ) -> TimeboxingStanding: ...

    async def open_sessions_for_day(
        self, *, owner_user_id: str, planning_date: date
    ) -> list[OpenSessionRow]:
        """Every still-open session this user holds for one planned day.

        The expiry of an auto-started session asks this instead of
        ``standing_for``: it needs *which* sessions stand for the day it is
        closing and how far each one got, not whether the user is busy
        somewhere. A recency window cannot answer that -- it hides the
        untouched session it should close and finds the live one it must not.
        """

        ...

    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        """The session if one exists, never creating one."""

        ...


class StaleSessionRevision(RuntimeError):
    """The persisted session no longer matches the expected revision."""


class InMemoryPlanningSessionRepository:
    """Revision-safe repository used by unit and deterministic replay tests."""

    def __init__(
        self,
        snapshots: list[PlanningSessionSnapshot] | None = None,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        snapshots = snapshots or []
        self._snapshots = {
            snapshot.session_key: snapshot.model_copy(deep=True)
            for snapshot in snapshots
        }
        self._outcomes: dict[tuple[str, str], TurnOutcome] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._clock = clock
        self._updated_at: dict[str, datetime] = {
            key: self._clock() for key in self._snapshots
        }

    @asynccontextmanager
    async def session_guard(self, session_key: str) -> AsyncIterator[None]:
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            yield

    async def load(self, session_key: str) -> PlanningSessionSnapshot | None:
        snapshot = self._snapshots.get(session_key)
        return None if snapshot is None else snapshot.model_copy(deep=True)

    async def load_or_create(
        self, session_key: str, *, owner_user_id: str
    ) -> PlanningSessionSnapshot:
        snapshot = self._snapshots.get(session_key)
        if snapshot is None:
            snapshot = PlanningSessionSnapshot.new(
                session_key=session_key, owner_user_id=owner_user_id
            )
            self._snapshots[session_key] = snapshot
            self._updated_at[session_key] = self._clock()
        return snapshot.model_copy(deep=True)

    async def load_outcome(
        self, session_key: str, *, interaction_id: str
    ) -> TurnOutcome | None:
        outcome = self._outcomes.get((session_key, interaction_id))
        return deepcopy(outcome)

    async def save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        expected_revision: int,
        interaction_id: str,
        outcome: TurnOutcome,
    ) -> PlanningSessionSnapshot:
        current = self._snapshots.get(snapshot.session_key)
        if current is None or current.revision != expected_revision:
            raise StaleSessionRevision

        replay_key = (snapshot.session_key, interaction_id)
        if replay_key in self._outcomes:
            return current.model_copy(deep=True)

        saved = snapshot.model_copy(
            deep=True,
            update={
                "revision": expected_revision + 1,
                "handled_interactions": [
                    *snapshot.handled_interactions,
                    HandledInteraction(
                        interaction_id=interaction_id,
                        outcome_kind=outcome.kind,
                        session_revision=expected_revision + 1,
                    ),
                ],
            },
        )
        self._snapshots[snapshot.session_key] = saved
        self._updated_at[snapshot.session_key] = self._clock()
        self._outcomes[replay_key] = deepcopy(outcome)
        return saved.model_copy(deep=True)

    async def standing_for(
        self,
        *,
        owner_user_id: str,
        open_since: datetime,
        planned_from: date,
        planned_to: date,
    ) -> TimeboxingStanding:
        open_key: str | None = None
        committed_key: str | None = None
        for key, snapshot in self._snapshots.items():
            if snapshot.owner_user_id != owner_user_id:
                continue
            if snapshot.status == "open" and self._updated_at[key] >= open_since:
                open_key = key
            elif (
                snapshot.status == "committed"
                and snapshot.planning_day is not None
                and planned_from <= snapshot.planning_day.date <= planned_to
            ):
                committed_key = key
        return TimeboxingStanding(
            open_session_key=open_key, committed_session_key=committed_key
        )

    async def open_sessions_for_day(
        self, *, owner_user_id: str, planning_date: date
    ) -> list[OpenSessionRow]:
        rows = [
            OpenSessionRow(
                session_key=key,
                revision=snapshot.revision,
                updated_at=self._updated_at[key],
            )
            for key, snapshot in self._snapshots.items()
            if snapshot.owner_user_id == owner_user_id
            and snapshot.status == "open"
            and snapshot.planning_day is not None
            and snapshot.planning_day.date == planning_date
        ]
        rows.sort(key=lambda row: row.updated_at, reverse=True)
        return rows


class _BestEffortProgress:
    def __init__(self, sink: ProgressSink) -> None:
        self._sink = sink

    async def emit(self, event: object) -> None:
        try:
            await self._sink.emit(event)
        except Exception as exc:  # noqa: BLE001 - observation must never own outcome
            logger.warning(
                "planning progress delivery failed",
                extra={"error_type": type(exc).__name__},
            )


class AdaptiveTimeboxing:
    """Deep facade that advances a session by artifacts, never stored stages."""

    def __init__(
        self,
        *,
        repository: PlanningSessionRepository,
        requirements: TimeboxRequirements,
        planner: PlannerPort,
        context: PlanningContextPort,
        commit: CommitPort,
    ) -> None:
        self._repository = repository
        self._requirements = requirements
        self._planner = planner
        self._context = context
        self._commit = commit

    async def turn(
        self, request: TurnRequest, *, progress: ProgressSink
    ) -> TurnOutcome:
        """Apply one typed intent and persist one replayable domain outcome."""

        async with self._repository.session_guard(request.session_key):
            return await self._turn_guarded(request, progress=progress)

    async def _turn_guarded(
        self, request: TurnRequest, *, progress: ProgressSink
    ) -> TurnOutcome:
        """Execute after acquiring the repository's session exclusion seam."""

        snapshot = await self._repository.load_or_create(
            request.session_key, owner_user_id=request.actor_user_id
        )
        if request.actor_user_id != snapshot.owner_user_id:
            return TurnFailed(
                code="session_owner_mismatch",
                message="This planning session belongs to a different user.",
            )
        prior = await self._repository.load_outcome(
            request.session_key, interaction_id=request.interaction_id
        )
        if prior is not None:
            return prior

        if (
            request.expected_revision is not None
            and request.expected_revision != snapshot.revision
        ):
            return TurnFailed(
                code="stale_session_revision",
                message="The planning session changed before this interaction arrived.",
            )

        if snapshot.status == "committed" and isinstance(
            request.intent, CancelSession
        ):
            # The day is on the calendar. A `cancelled` status over a written
            # calendar would describe a day that does not exist; what the user
            # can still do is change it, and that path is the reopen below.
            return TurnFailed(
                code="session_committed",
                message=(
                    "This day is already on the calendar. Tell me what to "
                    "change and I will revise it."
                ),
            )

        base_revision = snapshot.revision
        progress_sink = _BestEffortProgress(progress)
        applied, early_outcome = self._apply_intent(snapshot, request)
        if early_outcome is not None:
            # A refusal, or an intent whose whole effect is already in the
            # snapshot (GoBack to a question): nothing downstream to derive.
            return await self._save(
                applied,
                base_revision=base_revision,
                request=request,
                outcome=early_outcome,
            )
        snapshot = applied

        if isinstance(request.intent, CancelSession):
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=Cancelled(),
            )

        if snapshot.planning_day is None:
            gate_outcome, snapshot = await self._planning_day_gate(snapshot, request)
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=gate_outcome,
            )

        pending = self._pending_approval(snapshot)
        if pending is not None:
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=AwaitingApproval(artifact=pending),
            )

        target = self._derive_target(snapshot)
        if target is ArtifactKind.COMMIT_RECEIPT:
            commit_outcome, snapshot = await self._commit_candidate(snapshot)
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=commit_outcome,
            )

        if target is None:
            receipt = self._latest_artifact(snapshot, ArtifactKind.COMMIT_RECEIPT)
            if receipt is None:
                final_outcome: TurnOutcome = TurnFailed(
                    code="invalid_session_state",
                    message="The planning session has no next artifact.",
                )
            else:
                final_outcome = Committed(receipt=receipt)
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=final_outcome,
            )

        await progress_sink.emit(
            {"phase": "resolving_context", "status": "started"}
        )
        readiness = self._requirements.evaluate(target, snapshot)
        try:
            resolved = await self._context.resolve(
                snapshot,
                target=target,
                progress=progress_sink,
            )
        except Exception as exc:  # noqa: BLE001 - dependency maps to domain failure
            logger.error(
                "planning context resolution failed: %s",
                exc,
                extra={"error_type": type(exc).__name__},
                exc_info=True,
            )
            await progress_sink.emit(
                {"phase": "resolving_context", "status": "failed"}
            )
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=TurnFailed(
                    code="dependency_unavailable",
                    message="Required planning context is temporarily unavailable.",
                ),
            )

        snapshot = self._merge_facts(snapshot, resolved.facts)
        rows = resolved.applicable_constraints
        if isinstance(rows, list):
            # Written on every resolve: the read is model-free and the set can
            # change mid-session. The presence fact stays count-only.
            snapshot = snapshot.model_copy(update={"applicable_constraints": rows})
        if resolved.suspended_constraint_count is not None:
            # Independent of the rows above: a resolve may know the rows and
            # not the count. Absence keeps whatever the last resolve that did
            # look wrote, rather than asserting zero on its behalf.
            snapshot = snapshot.model_copy(
                update={
                    "suspended_constraint_count": resolved.suspended_constraint_count
                }
            )
        readiness = self._requirements.evaluate(target, snapshot)
        blocker = readiness.first_hard_user_blocker()
        if blocker is not None:
            # No options. The catalog knows what it needs, not what today's
            # alternatives are, and an option set is a judgement about one day.
            # Only the planner has looked at that day, so only the planner can
            # offer one -- see the blocker branch of _apply_planning_result.
            return await self._save(
                self._hold_question(snapshot, blocker, []),
                base_revision=base_revision,
                request=request,
                outcome=AwaitingUser(
                    requirement_id=blocker.requirement_id,
                    question=blocker.question,
                    why_needed=blocker.why_needed,
                ),
            )

        if target is ArtifactKind.SKELETON and snapshot.stage1 != "closed":
            stage1_snapshot, stage1_outcome = self._stage1_outcome(snapshot, readiness)
            return await self._save(
                stage1_snapshot,
                base_revision=base_revision,
                request=request,
                outcome=stage1_outcome,
            )

        if readiness.system_owned_gaps():
            return await self._save(
                snapshot,
                base_revision=base_revision,
                request=request,
                outcome=TurnFailed(
                    code="dependency_unavailable",
                    message="Required planning context is incomplete.",
                ),
            )

        # A step that starts and never reports done spins for the life of the
        # thread: ProgressChannel.close() deliberately refuses to tick one that
        # never finished, so the row is left mid-flight rather than silently
        # completed.
        await progress_sink.emit(
            {"phase": "resolving_context", "status": "succeeded"}
        )

        brief = self._build_brief(snapshot, target, readiness, resolved)
        await progress_sink.emit({"phase": "planning", "status": "started"})
        try:
            result = await self._planner.produce(brief, progress_sink)
        except CandidateNotApplied as exc:
            # Not a dependency failure: the provider answered, and what it
            # answered cannot be committed. Mapping it to
            # ``dependency_unavailable`` would say "temporarily" about
            # something that will repeat identically, and would hide the one
            # sentence naming the step that was skipped.
            logger.error("planner offered an unapplied candidate: %s", exc)
            outcome = TurnFailed(
                code="candidate_not_applied",
                message=(
                    "The plan could not be prepared for commit. Ask again and "
                    "it will be rebuilt."
                ),
            )
        except Exception as exc:  # noqa: BLE001 - provider details stay behind port
            logger.error(
                "planner invocation failed error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            outcome = TurnFailed(
                code="dependency_unavailable",
                message="The planner is temporarily unavailable.",
            )
        else:
            snapshot, outcome = self._apply_planning_result(
                snapshot, target, readiness, result, request.actor_user_id
            )

        saved_outcome = await self._save(
            snapshot,
            base_revision=base_revision,
            request=request,
            outcome=outcome,
        )
        # Bound to what was actually saved. An unconditional success tick
        # reported a green planning step on a turn that failed, and a wrong
        # tick is worse than an unfinished one: a spinner invites waiting, a
        # tick invites belief.
        await progress_sink.emit(
            {
                "phase": "planning",
                "status": (
                    "failed"
                    if getattr(saved_outcome, "kind", None) == "turn_failed"
                    else "succeeded"
                ),
            }
        )
        return saved_outcome

    def _apply_intent(
        self, snapshot: PlanningSessionSnapshot, request: TurnRequest
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome | None]:
        intent = request.intent
        if isinstance(intent, StartSession):
            return snapshot, None
        if isinstance(intent, Advance):
            if snapshot.stage1 == "proposed":
                # Consent: the user saw the proposal and said go on.
                return snapshot.model_copy(update={"stage1": "closed"}), None
            return snapshot, None
        if isinstance(intent, CancelSession):
            return snapshot.model_copy(update={"status": "cancelled"}), None
        if isinstance(intent, ConfirmPlanningDay):
            updated = self._invalidate(snapshot, ArtifactKind.PLANNING_DAY)
            planning_day_artifact = PlanningArtifact.create(
                kind=ArtifactKind.PLANNING_DAY,
                revision=self._next_artifact_revision(
                    updated, ArtifactKind.PLANNING_DAY
                ),
                payload=intent.planning_day.model_dump(mode="json"),
                dependency_revisions={},
            )
            approval = ArtifactApproval(
                artifact_id=planning_day_artifact.artifact_id,
                artifact_revision=planning_day_artifact.revision,
                artifact_digest=planning_day_artifact.digest,
                actor_user_id=request.actor_user_id,
                session_revision=snapshot.revision,
            )
            return updated.model_copy(
                update={
                    "planning_day": intent.planning_day,
                    "artifacts": [*updated.artifacts, planning_day_artifact],
                    "approvals": [*updated.approvals, approval],
                    # A newly confirmed day starts Stage 1 afresh, whatever
                    # the prior day's stage1 stood at.
                    "stage1": "open",
                }
            ), None
        if isinstance(intent, ProvidePlanningFacts):
            merged = self._merge_facts(snapshot, intent.facts)
            if merged.facts == snapshot.facts:
                return snapshot, None
            stage1_kinds = {FactKind.ELICITED_STATEMENT, FactKind.SUSPENDED_CONSTRAINT}
            if any(fact.kind in stage1_kinds for fact in intent.facts):
                # A Stage 1 fact re-enters the loop: a new fact can uncover a cell.
                merged = merged.model_copy(update={"stage1": "open"})
            elif merged.stage1 == "proposed":
                # Anything else after a proposal is consent, and the message is
                # handled in Stage 2, so "let's plan, deep work first" is one turn.
                merged = merged.model_copy(update={"stage1": "closed"})
            return self._reopen(
                self._invalidate(merged, ArtifactKind.CAPTURED_INPUTS)
            ), None
        if isinstance(intent, ReviseArtifact):
            target = self._revision_target(snapshot, intent)
            if target is None:
                return snapshot, TurnFailed(
                    code="stale_revision_target",
                    message="This revision names a plan that is no longer current.",
                )
            # One shape for every stage. The 2026-09-02 case was the committed
            # day: the instruction is filed as a fact the planner reads while
            # rebuilding, what it supersedes goes, the receipt stays -- the
            # next commit must be a change to this day, not a second copy of
            # it. On 2026-09-03 the same sentence typed over the candidate was
            # refused `unsupported_intent` (#258); a skeleton or a candidate
            # is revised the same way, and what it was built from -- the
            # facts, the approved skeleton under a candidate -- stands.
            instructed = self._merge_facts(
                snapshot,
                [
                    *intent.facts,
                    PlanningFact(
                        fact_id=str(uuid4()),
                        kind=FactKind.REVISION_INSTRUCTION,
                        value={
                            "artifact_kind": target.kind.value,
                            "artifact_id": target.artifact_id,
                            "instruction": intent.instruction,
                        },
                        source="user",
                        source_interaction_id=request.interaction_id,
                    ),
                ],
            )
            if target.kind is ArtifactKind.COMMIT_RECEIPT:
                return self._reopen(
                    self._invalidate(instructed, ArtifactKind.CAPTURED_INPUTS)
                ), None
            return self._discard(instructed, target.kind), None
        if isinstance(intent, ApproveArtifact):
            artifact = self._artifact_matching_approval(snapshot, intent)
            if artifact is None:
                return snapshot, TurnFailed(
                    code="stale_approval",
                    message="This approval no longer matches the current artifact.",
                )
            if self._is_approved(snapshot, artifact):
                return snapshot, None
            approval = ArtifactApproval(
                artifact_id=artifact.artifact_id,
                artifact_revision=artifact.revision,
                artifact_digest=artifact.digest,
                actor_user_id=request.actor_user_id,
                session_revision=snapshot.revision,
            )
            return snapshot.model_copy(
                update={"approvals": [*snapshot.approvals, approval]}
            ), None
        if isinstance(intent, ChooseBlockerOption):
            chosen = self._offered_option(snapshot, intent)
            if chosen is None:
                # One code covers "no question is open", "that is not the open
                # question" and "that was not one of its options" deliberately.
                # Separate codes would tell anyone crafting a press which half
                # of the guess was right, and the press is the one place a
                # value arrives already claiming to be trusted.
                return snapshot, TurnFailed(
                    code="stale_blocker_choice",
                    message="That choice no longer matches an open question.",
                )
            pending = snapshot.pending_blocker
            assert pending is not None
            answered = self._merge_facts(
                snapshot,
                [
                    PlanningFact(
                        fact_id=str(uuid4()),
                        kind=pending.fact_kind,
                        # The option id is deliberately not recorded. It is a
                        # handle on one turn's question and the next question
                        # mints the same one again, so filing it as a durable
                        # fact would present a recycled identifier as a stable
                        # one. What the user chose is the label and its effect.
                        value={
                            "requirement_id": pending.requirement_id,
                            "label": chosen.label,
                            "effect": chosen.effect,
                        },
                        source="user",
                        source_interaction_id=request.interaction_id,
                    )
                ],
            )
            answered = answered.model_copy(update={"pending_blocker": None})
            # A pressed answer is a supplied fact, so it invalidates downstream
            # work for the same reason ProvidePlanningFacts does: a skeleton
            # built on the shape the user has just overruled is not a skeleton
            # of this day any more.
            return self._invalidate(answered, ArtifactKind.CAPTURED_INPUTS), None
        if isinstance(intent, FileAssumption):
            if self._requirements.target_of(intent.requirement_id) is None:
                return snapshot, TurnFailed(
                    code="unknown_requirement",
                    message="That question is not one this session asked.",
                )
            filed = PlannerAssumption(
                assumption_id=str(uuid4()),
                requirement_id=intent.requirement_id,
                value=intent.value,
                why_needed=intent.why_needed,
                filed_by="user",
            )
            # Invalidate first, then append: `_without` retires an assumption
            # whose target artifact is invalidated (it served an artifact
            # that no longer exists), and this one's target is SKELETON --
            # the same kind CAPTURED_INPUTS invalidation reaches. Appending
            # before invalidating let the assumption just filed erase itself.
            invalidated = self._invalidate(snapshot, ArtifactKind.CAPTURED_INPUTS)
            pending = invalidated.pending_blocker
            updated = invalidated.model_copy(
                update={
                    "assumptions": [*invalidated.assumptions, filed],
                    "pending_blocker": None
                    if pending is not None and pending.requirement_id == intent.requirement_id
                    else pending,
                }
            )
            # Always fall through. The run loop evaluates readiness fresh,
            # holds a hard user blocker before it ever reaches Stage 1, and
            # arrives at `_stage1_outcome` itself once Stage 1 is open --
            # `stage1_gate` already subtracts any cell this assumption
            # answers, so the run loop's own gate agrees with this turn
            # rather than needing this branch to pre-empt it.
            return updated, None
        if isinstance(intent, DenyAssumption):
            kept = [a for a in snapshot.assumptions if a.assumption_id != intent.assumption_id]
            if len(kept) == len(snapshot.assumptions):
                return snapshot, TurnFailed(
                    code="stale_assumption",
                    message="That assumption is no longer on record.",
                )
            # The cell it answered re-opens; a denial is the user asking to be asked.
            updated = snapshot.model_copy(update={"assumptions": kept, "stage1": "open"})
            return self._invalidate(updated, ArtifactKind.CAPTURED_INPUTS), None
        if isinstance(intent, RestoreConstraint):
            wanted = suspension_fact_id(intent.constraint_uid)
            kept_facts = [f for f in snapshot.facts if f.fact_id != wanted]
            if len(kept_facts) == len(snapshot.facts):
                return snapshot, TurnFailed(
                    code="stale_restore",
                    message="That rule is not set aside for this session.",
                )
            # A restored rule can uncover a cell, same as a new fact.
            updated = snapshot.model_copy(update={"facts": kept_facts, "stage1": "open"})
            return self._invalidate(updated, ArtifactKind.CAPTURED_INPUTS), None
        if isinstance(intent, GoBack):
            return self._go_back(snapshot)
        if isinstance(intent, ReviseArtifact):
            return snapshot, TurnFailed(
                code="unsupported_intent",
                message="This typed planning operation is not available yet.",
            )
        return snapshot, TurnFailed(
            code="invalid_intent", message="The planning intent is not supported."
        )

    def _go_back(
        self, snapshot: PlanningSessionSnapshot
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome | None]:
        """One stage down the ladder the artifacts define, never past a commit.

        Top match wins. Each branch leaves the run loop able to re-present the
        previous stage from what is left: dropping the candidate makes
        `_pending_approval` show the skeleton again; dropping the skeleton and
        holding the activity question is the stage-two card; clearing
        `planning_day` makes `_planning_day_gate` show the day it already has.
        Facts are kept throughout -- back is not forget.

        No skeleton yet means Stage 1: there is no rung between the activity
        question and the day card for it, on purpose. A re-presented Stage 1
        with nothing newly stated is the same proposal `_stage1_outcome`
        already made, and a probe already answered is never re-asked -- Back
        in Stage 1 is the planning-day rung below, which clears the day and
        resets `stage1` to `"open"`, and a fresh `ConfirmPlanningDay` starts
        Stage 1 over rather than resuming it.

        The first rung is the receipt, not the status: `_reopen` puts a
        committed session back to `open` when the user asks for a revision and
        `_invalidate` keeps the receipt, so a status-only guard let Back walk a
        written day -- one press to the activity question, a second clearing
        the `planning_day` of a day already on the calendar, after which
        another day could be picked and committed as a second full day. The
        next commit must be a change to *this* day.
        """

        if has_commit_receipt(snapshot) or snapshot.status == "committed":
            return snapshot, TurnFailed(
                code="session_committed",
                message=(
                    "This day is already on the calendar. Tell me what to "
                    "change and I will revise it."
                ),
            )
        if snapshot.status != "open":
            # Cancelled. There is no ladder in a session that has ended, and
            # falling through the rungs answered as though there were.
            return snapshot, TurnFailed(
                code="session_cancelled",
                message="This session was cancelled. Start a new one to plan a day.",
            )
        if self._latest_artifact(snapshot, ArtifactKind.VALIDATED_CANDIDATE):
            reopened = self._invalidate(snapshot, ArtifactKind.SKELETON)
            return reopened.model_copy(update={"pending_blocker": None}), None
        if self._latest_artifact(snapshot, ArtifactKind.SKELETON):
            without_skeleton = self._invalidate(
                snapshot, ArtifactKind.CAPTURED_INPUTS
            )
            gap = self._requirements.evaluate(
                ArtifactKind.SKELETON, without_skeleton
            ).by_id("skeleton.requested_activity")
            return (
                self._hold_question(without_skeleton, gap, []),
                AwaitingUser(
                    requirement_id=gap.requirement_id,
                    question=gap.question,
                    why_needed=gap.why_needed,
                ),
            )
        if snapshot.planning_day is not None:
            day_ids = {
                artifact.artifact_id
                for artifact in snapshot.artifacts
                if artifact.kind is ArtifactKind.PLANNING_DAY
            }
            return snapshot.model_copy(
                update={
                    "planning_day": None,
                    "pending_blocker": None,
                    # Clearing the day undoes Stage 1's premise -- a future
                    # `ConfirmPlanningDay` starts it over, not resumes it.
                    "stage1": "open",
                    "approvals": [
                        approval
                        for approval in snapshot.approvals
                        if approval.artifact_id not in day_ids
                    ],
                }
            ), None
        return snapshot, TurnFailed(
            code="nothing_to_go_back_to",
            message="This is the first step; there is nothing before it.",
        )

    def _stage1_outcome(
        self, snapshot: PlanningSessionSnapshot, readiness: ReadinessReport
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome]:
        """What Stage 1 shows right now: the top open cell, or a proposal to close.

        The one place `stage1_gate` becomes a `TurnOutcome`, and the run
        loop -- after resolving context and holding any hard user blocker --
        is its only caller: `FileAssumption` and `GoBack` fall through to it
        rather than answering for it, so `GateMet` and the `stage1 ==
        "proposed"` transition happen exactly once per turn, in one place.
        `stage1_gate` itself already subtracts any cell a filed assumption
        answers, so the `Gate` read back here needs no further narrowing.
        """
        gate: Gate = stage1_gate(snapshot)
        if gate.open_cells:
            top = gate.open_cells[0]
            gap = readiness.by_id(top.id)
            return self._hold_question(snapshot, gap, []), AwaitingUser(
                requirement_id=gap.requirement_id,
                question=gap.question,
                why_needed=gap.why_needed,
                gate=gate,
            )
        return snapshot.model_copy(update={"stage1": "proposed"}), GateMet(gate=gate)

    async def _planning_day_gate(
        self, snapshot: PlanningSessionSnapshot, request: TurnRequest
    ) -> tuple[TurnOutcome, PlanningSessionSnapshot]:
        existing = self._latest_artifact(snapshot, ArtifactKind.PLANNING_DAY)
        if existing is not None:
            return AwaitingApproval(artifact=existing), snapshot
        try:
            day = await self._context.propose_planning_day(request)
        except Exception as exc:  # noqa: BLE001 - dependency maps to domain failure
            logger.error(
                "planning day proposal failed: %s",
                exc,
                extra={"error_type": type(exc).__name__},
                exc_info=True,
            )
            return (
                TurnFailed(
                    code="dependency_unavailable",
                    message="The planning day could not be derived.",
                ),
                snapshot,
            )
        artifact = PlanningArtifact.create(
            kind=ArtifactKind.PLANNING_DAY,
            revision=self._next_artifact_revision(
                snapshot, ArtifactKind.PLANNING_DAY
            ),
            payload=day.model_dump(mode="json"),
            dependency_revisions={},
        )
        return (
            AwaitingApproval(artifact=artifact),
            snapshot.model_copy(update={"artifacts": [*snapshot.artifacts, artifact]}),
        )

    def _pending_approval(
        self, snapshot: PlanningSessionSnapshot
    ) -> PlanningArtifact | None:
        for kind in (ArtifactKind.SKELETON, ArtifactKind.VALIDATED_CANDIDATE):
            artifact = self._latest_artifact(snapshot, kind)
            if artifact is not None and not self._is_approved(snapshot, artifact):
                return artifact
        return None

    def _derive_target(
        self, snapshot: PlanningSessionSnapshot
    ) -> ArtifactKind | None:
        # Status, not the receipt: a reopened session carries the receipt of
        # the commit it is now revising, and still has a next artifact.
        if snapshot.status == "committed":
            return None
        skeleton = self._latest_artifact(snapshot, ArtifactKind.SKELETON)
        if skeleton is None:
            return ArtifactKind.SKELETON
        candidate = self._latest_artifact(
            snapshot, ArtifactKind.VALIDATED_CANDIDATE
        )
        if candidate is None:
            return ArtifactKind.VALIDATED_CANDIDATE
        return ArtifactKind.COMMIT_RECEIPT

    async def _commit_candidate(
        self, snapshot: PlanningSessionSnapshot
    ) -> tuple[TurnOutcome, PlanningSessionSnapshot]:
        candidate = self._latest_artifact(
            snapshot, ArtifactKind.VALIDATED_CANDIDATE
        )
        if candidate is None or not self._is_approved(snapshot, candidate):
            return (
                TurnFailed(
                    code="stale_approval",
                    message="The current candidate has not been exactly approved.",
                ),
                snapshot,
            )
        try:
            receipt = await self._commit.commit(candidate, digest=candidate.digest)
        except Exception as exc:  # noqa: BLE001 - external ambiguity is typed
            logger.error(
                "candidate commit failed: %s",
                exc,
                extra={"error_type": type(exc).__name__},
                exc_info=True,
            )
            return (
                TurnFailed(
                    code="ambiguous_external_effect",
                    message="The calendar commit outcome requires inspection.",
                ),
                snapshot,
            )
        if receipt.kind is not ArtifactKind.COMMIT_RECEIPT:
            return (
                TurnFailed(
                    code="invalid_commit_receipt",
                    message="The commit adapter returned an invalid receipt.",
                ),
                snapshot,
            )
        # What the receipt SAYS, not merely that there is one. A receipt is the
        # adapter's report of an external effect, and the kernel used to accept
        # its existence as proof the effect happened: a tmbx refusal came back
        # as `{"committed": false, "reason": "malformed_input"}` and closed the
        # session as committed, against an empty calendar and with no way to
        # try again.
        payload = receipt.payload if isinstance(receipt.payload, dict) else {}
        committed = payload.get("committed")
        if committed is not True:
            if committed is False:
                # An unambiguous refusal: nothing was written, so the day stays
                # committable. Ambiguity never reaches here -- an effect that
                # may have landed raises out of the adapter and is answered
                # above with `ambiguous_external_effect`, which does not invite
                # a retry.
                logger.error(
                    "candidate commit refused",
                    extra={"reason_code": str(payload.get("reason") or "unstated")},
                )
                return (
                    TurnFailed(
                        code="commit_refused",
                        message="The calendar refused this plan. Nothing was changed.",
                    ),
                    snapshot,
                )
            # A receipt that cannot say whether it committed is not evidence
            # that it did. Fail closed: the alternative is a session that
            # believes a calendar it never checked.
            return (
                TurnFailed(
                    code="invalid_commit_receipt",
                    message="The commit adapter returned an invalid receipt.",
                ),
                snapshot,
            )
        # The adapter reports the effect; the kernel owns identity. A port
        # numbers every receipt 1 because it has no session to count in, and
        # two receipts at revision 1 with one id would make the second commit
        # invisible to `_latest_artifact`.
        receipt = PlanningArtifact.create(
            kind=ArtifactKind.COMMIT_RECEIPT,
            revision=self._next_artifact_revision(
                snapshot, ArtifactKind.COMMIT_RECEIPT
            ),
            payload=receipt.payload,
            dependency_revisions=receipt.dependency_revisions,
        )
        updated = snapshot.model_copy(
            update={
                "artifacts": [*snapshot.artifacts, receipt],
                "status": "committed",
            }
        )
        return Committed(receipt=receipt), updated

    def _apply_planning_result(
        self,
        snapshot: PlanningSessionSnapshot,
        target: ArtifactKind,
        readiness: ReadinessReport,
        result: PlanningResult,
        actor_user_id: str,
    ) -> tuple[PlanningSessionSnapshot, TurnOutcome]:
        gaps = {gap.requirement_id: gap for gap in readiness.gaps}
        user_blockers: list[tuple[ReadinessGap, UserBlockerDraft]] = []
        for blocker in result.blockers:
            gap = gaps.get(blocker.requirement_id)
            if gap is None or gap.satisfied:
                logger.error(
                    "planner result refused reason=%s requirement_id=%s known=%s",
                    "blocker_requirement_not_open",
                    blocker.requirement_id,
                    gap is not None,
                )
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner addressed a requirement that is not open.",
                )
            if gap is not None and gap.owner is RequirementOwner.PLANNER:
                # Its three sibling rules name the requirement they tripped on
                # and this one did not, so a live refusal logged one line with
                # no id in it and the answer had to be decompressed out of the
                # harness session log.
                logger.error(
                    "planner result refused reason=%s requirement_id=%s "
                    "resolution=%s",
                    "planner_owned_blocker",
                    blocker.requirement_id,
                    gap.resolution,
                )
                return snapshot, TurnFailed(
                    code="illegal_user_blocker",
                    message="The planner delegated a planner-owned decision.",
                )
            if gap is None or gap.owner is not RequirementOwner.USER:
                logger.error(
                    "planner result refused reason=%s requirement_id=%s owner=%s",
                    "blocker_not_user_owned",
                    blocker.requirement_id,
                    gap.owner.value if gap else None,
                )
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner returned an invalid blocker.",
                )
            user_blockers.append((gap, blocker))

        assumptions: list[PlannerAssumption] = []
        for draft in result.assumptions:
            gap = gaps.get(draft.requirement_id)
            # Three mistakes wore one code, and only one is harmless.
            #
            # An id this turn has never heard of -- another stage's vocabulary --
            # is a filing error on metadata: the judgement it records was really
            # made and the artifact beside it is valid. That cost a whole turn in
            # production, one draw in six, after the patch repair loop had
            # already converged on something tmbx accepted.
            #
            # The other two are not filing errors. Assuming a requirement that is
            # already satisfied asserts a guess where a fact exists, and assuming
            # one the planner does not own decides something belonging to the
            # user or the system. Accepting the artifact would act on either, so
            # both still fail the turn.
            if gap is not None and (
                gap.satisfied or gap.owner is not RequirementOwner.PLANNER
            ):
                logger.error(
                    "planner result refused reason=%s requirement_id=%s known=%s "
                    "satisfied=%s owner=%s open_planner_gaps=%s",
                    "assumption_not_planner_owned",
                    draft.requirement_id,
                    gap is not None,
                    gap.satisfied if gap else None,
                    gap.owner.value if gap else None,
                    sorted(
                        gap.requirement_id
                        for gap in readiness.planner_owned_gaps()
                    ),
                )
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner returned an assumption it does not own.",
                )
            if gap is None:
                # Dropped, never silently: a planner reaching for the wrong
                # stage's vocabulary is a real defect worth seeing, it just is
                # not worth the user's turn.
                logger.warning(
                    "planner assumption dropped reason=%s requirement_id=%s "
                    "known=%s satisfied=%s open_planner_gaps=%s",
                    "assumption_not_open_this_turn",
                    draft.requirement_id,
                    gap is not None,
                    gap.satisfied if gap else None,
                    sorted(
                        gap.requirement_id
                        for gap in readiness.planner_owned_gaps()
                    ),
                )
                continue
            assumptions.append(
                PlannerAssumption(
                    assumption_id=str(uuid4()),
                    requirement_id=draft.requirement_id,
                    value=draft.value,
                    why_needed=draft.why_needed,
                    invalidated_by=draft.invalidated_by,
                )
            )

        if user_blockers:
            gap, blocker = user_blockers[0]
            return self._hold_question(snapshot, gap, blocker.options), AwaitingUser(
                requirement_id=gap.requirement_id,
                question=gap.question,
                why_needed=blocker.why_needed,
                options=blocker.options,
            )

        matching = [
            update for update in result.artifact_updates if update.kind is target
        ]
        if not matching:
            if result.continuation is not None:
                # Nothing to approve yet, but nothing went wrong either: the
                # planner is mid-fix and said so. Keep its assumptions and let
                # it resume, rather than discarding a turn's work because it
                # could not finish inside one.
                return (
                    self._continue_later(snapshot, assumptions),
                    self._another_turn(result),
                )
            return snapshot, TurnFailed(
                code="missing_required_artifact",
                message="The planner did not produce the required artifact.",
            )
        if len(matching) != 1 or len(result.artifact_updates) != 1:
            logger.error(
                "planner result refused reason=%s target=%s kinds=%s",
                "contradictory_artifact_updates",
                target.value,
                [update.kind.value for update in result.artifact_updates],
            )
            return snapshot, TurnFailed(
                code="invalid_planner_result",
                message="The planner returned contradictory artifact updates.",
            )

        draft = matching[0]
        if target is ArtifactKind.VALIDATED_CANDIDATE:
            # The submit tool already refuses this inside the turn, while the
            # planner can still fix it; this is the kernel's own copy of the
            # same set arithmetic, for a host that publishes no required-slug
            # file. A candidate missing a required kind must not reach the
            # user for approval (#214).
            missing = required_slugs(snapshot.facts) - slugs_on_candidate(draft.payload)
            if missing:
                logger.error(
                    "planner result refused reason=%s slugs=%s",
                    "required_block_missing",
                    sorted(missing),
                )
                return snapshot, TurnFailed(
                    code="required_block_missing",
                    message=(
                        "The plan is missing a required block: "
                        + ", ".join(sorted(missing))
                        + "."
                    ),
                )

        updated = self._invalidate(snapshot, target)
        updated = updated.model_copy(
            update={
                "approvals": [
                    approval
                    for approval in updated.approvals
                    if not self._approval_matches_kind(updated, approval, target)
                ]
            }
        )
        artifact = PlanningArtifact.create(
            kind=target,
            revision=self._next_artifact_revision(updated, target),
            payload=draft.payload,
            dependency_revisions=draft.dependency_revisions,
        )
        updated = updated.model_copy(
            update={
                "artifacts": [*updated.artifacts, artifact],
                "assumptions": [*updated.assumptions, *assumptions],
            }
        )
        if result.continuation is not None:
            # It produced something *and* wants to keep going. The artifact is
            # kept -- it is real work -- but it is not offered for approval,
            # because the planner has just said it is not finished.
            return updated, self._another_turn(result)
        return updated, AwaitingApproval(artifact=artifact)

    def _another_turn(self, result: PlanningResult) -> NeedsAnotherTurn:
        """Log it and type it.

        Logged at warning because a planner that asks every turn is a bug, and
        a silent continuation is indistinguishable from slow progress -- which
        is how a loop would hide.
        """

        assert result.continuation is not None
        logger.warning(
            "planner asked for another turn reason=%s", result.continuation.reason
        )
        return NeedsAnotherTurn(reason=result.continuation.reason)

    def _continue_later(
        self,
        snapshot: PlanningSessionSnapshot,
        assumptions: list[PlannerAssumption],
    ) -> PlanningSessionSnapshot:
        """Keep what the turn established, without advancing the stage."""

        if not assumptions:
            return snapshot
        return snapshot.model_copy(
            update={"assumptions": [*snapshot.assumptions, *assumptions]}
        )

    def _build_brief(
        self,
        snapshot: PlanningSessionSnapshot,
        target: ArtifactKind,
        readiness: ReadinessReport,
        context: PlanningContext,
    ) -> PlanningBrief:
        assert snapshot.planning_day is not None
        return PlanningBrief(
            session_key=snapshot.session_key,
            base_revision=snapshot.revision,
            observed_at=context.observed_at,
            locked_day=snapshot.planning_day,
            facts=snapshot.facts,
            assumptions=snapshot.assumptions,
            current_artifacts=[
                ArtifactSnapshot(
                    artifact_id=artifact.artifact_id,
                    kind=artifact.kind,
                    revision=artifact.revision,
                    digest=artifact.digest,
                    payload=artifact.payload,
                )
                for artifact in snapshot.artifacts
            ],
            approvals=snapshot.approvals,
            applicable_constraints=self._unsuspended(
                snapshot, context.applicable_constraints
            ),
            calendar_snapshot=context.calendar_snapshot,
            target_artifact=target,
            readiness={
                "target_artifact": target.value,
                "gaps": [
                    {
                        "requirement_id": gap.requirement_id,
                        "owner": gap.owner.value,
                        "hard": gap.hard,
                        "satisfied": gap.satisfied,
                        "resolution": gap.resolution,
                        "why_needed": gap.why_needed,
                    }
                    for gap in readiness.gaps
                ],
            },
            allowed_outputs={target},
        )

    @staticmethod
    def _unsuspended(snapshot: PlanningSessionSnapshot, rows: JsonValue) -> JsonValue:
        """Rows minus the ones a SUSPENDED_CONSTRAINT fact names, by uid.

        Set membership over identifiers this system minted. The snapshot keeps
        the full list so the card can show a suspended row as suspended; only
        the planner stops seeing it.
        """
        if not isinstance(rows, list):
            return rows
        suspended: set[JsonValue] = set()
        for fact in snapshot.facts:
            if fact.kind is not FactKind.SUSPENDED_CONSTRAINT:
                continue
            if not isinstance(fact.value, dict) or "uid" not in fact.value:
                # A malformed suspension is not the same as no suspension --
                # reading it as absent would let the rule it names through.
                raise ValueError(
                    f"suspended-constraint fact {fact.fact_id!r} carries no uid"
                )
            suspended.add(fact.value["uid"])
        return [row for row in rows if not (isinstance(row, dict) and row.get("uid") in suspended)]

    async def _save(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        base_revision: int,
        request: TurnRequest,
        outcome: TurnOutcome,
    ) -> TurnOutcome:
        try:
            await self._repository.save(
                self._release_question(snapshot, outcome),
                expected_revision=base_revision,
                interaction_id=request.interaction_id,
                outcome=outcome,
            )
        except StaleSessionRevision:
            prior = await self._repository.load_outcome(
                request.session_key, interaction_id=request.interaction_id
            )
            if prior is not None:
                return prior
            return TurnFailed(
                code="stale_session_revision",
                message="The planning session changed while this turn was running.",
            )
        return outcome

    @staticmethod
    def _hold_question(
        snapshot: PlanningSessionSnapshot,
        gap: ReadinessGap,
        options: list[BlockerOption],
    ) -> PlanningSessionSnapshot:
        """Record the question being put, so a press a turn later can be checked.

        The alternative was recomputing the option set when the press arrives,
        which would let a changed plan offer a different set than the one the
        user is looking at -- and the press would then answer a question nobody
        asked. A requirement no fact can satisfy is held as nothing rather than
        as an unanswerable record: the user still gets the question, and every
        press against it is refused.
        """

        fact_kind = next(
            (
                kind
                for kind in gap.requirement.satisfied_by
                if isinstance(kind, FactKind)
            ),
            None,
        )
        return snapshot.model_copy(
            update={
                "pending_blocker": None
                if fact_kind is None
                else PendingBlocker(
                    requirement_id=gap.requirement_id,
                    fact_kind=fact_kind,
                    options=options,
                )
            }
        )

    @staticmethod
    def _offered_option(
        snapshot: PlanningSessionSnapshot, intent: ChooseBlockerOption
    ) -> BlockerOption | None:
        """Return the offered option this press names, or nothing.

        Membership over identifiers the host minted, which is the whole point of
        minting them: nothing here reads what the user meant.
        """

        pending = snapshot.pending_blocker
        if pending is None or pending.requirement_id != intent.requirement_id:
            return None
        return next(
            (
                option
                for option in pending.options
                if option.option_id == intent.option_id
            ),
            None,
        )

    @staticmethod
    def _release_question(
        snapshot: PlanningSessionSnapshot, outcome: TurnOutcome
    ) -> PlanningSessionSnapshot:
        """Stop holding a question the turn is no longer asking.

        An outcome that moved the session on -- an artifact to approve, a
        commit, a cancellation -- means the question is answered or gone, and a
        pending record outliving it would let a second press file an answer
        against whatever replaced it. A ``TurnFailed`` is the exception: a
        refused press means nothing happened, and clearing here would turn one
        recoverable refusal into live-looking buttons with no way to answer.
        """

        if isinstance(outcome, (AwaitingUser, TurnFailed)):
            return snapshot
        if snapshot.pending_blocker is None:
            return snapshot
        return snapshot.model_copy(update={"pending_blocker": None})

    def _invalidate(
        self, snapshot: PlanningSessionSnapshot, changed_kind: ArtifactKind
    ) -> PlanningSessionSnapshot:
        """Something above `changed_kind` moved: what derives from it goes,
        and `changed_kind` itself is shown for approval again."""

        # A receipt is the record of something that happened to the calendar,
        # not something derived from the inputs above it. Invalidation is for
        # what can be rebuilt; history cannot, and a session that forgets it
        # committed will commit the whole day again (#224).
        invalidated = self._requirements.invalidate_from(changed_kind) - {
            ArtifactKind.COMMIT_RECEIPT
        }
        return self._without(
            snapshot, artifacts=invalidated, approvals=invalidated | {changed_kind}
        )

    def _discard(
        self, snapshot: PlanningSessionSnapshot, kind: ArtifactKind
    ) -> PlanningSessionSnapshot:
        """`kind` is being redrafted: it goes, with what derives from it. What
        it was derived from stands, approvals included -- a candidate revised
        is redrafted from the same approved skeleton."""

        gone = (self._requirements.invalidate_from(kind) | {kind}) - {
            ArtifactKind.COMMIT_RECEIPT
        }
        return self._without(snapshot, artifacts=gone, approvals=gone)

    def _without(
        self,
        snapshot: PlanningSessionSnapshot,
        *,
        artifacts: frozenset[ArtifactKind],
        approvals: frozenset[ArtifactKind],
    ) -> PlanningSessionSnapshot:
        withdrawn_ids = {
            artifact.artifact_id
            for artifact in snapshot.artifacts
            if artifact.kind in approvals
        }
        # An assumption is what the planner decided to produce one artifact.
        # Live on 2026-09-03 every planner run appended its assumptions and
        # nothing retired them, so after Back-and-redo the card listed the
        # same placement decision twice, then three times. The artifact an
        # assumption served is the one whose removal retires it.
        return snapshot.model_copy(
            update={
                "artifacts": [
                    artifact
                    for artifact in snapshot.artifacts
                    if artifact.kind not in artifacts
                ],
                "approvals": [
                    approval
                    for approval in snapshot.approvals
                    if approval.artifact_id not in withdrawn_ids
                ],
                "assumptions": [
                    assumption
                    for assumption in snapshot.assumptions
                    if self._requirements.target_of(assumption.requirement_id)
                    not in artifacts
                ],
            }
        )

    @staticmethod
    def _revision_target(
        snapshot: PlanningSessionSnapshot, intent: ReviseArtifact
    ) -> PlanningArtifact | None:
        """The artifact a revision names, only if it is exactly the current one."""

        current = max(
            (a for a in snapshot.artifacts if a.artifact_id == intent.artifact_id),
            key=lambda artifact: artifact.revision,
            default=None,
        )
        if (
            current is None
            or current.revision != intent.artifact_revision
            or current.digest != intent.artifact_digest
        ):
            return None
        return current

    @staticmethod
    def _reopen(snapshot: PlanningSessionSnapshot) -> PlanningSessionSnapshot:
        """A committed day the user has more to say about is open again.

        Only `committed` reopens. `cancelled` is refused upstream and an open
        session is already open; this is not a general status reset.
        """

        if snapshot.status != "committed":
            return snapshot
        return snapshot.model_copy(update={"status": "open"})

    @staticmethod
    def _merge_facts(
        snapshot: PlanningSessionSnapshot, facts: list[PlanningFact]
    ) -> PlanningSessionSnapshot:
        by_id = {fact.fact_id: fact for fact in snapshot.facts}
        for fact in facts:
            by_id[fact.fact_id] = fact
        return snapshot.model_copy(update={"facts": list(by_id.values())})

    @staticmethod
    def _latest_artifact(
        snapshot: PlanningSessionSnapshot, kind: ArtifactKind
    ) -> PlanningArtifact | None:
        return max(
            (artifact for artifact in snapshot.artifacts if artifact.kind is kind),
            key=lambda artifact: artifact.revision,
            default=None,
        )

    @classmethod
    def _next_artifact_revision(
        cls, snapshot: PlanningSessionSnapshot, kind: ArtifactKind
    ) -> int:
        latest = cls._latest_artifact(snapshot, kind)
        return 1 if latest is None else latest.revision + 1

    @staticmethod
    def _is_approved(
        snapshot: PlanningSessionSnapshot, artifact: PlanningArtifact
    ) -> bool:
        return any(
            approval.artifact_id == artifact.artifact_id
            and approval.artifact_revision == artifact.revision
            and approval.artifact_digest == artifact.digest
            for approval in snapshot.approvals
        )

    @classmethod
    def _artifact_matching_approval(
        cls, snapshot: PlanningSessionSnapshot, intent: ApproveArtifact
    ) -> PlanningArtifact | None:
        for kind in (ArtifactKind.SKELETON, ArtifactKind.VALIDATED_CANDIDATE):
            artifact = cls._latest_artifact(snapshot, kind)
            if (
                artifact is not None
                and artifact.artifact_id == intent.artifact_id
                and artifact.revision == intent.artifact_revision
                and artifact.digest == intent.artifact_digest
            ):
                return artifact
        return None

    @staticmethod
    def _approval_matches_kind(
        snapshot: PlanningSessionSnapshot,
        approval: ArtifactApproval,
        kind: ArtifactKind,
    ) -> bool:
        return any(
            artifact.kind is kind and artifact.artifact_id == approval.artifact_id
            for artifact in snapshot.artifacts
        )


__all__ = [
    "AdaptiveTimeboxing",
    "CommitPort",
    "InMemoryPlanningSessionRepository",
    "OpenSessionRow",
    "PlannerPort",
    "PlanningContext",
    "PlanningContextPort",
    "PlanningSessionRepository",
    "ProgressSink",
    "StaleSessionRevision",
    "TimeboxingSessionLedger",
    "TimeboxingStanding",
    "TurnRequest",
]
