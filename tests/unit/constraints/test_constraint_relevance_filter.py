"""Tests for _is_stage_relevant_constraint — Bug A: aspect-classified constraints
should only be included when their aspect_id is present in the session."""

from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintStatus,
)
from fateforger.agents.timeboxing.stage_gating import TimeboxingStage

_fn = TimeboxingFlowAgent._is_stage_relevant_constraint


def _make_aspect_constraint(
    aspect_id: str,
    *,
    frame_slot: str | None = None,
    schedule_start: str | None = None,
    schedule_end: str | None = None,
    is_startup_prefetch: bool = False,
    scope: ConstraintScope = ConstraintScope.PROFILE,
    status: ConstraintStatus = ConstraintStatus.LOCKED,
    necessity: ConstraintNecessity = ConstraintNecessity.MUST,
) -> Constraint:
    aspect_cls = {
        "aspect_id": aspect_id,
        "frame_slot": frame_slot,
        "schedule_start": schedule_start,
        "schedule_end": schedule_end,
        "is_startup_prefetch": is_startup_prefetch,
    }
    return Constraint(
        name=f"Test constraint ({aspect_id})",
        scope=scope,
        status=status,
        necessity=necessity,
        hints={"aspect_classification": aspect_cls},
    )


def _make_plain_constraint(
    name: str = "Plain constraint",
    *,
    scope: ConstraintScope = ConstraintScope.PROFILE,
    status: ConstraintStatus = ConstraintStatus.LOCKED,
    necessity: ConstraintNecessity = ConstraintNecessity.MUST,
    hints: dict | None = None,
) -> Constraint:
    return Constraint(
        name=name,
        scope=scope,
        status=status,
        necessity=necessity,
        hints=hints or {},
    )


# ── Bug A tests ──────────────────────────────────────────────────────────────

class TestAspectClassifiedNotInSession:
    """Bug A: aspect-classified constraints with no session match must be excluded."""

    def test_market_visit_excluded_when_not_in_session_collect(self):
        """'Market opening hours' (aspect_id=market_visit) must not appear at
        CollectConstraints when no market visit is in the session."""
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is False

    def test_market_visit_excluded_when_not_in_session_skeleton(self):
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is False

    def test_market_visit_excluded_when_not_in_session_capture_inputs(self):
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.CAPTURE_INPUTS,
            session_aspect_ids=set(),
        )
        assert result is False

    def test_market_visit_excluded_when_not_in_session_refine(self):
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.REFINE,
            session_aspect_ids=set(),
        )
        assert result is False


class TestAspectClassifiedInSession:
    """Aspect-classified constraints ARE included when their aspect_id is present."""

    def test_market_visit_included_when_in_session(self):
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids={"market_visit"},
        )
        assert result is True

    def test_market_visit_included_at_collect_when_in_session(self):
        constraint = _make_aspect_constraint(
            "market_visit", schedule_start="08:00", schedule_end="16:00"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids={"market_visit"},
        )
        assert result is True


class TestFrameSlotAlwaysIncluded:
    """Constraints with a frame_slot (fixed daily routines) are always included."""

    def test_frame_slot_included_without_session_match(self):
        constraint = _make_aspect_constraint("morning_ritual", frame_slot="morning")
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_frame_slot_included_at_skeleton(self):
        constraint = _make_aspect_constraint("gym", frame_slot="evening")
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is True


class TestStartupPrefetchAlwaysIncluded:
    """is_startup_prefetch=True constraints always included."""

    def test_startup_prefetch_included_without_session_match(self):
        constraint = _make_aspect_constraint(
            "sleep_window", is_startup_prefetch=True, schedule_start="23:30"
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True


class TestSessionScopedAlwaysIncluded:
    """SESSION-scoped constraints (extracted from user input / calendar) always included."""

    def test_session_scoped_always_included(self):
        constraint = _make_aspect_constraint(
            "gym",
            scope=ConstraintScope.SESSION,
            status=ConstraintStatus.PROPOSED,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True


class TestFrameSlotAnchorFilter:
    """Any non-null frame_slot must be treated as a startup anchor, not just sleep/work."""

    def test_dinner_frame_slot_passes_filter(self):
        constraint = _make_aspect_constraint("dinner_slot", frame_slot="dinner")
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_morning_ritual_frame_slot_passes_filter(self):
        constraint = _make_aspect_constraint("morning_routine", frame_slot="morning_ritual")
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_custom_slug_frame_slot_passes_filter(self):
        constraint = _make_aspect_constraint("sax_slot", frame_slot="saxophone_practice")
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is True


class TestMustLockedNonAspectConstraints:
    """MUST+LOCKED profile constraints without aspect_classification still pass (e.g. Commute Duration)."""

    def test_commute_duration_always_included(self):
        constraint = _make_plain_constraint(
            "Commute Duration",
            necessity=ConstraintNecessity.MUST,
            status=ConstraintStatus.LOCKED,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_oats_timing_always_included(self):
        constraint = _make_plain_constraint(
            "Oats Timing",
            necessity=ConstraintNecessity.MUST,
            status=ConstraintStatus.LOCKED,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is True


class TestProfileProposedMustIncluded:
    """PROFILE/PROPOSED/MUST constraints (user lifestyle preferences) must be loaded.

    Bug: The MUST+LOCKED gate was too strict — PROPOSED constraints were silently
    dropped even though they represent the user's stated durable preferences.
    """

    def test_evening_shutdown_ritual_included(self):
        """Evening Shutdown Ritual is PROFILE/PROPOSED/MUST — must be loaded."""
        constraint = _make_plain_constraint(
            "Evening Shutdown Ritual",
            scope=ConstraintScope.PROFILE,
            status=ConstraintStatus.PROPOSED,
            necessity=ConstraintNecessity.MUST,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_dinner_included(self):
        constraint = _make_plain_constraint(
            "Dinner",
            scope=ConstraintScope.PROFILE,
            status=ConstraintStatus.PROPOSED,
            necessity=ConstraintNecessity.MUST,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_shutdown_ritual_included_at_refine(self):
        constraint = _make_plain_constraint(
            "Shutdown Ritual",
            scope=ConstraintScope.PROFILE,
            status=ConstraintStatus.PROPOSED,
            necessity=ConstraintNecessity.MUST,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.REFINE,
            session_aspect_ids=set(),
        )
        assert result is True

    def test_profile_proposed_should_still_excluded(self):
        """PROFILE/PROPOSED/SHOULD (lower-priority preferences) are NOT loaded — to avoid noise."""
        constraint = _make_plain_constraint(
            "Sci-fi Reading",
            scope=ConstraintScope.PROFILE,
            status=ConstraintStatus.PROPOSED,
            necessity=ConstraintNecessity.SHOULD,
        )
        result = _fn(
            constraint=constraint,
            stage=TimeboxingStage.SKELETON,
            session_aspect_ids=set(),
        )
        assert result is False
