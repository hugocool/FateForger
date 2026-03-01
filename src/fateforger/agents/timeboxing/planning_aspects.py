"""
Planning-aspects graph model.

A *planning aspect* is anything the user cares about when building their day:
a recurring activity, a time-bound commitment, a lifestyle preference, a care
responsibility. Aspects are typed by an open ``category`` string (no fixed
enum) so the taxonomy grows with the user — "gym", "field_hockey", "dog_walk",
"school_run" are all valid alongside the well-known seeds.

Graph structure
---------------
- :class:`PlanningAspect`      — node: one area of life / activity
- :class:`ExclusionRelation`   — directed edge: A present ⇒ B should not be
- :class:`ConditionalPreference` — directed edge: A absent ⇒ prefer B

Seed categories
---------------
``SEED_ASPECT_CATEGORIES`` contains well-known category slugs so callers can
reference them without hard-coding bare strings, but they are *not* enforced.
Any LLM-generated string is a valid ``category``.

LLM classification contract
-----------------------------
When a constraint is extracted, the LLM assignes a
:class:`ConstraintAspectClassification` object and stores it as
``hints["aspect_classification"]``. Agent code that previously used keyword
scanning or regex must read this field instead.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Seed category slugs — open strings, not an enum
# ---------------------------------------------------------------------------


class SeedAspectCategory:
    """Well-known ``category`` slugs for common planning aspects.

    Import these to avoid bare string literals in code that branches on
    category. The values are intentionally lower-snake-case and stable.
    """

    SLEEP = "sleep"
    WORK = "work"
    EXERCISE = "exercise"
    FAMILY = "family"
    PET = "pet"
    SOCIAL = "social"
    TRANSPORT = "transport"
    HOBBY = "hobby"
    NUTRITION = "nutrition"
    HEALTH = "health"
    LEARNING = "learning"


# ---------------------------------------------------------------------------
# LLM classification record — stored in hints["aspect_classification"]
# ---------------------------------------------------------------------------


class ConstraintAspectClassification(BaseModel):
    """Structured metadata the LLM assigns to a constraint at extraction time.

    Stored as ``constraint.hints["aspect_classification"]`` (serialised as a
    plain dict). Agent code that reads this field MUST NOT fall back to
    keyword or regex scanning; use ``None`` / the field's default instead.

    Attributes
    ----------
    aspect_id:
        Stable, LLM-assigned slug for the planning aspect this constraint
        belongs to (e.g. ``"gym_training"``, ``"field_hockey"``,
        ``"morning_routine"``). Used as the graph-node identifier.
    aspect_label:
        Human-readable display name (e.g. ``"Gym training"``).
    category:
        Open-string category slug. Use :class:`SeedAspectCategory` constants
        for the common cases; any string is valid.
    frame_slot:
        If the constraint maps to a well-known contracts frame slot for legacy
        integration, set this (e.g. ``"sleep_target"``, ``"work_window"``).
        ``None`` when there is no frame-slot mapping.
    is_startup_prefetch:
        ``True`` when this constraint should be loaded at Stage-1 start so
        the agent can reason about the day without waiting for the full
        retrieval. Set this for constraints that anchor the day (sleep
        schedule, work window, key transport).
    schedule_start:
        ``HH:MM`` if the constraint encodes a known start time (e.g. wake-up
        time, work start). ``None`` when no concrete time was stated.
    schedule_end:
        ``HH:MM`` if the constraint encodes a known end time. ``None``
        otherwise.
    duration_min:
        Minimum or typical duration in minutes if stated. ``None`` otherwise.
    is_conditional:
        ``True`` when this constraint only applies given other aspects being
        present or absent (e.g. "if I have a late meeting, skip the gym").
    conditional_on_absent:
        List of ``aspect_id`` values that must be *absent* (not confirmed on
        the day) for this constraint to apply.
    conditional_on_present:
        List of ``aspect_id`` values that must be *present* for this
        constraint to apply.
    excludes_aspect_ids:
        When this aspect is confirmed, the listed ``aspect_id`` values should
        not be suggested. Drives :class:`ExclusionRelation` graph edges.
    """

    aspect_id: str
    aspect_label: str
    category: str
    frame_slot: str | None = None
    is_startup_prefetch: bool = False
    schedule_start: str | None = None  # HH:MM
    schedule_end: str | None = None  # HH:MM
    duration_min: int | None = None
    is_conditional: bool = False
    conditional_on_absent: list[str] = Field(default_factory=list)
    conditional_on_present: list[str] = Field(default_factory=list)
    excludes_aspect_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_hints(
        cls, hints: dict[str, Any]
    ) -> "ConstraintAspectClassification | None":
        """Deserialise from a constraint ``hints`` dict; returns ``None`` on failure."""
        raw = hints.get("aspect_classification") if isinstance(hints, dict) else None
        if not isinstance(raw, dict):
            return None
        try:
            return cls.model_validate(raw)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Graph nodes and edges
# ---------------------------------------------------------------------------


class PlanningAspect(BaseModel):
    """One activity or area-of-life that shapes how a day should be planned.

    Attributes
    ----------
    aspect_id:
        Stable slug, LLM-assigned (e.g. ``"gym_training"``). Used as the
        node identifier in the graph.
    label:
        Human-readable name.
    category:
        Open category string. Use :class:`SeedAspectCategory` for common
        values.
    is_confirmed:
        ``True`` when a calendar event for this aspect already exists on the
        session day (set by the calendar-pass resolver, not by the LLM).
    is_desired:
        ``False`` when the user has said they *don't* want this aspect
        today (e.g. "skip the gym today").
    desire_strength:
        Soft-preference weight in ``[0.0, 1.0]``. 1.0 = locked/must-have;
        0.0 = explicitly excluded. Default 0.5.
    schedule_start:
        Preferred or required start time (``HH:MM``). ``None`` when flexible.
    schedule_end:
        Preferred or required end time (``HH:MM``). ``None`` when flexible.
    duration_min:
        Typical or minimum duration in minutes. ``None`` when unknown.
    """

    aspect_id: str
    label: str
    category: str
    is_confirmed: bool = False
    is_desired: bool = True
    desire_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    schedule_start: str | None = None  # HH:MM
    schedule_end: str | None = None  # HH:MM
    duration_min: int | None = None


class ExclusionRelation(BaseModel):
    """An edge expressing: if *aspect_a* is confirmed, *aspect_b* should not be scheduled.

    Set ``symmetric=True`` when the exclusion is mutual (e.g. two aspects
    that cannot both fit in the day).
    """

    aspect_a: str  # aspect_id
    aspect_b: str  # aspect_id
    symmetric: bool = False


class ConditionalPreference(BaseModel):
    """An edge expressing: when *when_absent* is not confirmed, prefer *prefer*.

    Strength follows the same 0–1 scale as :attr:`PlanningAspect.desire_strength`.
    """

    when_absent: str  # aspect_id
    prefer: str  # aspect_id
    strength: float = Field(default=0.5, ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Graph container
# ---------------------------------------------------------------------------


class PlanningAspectGraph(BaseModel):
    """Typed graph of planning aspects and their relationships for one session.

    Accessors filter by :attr:`PlanningAspect.category`; they do **not** use
    string scanning — they rely entirely on the structured ``category`` values
    assigned by :class:`ConstraintAspectClassification` at extraction time.
    """

    aspects: list[PlanningAspect] = Field(default_factory=list)
    exclusions: list[ExclusionRelation] = Field(default_factory=list)
    conditional_preferences: list[ConditionalPreference] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Typed aspect accessors (structural, no string scanning)
    # ------------------------------------------------------------------

    def by_category(self, category: str) -> list[PlanningAspect]:
        """Return all aspects whose ``category`` equals *category*."""
        return [a for a in self.aspects if a.category == category]

    def sleep_windows(self) -> list[PlanningAspect]:
        """Return aspects in the ``sleep`` category."""
        return self.by_category(SeedAspectCategory.SLEEP)

    def work_windows(self) -> list[PlanningAspect]:
        """Return aspects in the ``work`` category."""
        return self.by_category(SeedAspectCategory.WORK)

    def exercise_aspects(self) -> list[PlanningAspect]:
        """Return aspects in the ``exercise`` category."""
        return self.by_category(SeedAspectCategory.EXERCISE)

    def confirmed_aspects(self) -> list[PlanningAspect]:
        """Return all aspects that are confirmed on calendar."""
        return [a for a in self.aspects if a.is_confirmed]

    def desired_aspects(self) -> list[PlanningAspect]:
        """Return aspects that are desired and not explicitly excluded."""
        return [a for a in self.aspects if a.is_desired and a.desire_strength > 0.0]

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    def excluded_by(self, aspect_id: str) -> list[str]:
        """Return aspect_ids that are excluded when *aspect_id* is confirmed."""
        excluded: list[str] = []
        for excl in self.exclusions:
            if excl.aspect_a == aspect_id:
                excluded.append(excl.aspect_b)
            elif excl.symmetric and excl.aspect_b == aspect_id:
                excluded.append(excl.aspect_a)
        return excluded

    def preferred_when_absent(self, aspect_id: str) -> list[tuple[str, float]]:
        """Return (aspect_id, strength) pairs preferred when *aspect_id* is absent."""
        return [
            (pref.prefer, pref.strength)
            for pref in self.conditional_preferences
            if pref.when_absent == aspect_id
        ]

    def get_aspect(self, aspect_id: str) -> PlanningAspect | None:
        """Look up an aspect by its stable slug."""
        for aspect in self.aspects:
            if aspect.aspect_id == aspect_id:
                return aspect
        return None

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def upsert_aspect_from_classification(
        self, cls: ConstraintAspectClassification
    ) -> PlanningAspect:
        """Insert or update a :class:`PlanningAspect` node from a classification.

        Existing node fields are updated in-place so caller can overlay
        ``is_confirmed`` separately after the calendar pass.
        """
        existing = self.get_aspect(cls.aspect_id)
        if existing is not None:
            existing.label = cls.aspect_label
            existing.category = cls.category
            if cls.schedule_start:
                existing.schedule_start = cls.schedule_start
            if cls.schedule_end:
                existing.schedule_end = cls.schedule_end
            if cls.duration_min is not None:
                existing.duration_min = cls.duration_min
            return existing

        aspect = PlanningAspect(
            aspect_id=cls.aspect_id,
            label=cls.aspect_label,
            category=cls.category,
            schedule_start=cls.schedule_start,
            schedule_end=cls.schedule_end,
            duration_min=cls.duration_min,
        )
        self.aspects.append(aspect)

        for excluded_id in cls.excludes_aspect_ids:
            if not any(
                e.aspect_a == cls.aspect_id and e.aspect_b == excluded_id
                for e in self.exclusions
            ):
                self.exclusions.append(
                    ExclusionRelation(aspect_a=cls.aspect_id, aspect_b=excluded_id)
                )

        for absent_id in cls.conditional_on_absent:
            if not any(
                p.when_absent == absent_id and p.prefer == cls.aspect_id
                for p in self.conditional_preferences
            ):
                self.conditional_preferences.append(
                    ConditionalPreference(when_absent=absent_id, prefer=cls.aspect_id)
                )

        return aspect


# ---------------------------------------------------------------------------
# Pre-population helper
# ---------------------------------------------------------------------------


def make_seed_aspect_graph() -> PlanningAspectGraph:
    """Return an empty :class:`PlanningAspectGraph` pre-seeded with stub nodes for the
    well-known categories.

    These stubs carry no schedule times; they are present so the graph is not
    empty when a new user starts their first session. The LLM will fill in
    concrete times and preferences as the user speaks.
    """
    return PlanningAspectGraph(
        aspects=[
            PlanningAspect(
                aspect_id="sleep_window",
                label="Sleep window",
                category=SeedAspectCategory.SLEEP,
            ),
            PlanningAspect(
                aspect_id="work_window",
                label="Work window",
                category=SeedAspectCategory.WORK,
            ),
            PlanningAspect(
                aspect_id="exercise",
                label="Exercise",
                category=SeedAspectCategory.EXERCISE,
            ),
        ]
    )


__all__ = [
    "ConstraintAspectClassification",
    "ConditionalPreference",
    "ExclusionRelation",
    "PlanningAspect",
    "PlanningAspectGraph",
    "SeedAspectCategory",
    "make_seed_aspect_graph",
]
