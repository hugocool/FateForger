# src/memory/constraint.py
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field

from memory.identity import mint_uid
from memory.models import Tier


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
