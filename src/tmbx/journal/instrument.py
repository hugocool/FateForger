# src/tmbx/journal/instrument.py
"""Decorators that journal the legacy patcher and submitter.

Instrumentation by decoration: both wrapped objects are constructed at a
single site each, so five call sites get covered by two changed lines.

Journal writes never break planning. Every write is guarded.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date as date_type
from typing import Any, Iterable

from .constraint_refs import constraint_refs
from .models import EntryKind, JournalEntry, PatchOutcome

logger = logging.getLogger(__name__)


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
        except Exception:  # pragma: no cover - defensive
            pass
    return "{}"


class JournalingPatcher:
    """Wrap a patcher, recording one attempt row per ``apply_patch`` call."""

    def __init__(self, inner: Any, store: Any, *, calendar_id: str = "primary") -> None:
        self._inner = inner
        self._store = store
        self._calendar_id = calendar_id

    def __getattr__(self, name: str) -> Any:
        """Pass through everything not explicitly wrapped."""
        return getattr(self._inner, name)

    async def _write(self, entry: JournalEntry) -> None:
        try:
            await self._store.append(entry)
        except Exception:
            logger.warning("journal write failed; continuing", exc_info=True)

    async def apply_patch(self, **kwargs: Any) -> Any:
        current = kwargs.get("current")
        constraints: Iterable[Any] = kwargs.get("constraints") or []
        instruction = kwargs.get("user_message")

        base = dict(
            calendar_id=self._calendar_id,
            plan_date=_plan_date(current),
            instruction=instruction,
            kind=EntryKind.ATTEMPT,
        )
        refs = constraint_refs(constraints)

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


class JournalingSubmitter:
    """Wrap a submitter, recording commit and undo rows.

    Stamps ``tmbx_tx_id`` onto each returned transaction so a later undo can
    reference the commit it reverses.
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
        tx = await self._inner.submit_plan(desired, **kwargs)
        tx_id = uuid.uuid4().hex
        try:
            setattr(tx, "tmbx_tx_id", tx_id)
            setattr(tx, "tmbx_calendar_id", kwargs.get("calendar_id", "primary"))
            setattr(tx, "tmbx_plan_date", _plan_date(desired))
        except Exception:  # pragma: no cover - defensive
            pass

        entry = JournalEntry(
            calendar_id=kwargs.get("calendar_id", "primary"),
            plan_date=_plan_date(desired),
            kind=EntryKind.COMMIT,
            outcome=PatchOutcome.APPLIED,
            tx_id=tx_id,
        )
        await self._write(entry)
        return tx

    async def undo_transaction(self, tx: Any) -> Any:
        undo_tx = await self._inner.undo_transaction(tx)
        if undo_tx is None:
            return None

        entry = JournalEntry(
            calendar_id=getattr(tx, "tmbx_calendar_id", "primary"),
            plan_date=getattr(tx, "tmbx_plan_date", date_type.today()),
            kind=EntryKind.UNDO,
            outcome=PatchOutcome.APPLIED,
            tx_id=uuid.uuid4().hex,
            undoes_tx=getattr(tx, "tmbx_tx_id", None),
        )
        await self._write(entry)
        return undo_tx

    async def undo_last(self) -> Any:
        tx = getattr(self._inner, "last_transaction", None)
        if tx is None:
            return await self._inner.undo_last()
        return await self.undo_transaction(tx)


__all__ = ["JournalingPatcher", "JournalingSubmitter"]
