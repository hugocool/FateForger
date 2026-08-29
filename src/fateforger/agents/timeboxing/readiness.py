"""Typed ownership and dependency rules for artifact-led timeboxing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .session_contracts import (
    ArtifactKind,
    FactKind,
    PlanningArtifact,
    PlanningFact,
    PlanningSessionSnapshot,
)


class RequirementOwner(StrEnum):
    """The actor responsible for resolving a missing requirement."""

    PLANNER = "planner"
    SYSTEM = "system"
    USER = "user"


RequirementResolution = Literal["assume", "ask", "fetch", "validate"]


@dataclass(frozen=True, slots=True)
class ArtifactRequirement:
    """A typed dependency needed to produce one target artifact."""

    requirement_id: str
    target_artifact: ArtifactKind
    satisfied_by: tuple[FactKind | ArtifactKind, ...]
    owner: RequirementOwner
    hard: bool
    why_needed: str
    resolution: RequirementResolution
    #: What to put to the user when this requirement is what stands in the way.
    #: The catalog owns this because the alternative was rendering the
    #: requirement_id, and "Please provide skeleton.requested_activity." is a
    #: sentence written for a debugger, shown to the person being asked.
    question: str


@dataclass(frozen=True, slots=True)
class ReadinessGap:
    """The current satisfaction state of one artifact requirement."""

    requirement: ArtifactRequirement
    satisfied: bool

    @property
    def requirement_id(self) -> str:
        return self.requirement.requirement_id

    @property
    def owner(self) -> RequirementOwner:
        return self.requirement.owner

    @property
    def hard(self) -> bool:
        return self.requirement.hard

    @property
    def why_needed(self) -> str:
        return self.requirement.why_needed

    @property
    def resolution(self) -> RequirementResolution:
        return self.requirement.resolution

    @property
    def question(self) -> str:
        return self.requirement.question


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """A complete, typed readiness assessment for one target artifact."""

    target_artifact: ArtifactKind
    gaps: tuple[ReadinessGap, ...]

    def by_id(self, requirement_id: str) -> ReadinessGap:
        """Return one requirement assessment by its stable requirement ID."""

        for gap in self.gaps:
            if gap.requirement_id == requirement_id:
                return gap
        raise KeyError(requirement_id)

    def first_hard_user_blocker(self) -> ReadinessGap | None:
        """Return the first unresolved hard requirement owned by the user."""

        return next(
            (
                gap
                for gap in self.gaps
                if not gap.satisfied and gap.owner is RequirementOwner.USER and gap.hard
            ),
            None,
        )

    def planner_owned_gaps(self) -> tuple[ReadinessGap, ...]:
        """Return unresolved decisions that must stay with the planner."""

        return tuple(
            gap
            for gap in self.gaps
            if not gap.satisfied and gap.owner is RequirementOwner.PLANNER
        )

    def system_owned_gaps(self) -> tuple[ReadinessGap, ...]:
        """Return unresolved requirements for the host to fetch or validate."""

        return tuple(
            gap
            for gap in self.gaps
            if not gap.satisfied and gap.owner is RequirementOwner.SYSTEM
        )


_REQUIREMENTS: tuple[ArtifactRequirement, ...] = (
    ArtifactRequirement(
        requirement_id="skeleton.locked_day",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(ArtifactKind.PLANNING_DAY,),
        owner=RequirementOwner.SYSTEM,
        hard=True,
        why_needed="a skeleton must be planned for a locked day",
        resolution="validate",
        question="Which day are we planning?",
    ),
    ArtifactRequirement(
        requirement_id="skeleton.requested_activity",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(FactKind.REQUESTED_ACTIVITY,),
        owner=RequirementOwner.USER,
        hard=True,
        why_needed="a skeleton needs at least one intended activity or goal",
        resolution="ask",
        question="What do you want to get out of the day?",
    ),
    ArtifactRequirement(
        requirement_id="skeleton.ordinary_placement",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(FactKind.ORDINARY_PLACEMENT,),
        owner=RequirementOwner.PLANNER,
        hard=True,
        why_needed="ordinary activities need a feasible placement in the day",
        resolution="assume",
        question="Where should the flexible blocks go?",
    ),
    ArtifactRequirement(
        requirement_id="skeleton.gym_placement",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(FactKind.GYM_PLACEMENT,),
        owner=RequirementOwner.PLANNER,
        hard=True,
        why_needed="gym needs a feasible placement when no fixed time is supplied",
        resolution="assume",
        question="When would you like the gym?",
    ),
    ArtifactRequirement(
        requirement_id="candidate.approved_skeleton",
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        satisfied_by=(ArtifactKind.SKELETON,),
        owner=RequirementOwner.SYSTEM,
        hard=True,
        why_needed="a candidate may only refine the exact approved skeleton",
        resolution="validate",
        question="Shall I work from the outline above?",
    ),
    ArtifactRequirement(
        requirement_id="candidate.calendar_snapshot",
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        satisfied_by=(FactKind.CALENDAR_SNAPSHOT,),
        owner=RequirementOwner.SYSTEM,
        hard=True,
        why_needed="a candidate must account for the current calendar",
        resolution="fetch",
        question="I could not read the calendar for that day. Try again?",
    ),
    ArtifactRequirement(
        requirement_id="candidate.active_constraints",
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        satisfied_by=(FactKind.ACTIVE_CONSTRAINTS,),
        owner=RequirementOwner.SYSTEM,
        hard=True,
        why_needed="a candidate must account for active planning constraints",
        resolution="fetch",
        question="I could not read your saved rules. Try again?",
    ),
    ArtifactRequirement(
        requirement_id="candidate.concrete_placements",
        target_artifact=ArtifactKind.VALIDATED_CANDIDATE,
        satisfied_by=(FactKind.CONCRETE_PLACEMENTS,),
        owner=RequirementOwner.PLANNER,
        hard=True,
        why_needed="a candidate needs concrete feasible placements",
        resolution="assume",
        question="Some blocks still need a time. Shall I choose?",
    ),
    ArtifactRequirement(
        requirement_id="commit.approved_candidate",
        target_artifact=ArtifactKind.COMMIT_RECEIPT,
        satisfied_by=(ArtifactKind.VALIDATED_CANDIDATE,),
        owner=RequirementOwner.SYSTEM,
        hard=True,
        why_needed="a calendar commit requires the exact approved candidate",
        resolution="validate",
        question="Shall I put this on the calendar?",
    ),
)

_DIRECT_DEPENDENTS: dict[ArtifactKind, frozenset[ArtifactKind]] = {
    ArtifactKind.PLANNING_DAY: frozenset({ArtifactKind.DAY_FRAME}),
    ArtifactKind.DAY_FRAME: frozenset({ArtifactKind.CAPTURED_INPUTS}),
    ArtifactKind.CAPTURED_INPUTS: frozenset({ArtifactKind.PLANNING_BRIEF}),
    ArtifactKind.PLANNING_BRIEF: frozenset({ArtifactKind.SKELETON}),
    ArtifactKind.SKELETON: frozenset({ArtifactKind.VALIDATED_CANDIDATE}),
    ArtifactKind.VALIDATED_CANDIDATE: frozenset({ArtifactKind.COMMIT_RECEIPT}),
    ArtifactKind.COMMIT_RECEIPT: frozenset(),
}


class TimeboxRequirements:
    """Evaluate typed requirements and downstream artifact invalidation."""

    def evaluate(
        self,
        target_artifact: ArtifactKind,
        snapshot: PlanningSessionSnapshot,
    ) -> ReadinessReport:
        """Assess only the requirements relevant to ``target_artifact``."""

        return ReadinessReport(
            target_artifact=target_artifact,
            gaps=tuple(
                ReadinessGap(
                    requirement=requirement,
                    satisfied=self._is_satisfied(requirement, snapshot),
                )
                for requirement in _REQUIREMENTS
                if requirement.target_artifact is target_artifact
            ),
        )

    def invalidate_from(
        self, changed_artifact: ArtifactKind
    ) -> frozenset[ArtifactKind]:
        """Return descendants to discard along with approvals bound to them."""

        invalidated: set[ArtifactKind] = set()
        pending = list(_DIRECT_DEPENDENTS[changed_artifact])
        while pending:
            artifact = pending.pop()
            if artifact in invalidated:
                continue
            invalidated.add(artifact)
            pending.extend(_DIRECT_DEPENDENTS[artifact])
        return frozenset(invalidated)

    def _is_satisfied(
        self,
        requirement: ArtifactRequirement,
        snapshot: PlanningSessionSnapshot,
    ) -> bool:
        if requirement.requirement_id == "skeleton.locked_day":
            return snapshot.planning_day is not None
        if requirement.requirement_id == "skeleton.gym_placement":
            return not self._has_fact(snapshot.facts, FactKind.GYM) or self._has_fact(
                snapshot.facts, FactKind.GYM_PLACEMENT
            )
        if requirement.requirement_id == "candidate.approved_skeleton":
            return self._has_exact_approval(snapshot, ArtifactKind.SKELETON)
        if requirement.requirement_id == "commit.approved_candidate":
            return self._has_exact_approval(snapshot, ArtifactKind.VALIDATED_CANDIDATE)

        return all(
            self._has_fact(snapshot.facts, kind)
            for kind in requirement.satisfied_by
            if isinstance(kind, FactKind)
        )

    @staticmethod
    def _has_fact(facts: list[PlanningFact], kind: FactKind) -> bool:
        return any(fact.kind is kind for fact in facts)

    @staticmethod
    def _has_exact_approval(
        snapshot: PlanningSessionSnapshot,
        kind: ArtifactKind,
    ) -> bool:
        artifact = TimeboxRequirements._latest_artifact(snapshot.artifacts, kind)
        if artifact is None:
            return False
        return any(
            approval.artifact_id == artifact.artifact_id
            and approval.artifact_revision == artifact.revision
            and approval.artifact_digest == artifact.digest
            for approval in snapshot.approvals
        )

    @staticmethod
    def _latest_artifact(
        artifacts: list[PlanningArtifact], kind: ArtifactKind
    ) -> PlanningArtifact | None:
        matching = [artifact for artifact in artifacts if artifact.kind is kind]
        return max(matching, key=lambda artifact: artifact.revision, default=None)


def invalidate_from(changed_artifact: ArtifactKind) -> frozenset[ArtifactKind]:
    """Return artifacts invalidated by a changed artifact.

    Callers must discard approvals bound to each returned artifact at the same
    time, because every approval is tied to one exact artifact identity.
    """

    return TimeboxRequirements().invalidate_from(changed_artifact)


__all__ = [
    "ArtifactRequirement",
    "ReadinessGap",
    "ReadinessReport",
    "RequirementOwner",
    "TimeboxRequirements",
    "invalidate_from",
]
