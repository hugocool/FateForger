# src/tmbx/journal/instrument.py
"""Decorators that journal the legacy patcher and submitter.

Instrumentation by decoration: both wrapped objects are constructed at a
single site each, so six call sites get covered by two changed lines —
``apply_patch`` (x2), ``apply_patch_legacy`` (x1), ``submit_plan`` (x2) and
``undo_transaction`` (x1) in ``agent.py``.

``apply_patch_legacy`` needs its own explicit journaling method rather than
relying on ``__getattr__`` passthrough: ``TimeboxPatcher.apply_patch_legacy``
converts ``Timebox`` to ``TBPlan``, then calls ``self.apply_patch(...)`` on
itself — the *inner*, unwrapped patcher — and converts back. If this wrapper
only exposed ``apply_patch`` and let ``apply_patch_legacy`` fall through
``__getattr__``, the call would resolve straight to the unwrapped inner
object and produce no journal row at all, even though the call itself
succeeds. Journaling has to wrap the legacy entrypoint directly.

Journal writes never break planning. Every write is guarded, and so is
every other piece of context-gathering that runs before or after the
wrapped call (constraint extraction, calendar-id resolution) — none of it
is allowed to stop the underlying patcher or submitter from doing its job.

``JournalingPatcher``'s ``calendar_id_fn`` defaults to a function that
returns ``UNRESOLVED_CALENDAR_ID`` rather than a guessed calendar. Resolving
to a plausible-looking default (e.g. ``"primary"``, or the installation-wide
``CalendarPreferences`` default) would replace a visibly wrong value with a
value that looks resolved but isn't — a harder bug to find later, since
nothing in the agent path actually reads which calendar the session
concerns. Until a caller supplies a real resolver bound to the active
session, every ATTEMPT row is honestly marked unresolved instead of
attributed to a calendar it may not belong to. Passing a resolver that
returns the session's real calendar id is the caller's responsibility.

Legacy transaction status vocabulary differs by direction, and this module
maps each direction separately rather than assuming one success string:
``submit_plan``'s returned transaction reports ``status="committed"`` on
full success and ``"partial"``/``"partial_halted"`` otherwise
(sync_engine.py:452-458); ``undo_transaction``'s returned transaction
reports ``status="undone"`` on full success and ``"undo_partial"``
otherwise (sync_engine.py:520-525) — undo re-executes compensating ops and
only remaps to ``"undone"`` once *that* inner execution reports
``"committed"``. Getting this wrong in either direction would silently
defeat the Task 3 fix that keys ``derive_dispositions``' ``undone_tx`` set
off ``outcome is PatchOutcome.APPLIED`` (disposition.py:36-40): a
partially-failed commit or undo would otherwise be journaled as a clean
success.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_type
from typing import Any, Callable, Iterable

from .constraint_refs import constraint_refs
from .models import ConstraintRef, EntryKind, JournalEntry, PatchOutcome

logger = logging.getLogger(__name__)

UNRESOLVED_CALENDAR_ID = "unresolved:no-calendar-id-resolver"
"""Sentinel recorded in place of a guessed calendar id.

``JournalEntry.calendar_id`` is a non-null indexed column, so there is no
NULL to fall back to. This string is unmistakably not a real Google Calendar
id (never "primary", never an email address), sorts and indexes like any
other value, and is stable so ``JournalStore.by_day`` can group these rows
together deliberately — querying by this constant is how a caller finds
"all rows where we don't know the calendar" rather than silently reading
them as if they were on "primary".
"""


def _plan_date(obj: Any) -> date_type:
    """Best-effort plan date, falling back to today."""
    value = getattr(obj, "date", None)
    return value if isinstance(value, date_type) else date_type.today()


def _ops_json(patch: Any) -> str:
    """Serialise a patch to JSON without assuming its type."""
    dumper = getattr(patch, "model_dump_json", None)
    if callable(dumper):
        try:
            return str(dumper())
        except Exception:
            logger.warning(
                "patch serialisation failed; using empty ops_json", exc_info=True
            )
    return "{}"


def _safe_constraint_refs(constraints: Iterable[Any]) -> list[ConstraintRef]:
    """Extract constraint refs, never letting the extraction break planning.

    In production ``constraints`` is ``list[Constraint]``, a SQLModel
    ``table=True`` ORM object. Attribute access on a detached or expired
    instance raises ``DetachedInstanceError``, which duck-typed ``getattr``
    calls inside ``constraint_refs`` do not protect against. Losing
    constraint context in the journal is acceptable; failing to plan is not.
    """
    try:
        return constraint_refs(constraints)
    except Exception:
        logger.warning(
            "constraint_refs failed; continuing without constraint context",
            exc_info=True,
        )
        return []


def _status_outcome(tx: Any, success_status: str) -> PatchOutcome:
    """Map a transaction's ``status`` onto a ``PatchOutcome``.

    Only the exact ``success_status`` counts as success. The legacy sync
    engine can return a partial-failure status without raising, so status
    must be inspected rather than assumed. A missing ``status`` attribute
    (a fake, or a future transaction type) degrades to failure — the safe
    default, since downstream disposition derivation only trusts APPLIED
    outcomes to mark undo targets as undone.
    """
    return (
        PatchOutcome.APPLIED
        if getattr(tx, "status", None) == success_status
        else PatchOutcome.APPLY_FAILED
    )


class JournalingPatcher:
    """Wrap a patcher, recording one attempt row per ``apply_patch`` call."""

    def __init__(
        self,
        inner: Any,
        store: Any,
        calendar_id_fn: Callable[[], str] = lambda: UNRESOLVED_CALENDAR_ID,
    ) -> None:
        self._inner = inner
        self._store = store
        self._calendar_id_fn = calendar_id_fn

    def __getattr__(self, name: str) -> Any:
        """Pass through everything not explicitly wrapped."""
        return getattr(self._inner, name)

    async def _write(self, entry: JournalEntry) -> None:
        try:
            await self._store.append(entry)
        except Exception:
            logger.warning("journal write failed; continuing", exc_info=True)

    def _resolve_calendar_id(self) -> str:
        try:
            return self._calendar_id_fn()
        except Exception:
            logger.warning(
                "calendar_id_fn raised; recording calendar_id as unresolved "
                "rather than guessing",
                exc_info=True,
            )
            return UNRESOLVED_CALENDAR_ID

    async def apply_patch(self, **kwargs: Any) -> Any:
        current = kwargs.get("current")
        constraints: Iterable[Any] = kwargs.get("constraints") or []
        instruction = kwargs.get("user_message")

        base = dict(
            calendar_id=self._resolve_calendar_id(),
            plan_date=_plan_date(current),
            instruction=instruction,
            kind=EntryKind.ATTEMPT,
        )
        refs = _safe_constraint_refs(constraints)

        try:
            result = await self._inner.apply_patch(**kwargs)
        except Exception as exc:
            entry = JournalEntry(
                **base, outcome=PatchOutcome.APPLY_FAILED, error=str(exc)[:2000]
            )
            entry.set_constraints(refs)
            await self._write(entry)
            raise

        _, patch = result
        entry = JournalEntry(
            **base, outcome=PatchOutcome.APPLIED, ops_json=_ops_json(patch)
        )
        entry.set_constraints(refs)
        await self._write(entry)
        return result

    async def apply_patch_legacy(self, **kwargs: Any) -> Any:
        """Journal the ``Timebox``-in/``Timebox``-out legacy patch path.

        Must call ``self._inner.apply_patch_legacy`` directly rather than
        delegating to this wrapper's own ``apply_patch``: the inner
        ``apply_patch_legacy`` already does its own TBPlan conversion and
        calls ``self.apply_patch(...)`` on *itself* (the unwrapped inner
        object), so routing through the wrapper here would double-convert
        and still bypass journaling on the inner call. See the module
        docstring.

        The legacy interface returns a ``Timebox`` directly rather than a
        ``(TBPlan, TBPatch)`` tuple, so there is no patch object to
        serialise — ``ops_json`` is recorded as ``"{}"``.
        """
        current = kwargs.get("current")
        constraints: Iterable[Any] = kwargs.get("constraints") or []
        instruction = kwargs.get("user_message")

        base = dict(
            calendar_id=self._resolve_calendar_id(),
            plan_date=_plan_date(current),
            instruction=instruction,
            kind=EntryKind.ATTEMPT,
        )
        refs = _safe_constraint_refs(constraints)

        try:
            result = await self._inner.apply_patch_legacy(**kwargs)
        except Exception as exc:
            entry = JournalEntry(
                **base, outcome=PatchOutcome.APPLY_FAILED, error=str(exc)[:2000]
            )
            entry.set_constraints(refs)
            await self._write(entry)
            raise

        entry = JournalEntry(**base, outcome=PatchOutcome.APPLIED, ops_json="{}")
        entry.set_constraints(refs)
        await self._write(entry)
        return result


class JournalingSubmitter:
    """Wrap a submitter, recording commit and undo rows.

    Stamps ``tmbx_tx_id``, ``tmbx_calendar_id`` and ``tmbx_plan_date`` onto
    each returned transaction so a later undo can reference the commit it
    reverses and land its journal row on the right calendar-day.
    """

    def __init__(self, inner: Any, store: Any) -> None:
        self._inner = inner
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def _write(self, entry: JournalEntry) -> None:
        try:
            await self._store.append(entry)
        except Exception:
            logger.warning("journal write failed; continuing", exc_info=True)

    async def submit_plan(self, desired: Any, **kwargs: Any) -> Any:
        calendar_id = kwargs.get("calendar_id", "primary")
        plan_date = _plan_date(desired)

        try:
            tx = await self._inner.submit_plan(desired, **kwargs)
        except Exception as exc:
            entry = JournalEntry(
                calendar_id=calendar_id,
                plan_date=plan_date,
                kind=EntryKind.COMMIT,
                outcome=PatchOutcome.APPLY_FAILED,
                error=str(exc)[:2000],
            )
            await self._write(entry)
            raise

        tx_id = uuid.uuid4().hex
        try:
            setattr(tx, "tmbx_tx_id", tx_id)
            setattr(tx, "tmbx_calendar_id", calendar_id)
            setattr(tx, "tmbx_plan_date", plan_date)
        except Exception:  # pragma: no cover - defensive
            pass

        entry = JournalEntry(
            calendar_id=calendar_id,
            plan_date=plan_date,
            kind=EntryKind.COMMIT,
            outcome=_status_outcome(tx, "committed"),
            tx_id=tx_id,
        )
        await self._write(entry)
        return tx

    async def undo_transaction(self, tx: Any) -> Any:
        calendar_id = getattr(tx, "tmbx_calendar_id", "primary")
        plan_date = getattr(tx, "tmbx_plan_date", date_type.today())
        undoes_tx = getattr(tx, "tmbx_tx_id", None)

        try:
            undo_tx = await self._inner.undo_transaction(tx)
        except Exception as exc:
            entry = JournalEntry(
                calendar_id=calendar_id,
                plan_date=plan_date,
                kind=EntryKind.UNDO,
                outcome=PatchOutcome.APPLY_FAILED,
                error=str(exc)[:2000],
                undoes_tx=undoes_tx,
            )
            await self._write(entry)
            raise

        if undo_tx is None:
            return None

        entry = JournalEntry(
            calendar_id=calendar_id,
            plan_date=plan_date,
            kind=EntryKind.UNDO,
            outcome=_status_outcome(undo_tx, "undone"),
            tx_id=uuid.uuid4().hex,
            undoes_tx=undoes_tx,
        )
        await self._write(entry)
        return undo_tx

    async def undo_last(self) -> Any:
        tx = getattr(self._inner, "last_transaction", None)
        if tx is None:
            return await self._inner.undo_last()
        return await self.undo_transaction(tx)


__all__ = ["UNRESOLVED_CALENDAR_ID", "JournalingPatcher", "JournalingSubmitter"]
