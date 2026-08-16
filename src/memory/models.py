# src/memory/models.py
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from memory.identity import mint_uid


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
