"""Typed contracts for the guided weekly review session."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel, Field


class WeeklyReviewPhase(str, Enum):
    """Ordered phases for the guided weekly review flow."""

    REFLECT = "reflect"
    SCAN_BOARD = "scan_board"
    OUTCOMES = "outcomes"
    SYSTEMS_RISKS = "systems_risks"
    CLOSE = "close"


class WeeklyReviewOutcome(BaseModel):
    """Outcome record captured during weekly review."""

    title: str
    definition_of_done: str
    is_must: bool = False


class WeeklyReviewRisk(BaseModel):
    """Risk + mitigation captured in weekly review."""

    risk: str
    mitigation: str


class WeeklyReviewRecap(BaseModel):
    """Structured recap persisted after closing a review session."""

    wins: list[str] = Field(default_factory=list)
    misses: list[str] = Field(default_factory=list)
    progress_updates: list[str] = Field(default_factory=list)
    outcomes: list[WeeklyReviewOutcome] = Field(default_factory=list)
    start_stop_continue: list[str] = Field(default_factory=list)
    risks: list[WeeklyReviewRisk] = Field(default_factory=list)
    weekly_constraints: list[str] = Field(default_factory=list)
    weekly_intention: str = ""
    summary: str = ""


class WeeklyReviewTurn(BaseModel):
    """Structured output for one review turn."""

    phase: WeeklyReviewPhase
    gate_met: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    phase_summary: list[str] = Field(default_factory=list)
    assistant_message: str
    recap: WeeklyReviewRecap | None = None
    session_complete: bool = False


class ReviewIntentDecision(BaseModel):
    """Classifier output for deciding whether to enter guided review mode."""

    start_session: bool = False
    rationale: str = ""


class WeeklyReviewRecapRequest(BaseModel):
    """Request latest recap by user id."""

    user_id: str


class WeeklyReviewRecapResponse(BaseModel):
    """Response carrying latest recap, if found."""

    found: bool = False
    recap: WeeklyReviewRecap | None = None


@dataclass
class WeeklyReviewSessionState:
    """In-memory v0 state for one guided review session."""

    phase: WeeklyReviewPhase = WeeklyReviewPhase.REFLECT
    phase_summaries: dict[WeeklyReviewPhase, list[str]] = field(default_factory=dict)
    turns: int = 0
    user_id: str = ""


__all__ = [
    "ReviewIntentDecision",
    "WeeklyReviewOutcome",
    "WeeklyReviewPhase",
    "WeeklyReviewRecap",
    "WeeklyReviewRecapRequest",
    "WeeklyReviewRecapResponse",
    "WeeklyReviewRisk",
    "WeeklyReviewSessionState",
    "WeeklyReviewTurn",
]
