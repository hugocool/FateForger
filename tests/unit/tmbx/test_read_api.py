# tests/unit/tmbx/test_read_api.py
from __future__ import annotations

from datetime import date

import pytest

from tmbx.journal.disposition import Disposition
from tmbx.journal.models import ConstraintRef, EntryKind, JournalEntry, PatchOutcome
from tmbx.journal.read_api import JournalReader
from tmbx.journal.store import JournalStore, init_journal


@pytest.fixture
async def reader(tmp_path):
    store = JournalStore(await init_journal(tmp_path / "j.db"))

    attempt = JournalEntry(
        calendar_id="primary",
        plan_date=date(2026, 8, 17),
        instruction="move lunch",
        ops_json='{"ops":[]}',
        ops_schema_version=1,
        outcome=PatchOutcome.APPLIED,
        kind=EntryKind.ATTEMPT,
    )
    attempt.set_constraints(
        [ConstraintRef(uid="c1", uid_kind="minted", reason="graphflow_turn")]
    )
    await store.append(attempt)
    await store.append(
        JournalEntry(
            calendar_id="primary",
            plan_date=date(2026, 8, 17),
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id="tx1",
        )
    )
    return JournalReader(store)


async def test_records_carry_derived_disposition(reader):
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))
    by_kind = {r.kind: r for r in records}
    assert by_kind["attempt"].disposition == Disposition.ABANDONED
    assert by_kind["commit"].disposition == Disposition.ACCEPTED


async def test_records_carry_constraint_refs_with_provenance(reader):
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))
    attempt = next(r for r in records if r.kind == "attempt")
    assert attempt.constraints[0].uid == "c1"
    assert attempt.constraints[0].uid_kind == "minted"
    assert attempt.constraints[0].reason == "graphflow_turn"


async def test_date_range_is_inclusive(reader):
    assert await reader.records("primary", date(2026, 8, 18), date(2026, 8, 19)) == []
    assert len(await reader.records("primary", date(2026, 8, 16), date(2026, 8, 18))) == 2


async def test_commit_on_earlier_day_is_not_superseded_by_later_day_commit(tmp_path):
    """Pins per-day disposition derivation against a whole-range regression.

    ``derive_dispositions`` ranks commits by raw ``entry.id``, not by day.
    If ``records`` ever collected entries across the whole queried range
    and called ``derive_dispositions`` once over the concatenation, the
    day-1 commit's id would be smaller than the day-2 commit's id, so
    day-1's commit would flip from ACCEPTED to SUPERSEDED even though
    supersession is a within-day relation. This only passes under correct
    per-day derivation.
    """
    store = JournalStore(await init_journal(tmp_path / "j.db"))

    await store.append(
        JournalEntry(
            calendar_id="primary",
            plan_date=date(2026, 8, 17),
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id="tx-day1",
        )
    )
    await store.append(
        JournalEntry(
            calendar_id="primary",
            plan_date=date(2026, 8, 18),
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLIED,
            kind=EntryKind.COMMIT,
            tx_id="tx-day2",
        )
    )

    reader = JournalReader(store)
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 18))

    by_day = {r.plan_date: r for r in records}
    assert by_day[date(2026, 8, 17)].disposition == Disposition.ACCEPTED
    assert by_day[date(2026, 8, 18)].disposition == Disposition.ACCEPTED


async def test_error_is_carried_through_for_failed_rows(tmp_path):
    """A FAILED disposition without the error message doesn't teach anything."""
    store = JournalStore(await init_journal(tmp_path / "j.db"))

    await store.append(
        JournalEntry(
            calendar_id="primary",
            plan_date=date(2026, 8, 17),
            ops_json="{}",
            ops_schema_version=1,
            outcome=PatchOutcome.APPLY_FAILED,
            kind=EntryKind.ATTEMPT,
            error="conflict: event already exists",
        )
    )

    reader = JournalReader(store)
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))

    assert len(records) == 1
    assert records[0].disposition == Disposition.FAILED
    assert records[0].error == "conflict: event already exists"


async def test_unresolvable_constraint_ref_survives_round_trip(tmp_path):
    """The common case in real data: no minted uid.

    An audit found 1,620 of 1,662 constraint rows have no minted uid. The
    reader must carry that honest absence through rather than inventing an
    identity (the deleted ``derived_uid`` mechanism this replaces).
    """
    store = JournalStore(await init_journal(tmp_path / "j.db"))

    entry = JournalEntry(
        calendar_id="primary",
        plan_date=date(2026, 8, 17),
        instruction="move lunch",
        ops_json='{"ops":[]}',
        ops_schema_version=1,
        outcome=PatchOutcome.APPLIED,
        kind=EntryKind.ATTEMPT,
    )
    entry.set_constraints(
        [ConstraintRef(uid="", uid_kind="unresolvable", reason="no minted uid")]
    )
    await store.append(entry)

    reader = JournalReader(store)
    records = await reader.records("primary", date(2026, 8, 17), date(2026, 8, 17))

    assert len(records) == 1
    ref = records[0].constraints[0]
    assert ref.uid == ""
    assert ref.uid_kind == "unresolvable"
    assert ref.reason == "no minted uid"
