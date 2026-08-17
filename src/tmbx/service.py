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
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import date as date_type
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

from .calendar.port import CalendarEvent, CalendarPort, Snapshot, drift, make_snapshot
from .core.commitment import overspecified
from .core.models import (
    ET,
    AfterPrev,
    BeforeNext,
    Block,
    FixedStart,
    FixedWindow,
    Plan,
    Timing,
)
from .core.ops import MoveBlock, Patch, RemoveBlock, UpdateBlock, apply_ops
from .core.render import render_plan
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
    plan: Plan
    rendered: str
    violations: list[str]
    overspecified: list[str]


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


def _event_to_block(event: CalendarEvent, index: int, uid: str) -> Block:
    """Build a block from a calendar event, minting a handle if absent.

    ``uid`` is supplied by the caller, never derived from ``event`` here.
    For a tmbx-owned event it is the real ``event.uid``; for a foreign one
    (see ``PlanService._plan_from_calendar``) it is a throwaway placeholder
    that exists only to satisfy ``Block.uid: str`` and must never be
    mistaken for a durable, correlatable identity.

    Every calendar event is a fixed window *at minimum* — its start/end
    are the observed fact, never the model's intent — so ``anchor_source``
    is always ``"calendar"`` regardless of which ``Timing`` variant comes
    back. Type and mode are reconstructed from ``event.block_type``/
    ``event.timing_mode`` (round-tripped through provider extended
    properties by a real adapter — see ``gcal.py``) when both are present
    and valid; a plain ``FixedWindow``/``ET.M`` fallback covers four
    distinct cases, three of which are logged:

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
                anchor_source="calendar",
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

    return Block(
        uid=uid,
        h=handle,
        slug=event.slug,
        n=event.summary,
        d=event.description,
        t=_FALLBACK_TYPE,
        p=_fallback_timing(event),
        anchor_source="calendar",
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

    ``block_type``/``timing_mode`` matter here for the same reason the
    identity fields do: a patch that only relaxes ``fs`` to ``ap``, or
    only retypes ``M`` to ``DW``, changes neither the resolved time nor
    any other currently-compared field, so without these two in the
    comparison that edit would be (wrongly) treated as a no-op and never
    actually reach the calendar — silently reintroducing the exact
    round-trip loss this comparison exists to close.
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

        violations: list[str] = []
        outcome = PatchOutcome.APPLIED
        try:
            patched = apply_ops(plan, patch, mint_uid=self._mint_uid)
        except ValueError as exc:
            await self._journal(snapshot, patch, PatchOutcome.APPLY_FAILED, error=str(exc))
            raise

        try:
            patched.resolve()
        except ValueError as exc:
            violations.append(str(exc))
            outcome = PatchOutcome.VALIDATION_FAILED

        await self._journal(snapshot, patch, outcome)
        return ApplyResult(
            plan=patched,
            rendered=render_plan(patched, foreign_uids),
            violations=violations,
            overspecified=overspecified(patched),
        )

    async def commit(
        self,
        snapshot: Snapshot,
        patch: Patch,
        *,
        expect: Literal["clean", "force"] = "clean",
    ) -> CommitResult:
        """Check preconditions, write to the calendar, journal the commit."""
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

        tx_id = uuid.uuid4().hex
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

        ``block_type``/``timing_mode`` are set from the resolved block's
        own ``t``/``p.a`` on every write — a real provider round-trips
        these through extended properties (see ``gcal.py``) so a later
        read can reconstruct the actual ``Timing`` variant via
        ``_event_to_block`` instead of always seeing a plain fixed
        window.
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
    "ReadResult",
    "is_valid_base32hex_event_id",
]
