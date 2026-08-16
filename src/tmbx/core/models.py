"""Plan and block models with three-level identity.

Identity is deliberately three concepts:

* ``uid``  — instance identity. Server-minted, opaque, durable, never shown
  to the model. Survives rename, retime, reorder.
* ``slug`` — pattern identity. Names the recurring *kind* of block, stable
  across days. What memory anchors attach to.
* ``h``    — addressing handle. Model-assigned, persisted by the server,
  re-rendered every turn, valid for the turn it was rendered in.

The four-mode time grammar is carried over unchanged: the model states
intent, ``Plan.resolve()`` does the arithmetic.

Handles are minted identifiers, not user prose, but this project bans ``re``
outright — no regex, anywhere, for judging what a string means. Handle shape
is therefore validated with ``is_valid_handle()``, a plain string predicate:
2-5 uppercase ASCII letters followed by 1-2 ASCII digits.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Annotated, Literal, Union

from isodate import parse_duration as _parse_duration
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MIN_LETTERS = 2
_MAX_LETTERS = 5
_MIN_DIGITS = 1
_MAX_DIGITS = 2


def is_valid_handle(value: str) -> bool:
    """True if ``value`` is 2-5 uppercase ASCII letters then 1-2 ASCII digits.

    Plain string predicates only — no regex. Handles are minted by the
    model/server, not free-form user content, but the ban on ``re`` is
    absolute across ``src/tmbx``.
    """
    n = len(value)
    i = 0
    while i < n and "A" <= value[i] <= "Z":
        i += 1
    letters, digits = value[:i], value[i:]

    if not (_MIN_LETTERS <= len(letters) <= _MAX_LETTERS):
        return False
    if not (_MIN_DIGITS <= len(digits) <= _MAX_DIGITS):
        return False
    return all("0" <= ch <= "9" for ch in digits)


class ET(str, Enum):
    """Event type."""

    M = "M"
    C = "C"
    DW = "DW"
    SW = "SW"
    PR = "PR"
    H = "H"
    R = "R"
    BU = "BU"
    BG = "BG"


def _coerce_duration(value: object) -> object:
    return _parse_duration(value) if isinstance(value, str) else value


def _coerce_time(value: object) -> object:
    return time.fromisoformat(value) if isinstance(value, str) else value


class AfterPrev(BaseModel):
    """Duration only; starts when the previous block ends."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["ap"] = "ap"
    dur: timedelta

    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class BeforeNext(BaseModel):
    """Duration only; ends when the next block starts."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["bn"] = "bn"
    dur: timedelta

    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class FixedStart(BaseModel):
    """Pinned start, inferred end."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fs"] = "fs"
    st: time
    dur: timedelta

    _t = field_validator("st", mode="before")(lambda cls, v: _coerce_time(v))
    _d = field_validator("dur", mode="before")(lambda cls, v: _coerce_duration(v))


class FixedWindow(BaseModel):
    """Pinned start and end; duration is an output."""

    model_config = ConfigDict(extra="forbid")
    a: Literal["fw"] = "fw"
    st: time
    et: time

    _s = field_validator("st", mode="before")(lambda cls, v: _coerce_time(v))
    _e = field_validator("et", mode="before")(lambda cls, v: _coerce_time(v))


Timing = Annotated[
    Union[AfterPrev, BeforeNext, FixedStart, FixedWindow],
    Field(discriminator="a"),
]

AnchorSource = Literal["user", "constraint", "calendar"]


class Block(BaseModel):
    """One timeboxed block."""

    model_config = ConfigDict(extra="forbid")

    uid: str = Field(description="Server-minted instance identity")
    h: str = Field(description="Addressing handle, e.g. DW1")
    slug: str | None = Field(default=None, description="Recurring block kind")
    n: str = Field(description="Name / summary")
    d: str = Field(default="", description="Short description")
    t: ET
    p: Timing
    anchor_source: AnchorSource | None = Field(
        default=None,
        description="Why this block is pinned. Required when p is fs or fw.",
    )

    @field_validator("h")
    @classmethod
    def _handle_shape(cls, value: str) -> str:
        if not is_valid_handle(value):
            raise ValueError(
                f"handle {value!r} must be 2-5 uppercase letters then 1-2 digits"
            )
        return value

    @model_validator(mode="after")
    def _background_needs_fixed_timing(self) -> "Block":
        if self.t is ET.BG and self.p.a not in ("fs", "fw"):
            raise ValueError("BG blocks require fs or fw timing")
        return self


class Resolved(BaseModel):
    """A block with concrete times computed."""

    uid: str
    h: str
    n: str
    t: ET
    mode: str
    start: time
    end: time
    dur: timedelta


class Plan(BaseModel):
    """A day's plan."""

    model_config = ConfigDict(extra="forbid")

    blocks: list[Block] = Field(default_factory=list)
    date: date_type
    tz: str = "Europe/Amsterdam"

    @model_validator(mode="after")
    def _handles_unique(self) -> "Plan":
        seen = [b.h for b in self.blocks]
        dupes = {h for h in seen if seen.count(h) > 1}
        if dupes:
            raise ValueError(f"duplicate handles: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _chain_is_anchored(self) -> "Plan":
        chain = [b for b in self.blocks if b.t is not ET.BG]
        if chain and not any(b.p.a in ("fs", "fw") for b in chain):
            raise ValueError("chain needs at least one fs or fw anchor")
        return self

    def by_handle(self, handle: str) -> Block | None:
        """Look up a block by its addressing handle."""
        return next((b for b in self.blocks if b.h == handle), None)

    def resolve(self, *, check_overlap: bool = True) -> list[Resolved]:
        """Compute concrete start/end for every block.

        Forward pass handles ap/fs/fw; a backward pass closes bn.
        """
        day = self.date
        rows: list[dict] = []
        last_end: datetime | None = None

        for block in self.blocks:
            row: dict = {
                "uid": block.uid,
                "h": block.h,
                "n": block.n,
                "t": block.t,
                "mode": block.p.a,
            }
            p = block.p

            if p.a == "ap":
                if last_end is None:
                    raise ValueError(f"{block.h}: after_previous has no preceding block")
                start_dt, end_dt = last_end, last_end + p.dur
            elif p.a == "fs":
                start_dt = datetime.combine(day, p.st)
                end_dt = start_dt + p.dur
            elif p.a == "fw":
                start_dt = datetime.combine(day, p.st)
                end_dt = datetime.combine(day, p.et)
            else:  # bn — resolved backwards
                row["_pending_dur"] = p.dur
                rows.append(row)
                continue

            row.update(start=start_dt.time(), end=end_dt.time(), dur=end_dt - start_dt)
            last_end = end_dt
            rows.append(row)

        next_start: datetime | None = None
        for row in reversed(rows):
            if "_pending_dur" in row:
                if next_start is None:
                    raise ValueError(f"{row['h']}: before_next has no following block")
                dur = row.pop("_pending_dur")
                start_dt = next_start - dur
                row.update(start=start_dt.time(), end=next_start.time(), dur=dur)
            next_start = datetime.combine(day, row["start"])

        resolved = [Resolved(**row) for row in rows]

        if check_overlap:
            chain = [r for r in resolved if r.t is not ET.BG]
            for a, b in zip(chain, chain[1:]):
                if datetime.combine(day, a.end) > datetime.combine(day, b.start):
                    raise ValueError(
                        f"Overlap: {a.h} ends {a.end} but {b.h} starts {b.start}"
                    )

        return resolved


__all__ = [
    "AfterPrev",
    "AnchorSource",
    "BeforeNext",
    "Block",
    "ET",
    "FixedStart",
    "FixedWindow",
    "Plan",
    "Resolved",
    "Timing",
    "is_valid_handle",
]
