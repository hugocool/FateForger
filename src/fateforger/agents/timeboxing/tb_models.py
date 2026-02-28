"""Lightweight timebox models for token-efficient LLM generation.

These are the **sole LLM-facing models** for timebox planning.  Heavy
``CalendarEvent`` (SQLModel) stays for DB persistence / Slack display;
never pass it to an LLM.

Extracted from ``notebooks/making_timebox_session_stage_4_work.ipynb`` cell 33.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Annotated, Literal, Union

from isodate import parse_duration as _parse_dur
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── EventType (compact codes, no SQLAlchemy) ──────────────────────────────


class ET(str, Enum):
    """Event type — compact codes for LLM generation."""

    M = "M"  # meeting
    C = "C"  # commute
    DW = "DW"  # deep work
    SW = "SW"  # shallow work
    PR = "PR"  # plan & review
    H = "H"  # habit / routine
    R = "R"  # regeneration (meals, sleep, rest)
    BU = "BU"  # buffer
    BG = "BG"  # background (must have fixed timing)


# Map ET codes → Google Calendar colorId strings.
ET_COLOR_MAP: dict[str, str] = {
    "M": "6",
    "C": "4",
    "DW": "9",
    "SW": "8",
    "PR": "10",
    "H": "7",
    "R": "2",
    "BU": "5",
    "BG": "1",
}


def gcal_color_to_et(color_id: str | None) -> ET:
    """Best-effort reverse mapping: GCal colorId → ET code."""
    _reverse = {v: k for k, v in ET_COLOR_MAP.items()}
    if color_id and color_id in _reverse:
        return ET(_reverse[color_id])
    return ET.M  # default: treat unknown calendar events as meetings


# ── Time anchoring (discriminated union on field ``a``) ───────────────────


class AfterPrev(BaseModel):
    """Starts immediately after the previous event ends. Default."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["ap"] = "ap"
    dur: timedelta = Field(..., description="Duration (ISO 8601, e.g. PT30M)")

    _parse = field_validator("dur", mode="before")(
        lambda cls, v: _parse_dur(v) if isinstance(v, str) else v
    )


class BeforeNext(BaseModel):
    """Ends immediately when the next event starts."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["bn"] = "bn"
    dur: timedelta = Field(..., description="Duration (ISO 8601)")

    _parse = field_validator("dur", mode="before")(
        lambda cls, v: _parse_dur(v) if isinstance(v, str) else v
    )


class FixedStart(BaseModel):
    """Pinned to a specific start time."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fs"] = "fs"
    st: time = Field(..., description="Start time (HH:MM)")
    dur: timedelta = Field(..., description="Duration (ISO 8601)")

    _parse_t = field_validator("st", mode="before")(
        lambda cls, v: time.fromisoformat(v) if isinstance(v, str) else v
    )
    _parse_d = field_validator("dur", mode="before")(
        lambda cls, v: _parse_dur(v) if isinstance(v, str) else v
    )


class FixedWindow(BaseModel):
    """Pinned start and end — for meetings, background events, etc."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fw"] = "fw"
    st: time = Field(..., description="Start time (HH:MM)")
    et: time = Field(..., description="End time (HH:MM)")

    _parse_st = field_validator("st", mode="before")(
        lambda cls, v: time.fromisoformat(v) if isinstance(v, str) else v
    )
    _parse_et = field_validator("et", mode="before")(
        lambda cls, v: time.fromisoformat(v) if isinstance(v, str) else v
    )


Timing = Annotated[
    Union[AfterPrev, BeforeNext, FixedStart, FixedWindow],
    Field(discriminator="a"),
]


# ── TBEvent (the generation-time event model) ────────────────────────────


class TBEvent(BaseModel):
    """A single timeboxed event — minimal fields for LLM generation.

    ~40 tokens per event vs ~180 for production ``CalendarEvent``.
    """

    model_config = ConfigDict(extra="forbid")

    n: str = Field(..., description="Event name / summary")
    d: str = Field("", description="Short description")
    t: ET = Field(..., description="Event type code")
    p: Timing = Field(..., description="Time placement")

    @model_validator(mode="after")
    def bg_needs_fixed(self) -> "TBEvent":
        """Background events must have a fixed window or fixed start."""
        if self.t == ET.BG and self.p.a not in ("fs", "fw"):
            raise ValueError(
                "Background events (BG) require fixed_start or fixed_window timing"
            )
        return self


# ── TBPlan (the generation-time timebox) ──────────────────────────────────


class TBPlan(BaseModel):
    """A day's timebox plan — lightweight container for LLM generation."""

    model_config = ConfigDict(extra="forbid")

    events: list[TBEvent] = Field(default_factory=list)
    date: date_type = Field(default_factory=date_type.today)
    tz: str = Field(default="Europe/Amsterdam", description="IANA timezone")

    @model_validator(mode="after")
    def chain_must_be_anchored(self) -> "TBPlan":
        """At least one non-BG event must have a fixed time to anchor the chain."""
        chain = [e for e in self.events if e.t != ET.BG]
        if chain and not any(e.p.a in ("fs", "fw") for e in chain):
            raise ValueError(
                "Event chain needs at least one fixed_start or fixed_window anchor"
            )
        return self

    def resolve_times(self, *, validate_non_overlap: bool = True) -> list[dict]:
        """Deterministically compute concrete start/end for every event.

        Returns:
            List of dicts with keys: ``n``, ``d``, ``t``, ``start_time``,
            ``end_time``, ``duration``, ``index``.
        """
        planning_date = self.date
        resolved: list[dict] = []

        # ── Forward pass: after_previous, fixed_start, fixed_window ──
        last_end_dt: datetime | None = None
        for i, ev in enumerate(self.events):
            r: dict = {"n": ev.n, "d": ev.d, "t": ev.t.value, "index": i}
            p = ev.p

            if p.a == "ap":  # after_previous
                if last_end_dt is None:
                    raise ValueError(
                        f"Event '{ev.n}' (after_previous) has no preceding event"
                    )
                start_dt = last_end_dt
                end_dt = start_dt + p.dur
                r.update(
                    start_time=start_dt.time(),
                    end_time=end_dt.time(),
                    duration=p.dur,
                )

            elif p.a == "fs":  # fixed_start
                start_dt = datetime.combine(planning_date, p.st)
                end_dt = start_dt + p.dur
                r.update(
                    start_time=p.st,
                    end_time=end_dt.time(),
                    duration=p.dur,
                )

            elif p.a == "fw":  # fixed_window
                start_dt = datetime.combine(planning_date, p.st)
                end_dt = datetime.combine(planning_date, p.et)
                r.update(
                    start_time=p.st,
                    end_time=p.et,
                    duration=end_dt - start_dt,
                )

            elif p.a == "bn":  # before_next — resolved in backward pass
                r.update(duration=p.dur, _pending="bn")
                resolved.append(r)
                continue  # don't update last_end_dt yet

            last_end_dt = datetime.combine(planning_date, r["end_time"])
            resolved.append(r)

        # ── Backward pass: resolve before_next ──
        next_start_dt: datetime | None = None
        for r in reversed(resolved):
            if r.get("_pending") == "bn":
                if next_start_dt is None:
                    raise ValueError(
                        f"Event '{r['n']}' (before_next) has no following event"
                    )
                end_dt = next_start_dt
                start_dt = end_dt - r["duration"]
                r.update(start_time=start_dt.time(), end_time=end_dt.time())
                del r["_pending"]
            if "start_time" in r:
                next_start_dt = datetime.combine(planning_date, r["start_time"])

        # ── Overlap check (non-BG only) ──
        # Desired/generated plans should remain strict, but remote calendar
        # snapshots can legitimately contain overlaps from prior edits.
        if validate_non_overlap:
            chain = [r for r in resolved if r["t"] != "BG"]
            for a, b in zip(chain, chain[1:]):
                a_end = datetime.combine(planning_date, a["end_time"])
                b_start = datetime.combine(planning_date, b["start_time"])
                if a_end > b_start:
                    raise ValueError(
                        f"Overlap: '{a['n']}' ends {a['end_time']} "
                        f"but '{b['n']}' starts {b['start_time']}"
                    )

        return resolved


__all__ = [
    "ET",
    "ET_COLOR_MAP",
    "AfterPrev",
    "BeforeNext",
    "FixedStart",
    "FixedWindow",
    "TBEvent",
    "TBPlan",
    "Timing",
    "gcal_color_to_et",
]
