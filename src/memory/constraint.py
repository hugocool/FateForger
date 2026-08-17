# src/memory/constraint.py
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from memory.identity import mint_uid
from memory.models import HALF_LIFE_DAYS, DecayClass, Tier, as_aware_utc


class Necessity(str, Enum):
    MUST = "must"
    SHOULD = "should"


class Scope(str, Enum):
    SESSION = "session"
    PROFILE = "profile"


class Status(str, Enum):
    PROPOSED = "proposed"
    LOCKED = "locked"


class Source(str, Enum):
    """Who asserted this, in the consuming server's vocabulary.

    Distinct from Channel, which is where the statement arrived. A rule stated
    during weekly review and one stated mid-planning both come from the user;
    only a rule inferred from calendar data comes from the calendar.
    """

    USER = "user"
    CALENDAR = "calendar"
    SYSTEM = "system"
    FEEDBACK = "feedback"


class Applicability(BaseModel):
    """When a constraint applies, expressed structurally.

    Every field here is compared arithmetically at read time — a date against
    a range, a weekday against a list of weekdays. None of it is a judgement
    about meaning, so the read path needs no model call.
    """

    start_date: date | None = None
    end_date: date | None = None
    days_of_week: list[int] = Field(default_factory=list)  # 0=Mon .. 6=Sun

    @field_validator("days_of_week")
    @classmethod
    def _weekdays_in_range(cls, value: list[int]) -> list[int]:
        """Reject weekday indices outside 0-6.

        Monday=0..Sunday=6, matching date.weekday(). A model that reverts to
        ISO numbering (Monday=1..Sunday=7) would encode Sunday as 7, which
        matches no real date — the constraint would then be silently served
        on no day at all. Bounds-checking a model-supplied integer index is
        arithmetic on a system-defined range, not a judgement about meaning.
        """
        out_of_range = [d for d in value if d < 0 or d > 6]
        if out_of_range:
            raise ValueError(
                f"weekday index out of range 0-6 (Monday=0..Sunday=6): {out_of_range}"
            )
        return value

    def applies_on(self, day: date) -> bool:
        if self.start_date is not None and day < self.start_date:
            return False
        if self.end_date is not None and day > self.end_date:
            return False
        if self.days_of_week and day.weekday() not in self.days_of_week:
            return False
        return True


class ConstraintView(BaseModel):
    """What the timeboxing patcher consumes.

    Deliberately narrow: the patcher renders these and nothing else. Memory
    owns relevance filtering so the two sides cannot diverge.
    """

    uid: str
    name: str
    description: str
    necessity: Necessity
    scope: Scope
    status: Status
    source: Source
    frame_slot: str | None = None


class Constraint(BaseModel):
    """A canonical rule, derived from one or more observations.

    L2: never authored directly, always projected from the immutable log.
    `source_observation_uids` is the provenance link back to L1 — it is what
    makes re-projection possible when the taxonomy changes (I4).
    """

    uid: str = Field(default_factory=mint_uid)
    name: str
    description: str
    necessity: Necessity
    scope: Scope
    status: Status
    source: Source
    frame_slot: str | None = None
    tier: Tier = Tier.SESSION
    applicability: Applicability = Field(default_factory=Applicability)
    source_observation_uids: list[str] = Field(default_factory=list)
    created_at: datetime
    # Default PERMANENT: a rule wrongly marked permanent is merely noisy, one
    # wrongly marked short-lived disappears without being asked.
    decay_class: DecayClass = DecayClass.PERMANENT
    # Deliberately no default: a constraint whose observation date is unknown
    # cannot be reasoned about, and a default would silently pick a lie.
    last_observed_at: datetime

    @field_validator("created_at", "last_observed_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime) -> datetime:
        return as_aware_utc(value)

    def has_faded(self, on: date) -> bool:
        """True when no observation is recent enough to keep this alive.

        Arithmetic only — a timestamp against a threshold. No model call, so
        this is safe to run in the read path (I1).
        """
        half_life = HALF_LIFE_DAYS[self.decay_class]
        if half_life is None:
            return False
        return (on - self.last_observed_at.date()).days > half_life

    def to_view(self) -> ConstraintView:
        return ConstraintView(
            uid=self.uid,
            name=self.name,
            description=self.description,
            necessity=self.necessity,
            scope=self.scope,
            status=self.status,
            source=self.source,
            frame_slot=self.frame_slot,
        )
