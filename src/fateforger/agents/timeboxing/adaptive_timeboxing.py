"""Artifact-led planning-session orchestration with an in-memory repository."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .readiness import (
    ReadinessGap,
    ReadinessReport,
    RequirementOwner,
    TimeboxRequirements,
)
from .session_contracts import (
    Advance,
    ApproveArtifact,
    ArtifactApproval,
    ArtifactKind,
    ArtifactSnapshot,
    AwaitingApproval,
    AwaitingUser,
    BlockerOption,
    Cancelled,
    CancelSession,
    ChooseBlockerOption,
    Committed,
    ConfirmPlanningDay,
    FactKind,
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
    ReviseArtifact,
    StartSession,
    TimeboxIntent,
    TurnFailed,
    TurnOutcome,
    UserBlockerDraft,
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


class StaleSessionRevision(RuntimeError):
    """The persisted session no longer matches the expected revision."""


class InMemoryPlanningSessionRepository:
    """Revision-safe repository used by unit and deterministic replay tests."""

    def __init__(
        self, snapshots: list[PlanningSessionSnapshot] | None = None
    ) -> None:
        snapshots = snapshots or []
        self._snapshots = {
            snapshot.session_key: snapshot.model_copy(deep=True)
            for snapshot in snapshots
        }
        self._outcomes: dict[tuple[str, str], TurnOutcome] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def session_guard(self, session_key: str) -> AsyncIterator[None]:
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            yield

    async def load_or_create(
        self, session_key: str, *, owner_user_id: str
    ) -> PlanningSessionSnapshot:
        snapshot = self._snapshots.get(session_key)
        if snapshot is None:
            snapshot = PlanningSessionSnapshot.new(
                session_key=session_key, owner_user_id=owner_user_id
            )
            self._snapshots[session_key] = snapshot
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
        self._outcomes[replay_key] = deepcopy(outcome)
        return saved.model_copy(deep=True)


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

        base_revision = snapshot.revision
        progress_sink = _BestEffortProgress(progress)
        applied, intent_failure = self._apply_intent(snapshot, request)
        if intent_failure is not None:
            return await self._save(
                applied,
                base_revision=base_revision,
                request=request,
                outcome=intent_failure,
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
                "planning context resolution failed",
                extra={"error_type": type(exc).__name__},
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
        except Exception as exc:  # noqa: BLE001 - provider details stay behind port
            logger.error(
                "planner invocation failed",
                extra={"error_type": type(exc).__name__},
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
    ) -> tuple[PlanningSessionSnapshot, TurnFailed | None]:
        intent = request.intent
        if isinstance(intent, (StartSession, Advance)):
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
                }
            ), None
        if isinstance(intent, ProvidePlanningFacts):
            merged = self._merge_facts(snapshot, intent.facts)
            if merged.facts == snapshot.facts:
                return snapshot, None
            return self._invalidate(merged, ArtifactKind.CAPTURED_INPUTS), None
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
        if isinstance(intent, (ReviseArtifact, GoBack)):
            return snapshot, TurnFailed(
                code="unsupported_intent",
                message="This typed planning operation is not available yet.",
            )
        return snapshot, TurnFailed(
            code="invalid_intent", message="The planning intent is not supported."
        )

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
                "planning day proposal failed",
                extra={"error_type": type(exc).__name__},
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
        receipt = self._latest_artifact(snapshot, ArtifactKind.COMMIT_RECEIPT)
        if receipt is not None:
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
                "candidate commit failed",
                extra={"error_type": type(exc).__name__},
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
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner addressed a requirement that is not open.",
                )
            if gap is not None and gap.owner is RequirementOwner.PLANNER:
                return snapshot, TurnFailed(
                    code="illegal_user_blocker",
                    message="The planner delegated a planner-owned decision.",
                )
            if gap is None or gap.owner is not RequirementOwner.USER:
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner returned an invalid blocker.",
                )
            user_blockers.append((gap, blocker))

        assumptions: list[PlannerAssumption] = []
        for draft in result.assumptions:
            gap = gaps.get(draft.requirement_id)
            if (
                gap is None
                or gap.satisfied
                or gap.owner is not RequirementOwner.PLANNER
            ):
                return snapshot, TurnFailed(
                    code="invalid_planner_result",
                    message="The planner returned an assumption it does not own.",
                )
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
            return snapshot, TurnFailed(
                code="missing_required_artifact",
                message="The planner did not produce the required artifact.",
            )
        if len(matching) != 1 or len(result.artifact_updates) != 1:
            return snapshot, TurnFailed(
                code="invalid_planner_result",
                message="The planner returned contradictory artifact updates.",
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
        draft = matching[0]
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
        return updated, AwaitingApproval(artifact=artifact)

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
            applicable_constraints=context.applicable_constraints,
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
        invalidated = self._requirements.invalidate_from(changed_kind)
        approval_invalidated_kinds = invalidated | {changed_kind}
        invalidated_approval_ids = {
            artifact.artifact_id
            for artifact in snapshot.artifacts
            if artifact.kind in approval_invalidated_kinds
        }
        return snapshot.model_copy(
            update={
                "artifacts": [
                    artifact
                    for artifact in snapshot.artifacts
                    if artifact.kind not in invalidated
                ],
                "approvals": [
                    approval
                    for approval in snapshot.approvals
                    if approval.artifact_id not in invalidated_approval_ids
                ],
            }
        )

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
    "PlannerPort",
    "PlanningContext",
    "PlanningContextPort",
    "PlanningSessionRepository",
    "ProgressSink",
    "StaleSessionRevision",
    "TurnRequest",
]
