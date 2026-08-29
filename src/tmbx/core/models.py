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
from itertools import pairwise
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


def _resolve_window(day: date_type, st: time, et: time) -> tuple[datetime, datetime]:
    """Combine a fixed start/end time-of-day pair into concrete datetimes.

    Midnight-crossing rule: when ``et < st`` (strictly) the window is taken
    to cross midnight — the end lands on the day *after* ``day`` rather than
    producing a negative duration. Real calendars contain events like this
    (an overnight shift, a late set), and a plan must be able to represent a
    day that has one.

    The comparison is strict on purpose: ``et == st`` is a same-day,
    zero-duration window, not a 24-hour one. Only a strictly *earlier* end
    means the window wrapped around midnight.
    """
    start_dt = datetime.combine(day, st)
    end_day = day + timedelta(days=1) if et < st else day
    end_dt = datetime.combine(end_day, et)
    return start_dt, end_dt


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
    """Pinned start and end; duration is an output.

    If ``et < st`` the window is resolved as crossing midnight (see
    ``_resolve_window`` in ``Plan.resolve``) rather than yielding a negative
    duration. ``et == st`` is a same-day, zero-duration window.
    """

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

BOUNDARY_ANCHOR_SOURCES: frozenset[str] = frozenset({"constraint"})
"""Anchor sources whose pin is a boundary, not a convenience.

A pin can be the only thing in a plan enforcing a rule that lives outside
it. ``overspecified()`` measures whether a pin changes any resolved time
*today* and, by that measure alone, such a pin looks gratuitous — so the
model is told to relax it, and the rule silently stops applying. Measured
under a joint tmbx + constraint-memory session: 2 of 4 runs relaxed a
bedtime pinned by a MUST sleep-at-23:00 constraint, one recording *"Relax
BED1 to ap mode to prevent overspecification"*.

Two rules read this set, deliberately the same one so they cannot drift:
``commitment.overspecified`` will not flag such a pin, and
``ops.validate_patch`` refuses an update that relaxes one out of fixed
timing.

Only ``constraint`` is in it, and the two exclusions are decisions, not
oversights:

* ``user`` is what this codebase reaches for whenever ``Block`` demands a
  source and nothing better is at hand — every fs/fw fixture in the test
  suite, and, in practice, the model's own default. ``Block`` has no way
  to say "pinned for no attributable reason", so the *absence* of a real
  reason is spelled ``user``. Treating it as a boundary would suppress the
  flag for nearly every pin, which deletes the check rather than fixing
  it — three existing tests assert that a ``user``-anchored gratuitous pin
  is still flagged, and they are right. A user is also present in the
  conversation and can restate a preference; a standing constraint cannot.
* ``calendar`` is an observed fact, not an assertion of intent. It is also
  the fallback for an event carrying no stored provenance at all, so
  treating it as a boundary would grandfather every pre-existing calendar
  event out of the check.

Widening this set is a real decision about the model's advice, not a
config tweak: everything in it becomes both un-flaggable and un-relaxable.
"""


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

    @model_validator(mode="after")
    def _fixed_timing_needs_anchor_source(self) -> "Block":
        if self.p.a in ("fs", "fw") and self.anchor_source is None:
            raise ValueError(
                f"{self.h}: anchor_source is required when timing is fs or fw"
            )
        return self


class ViolationKind(str, Enum):
    """Why a plan does not fit.

    A closed set with exactly one member per raise site in
    ``Plan.resolve()`` — which is what lets a renderer switch on it
    exhaustively instead of reading the message. Adding a way for a plan to
    fail means adding a member here; there is no "other".
    """

    OVERLAP = "overlap"
    CIRCULAR_CHAIN = "circular_chain"
    UNANCHORED_AFTER_PREV = "unanchored_after_prev"
    UNANCHORED_BEFORE_NEXT = "unanchored_before_next"
    NEGATIVE_DURATION = "negative_duration"


class ViolationBlock(BaseModel):
    """One block implicated in a violation, as a renderer needs to show it.

    ``n`` rides along with ``h`` because a confirmation card shows the user
    "Wind Down", not "WIND1"; a renderer holding only the handle would have
    to go back to the plan to find the name, which is exactly the
    re-derivation this payload exists to remove.

    ``start``/``end`` are ``None`` when the block never resolved — an
    unanchored or circular chain has no times to report, and inventing some
    would be worse than saying so.
    """

    model_config = ConfigDict(extra="forbid")

    h: str
    n: str
    start: time | None = None
    end: time | None = None


class Violation(BaseModel):
    """A plan that does not fit, as data a decision is built from.

    A violation is a decision point for a user, not merely an error: the day
    they asked for does not fit and someone has to choose what gives way.
    That choice surfaces in Slack as a confirmation card, so the payload has
    to carry the situation itself — which blocks collide, and by how much —
    rather than a sentence a renderer would have to take apart again.

    ``magnitude`` is the size of the discrepancy where the violation has
    one: how long two blocks overlap, or how far a block's end precedes its
    own start. It is ``None`` for a structural failure (an unanchored or
    circular chain), where "how much" has no answer — deliberately absent
    rather than zero, which would read as "they overlap by nothing".

    ``message`` stays on the model because a text host still needs a
    sentence, and one place to compose it beats each host inventing its own.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ViolationKind
    blocks: list[ViolationBlock]
    magnitude: timedelta | None = None
    message: str


class PlanViolation(ValueError):
    """``Plan.resolve()`` could not produce a day that fits.

    Subclasses ``ValueError`` deliberately: ``overspecified()``,
    ``PlanService.apply`` and the server's ``invalid_patch`` branch all
    catch ``ValueError`` around a resolve, and narrowing that would change
    three call sites' behaviour silently. The structured ``violation`` is an
    addition to what those callers already see, never a replacement.
    """

    def __init__(self, violation: Violation) -> None:
        super().__init__(violation.message)
        self.violation = violation


class Resolved(BaseModel):
    """A block with concrete times computed.

    ``start``/``end`` are wall-clock ``time`` — what the render layer shows.
    ``start_dt``/``end_dt`` are the full datetimes those were derived from,
    carrying the actual day (which may be the day *after* the plan's date
    for a block that crosses midnight). Anything that needs to compare two
    resolved blocks' ordering — the overlap check, most notably — must use
    ``start_dt``/``end_dt``, not recombine ``start``/``end`` with the plan's
    single ``date``: a block that legitimately lands on the next day would
    get silently truncated back onto the plan's date and compare wrong.
    """

    uid: str
    h: str
    n: str
    t: ET
    mode: str
    start: time
    end: time
    start_dt: datetime
    end_dt: datetime
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

        Forward pass handles ap/fs/fw; a backward pass closes bn. ``BG``
        blocks never participate in chain propagation in either pass — they
        are resolved from their own fixed timing and never move
        ``last_end``/``next_start``, exactly as they are already excluded
        from ``_chain_is_anchored`` and the overlap check below.

        An ``ap`` block may not follow an unresolved ``bn`` block: ``bn``
        ends when the next block starts and ``ap`` starts when the previous
        block ends, so the pair defines each other with nothing to anchor
        on. That configuration is not mis-resolved here — it is rejected.

        Every rejection raises ``PlanViolation`` (a ``ValueError``) carrying
        a structured ``Violation``: which blocks are implicated, by how
        much, and why. Callers gate on that — ``PlanService.commit`` refuses
        a patch whose resulting plan violates — and hosts render it as a
        decision for the user. Resolution stops at the first violation
        found, so a ``Violation`` describes one problem, not all of them.
        """
        day = self.date
        rows: list[dict] = []
        last_end: datetime | None = None
        pending_bn_handle: str | None = None

        for block in self.blocks:
            row: dict = {
                "uid": block.uid,
                "h": block.h,
                "n": block.n,
                "t": block.t,
                "mode": block.p.a,
            }
            p = block.p

            if block.t is ET.BG:
                # BG is invisible to the chain: resolve its own fixed timing
                # but never touch last_end / pending_bn_handle.
                if p.a == "fs":
                    start_dt = datetime.combine(day, p.st)
                    end_dt = start_dt + p.dur
                else:  # fw — Block validation guarantees fs or fw for BG
                    start_dt, end_dt = _resolve_window(day, p.st, p.et)
                row.update(
                    start=start_dt.time(),
                    end=end_dt.time(),
                    start_dt=start_dt,
                    end_dt=end_dt,
                    dur=end_dt - start_dt,
                )
                rows.append(row)
                continue

            if p.a == "ap":
                if pending_bn_handle is not None:
                    pending = self.by_handle(pending_bn_handle)
                    raise PlanViolation(
                        Violation(
                            kind=ViolationKind.CIRCULAR_CHAIN,
                            blocks=[
                                ViolationBlock(
                                    h=pending_bn_handle,
                                    n=pending.n if pending else pending_bn_handle,
                                ),
                                ViolationBlock(h=block.h, n=block.n),
                            ],
                            message=(
                                f"{pending_bn_handle} and {block.h}: circular chain — "
                                f"{pending_bn_handle} (bn) ends when {block.h} starts, "
                                f"{block.h} (ap) starts when {pending_bn_handle} ends. "
                                "An ap block may only follow a block whose end is "
                                "already determined."
                            ),
                        )
                    )
                if last_end is None:
                    raise PlanViolation(
                        Violation(
                            kind=ViolationKind.UNANCHORED_AFTER_PREV,
                            blocks=[ViolationBlock(h=block.h, n=block.n)],
                            message=f"{block.h}: after_previous has no preceding block",
                        )
                    )
                start_dt, end_dt = last_end, last_end + p.dur
            elif p.a == "fs":
                start_dt = datetime.combine(day, p.st)
                end_dt = start_dt + p.dur
            elif p.a == "fw":
                start_dt, end_dt = _resolve_window(day, p.st, p.et)
            else:  # bn — resolved backwards
                row["_pending_dur"] = p.dur
                rows.append(row)
                pending_bn_handle = block.h
                continue

            row.update(
                start=start_dt.time(),
                end=end_dt.time(),
                start_dt=start_dt,
                end_dt=end_dt,
                dur=end_dt - start_dt,
            )
            last_end = end_dt
            pending_bn_handle = None
            rows.append(row)

        next_start_dt: datetime | None = None
        for row in reversed(rows):
            if row["t"] is ET.BG:
                continue  # BG never participates in chain propagation
            if "_pending_dur" in row:
                if next_start_dt is None:
                    raise PlanViolation(
                        Violation(
                            kind=ViolationKind.UNANCHORED_BEFORE_NEXT,
                            blocks=[ViolationBlock(h=row["h"], n=row["n"])],
                            message=f"{row['h']}: before_next has no following block",
                        )
                    )
                dur = row.pop("_pending_dur")
                start_dt = next_start_dt - dur
                row.update(
                    start=start_dt.time(),
                    end=next_start_dt.time(),
                    start_dt=start_dt,
                    end_dt=next_start_dt,
                    dur=dur,
                )
            # Read the neighbour's own resolved datetime, not a
            # datetime.combine(day, row["start"]) reconstruction — that
            # would silently truncate a block that legitimately crossed
            # midnight back onto the plan's single date.
            next_start_dt = row["start_dt"]

        resolved = [Resolved(**row) for row in rows]

        # Invariant: no resolved block may have a negative duration, in any
        # mode. This is what keeps the overlap check below sound — it walks
        # adjacent pairs assuming monotonic, non-negative resolved times.
        for r in resolved:
            if r.dur < timedelta(0):
                raise PlanViolation(
                    Violation(
                        kind=ViolationKind.NEGATIVE_DURATION,
                        blocks=[ViolationBlock(h=r.h, n=r.n, start=r.start, end=r.end)],
                        # How far the end precedes the start: a positive
                        # size for a negative quantity, so "by how much"
                        # reads the same way it does for an overlap.
                        magnitude=-r.dur,
                        message=f"{r.h}: resolved duration is negative ({r.dur})",
                    )
                )

        if check_overlap:
            # Patch operations are set-semantic: additions sharing an anchor
            # are ordered by handle, not by operation position or clock time.
            # ``ap``/``bn`` resolution still uses plan order above, but once
            # every block has concrete datetimes, physical overlap must be
            # checked chronologically. Walking plan order made a 19:00 Dinner
            # followed by a 09:00 Deep Work handle look like an 11-hour
            # collision during the Issue #40 Slack replay.
            chain = sorted(
                (r for r in resolved if r.t is not ET.BG),
                key=lambda row: (row.start_dt, row.end_dt, row.h),
            )
            for a, b in pairwise(chain):
                # Compare the real datetimes, not a same-day recombination —
                # a block that legitimately crosses midnight must still be
                # comparable to its neighbours on the following day.
                if a.end_dt > b.start_dt:
                    raise PlanViolation(
                        Violation(
                            kind=ViolationKind.OVERLAP,
                            blocks=[
                                ViolationBlock(h=a.h, n=a.n, start=a.start, end=a.end),
                                ViolationBlock(h=b.h, n=b.n, start=b.start, end=b.end),
                            ],
                            magnitude=a.end_dt - b.start_dt,
                            message=(
                                f"Overlap: {a.h} ends {a.end} but {b.h} starts {b.start}"
                            ),
                        )
                    )

        return resolved


__all__ = [
    "AfterPrev",
    "AnchorSource",
    "BOUNDARY_ANCHOR_SOURCES",
    "BeforeNext",
    "Block",
    "ET",
    "FixedStart",
    "FixedWindow",
    "Plan",
    "PlanViolation",
    "Resolved",
    "Timing",
    "Violation",
    "ViolationBlock",
    "ViolationKind",
    "is_valid_handle",
]
