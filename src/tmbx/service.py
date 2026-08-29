# src/tmbx/service.py
"""Plan service — the mechanism behind every MCP tool.

Stateless with respect to sessions: every call is keyed by
``(calendar_id, day)``. Durable state lives in the calendar (the plan) and
the journal (the history) — never in this process. A ``Snapshot`` is a
self-contained token (calendar_id, day, tz, etags, event_ids), so
``apply``/``commit`` re-derive the plan from the calendar on every call
instead of remembering it from a prior ``read``. A process-local
snapshot->plan cache would be the same shape of bug as #112: a second host
process, or this one restarted, would not be able to act on a snapshot
handed back to it. ``undo`` carries this furthest — its precondition state
(``before_json``/``post_etags_json``) lives on the journal row, not in
memory, which is what lets it survive a restart.

Two defects in the legacy engine this closes:

* It writes against a stale snapshot with no precondition, so an edit made
  elsewhere mid-session is silently overwritten. ``commit`` compares live
  state against the snapshot (via ``drift``) before writing, and refuses
  rather than clobbering unless the caller explicitly asks to ``force``.
* Its undo replays a before-state unconditionally, destroying an edit made
  after the commit it's undoing. ``undo`` compares live state against
  ``post_etags`` — captured immediately after the write, never re-derived —
  and refuses the same way.
* It writes plans it has already reported as broken. ``commit`` resolves the
  patched plan and refuses when it does not fit (#170), the same shape as
  the drift refusal and past the same single override.

``undo`` is deliberately not gated on violations. Its precondition is total
— ``drift`` reports changed, vanished *and* appeared events, so undo only
proceeds when the live day is etag-identical to the state captured right
after the commit — which makes the restore byte-exact. It can therefore
restore a day that violates, but only one that already existed and that the
user reached deliberately (via ``expect="force"``, or by editing the
calendar directly); it can never manufacture a violation that was not there.
Gating it would strand the user in a state they explicitly asked to reverse,
with no override, since undo has no ``force`` by design.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import date as date_type
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, ValidationError, computed_field

from .calendar.port import CalendarEvent, CalendarPort, Snapshot, drift, make_snapshot
from .core.commitment import overspecified
from .core.models import (
    ET,
    AfterPrev,
    AnchorSource,
    BeforeNext,
    Block,
    FixedStart,
    FixedWindow,
    Plan,
    PlanViolation,
    Timing,
    Violation,
)
from .core.ops import MoveBlock, Patch, RemoveBlock, UpdateBlock, apply_ops
from .core.render import render_plan
from .core.unallocated import Gap, unallocated
from .journal.models import EntryKind, JournalEntry, PatchOutcome
from .journal.store import JournalStore

_logger = logging.getLogger(__name__)

# Google's custom event id must be base32hex: digits 0-9 and lowercase
# letters a-v only (no w/x/y/z), 5-1024 characters. The prior minter used
# a literal "tmbx" prefix — 'x' sits past 'v' in that alphabet, so every
# id it produced was invalid and every create-event against a real
# calendar would have been rejected. "tmb0" reads the same way with a
# digit standing in for the disallowed letter; the random suffix is drawn
# from the same alphabet rather than reused from ``uuid4().hex`` (which
# happens to be a subset today — 0-9a-f — but that's a coincidence this
# code should not depend on staying true).
_BASE32HEX_ALPHABET = "0123456789abcdefghijklmnopqrstuv"
_EVENT_ID_PREFIX = "tmb0"
_EVENT_ID_RANDOM_LEN = 20
_MIN_EVENT_ID_LEN = 5
_MAX_EVENT_ID_LEN = 1024


def is_valid_base32hex_event_id(value: str) -> bool:
    """True if ``value`` is a legal Google Calendar custom event id.

    Plain string predicate, no regex — an id this system itself mints is
    the documented exception to the project's "no judging string meaning"
    rule (``CLAUDE.md``): it carries no user content, only shape.
    """
    if not (_MIN_EVENT_ID_LEN <= len(value) <= _MAX_EVENT_ID_LEN):
        return False
    return all(ch in _BASE32HEX_ALPHABET for ch in value)


def _mint_event_id() -> str:
    """Mint a random, opaque event id valid as a Google custom event id.

    Identity stays random and content-free on purpose — never derived from
    ``date|name|start|index`` the way the legacy engine's
    ``sync_engine.base32hex_id`` did. A content-derived id is exactly the
    thing this package's two-level identity design (``uid``/``handle``)
    exists to avoid, and it breaks the moment a block is renamed.
    """
    random_part = "".join(
        secrets.choice(_BASE32HEX_ALPHABET) for _ in range(_EVENT_ID_RANDOM_LEN)
    )
    event_id = f"{_EVENT_ID_PREFIX}{random_part}"
    if not is_valid_base32hex_event_id(event_id):
        # Can't happen given the alphabet/length above — a hard failure
        # here beats silently handing Google an id it will reject.
        raise RuntimeError(f"minted event id {event_id!r} is not valid base32hex")
    return event_id

# Task 13 threaded a required ``tz`` through ``list_day``/``make_snapshot``
# after this brief was written. ``Plan.tz`` is where the domain's timezone
# lives, so its default is the one honest fallback for the one call site
# that has no snapshot yet to read a ``tz`` off of: the first ``read`` of a
# day. ``undo`` reads ``JournalEntry.tz`` instead — populated on every
# commit — rather than guessing.
_DEFAULT_TZ = Plan.model_fields["tz"].default


class ConflictError(RuntimeError):
    """A write was refused because the calendar changed since the snapshot."""

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__(f"calendar drifted: {', '.join(conflicts)}")
        self.conflicts = conflicts


class ForeignBlockError(RuntimeError):
    """A patch tried to modify or remove a block tmbx does not own.

    A foreign block — a calendar event with no tmbx ``uid``, e.g. a meeting
    someone else created — is read-only context: the chain must respect it
    and the model must see it, but tmbx must never write, retime, or delete
    one. A patch that tries anyway is refused outright rather than silently
    dropped, so the model learns foreign blocks are immovable instead of
    watching its own edit vanish.
    """

    def __init__(self, handles: list[str]) -> None:
        super().__init__(f"patch touches foreign block(s): {', '.join(handles)}")
        self.handles = handles


class RefusalOption(BaseModel):
    """One thing the user can choose in response to a refusal.

    Only the violation refusal carries these, because it is the only one
    that is a *choice*. A stale snapshot has one correct response (re-read)
    and a foreign block has one (drop the op) — neither asks the user
    anything. A plan that does not fit does: the day they described cannot
    be written as described, and someone has to say what gives way.

    ``id`` is the stable key a renderer switches on; ``label`` and
    ``consequence`` are default copy a generic card can use and a styled one
    can ignore. ``expect`` carries the literal argument that enacts the
    option, so a host never has to know that ``"force"`` is the word — the
    remedy travels with the refusal instead of living in prose a caller has
    to have read.
    """

    model_config = ConfigDict(extra="forbid")

    id: Literal["replan", "accept"]
    label: str
    consequence: str
    expect: Literal["force"] | None = None


_REPLAN_OPTION = RefusalOption(
    id="replan",
    label="Change the plan",
    consequence=(
        "Nothing is written. Build a patch that resolves the conflict — move, "
        "shorten, or drop one of the blocks named — and commit that instead."
    ),
)

_ACCEPT_OPTION = RefusalOption(
    id="accept",
    label="Write it anyway",
    consequence=(
        "Writes the plan exactly as previewed, conflicts included. The "
        "calendar will hold a day that does not fit."
    ),
    expect="force",
)


class PlanViolationError(RuntimeError):
    """A write was refused because the resulting plan does not fit.

    The third refusal cause, alongside ``ConflictError`` (the calendar
    drifted — re-read) and ``ForeignBlockError`` (the patch names a block
    tmbx does not own — route around it). This one's remedy is neither:
    re-plan, or accept the plan as it is.

    #170: this check did not exist. ``apply`` reported an overlap as
    advisory data on an ``ok: true`` preview and ``commit`` never looked at
    ``violations``, so a model could read the overlap, quote it correctly,
    and commit it anyway — measured 4 of 4 resamples under a real harness.
    Every earlier exercise of this path had a human reading the violation
    and declining, which looked like the service refusing. It never was.

    ``forceable`` says whether ``expect="force"`` can actually enact the
    ``accept`` option. It is asked structurally, of the plan itself (see
    ``_still_resolves``), never carried as a per-kind opinion — offering an
    "accept" that would fail anyway is the same shape of misleading signal
    this refusal exists to remove.
    """

    def __init__(self, violations: list[Violation], *, forceable: bool) -> None:
        super().__init__(
            "plan does not fit: " + "; ".join(v.message for v in violations)
        )
        self.violations = violations
        self.forceable = forceable
        self.options: list[RefusalOption] = (
            [_REPLAN_OPTION, _ACCEPT_OPTION] if forceable else [_REPLAN_OPTION]
        )


def _still_resolves(plan: Plan) -> bool:
    """Can this plan be turned into concrete times at all?

    The line between a violation ``expect="force"`` can write past and one
    it cannot. An overlap still resolves — every block has a real start and
    end, they simply collide — so forcing it writes a real, bad day. An
    unanchored or circular chain does not resolve at all: there are no
    datetimes to send, and ``_write``'s own ``resolve(check_overlap=False)``
    raises before the first calendar call, force or no force.

    Asked of the plan rather than kept as a list of which kinds are
    forceable: a list would be a second opinion about the same question,
    free to drift from the one ``_write`` actually acts on.
    """
    try:
        plan.resolve(check_overlap=False)
    except PlanViolation:
        return False
    return True


class ReadResult(BaseModel):
    """``read()``'s sibling for a caller that needs the plan as *text*.

    Kept as a separate return type — and ``read_rendered`` as a separate
    method — rather than changing what ``read()`` itself returns, so every
    existing caller of ``read()`` (including its own test suite, which
    unpacks a bare ``(Plan, Snapshot)`` tuple throughout) is untouched.
    """

    snapshot: Snapshot
    rendered: str
    blocks: int


class ApplyResult(BaseModel):
    """A preview. ``violations`` carries the same ``Violation`` objects a
    ``PlanViolationError`` would — one shape, so a card built to render the
    refusal renders the preview too, and neither can word it differently.

    ``committable`` is derived, never stored: a preview that says it is
    committable while carrying violations is exactly the mismatch #170 was.
    It answers only "would ``commit`` refuse this plan?" — it says nothing
    about calendar drift, which ``apply`` deliberately never checks.

    ``overspecified`` and ``unallocated`` are the two directions of least
    commitment and neither gates ``committable``: a day may be pinned harder
    than it needs to be, or have three hours in the middle with nothing in
    them, and still be a day the calendar will accept. They are what a
    caller reads to tell a reasoned plan from an arbitrary one — which is
    the difference the user cannot see in a rendered plan and cannot correct
    without being told.
    """

    plan: Plan
    rendered: str
    violations: list[Violation]
    overspecified: list[str]
    unallocated: list[Gap]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def committable(self) -> bool:
        return not self.violations


class CommitResult(BaseModel):
    tx_id: str | None
    committed: bool
    conflicts: list[str] = []


_FALLBACK_TYPE = ET.M


def _fallback_timing(event: CalendarEvent) -> Timing:
    """The one, deliberate default: a plain fixed window off the observed
    event times. Used both for foreign events (which never carry
    ``block_type``/``timing_mode`` at all — that is normal, permanent,
    and not logged) and as the last resort for an owned event whose
    stored value is missing or unparseable (logged — see
    ``_reconstruct_timing``)."""
    return FixedWindow(st=event.start.time(), et=event.end.time())


def _reconstruct_timing(mode: str, event: CalendarEvent) -> Timing | None:
    """Rebuild the stored ``Timing`` variant for one ``timing_mode`` literal.

    The event's own ``start``/``end`` are always the source of truth for
    *when* the block actually sits — including after someone drags it in
    Google — so every branch derives its numbers from them rather than
    from anything else that might have been stored: ``dur`` is always
    ``event.end - event.start``, ``st``/``et`` are always
    ``event.start.time()``/``event.end.time()``. The stored mode is only
    ever the source of truth for *how it was meant to flex* — which
    variant to rebuild, never what its numbers are. Returns ``None`` for
    an unrecognised mode string, so the caller can fall back deliberately
    rather than this function guessing.
    """
    duration = event.end - event.start
    if mode == "ap":
        return AfterPrev(dur=duration)
    if mode == "bn":
        return BeforeNext(dur=duration)
    if mode == "fs":
        return FixedStart(st=event.start.time(), dur=duration)
    if mode == "fw":
        return FixedWindow(st=event.start.time(), et=event.end.time())
    return None


def _reconstruct_block_type(value: str) -> ET | None:
    """Validate a stored ``block_type`` string against ``ET``.

    A plain constructor call against a closed enum, not a judgement about
    what the string *means* — the documented ``CLAUDE.md`` exception for
    an identifier the system itself minted and is reading back, exactly
    like ``is_valid_base32hex_event_id`` above.
    """
    try:
        return ET(value)
    except ValueError:
        return None


_ANCHOR_SOURCES: frozenset[str] = frozenset(get_args(AnchorSource))

_DEFAULT_ANCHOR_SOURCE: AnchorSource = "calendar"


def _stored_anchor_source(block: Block) -> str | None:
    """What a write should persist for a block's ``anchor_source``.

    ``"calendar"`` is deliberately not stored: it *is* the read-side
    default for an event carrying no recorded provenance (see
    ``_anchor_for``), so persisting it would be storing "nothing is known"
    as a value. Eliding it keeps the round trip lossless in both
    directions and keeps ``_event_unchanged`` honest — every event already
    on a real calendar predates this field, and writing the default would
    make the next commit rewrite every single one of them, an etag bump
    and a change notification per event, to record nothing at all.

    Paired with ``_anchor_for``; the two are only correct together, so
    either changing must change both.
    """
    if block.anchor_source == _DEFAULT_ANCHOR_SOURCE:
        return None
    return block.anchor_source


def _anchor_for(timing: Timing, stored: AnchorSource | None) -> AnchorSource | None:
    """The ``anchor_source`` a rebuilt block should carry.

    Stored provenance wins whenever there is any. Otherwise fixed timing
    gets ``"calendar"`` — ``Block`` requires a source for ``fs``/``fw``,
    and "it is where the calendar says it is" is the only claim tmbx can
    honestly make about an event it has no record for. Non-fixed timing
    gets ``None``: an ``ap`` block needs no source, and filling one in
    would leave stale provenance on a block that is not pinned at all.
    """
    if stored is not None:
        return stored
    return _DEFAULT_ANCHOR_SOURCE if timing.a in ("fs", "fw") else None


def _reconstruct_anchor_source(value: str | None) -> AnchorSource | None:
    """Validate a stored ``anchor_source`` string against ``AnchorSource``.

    Membership in a closed literal set the system itself minted and is
    reading back — the documented ``CLAUDE.md`` exception, exactly like
    ``_reconstruct_block_type`` above. The set comes from ``get_args`` on
    the type, never a hand-typed list, so widening ``AnchorSource`` cannot
    leave a second copy behind to drift.
    """
    if value is None or value not in _ANCHOR_SOURCES:
        return None
    return value  # type: ignore[return-value]


def _event_to_block(event: CalendarEvent, index: int, uid: str) -> Block:
    """Build a block from a calendar event, minting a handle if absent.

    ``uid`` is supplied by the caller, never derived from ``event`` here.
    For a tmbx-owned event it is the real ``event.uid``; for a foreign one
    (see ``PlanService._plan_from_calendar``) it is a throwaway placeholder
    that exists only to satisfy ``Block.uid: str`` and must never be
    mistaken for a durable, correlatable identity.

    Type, mode and ``anchor_source`` are reconstructed from
    ``event.block_type``/``event.timing_mode``/``event.anchor_source``
    (round-tripped through provider extended properties by a real adapter
    — see ``gcal.py``).

    ``anchor_source`` is reconstructed independently of the other two: it
    says why a block is pinned, not how, so an event whose type/mode are
    unusable can still have perfectly good provenance and must not lose it
    to an unrelated fallback. An absent or unrecognised value defaults to
    ``"calendar"``, which is the honest answer in both cases it actually
    occurs — a foreign event, whose time is an observed fact tmbx neither
    chose nor may change, and an event written before this round trip
    existed, where tmbx genuinely has no record of why. It is also the
    only default ``Block`` accepts: fixed timing requires *some* source,
    and inventing ``"user"`` or ``"constraint"`` would manufacture the
    exact provenance this field exists to keep honest.

    Type and mode are used only when both are present and valid; a plain
    ``FixedWindow``/``ET.M`` fallback covers four distinct cases, three of
    which are logged:

    * **Foreign** (``event.uid is None``): never carries these fields at
      all. Expected, permanent, not an anomaly — never logged. This is
      the guard that keeps a foreign event from ever looking owned; it
      must never regress.
    * **Owned, but missing/unparseable** (a partial write, or an event
      written before this round-trip existed — an older schema version):
      genuinely anomalous. Falls back the same way, deliberately, but
      logs a warning naming the event so a partial write is visible
      rather than silently guessed at.
    * **Owned, individually valid but jointly inconsistent** — e.g.
      ``block_type="BG"`` stored alongside ``timing_mode="ap"``, which
      ``Block``'s own validator rejects (``BG`` requires ``fs``/``fw``).
      Each value parses fine on its own, so the two checks above never
      catch it; the same fallback still applies, still logged, rather
      than letting ``Block`` construction raise and take the whole read
      down with it.
    * **Owned, both present and valid**: the real path — reconstructs the
      actual stored variant via ``_reconstruct_timing``.

    Guessing a type from ``summary``/``description`` text is never done
    in any of these cases — that would be exactly the string-meaning
    judgement this project bans; an unreconstructable block is always
    ``ET.M``, never inferred from its name.
    """
    handle = event.handle or f"EVT{index + 1}"
    block_type = _reconstruct_block_type(event.block_type) if event.block_type else None
    timing = _reconstruct_timing(event.timing_mode, event) if event.timing_mode else None
    anchor_source = _reconstruct_anchor_source(event.anchor_source)

    if block_type is not None and timing is not None:
        try:
            return Block(
                uid=uid,
                h=handle,
                slug=event.slug,
                n=event.summary,
                d=event.description,
                t=block_type,
                p=timing,
                anchor_source=_anchor_for(timing, anchor_source),
            )
        except ValidationError:
            reason = "jointly inconsistent"
    else:
        reason = "unusable"

    if event.uid is not None:
        _logger.warning(
            "calendar event %r has a tmbx uid but %s block_type/timing_mode "
            "(block_type=%r, timing_mode=%r) — defaulting to %s/fw. Likely "
            "a partial write or an event written before this round-trip "
            "existed.",
            event.event_id,
            reason,
            event.block_type,
            event.timing_mode,
            _FALLBACK_TYPE.value,
        )

    fallback_timing = _fallback_timing(event)
    return Block(
        uid=uid,
        h=handle,
        slug=event.slug,
        n=event.summary,
        d=event.description,
        t=_FALLBACK_TYPE,
        p=fallback_timing,
        anchor_source=_anchor_for(fallback_timing, anchor_source),
    )


def _event_unchanged(existing: CalendarEvent, candidate: CalendarEvent) -> bool:
    """True if writing ``candidate`` over ``existing`` would be a no-op.

    Compares only the fields tmbx actually controls — summary, description,
    start, end, uid, handle, slug, block_type, timing_mode — never ``etag``
    (``candidate`` never carries a real one; see ``_write``) or
    ``event_id`` (already the join key that got the two events paired
    up). A block whose resolved state is identical to what's live must
    not be re-``update``d just because some *other* block in the same
    commit changed.

    ``block_type``/``timing_mode``/``anchor_source`` matter here for the
    same reason the identity fields do: a patch that only relaxes ``fs``
    to ``ap``, only retypes ``M`` to ``DW``, or only re-sources a pin from
    ``constraint`` to ``user``, changes neither the resolved time nor any
    other currently-compared field, so without these three in the
    comparison that edit would be (wrongly) treated as a no-op and never
    actually reach the calendar — silently reintroducing the exact
    round-trip loss this comparison exists to close. Re-sourcing is the
    one sanctioned way to hand a constraint-held boundary back to the user
    (see ``ops._boundary_relaxation_errors``); a re-source that never
    reached the calendar would make that handover a no-op and leave the
    block un-relaxable forever.
    """
    return (
        existing.summary == candidate.summary
        and existing.description == candidate.description
        and existing.start == candidate.start
        and existing.end == candidate.end
        and existing.uid == candidate.uid
        and existing.handle == candidate.handle
        and existing.slug == candidate.slug
        and existing.block_type == candidate.block_type
        and existing.timing_mode == candidate.timing_mode
        and existing.anchor_source == candidate.anchor_source
    )


def _foreign_touches(patch: Patch, foreign_handles: set[str]) -> list[str]:
    """Handles a patch tries to retime, remove, or move that tmbx doesn't own.

    ``AddBlock.after`` naming a foreign handle is deliberately not flagged —
    that only positions a new block relative to it, it never touches the
    foreign block's own fields.
    """
    touched = {
        op.h
        for op in patch.ops
        if isinstance(op, (RemoveBlock, UpdateBlock, MoveBlock)) and op.h in foreign_handles
    }
    return sorted(touched)


class PlanService:
    """Read, preview, commit and undo day plans.

    Holds only a calendar port, a journal store, and a uid minter — no
    per-snapshot cache. See the module docstring for why.
    """

    def __init__(
        self,
        calendar: CalendarPort,
        store: JournalStore,
        *,
        mint_uid: Callable[[], str] | None = None,
    ) -> None:
        self.calendar = calendar
        self.store = store
        self._mint_uid = mint_uid or (lambda: uuid.uuid4().hex)
        self._commit_idempotency_locks: dict[str, asyncio.Lock] = {}
        self._commit_idempotency_refs: dict[str, int] = {}

    async def _plan_from_calendar(
        self, calendar_id: str, day: date_type, tz: str
    ) -> tuple[Plan, list[CalendarEvent], set[str]]:
        """Fetch live calendar state and rebuild it as a plan.

        The durable source of truth. Called fresh by ``read``, ``apply`` and
        ``commit`` alike — never cached — so every call sees what is
        actually on the calendar right now.

        Returns the plan, the raw events it was built from, and the set of
        block uids that are FOREIGN — read-only context tmbx did not
        create. An event with no ``uid`` extended property (a meeting, an
        invite, anything not made by this system) gets a throwaway uid
        minted here purely to satisfy ``Block.uid: str``; ownership is the
        calendar's own ``uid`` presence, never a string this method fills
        the field with. ``_write`` and the foreign-touch guard in
        ``apply``/``commit`` use this set to make sure a foreign block is
        never created, updated, retimed, or deleted.
        """
        events = await self.calendar.list_day(calendar_id, day, tz)
        events = sorted(events, key=lambda e: e.start)
        blocks: list[Block] = []
        foreign_uids: set[str] = set()
        for index, event in enumerate(events):
            if event.uid:
                blocks.append(_event_to_block(event, index, event.uid))
            else:
                placeholder = self._mint_uid()
                foreign_uids.add(placeholder)
                blocks.append(_event_to_block(event, index, placeholder))
        plan = Plan(date=day, tz=tz, blocks=blocks)
        return plan, events, foreign_uids

    async def read(
        self, calendar_id: str, day: date_type, tz: str = _DEFAULT_TZ
    ) -> tuple[Plan, Snapshot]:
        """Fetch live calendar state as a plan plus a snapshot token.

        Foreign events (see ``_plan_from_calendar``) are included as
        ordinary blocks — the model needs to see them, and the chain must
        respect them — but any patch that later tries to touch one is
        refused; see ``apply``/``commit``.
        """
        plan, events, _foreign_uids = await self._plan_from_calendar(calendar_id, day, tz)
        snapshot = make_snapshot(calendar_id, day, tz, events)
        return plan, snapshot

    async def read_rendered(
        self, calendar_id: str, day: date_type, tz: str = _DEFAULT_TZ
    ) -> ReadResult:
        """Like ``read()``, but returns the plan already rendered to text
        with foreign blocks marked — see ``core.render.render_plan``'s
        ``own`` column. The MCP server's ``plan_read`` tool uses this
        instead of calling ``render_plan`` itself, so foreign-block
        knowledge never has to be re-derived (or, worse, guessed from
        handle naming) outside this module.
        """
        plan, events, foreign_uids = await self._plan_from_calendar(calendar_id, day, tz)
        snapshot = make_snapshot(calendar_id, day, tz, events)
        return ReadResult(
            snapshot=snapshot,
            rendered=render_plan(plan, foreign_uids),
            blocks=len(plan.blocks),
        )

    async def apply(self, snapshot: Snapshot, patch: Patch) -> ApplyResult:
        """Pure preview: applies ops against the live plan, validates,
        journals the attempt, writes nothing to the calendar.

        The plan is re-derived from the calendar on every call (see the
        module docstring), so a caller holding one ``snapshot`` across two
        ``apply`` calls can see the preview move between them if the
        calendar changed in between — ``apply`` never checks drift, only
        ``commit`` does.
        """
        plan, _events, foreign_uids = await self._plan_from_calendar(
            snapshot.calendar_id, snapshot.day, snapshot.tz
        )

        foreign_handles = {b.h for b in plan.blocks if b.uid in foreign_uids}
        touched = _foreign_touches(patch, foreign_handles)
        if touched:
            error = f"patch touches foreign block(s): {', '.join(touched)}"
            await self._journal(snapshot, patch, PatchOutcome.APPLY_FAILED, error=error)
            raise ForeignBlockError(touched)

        violations: list[Violation] = []
        outcome = PatchOutcome.APPLIED
        try:
            patched = apply_ops(plan, patch, mint_uid=self._mint_uid)
        except ValueError as exc:
            await self._journal(snapshot, patch, PatchOutcome.APPLY_FAILED, error=str(exc))
            raise

        try:
            patched.resolve()
        except PlanViolation as exc:
            violations.append(exc.violation)
            outcome = PatchOutcome.VALIDATION_FAILED

        await self._journal(snapshot, patch, outcome)
        return ApplyResult(
            plan=patched,
            rendered=render_plan(patched, foreign_uids),
            violations=violations,
            overspecified=overspecified(patched),
            unallocated=unallocated(patched),
        )

    async def commit(
        self,
        snapshot: Snapshot,
        patch: Patch,
        *,
        expect: Literal["clean", "force"] = "clean",
        idempotency_key: str | None = None,
    ) -> CommitResult:
        """Check preconditions and make one durable write per idempotency key."""

        if idempotency_key is None:
            return await self._commit_once(snapshot, patch, expect=expect)

        lock = self._commit_idempotency_locks.setdefault(
            idempotency_key, asyncio.Lock()
        )
        self._commit_idempotency_refs[idempotency_key] = (
            self._commit_idempotency_refs.get(idempotency_key, 0) + 1
        )
        try:
            async with lock:
                existing = await self.store.by_tx_id(idempotency_key)
                if (
                    existing is not None
                    and existing.kind == EntryKind.COMMIT
                    and existing.outcome == PatchOutcome.APPLIED
                ):
                    return CommitResult(
                        tx_id=idempotency_key,
                        committed=True,
                        conflicts=[],
                    )
                return await self._commit_once(
                    snapshot,
                    patch,
                    expect=expect,
                    tx_id=idempotency_key,
                )
        finally:
            remaining = self._commit_idempotency_refs[idempotency_key] - 1
            if remaining:
                self._commit_idempotency_refs[idempotency_key] = remaining
            else:
                self._commit_idempotency_refs.pop(idempotency_key, None)
                if self._commit_idempotency_locks.get(idempotency_key) is lock:
                    self._commit_idempotency_locks.pop(idempotency_key, None)

    async def _commit_once(
        self,
        snapshot: Snapshot,
        patch: Patch,
        *,
        expect: Literal["clean", "force"],
        tx_id: str | None = None,
    ) -> CommitResult:
        """Perform the calendar write after idempotency ownership is resolved."""

        plan, live, foreign_uids = await self._plan_from_calendar(
            snapshot.calendar_id, snapshot.day, snapshot.tz
        )

        foreign_handles = {b.h for b in plan.blocks if b.uid in foreign_uids}
        touched = _foreign_touches(patch, foreign_handles)
        if touched:
            error = f"patch touches foreign block(s): {', '.join(touched)}"
            await self._journal(snapshot, patch, PatchOutcome.APPLY_FAILED, error=error)
            raise ForeignBlockError(touched)

        conflicts = drift(snapshot, live)
        if conflicts and expect != "force":
            raise ConflictError(conflicts)

        patched = apply_ops(plan, patch, mint_uid=self._mint_uid)

        # Gate on the plan the caller would actually get. Without this,
        # ``apply``'s violations were advisory only and a host could read an
        # overlap, report it accurately, and commit it in the next call
        # (#170). ``expect="force"`` stays the single deliberate override —
        # the same one the drift refusal above already uses — but it can
        # only enact a plan that resolves at all; see ``_still_resolves``.
        try:
            patched.resolve()
        except PlanViolation as exc:
            forceable = _still_resolves(patched)
            if not (forceable and expect == "force"):
                await self._journal(
                    snapshot,
                    patch,
                    PatchOutcome.VALIDATION_FAILED,
                    error=exc.violation.message,
                )
                raise PlanViolationError([exc.violation], forceable=forceable) from exc

        before = [event.model_copy(deep=True) for event in live]
        await self._write(snapshot.calendar_id, patched, live, foreign_uids)

        # Capture state as it stands immediately after the write. Undo
        # compares live state against THIS. Re-deriving it at undo time
        # would compare the calendar against itself, making the conflict
        # check a silent no-op.
        post_events = await self.calendar.list_day(
            snapshot.calendar_id, snapshot.day, snapshot.tz
        )
        post_snapshot = make_snapshot(
            snapshot.calendar_id, snapshot.day, snapshot.tz, post_events
        )

        tx_id = tx_id or uuid.uuid4().hex
        await self._journal(
            snapshot,
            patch,
            PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id=tx_id,
            before=before,
            post_etags=post_snapshot.etags,
        )
        return CommitResult(tx_id=tx_id, committed=True, conflicts=conflicts)

    async def undo(self, tx_id: str) -> CommitResult:
        """Restore the pre-commit state, refusing to clobber newer edits.

        Reads its precondition state from the journal, not from process
        memory, so undo survives a restart — the defect behind #112.

        Ownership applies here too: ``before_json`` is a snapshot of every
        event on the day, foreign ones included, so the restore/delete pass
        below only ever touches events that carry a real ``uid`` — a
        foreign event must never be re-``update``d back to data it already
        has (which would still bump its etag on a real provider) or treated
        as a deletion candidate.
        """
        row = await self.store.by_tx_id(tx_id)
        if (
            row is None
            or row.kind is not EntryKind.COMMIT
            or row.before_json is None
            or row.post_etags_json is None
        ):
            raise KeyError(f"unknown or non-undoable transaction {tx_id}")

        calendar_id, day, tz = row.calendar_id, row.plan_date, row.tz
        before_events = [
            CalendarEvent.model_validate(item) for item in json.loads(row.before_json)
        ]
        post_etags: dict[str, str] = json.loads(row.post_etags_json)

        live = await self.calendar.list_day(calendar_id, day, tz)
        # Reuse drift() rather than re-deriving an inline etag comparison,
        # so the commit precondition and the undo precondition stay one
        # algorithm. token/event_ids are irrelevant to drift() (it only
        # reads .etags) and are left at their defaults.
        post_snapshot = Snapshot(
            token="undo", calendar_id=calendar_id, day=day, tz=tz, etags=post_etags
        )
        conflicts = drift(post_snapshot, live)
        if conflicts:
            raise ConflictError(conflicts)

        current_ids = {event.event_id for event in live}
        owned_current_ids = {event.event_id for event in live if event.uid}
        owned_before = [event for event in before_events if event.uid]
        owned_before_ids = {event.event_id for event in owned_before}

        for event in owned_before:
            if event.event_id in current_ids:
                await self.calendar.update(calendar_id, event)
            else:
                await self.calendar.create(calendar_id, event)
        for event_id in owned_current_ids - owned_before_ids:
            await self.calendar.delete(calendar_id, event_id)

        undo_tx = uuid.uuid4().hex
        entry = JournalEntry(
            calendar_id=calendar_id,
            plan_date=day,
            tz=tz,
            kind=EntryKind.UNDO,
            outcome=PatchOutcome.APPLIED,
            tx_id=undo_tx,
            undoes_tx=tx_id,
        )
        await self.store.append(entry)
        return CommitResult(tx_id=undo_tx, committed=True)

    async def _write(
        self,
        calendar_id: str,
        plan: Plan,
        existing: list[CalendarEvent],
        foreign_uids: set[str],
    ) -> None:
        """Push a resolved plan to the calendar.

        ``existing`` is the just-fetched live event list (from the caller's
        drift check), reused rather than re-fetched, so the create-vs-update
        decision below is made from the freshest possible read — important
        under ``expect="force"``, where ``existing`` may disagree with the
        snapshot the caller committed against.

        Uses ``row.start_dt``/``row.end_dt`` (real datetimes), never
        ``datetime.combine(plan.date, row.start)`` — a block may cross
        midnight, and recombining a wall-clock ``time`` with the plan's
        single ``date`` silently truncates the day.

        Provider event ids come from a uid->event_id map built off
        ``existing``, never from the uid's string form. uid is opaque;
        deriving one identifier from the other silently breaks the moment
        uids are really minted.

        A block whose uid is in ``foreign_uids`` is skipped outright — no
        create, no update — and ``existing`` events with no real ``uid``
        are excluded from ``owned_ids`` entirely, so they can never be a
        deletion candidate either. ``apply``/``commit`` already refuse a
        patch that tries to touch a foreign block (``_foreign_touches``),
        so this is a second, structural guarantee: even a bug in that
        check could not make this method write to something tmbx doesn't
        own.

        A block whose resolved state exactly matches what's already on the
        calendar is skipped, not re-``update``d — see ``_event_unchanged``.
        Every commit resolves the *whole* plan, so without this check a
        one-block patch would rewrite every other block too: harmless
        against the fake, but against a real provider that's an etag bump
        and a change notification for every event on the day, on every
        commit.

        ``block_type``/``timing_mode``/``anchor_source`` are set from the
        block's own ``t``/``p.a``/``anchor_source`` on every write (the
        last via ``_stored_anchor_source``, which elides the read-side
        default) — a
        real provider round-trips these through extended properties (see
        ``gcal.py``) so a later read can reconstruct the actual ``Timing``
        variant, and the reason the block is pinned, via
        ``_event_to_block`` instead of always seeing a plain fixed window
        pinned for no stated reason.
        """
        resolved = {row.h: row for row in plan.resolve(check_overlap=False)}
        existing_by_id = {event.event_id: event for event in existing}
        owned_ids = {event.event_id for event in existing if event.uid}
        event_ids = {event.uid: event.event_id for event in existing if event.uid}
        keep: set[str] = set()

        for block in plan.blocks:
            if block.uid in foreign_uids:
                continue  # read-only context: never created, updated, or deleted
            row = resolved[block.h]
            event_id = event_ids.get(block.uid) or _mint_event_id()
            keep.add(event_id)
            event = CalendarEvent(
                event_id=event_id,
                summary=block.n,
                description=block.d,
                start=row.start_dt,
                end=row.end_dt,
                uid=block.uid,
                handle=block.h,
                slug=block.slug,
                block_type=block.t.value,
                timing_mode=block.p.a,
                anchor_source=_stored_anchor_source(block),
            )
            existing_event = existing_by_id.get(event_id)
            if existing_event is None:
                await self.calendar.create(calendar_id, event)
            elif not _event_unchanged(existing_event, event):
                await self.calendar.update(calendar_id, event)

        for event_id in owned_ids - keep:
            await self.calendar.delete(calendar_id, event_id)

    async def _journal(
        self,
        snapshot: Snapshot,
        patch: Patch,
        outcome: PatchOutcome,
        *,
        kind: EntryKind = EntryKind.ATTEMPT,
        tx_id: str | None = None,
        error: str | None = None,
        before: list[CalendarEvent] | None = None,
        post_etags: dict[str, str] | None = None,
    ) -> None:
        entry = JournalEntry(
            calendar_id=snapshot.calendar_id,
            plan_date=snapshot.day,
            tz=snapshot.tz,
            kind=kind,
            ops_json=patch.model_dump_json(),
            outcome=outcome,
            error=error,
            tx_id=tx_id,
            before_json=(
                json.dumps([event.model_dump(mode="json") for event in before])
                if before is not None
                else None
            ),
            post_etags_json=(
                json.dumps(post_etags) if post_etags is not None else None
            ),
        )
        await self.store.append(entry)


__all__ = [
    "ApplyResult",
    "CommitResult",
    "ConflictError",
    "ForeignBlockError",
    "PlanService",
    "PlanViolationError",
    "ReadResult",
    "RefusalOption",
    "is_valid_base32hex_event_id",
]
