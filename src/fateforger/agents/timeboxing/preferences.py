"""Constraint models and persistence for timeboxing preferences."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field as PydanticField
from sqlalchemy import Column
from sqlalchemy import DateTime as SQLDateTime
from sqlalchemy.types import JSON as SAJSON
from sqlmodel import Field, SQLModel


class ConstraintNecessity(str, Enum):
    MUST = "must"
    SHOULD = "should"
    PREFER = "prefer"


class ConstraintStatus(str, Enum):
    PROPOSED = "proposed"
    LOCKED = "locked"
    DECLINED = "declined"


class ConstraintSource(str, Enum):
    USER = "user"
    CALENDAR = "calendar"
    SYSTEM = "system"
    FEEDBACK = "feedback"


class ConstraintScope(str, Enum):
    SESSION = "session"
    PROFILE = "profile"
    DATESPAN = "datespan"


class ConstraintDayOfWeek(str, Enum):
    MO = "MO"
    TU = "TU"
    WE = "WE"
    TH = "TH"
    FR = "FR"
    SA = "SA"
    SU = "SU"


class ConstraintBase(SQLModel):
    name: str
    description: str
    necessity: ConstraintNecessity
    tags: List[str] = Field(default_factory=list, sa_column=Column(SAJSON))
    hints: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(SAJSON))
    status: Optional[ConstraintStatus] = None
    source: Optional[ConstraintSource] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scope: Optional[ConstraintScope] = None
    rationale: Optional[str] = None
    supersedes: List[str] = Field(default_factory=list, sa_column=Column(SAJSON))
    selector: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(SAJSON))
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    days_of_week: List[ConstraintDayOfWeek] = Field(
        default_factory=list, sa_column=Column(SAJSON)
    )
    timezone: Optional[str] = None
    recurrence: Optional[str] = None
    ttl_days: Optional[int] = Field(default=None, ge=1)


class Constraint(ConstraintBase, table=True):
    __tablename__ = "timeboxing_constraints"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str
    channel_id: Optional[str] = None
    thread_ts: Optional[str] = None
    created_at: datetime = Field(
        sa_column=Column(SQLDateTime, default=datetime.utcnow, nullable=False)
    )
    updated_at: datetime = Field(
        sa_column=Column(
            SQLDateTime,
            default=datetime.utcnow,
            onupdate=datetime.utcnow,
            nullable=False,
        )
    )


class ConstraintBatch(PydanticBaseModel):
    constraints: List[ConstraintBase] = PydanticField(default_factory=list)
    notes: Optional[str] = None


__all__ = [
    "Constraint",
    "ConstraintBase",
    "ConstraintBatch",
    "ConstraintDayOfWeek",
    "ConstraintNecessity",
    "ConstraintScope",
    "ConstraintSource",
    "ConstraintStatus",
]
