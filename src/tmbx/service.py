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
import uuid
from datetime import date as date_type
from typing import Callable, Literal

from pydantic import BaseModel

from .calendar.port import CalendarEvent, CalendarPort, Snapshot, drift, make_snapshot
from .core.commitment import overspecified
from .core.models import ET, Block, FixedWindow, Plan
from .core.ops import Patch, apply_ops
from .core.render import render_plan
from .journal.models import EntryKind, JournalEntry, PatchOutcome
from .journal.store import JournalStore

# Task 13 threaded a required ``tz`` through ``list_day``/``make_snapshot``
# after this brief was written. ``Plan.tz`` is where the domain's timezone
# lives, so its default is the one honest fallback for the two call sites
# that have no snapshot yet to read a ``tz`` off of: the first ``read`` of a
# day, and ``undo`` (whose ``JournalEntry`` row carries no ``tz`` column).
_DEFAULT_TZ = Plan.model_fields["tz"].default


class ConflictError(RuntimeError):
    """A write was refused because the calendar changed since the snapshot."""

    def __init__(self, conflicts: list[str]) -> None:
        super().__init__(f"calendar drifted: {', '.join(conflicts)}")
        self.conflicts = conflicts


class ApplyResult(BaseModel):
    plan: Plan
    rendered: str
    violations: list[str]
    overspecified: list[str]


class CommitResult(BaseModel):
    tx_id: str | None
    committed: bool
    conflicts: list[str] = []


def _event_to_block(event: CalendarEvent, index: int) -> Block:
    """Build a block from a calendar event, minting a handle if absent.

    Every calendar event is a fixed window by construction — its start/end
    are the observed fact, not the model's intent — so ``anchor_source`` is
    always ``"calendar"``. ``t`` defaults to ``ET.M``: ``CalendarEvent``
    carries no block-type field to read one from, and guessing a type from
    ``summary``/``description`` text would be exactly the string-meaning
    judgement this project bans.
    """
    handle = event.handle or f"EVT{index + 1}"
    return Block(
        uid=event.uid or f"u-{event.event_id}",
        h=handle,
        slug=event.slug,
        n=event.summary,
        d=event.description,
        t=ET.M,
        p=FixedWindow(st=event.start.time(), et=event.end.time()),
        anchor_source="calendar",
    )


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
    ) -> tuple[Plan, list[CalendarEvent]]:
        """Fetch live calendar state and rebuild it as a plan.

        The durable source of truth. Called fresh by ``read``, ``apply`` and
        ``commit`` alike — never cached — so every call sees what is
        actually on the calendar right now.
        """
        events = await self.calendar.list_day(calendar_id, day, tz)
        events = sorted(events, key=lambda e: e.start)
        plan = Plan(
            date=day,
            tz=tz,
            blocks=[_event_to_block(event, index) for index, event in enumerate(events)],
        )
        return plan, events

    async def read(
        self, calendar_id: str, day: date_type, tz: str = _DEFAULT_TZ
    ) -> tuple[Plan, Snapshot]:
        """Fetch live calendar state as a plan plus a snapshot token."""
        plan, events = await self._plan_from_calendar(calendar_id, day, tz)
        snapshot = make_snapshot(calendar_id, day, tz, events)
        return plan, snapshot

    async def apply(self, snapshot: Snapshot, patch: Patch) -> ApplyResult:
        """Pure preview: applies ops against the live plan, validates,
        journals the attempt, writes nothing to the calendar."""
        plan, _events = await self._plan_from_calendar(
            snapshot.calendar_id, snapshot.day, snapshot.tz
        )

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
            rendered=render_plan(patched),
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
        plan, live = await self._plan_from_calendar(
            snapshot.calendar_id, snapshot.day, snapshot.tz
        )

        conflicts = drift(snapshot, live)
        if conflicts and expect != "force":
            raise ConflictError(conflicts)

        patched = apply_ops(plan, patch, mint_uid=self._mint_uid)
        before = [event.model_copy(deep=True) for event in live]
        await self._write(snapshot.calendar_id, patched, live)

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
        """
        row = await self.store.by_tx_id(tx_id)
        if (
            row is None
            or row.kind is not EntryKind.COMMIT
            or row.before_json is None
            or row.post_etags_json is None
        ):
            raise KeyError(f"unknown or non-undoable transaction {tx_id}")

        calendar_id, day = row.calendar_id, row.plan_date
        before_events = [
            CalendarEvent.model_validate(item) for item in json.loads(row.before_json)
        ]
        post_etags: dict[str, str] = json.loads(row.post_etags_json)

        # No tz survives on the journal row (see _DEFAULT_TZ above). Only
        # matters to a real adapter's day-boundary math; FakeCalendar
        # ignores it, and this method never constructs a Plan, so the
        # fallback is otherwise inert here.
        live = await self.calendar.list_day(calendar_id, day, _DEFAULT_TZ)
        live_etags = {event.event_id: event.etag for event in live}
        conflicts = sorted(
            {
                event_id
                for event_id in set(post_etags) | set(live_etags)
                if post_etags.get(event_id) != live_etags.get(event_id)
            }
        )
        if conflicts:
            raise ConflictError(conflicts)

        current_ids = {event.event_id for event in live}
        before_ids = {event.event_id for event in before_events}
        for event in before_events:
            if event.event_id in current_ids:
                await self.calendar.update(calendar_id, event)
            else:
                await self.calendar.create(calendar_id, event)
        for event_id in current_ids - before_ids:
            await self.calendar.delete(calendar_id, event_id)

        undo_tx = uuid.uuid4().hex
        entry = JournalEntry(
            calendar_id=calendar_id,
            plan_date=day,
            kind=EntryKind.UNDO,
            outcome=PatchOutcome.APPLIED,
            tx_id=undo_tx,
            undoes_tx=tx_id,
        )
        await self.store.append(entry)
        return CommitResult(tx_id=undo_tx, committed=True)

    async def _write(
        self, calendar_id: str, plan: Plan, existing: list[CalendarEvent]
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
        """
        resolved = {row.h: row for row in plan.resolve(check_overlap=False)}
        existing_by_id = {event.event_id: event for event in existing}
        event_ids = {event.uid: event.event_id for event in existing if event.uid}
        keep: set[str] = set()

        for block in plan.blocks:
            row = resolved[block.h]
            event_id = event_ids.get(block.uid) or f"tmbx{uuid.uuid4().hex[:20]}"
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
            )
            if event_id in existing_by_id:
                await self.calendar.update(calendar_id, event)
            else:
                await self.calendar.create(calendar_id, event)

        for event_id in set(existing_by_id) - keep:
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


__all__ = ["ApplyResult", "CommitResult", "ConflictError", "PlanService"]
