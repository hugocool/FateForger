# src/memory/models.py
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from memory.identity import mint_uid


def as_aware_utc(value: datetime) -> datetime:
    """Coerce a datetime to timezone-aware UTC.

    The store must hold only aware timestamps: comparing a naive one against
    an aware one raises TypeError, and the two sources genuinely differ —
    the corpus backfill inherits naive local times from the legacy database
    while the live path stamps aware UTC. A naive value is interpreted as
    UTC rather than local time: this data is machine-recorded, and guessing
    a local zone would silently shift every historical timestamp.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class Channel(str, Enum):
    """Where an observation arrived from. Carries a durability prior."""

    PLANNING = "planning"
    REVIEW = "review"
    CALENDAR = "calendar"


class Provenance(str, Enum):
    """Whether this came from the user's world or from a rule's own output.

    GENERATED observations must never feed the learning loop: a rule that
    emits a calendar block would otherwise observe its own output as evidence.
    """

    OBSERVED = "observed"
    GENERATED = "generated"


class Reliability(str, Enum):
    """Three values, not two. Silence is not evidence."""

    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    UNEXAMINED = "unexamined"


class Tier(str, Enum):
    SESSION = "session"
    DURABLE = "durable"


class Observation(BaseModel):
    uid: str = Field(default_factory=mint_uid)
    text: str
    channel: Channel
    provenance: Provenance
    session_id: str | None = None
    observed_at: datetime
    anchors: list[str] = Field(default_factory=list)

    @field_validator("observed_at")
    @classmethod
    def _observed_at_is_aware(cls, value: datetime) -> datetime:
        return as_aware_utc(value)


class DecayClass(str, Enum):
    """How long a rule stays true without being re-observed.

    PROVISIONAL VOCABULARY. These four were hand-authored to seed the
    mechanism, which makes them a hardcoded opinion about what kinds of
    lifetime exist — the same shape of assumption the project's rules ban
    one level down. #153 covers making them inducible from observed
    behaviour. Treat this list as a seed, not a decision.
    """

    PERMANENT = "permanent"   # sleep window, meal structure — changes only if the person does
    SEASONAL = "seasonal"     # commute duration, client days — changes on a life event
    PROJECT = "project"       # the C2F family — true for a chapter of work
    DAILY = "daily"           # today's hockey — true for a day


# None means never fades. Values are deliberately generous: withholding a
# rule the user still holds is worse than serving one they have finished
# with, so every threshold errs toward keeping.
HALF_LIFE_DAYS: dict[DecayClass, int | None] = {
    DecayClass.PERMANENT: None,
    DecayClass.SEASONAL: 365,
    DecayClass.PROJECT: 90,
    DecayClass.DAILY: 2,
}
