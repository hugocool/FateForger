"""Typed ownership and dependency rules for artifact-led timeboxing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from .session_contracts import (
    ArtifactKind,
    CellRef,
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
    #: Which of the five stages this requirement's question belongs to. The
    #: card takes its stage from here, so the ladder is a property of the
    #: catalog and not of a map from fact kinds (#276).
    stage: int
    #: Present only for a Stage 1 coverage cell. Satisfaction of a cell is read
    #: from the matrix fact, not from the presence of a statement: one answer
    #: does not satisfy forty questions.
    cell: CellRef | None = None


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
        stage=1,
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
        stage=2,
    ),
    ArtifactRequirement(
        # Listed after the activity on purpose: first_hard_user_blocker() asks
        # one question a turn, and "what is the day for" comes before "when
        # does it start and end". The planner never satisfies this one -- it is
        # user-owned, so an assumption filed against it is refused by the
        # kernel, which is the whole point (#251).
        requirement_id="skeleton.day_frame",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(FactKind.DAY_FRAME,),
        owner=RequirementOwner.USER,
        hard=True,
        why_needed="a skeleton is laid inside the hours you are up",
        resolution="ask",
        question=(
            "When are you getting up, and when do you want to be asleep? "
            "Nothing on record says so for this day."
        ),
        stage=1,
    ),
    ArtifactRequirement(
        # Which names are unreadable is a judgement about what the user meant,
        # so the catalog cannot compute satisfaction; it holds the requirement
        # open, user-owned and soft, for the planner to raise a blocker
        # against with its proposed readings as the options. Soft, because a
        # readable name needs no answer and an open soft gap stops nothing --
        # first_hard_user_blocker() skips it and no other check reads it. On
        # 2026-09-02 the typo "agent-in-ysis" became a calendar title verbatim
        # because there was no id a blocker about it could name (#251).
        requirement_id="skeleton.activity_reading",
        target_artifact=ArtifactKind.SKELETON,
        satisfied_by=(FactKind.ACTIVITY_READING,),
        owner=RequirementOwner.USER,
        hard=False,
        why_needed="a block title must be a name the planner could read",
        resolution="ask",
        question="I could not read one of those. Which did you mean?",
        stage=2,
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
        stage=2,
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
        stage=3,
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
        stage=4,
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
        stage=4,
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
        stage=4,
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
        stage=5,
    ),
)


def _cell_requirements() -> tuple[ArtifactRequirement, ...]:
    """Forty requirements from two fixed lists. Soft, so none is a hard
    blocker; the elicitor picks which to ask, so catalog order means nothing."""
    from .elicitation import ALL_CELLS, CRITERION_BY_KEY, ROWS

    return tuple(
        ArtifactRequirement(
            requirement_id=cell.id,
            target_artifact=ArtifactKind.SKELETON,
            satisfied_by=(FactKind.ELICITED_STATEMENT,),
            owner=RequirementOwner.USER,
            hard=False,
            why_needed=ROWS[cell.row].label,
            resolution="ask",
            question=CRITERION_BY_KEY[cell.criterion].question,
            stage=1,
            cell=cell,
        )
        for cell in ALL_CELLS
    )


_CATALOG: tuple[ArtifactRequirement, ...] = (*_REQUIREMENTS, *_cell_requirements())

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
                for requirement in _CATALOG
                if requirement.target_artifact is target_artifact
            ),
        )

    @staticmethod
    def target_of(requirement_id: str) -> ArtifactKind | None:
        """Which artifact a requirement exists to produce; None if unknown.

        An assumption is recorded against a requirement id, and the artifact
        that id serves is the one whose disappearance retires the assumption.
        """

        for requirement in _CATALOG:
            if requirement.requirement_id == requirement_id:
                return requirement.target_artifact
        return None

    @staticmethod
    def stage_of(requirement_id: str) -> int:
        """The stage a requirement's question belongs to. KeyError for an id
        the catalog does not know: a question with no stage is a defect, not
        a stage-two question."""

        for requirement in _CATALOG:
            if requirement.requirement_id == requirement_id:
                return requirement.stage
        raise KeyError(requirement_id)

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
        if requirement.requirement_id == "candidate.approved_skeleton":
            return self._has_exact_approval(snapshot, ArtifactKind.SKELETON)
        if requirement.requirement_id == "commit.approved_candidate":
            return self._has_exact_approval(snapshot, ArtifactKind.VALIDATED_CANDIDATE)
        if requirement.cell is not None:
            from .elicitation import coverage_matrix

            matrix = coverage_matrix(snapshot)
            if matrix is None:
                return True
            return matrix.cells.get(requirement.requirement_id) != "uncovered"

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


__all__ = [
    "ArtifactRequirement",
    "ReadinessGap",
    "ReadinessReport",
    "RequirementOwner",
    "TimeboxRequirements",
]
