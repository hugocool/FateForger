# tests/unit/tmbx/test_service.py
"""Plan service — read, apply, commit, undo.

Adapted from the task-14 brief's reference test to the real signatures that
landed after the brief was written:

* ``FakeCalendar.list_day``/``mutate`` are calendar_id-scoped (Task 13 fix,
  commit a02f866) — the brief's ``calendar.mutate("e1")`` and
  ``calendar.list_day("primary", DAY)`` predate that and would raise
  TypeError/KeyError against the real fake.
"""

from __future__ import annotations

import itertools
import logging
from datetime import date, datetime, time, timedelta

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.core.models import ET, AfterPrev, BeforeNext, FixedStart, FixedWindow
from tmbx.core.ops import AddBlock, Patch, RemoveBlock, UpdateBlock
from tmbx.journal.models import PatchOutcome
from tmbx.journal.store import JournalStore, init_journal
from tmbx.service import (
    ConflictError,
    ForeignBlockError,
    PlanService,
    _mint_event_id,
    is_valid_base32hex_event_id,
)

DAY = date(2026, 8, 17)
TZ = "Europe/Amsterdam"


def _event(eid, h, start_h, end_h, uid=None, block_type="M", timing_mode="fw"):
    """``block_type``/``timing_mode`` default to the plain ``M``/``fw`` a
    real adapter would have written for an ordinary fixed-window meeting
    block — i.e. these fixtures already represent a fully migrated,
    already-round-tripped event, not a partial write. Tests that
    specifically exercise the missing-properties fallback (an older
    schema version) pass ``block_type=None``/``timing_mode=None``
    explicitly instead of relying on this default.
    """
    return CalendarEvent(
        event_id=eid,
        summary=f"Block {h}",
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=uid or f"u-{eid}",
        handle=h,
        block_type=block_type,
        timing_mode=timing_mode,
    )


def _foreign_event(eid, start_h, end_h, summary="Team Sync"):
    """A calendar event tmbx did not create: no uid, no handle. Exactly what
    a real meeting or invite looks like — the read-only-context fixture for
    the foreign-block tests below."""
    return CalendarEvent(
        event_id=eid,
        summary=summary,
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
    )


@pytest.fixture
async def service(tmp_path):
    calendar = FakeCalendar({"primary": [_event("e1", "PR1", 9, 10), _event("e2", "DW1", 10, 12)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    svc = PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")
    return svc


@pytest.fixture
async def service_with_foreign(tmp_path):
    """Two tmbx-owned blocks plus one foreign event — a meeting tmbx did not
    create and must never write to, modeled on tomorrow's real session
    against a personal calendar with a pre-existing 14:00 meeting."""
    calendar = FakeCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", "DW1", 10, 12),
                _foreign_event("e3", 13, 14),
            ]
        }
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    return PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")


async def test_read_returns_plan_and_snapshot(service):
    plan, snapshot = await service.read("primary", DAY)
    assert [b.h for b in plan.blocks] == ["PR1", "DW1"]
    assert snapshot.token


async def test_read_rendered_marks_no_foreign_blocks_when_none_exist(service):
    result = await service.read_rendered("primary", DAY)
    assert "PR1,tmbx," in result.rendered
    assert "DW1,tmbx," in result.rendered
    assert result.snapshot.token
    assert result.blocks == 2


async def test_read_rendered_marks_foreign_blocks(service_with_foreign):
    result = await service_with_foreign.read_rendered("primary", DAY)
    assert "EVT3,foreign," in result.rendered
    assert "PR1,tmbx," in result.rendered
    assert "DW1,tmbx," in result.rendered


async def test_apply_is_pure_and_writes_nothing_to_the_calendar(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.plan.by_handle("DW1").n == "Renamed"
    live = await service.calendar.list_day("primary", DAY, TZ)
    assert all(e.summary != "Renamed" for e in live)


async def test_apply_journals_an_attempt(service):
    _, snapshot = await service.read("primary", DAY)
    await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    rows = await service.store.by_day("primary", DAY)
    assert len(rows) == 1


async def test_apply_journals_apply_failed_and_reraises_for_an_invalid_patch(service):
    """The APPLY_FAILED branch: apply_ops itself rejects the patch
    (unknown handle), before any resolve()/render happens."""
    _, snapshot = await service.read("primary", DAY)
    with pytest.raises(ValueError):
        await service.apply(snapshot, Patch(ops=[UpdateBlock(h="NOPE", n="x")]))
    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].outcome == PatchOutcome.APPLY_FAILED
    assert rows[-1].error is not None


async def test_apply_records_a_violation_for_an_overlap_without_raising(service):
    """The VALIDATION_FAILED / violations branch: the patch is structurally
    valid (validate_patch doesn't check overlap) but produces one, caught
    by patched.resolve() rather than apply_ops. apply() must not raise —
    it reports the violation and still returns a rendered preview."""
    _, snapshot = await service.read("primary", DAY)
    # DW1 currently starts at 10:00, right after PR1 (09:00-10:00). Retime
    # it to start before PR1 ends.
    patch = Patch(ops=[UpdateBlock(h="DW1", p=FixedWindow(st=time(9, 30), et=time(11, 0)))])
    result = await service.apply(snapshot, patch)
    assert result.violations
    assert "Overlap" in result.violations[0]
    assert result.rendered  # render still works despite the overlap
    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].outcome == PatchOutcome.VALIDATION_FAILED


async def test_commit_writes_to_the_calendar_and_returns_a_tx_id(service):
    _, snapshot = await service.read("primary", DAY)
    result = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert result.committed and result.tx_id
    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Renamed" for e in live)


async def test_commit_does_not_rewrite_blocks_the_patch_did_not_touch(service):
    """``_write`` resolves and re-``update``s the *whole* plan on every
    commit; without a no-op guard that means every commit rewrites every
    untouched block too. Against ``FakeCalendar`` the only observable
    symptom is a bumped etag -- harmless here, but against a real provider
    it's an etag bump and a change notification for every event on the
    day, on every commit. PR1 (untouched by this patch) must come out with
    the exact same etag it went in with.
    """
    live_before = {e.event_id: e.etag for e in await service.calendar.list_day("primary", DAY, TZ)}

    _, snapshot = await service.read("primary", DAY)
    await service.commit(
        snapshot,
        Patch(
            ops=[
                AddBlock(
                    after="DW1",
                    h="BU1",
                    n="Buffer",
                    t=ET.BU,
                    p=AfterPrev(dur=timedelta(minutes=10)),
                )
            ]
        ),
    )

    live_after = {e.event_id: e.etag for e in await service.calendar.list_day("primary", DAY, TZ)}
    assert live_after["e1"] == live_before["e1"]  # PR1: untouched, etag unchanged
    assert live_after["e2"] == live_before["e2"]  # DW1: untouched, etag unchanged


async def test_commit_refuses_when_the_calendar_drifted(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("primary", "e1")
    with pytest.raises(ConflictError) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert "e1" in excinfo.value.conflicts


async def test_commit_forces_when_asked(service):
    _, snapshot = await service.read("primary", DAY)
    service.calendar.mutate("primary", "e1")
    result = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]), expect="force"
    )
    assert result.committed


async def test_undo_restores_and_is_itself_journaled(service):
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    await service.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Block DW1" for e in live)

    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].undoes_tx == committed.tx_id


async def test_undo_refuses_to_clobber_a_newer_edit(service):
    """The failure the legacy undo has: restoring over an edit made since."""
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    service.calendar.mutate("primary", "e2")
    with pytest.raises(ConflictError):
        await service.undo(committed.tx_id)


async def test_undo_survives_a_restart(service):
    """Undo state lives in the journal, not process memory — #112.

    A fresh service sharing only the calendar and the store must still undo.
    """
    _, snapshot = await service.read("primary", DAY)
    committed = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))

    restarted = PlanService(service.calendar, service.store, mint_uid=lambda: "u-x")
    await restarted.undo(committed.tx_id)

    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Block DW1" for e in live)


async def test_apply_and_commit_work_from_a_fresh_service_sharing_only_calendar_and_store(
    service,
):
    """Restart-safety isn't special to undo: apply()/commit() re-derive the
    plan from the calendar every call, so a second PlanService sharing only
    the calendar and the store can act on a snapshot handed to it."""
    _, snapshot = await service.read("primary", DAY)
    fresh = PlanService(service.calendar, service.store, mint_uid=lambda: "u-fresh")

    preview = await fresh.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert preview.plan.by_handle("DW1").n == "Renamed"

    committed = await fresh.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    assert committed.committed
    live = await service.calendar.list_day("primary", DAY, TZ)
    assert any(e.summary == "Renamed" for e in live)


# --- Foreign events: calendar entries tmbx did not create ------------------
#
# A foreign event (no tmbx uid — a meeting, an invite, anything not created
# by this system) belongs in the plan as read-only context: the model needs
# to see it and the chain must respect it, but tmbx must never create,
# update, or delete it. Tomorrow's real session runs against a personal
# calendar carrying exactly this: a pre-existing 14:00 meeting with no tmbx
# uid.


async def test_read_includes_foreign_events_as_read_only_context(service_with_foreign):
    plan, _snapshot = await service_with_foreign.read("primary", DAY)
    assert [b.h for b in plan.blocks] == ["PR1", "DW1", "EVT3"]
    assert plan.by_handle("EVT3").n == "Team Sync"


async def test_apply_refuses_a_patch_touching_a_foreign_block(service_with_foreign):
    svc = service_with_foreign
    plan, snapshot = await svc.read("primary", DAY)
    foreign_handle = plan.blocks[-1].h

    with pytest.raises(ForeignBlockError) as excinfo:
        await svc.apply(snapshot, Patch(ops=[UpdateBlock(h=foreign_handle, n="Hacked")]))
    assert foreign_handle in excinfo.value.handles

    rows = await svc.store.by_day("primary", DAY)
    assert rows[-1].outcome == PatchOutcome.APPLY_FAILED


async def test_commit_refuses_a_patch_touching_a_foreign_block(service_with_foreign):
    svc = service_with_foreign
    plan, snapshot = await svc.read("primary", DAY)
    foreign_handle = plan.blocks[-1].h

    with pytest.raises(ForeignBlockError):
        await svc.commit(snapshot, Patch(ops=[RemoveBlock(h=foreign_handle)]))

    live = await svc.calendar.list_day("primary", DAY, TZ)
    assert any(e.event_id == "e3" for e in live)


async def test_apply_may_anchor_a_new_block_after_a_foreign_block(service_with_foreign):
    """Referencing a foreign block as a position anchor is fine — it
    doesn't modify the foreign block itself."""
    svc = service_with_foreign
    plan, snapshot = await svc.read("primary", DAY)
    foreign_handle = plan.blocks[-1].h

    patch = Patch(
        ops=[
            AddBlock(
                after=foreign_handle, h="BU1", n="Buffer", t=ET.BU, p=AfterPrev(dur=timedelta(minutes=10))
            )
        ]
    )
    result = await svc.apply(snapshot, patch)
    assert result.plan.by_handle("BU1") is not None


async def test_commit_renaming_an_owned_block_leaves_a_foreign_event_untouched(
    service_with_foreign,
):
    svc = service_with_foreign
    _, snapshot = await svc.read("primary", DAY)
    await svc.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))

    live = await svc.calendar.list_day("primary", DAY, TZ)
    assert len(live) == 3
    foreign = next(e for e in live if e.event_id == "e3")
    assert foreign.summary == "Team Sync"
    assert foreign.etag == "v1"  # never written


async def test_commit_removing_an_owned_block_leaves_a_foreign_event_untouched(
    service_with_foreign,
):
    svc = service_with_foreign
    _, snapshot = await svc.read("primary", DAY)
    await svc.commit(snapshot, Patch(ops=[RemoveBlock(h="DW1")]))

    live = await svc.calendar.list_day("primary", DAY, TZ)
    assert {e.event_id for e in live} == {"e1", "e3"}
    foreign = next(e for e in live if e.event_id == "e3")
    assert foreign.etag == "v1"  # never written, never a deletion candidate


async def test_mixed_plan_round_trips_without_duplicating_the_foreign_event(
    service_with_foreign,
):
    svc = service_with_foreign
    _, snapshot = await svc.read("primary", DAY)
    await svc.commit(snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Renamed PR1")]))

    live = await svc.calendar.list_day("primary", DAY, TZ)
    assert len(live) == 3
    assert {e.event_id for e in live} == {"e1", "e2", "e3"}
    foreign = next(e for e in live if e.event_id == "e3")
    assert foreign.etag == "v1"


async def test_foreign_event_survives_an_undo(service_with_foreign):
    """The ownership rule applies to undo's restore path too — restoring an
    owned block's before-state must not touch a foreign event that was
    sitting in the same before/after snapshot."""
    svc = service_with_foreign
    _, snapshot = await svc.read("primary", DAY)
    committed = await svc.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Renamed")]))
    await svc.undo(committed.tx_id)

    live = await svc.calendar.list_day("primary", DAY, TZ)
    assert len(live) == 3
    foreign = next(e for e in live if e.event_id == "e3")
    assert foreign.etag == "v1"  # never written by commit or undo


# ---------------------------------------------------------------------------
# Minted event ids must be valid Google Calendar custom ids: base32hex
# (0-9, a-v only; no w/x/y/z), 5-1024 characters. The original minter used
# a literal "tmbx" prefix — 'x' is outside that alphabet — so every
# create-event against a real calendar would have been rejected.
# ---------------------------------------------------------------------------


def test_is_valid_base32hex_event_id_rejects_the_old_tmbx_prefixed_shape():
    """The exact bug this fixes: the old minter's output contained 'x'."""
    old_style_id = "tmbx" + "a" * 20
    assert "x" in old_style_id
    assert is_valid_base32hex_event_id(old_style_id) is False


def test_is_valid_base32hex_event_id_rejects_each_disallowed_letter():
    for letter in "wxyz":
        assert is_valid_base32hex_event_id(f"tmb0{letter}aaaa") is False


def test_is_valid_base32hex_event_id_accepts_the_new_prefix_and_digits_and_a_to_v():
    assert is_valid_base32hex_event_id("tmb0" + "0123456789abcdefghijklmnopqrstuv") is True


def test_is_valid_base32hex_event_id_enforces_the_minimum_length():
    assert is_valid_base32hex_event_id("abcd") is False  # 4 chars, too short
    assert is_valid_base32hex_event_id("abcde") is True  # 5 chars, the floor


def test_is_valid_base32hex_event_id_enforces_the_maximum_length():
    assert is_valid_base32hex_event_id("a" * 1024) is True  # the ceiling
    assert is_valid_base32hex_event_id("a" * 1025) is False


def test_mint_event_id_is_always_valid_base32hex():
    """Mint a few hundred and check every single one — the alphabet is
    drawn from directly (not assumed to coincide with ``uuid4().hex``), so
    this is the test that would actually have failed against the old
    "tmbx"-prefixed minter."""
    minted = [_mint_event_id() for _ in range(500)]
    assert all(is_valid_base32hex_event_id(event_id) for event_id in minted)
    assert all(event_id.startswith("tmb0") for event_id in minted)
    # No forbidden letter ever appears, in the prefix or the random tail.
    assert not any(letter in event_id for event_id in minted for letter in "wxyz")


def test_mint_event_id_is_random_not_content_derived():
    """Two calls must not collide — identity stays opaque and random, never
    derived from a block's name/date/start the way the legacy engine's
    ``sync_engine.base32hex_id`` did (which breaks on rename)."""
    assert _mint_event_id() != _mint_event_id()


async def test_commit_mints_a_valid_base32hex_event_id_for_a_new_block(service):
    """End-to-end: a patch that adds a brand-new block goes through
    ``PlanService._write``'s minting path, and the id that actually lands
    on the calendar is a legal Google custom event id."""
    _, snapshot = await service.read("primary", DAY)
    await service.commit(
        snapshot,
        Patch(
            ops=[
                AddBlock(
                    after="DW1",
                    h="BU1",
                    n="Buffer",
                    t=ET.BU,
                    p=AfterPrev(dur=timedelta(minutes=10)),
                )
            ]
        ),
    )

    live = await service.calendar.list_day("primary", DAY, TZ)
    new_event = next(e for e in live if e.handle == "BU1")
    assert is_valid_base32hex_event_id(new_event.event_id)


# ---------------------------------------------------------------------------
# Block type and timing mode must survive commit -> re-read. Before this
# fix every block read back as ET.M / FixedWindow regardless of what it
# actually was — the chain ossified in storage even though nothing about
# the *model's* intent had changed, and overspecified() had no way to see
# it because by the time it read the plan back, everything genuinely was
# pinned.
# ---------------------------------------------------------------------------


@pytest.fixture
async def empty_service(tmp_path):
    """A fresh, empty calendar -- these tests build their own plan from
    scratch rather than relying on the ``service``/``e1``/``e2`` fixture,
    since they need full control over every block's type and mode."""
    calendar = FakeCalendar({"primary": []})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    return PlanService(calendar, store, mint_uid=lambda: f"u-new-{next(counter)}")


async def test_commit_and_reread_round_trips_every_timing_mode(empty_service):
    """One commit per op, each anchored after the previous: a patch is a
    set resolved against the *pre-patch* plan (``ops.py``'s own module
    docstring — "no op may reference a block created by another op in the
    same patch"), so building a five-block chain in a single patch isn't
    legal; each block has to land for real before the next can anchor
    after it."""
    svc = empty_service

    async def _add(after, h, n, t, p, anchor_source=None):
        _, snapshot = await svc.read("primary", DAY)
        result = await svc.commit(
            snapshot,
            Patch(
                ops=[
                    AddBlock(
                        after=after, h=h, n=n, t=t, p=p, anchor_source=anchor_source
                    )
                ]
            ),
        )
        assert result.committed

    await _add(None, "ANC1", "Anchor", ET.M, FixedWindow(st=time(8, 0), et=time(9, 0)), "user")
    await _add("ANC1", "APX1", "After Prev", ET.DW, AfterPrev(dur=timedelta(minutes=30)))
    await _add(
        "APX1",
        "FSX1",
        "Fixed Start",
        ET.PR,
        FixedStart(st=time(10, 0), dur=timedelta(minutes=20)),
        "user",
    )
    # END1 lands right after FSX1 *before* BNX1 exists — bn resolves
    # backward from its follower, so that follower has to already be on
    # the plan at commit time. BNX1 is then inserted between FSX1 and
    # END1 (still "after FSX1"), becoming END1's new immediate
    # predecessor and giving bn something real to resolve against.
    await _add(
        "FSX1",
        "END1",
        "End Anchor",
        ET.R,
        FixedStart(st=time(11, 0), dur=timedelta(minutes=10)),
        "user",
    )
    await _add("FSX1", "BNX1", "Before Next", ET.H, BeforeNext(dur=timedelta(minutes=15)))

    plan, _snapshot = await svc.read("primary", DAY)
    by_handle = {b.h: b for b in plan.blocks}

    assert (by_handle["ANC1"].t, by_handle["ANC1"].p.a) == (ET.M, "fw")
    assert (by_handle["APX1"].t, by_handle["APX1"].p.a) == (ET.DW, "ap")
    assert (by_handle["FSX1"].t, by_handle["FSX1"].p.a) == (ET.PR, "fs")
    assert (by_handle["BNX1"].t, by_handle["BNX1"].p.a) == (ET.H, "bn")
    assert (by_handle["END1"].t, by_handle["END1"].p.a) == (ET.R, "fs")

    # The chain must still actually flex: APX1's duration is a property
    # of its stored ap mode, not a value frozen at commit time.
    assert by_handle["APX1"].p.dur == timedelta(minutes=30)
    assert by_handle["BNX1"].p.dur == timedelta(minutes=15)


@pytest.mark.parametrize("block_type", list(ET))
async def test_every_event_type_round_trips(empty_service, block_type):
    svc = empty_service
    _, snapshot = await svc.read("primary", DAY)
    # fs satisfies every type's timing constraint, BG included (BG
    # requires fs or fw), and alone in an otherwise-empty plan it also
    # satisfies the chain-anchor requirement (or, for BG, the chain is
    # empty and the requirement doesn't apply at all) -- so one op shape
    # covers all nine types uniformly.
    patch = Patch(
        ops=[
            AddBlock(
                after=None,
                h="TYP1",
                n="Typed block",
                t=block_type,
                p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                anchor_source="user",
            )
        ]
    )
    result = await svc.commit(snapshot, patch)
    assert result.committed

    plan, _snapshot = await svc.read("primary", DAY)
    assert plan.by_handle("TYP1").t == block_type


async def test_read_a_foreign_event_is_unaffected_by_type_mode_reconstruction(
    tmp_path, caplog
):
    """A foreign event never carries tmbx.type/tmbx.mode at all -- that is
    normal and permanent, not a degraded case, so it must default quietly
    (ET.M/fw, no warning) and, above all, must never be mistaken for an
    owned event just because this system now also round-trips type and
    mode. Regressing this cost a Critical once already."""
    calendar = FakeCalendar({"primary": [_foreign_event("e3", 13, 14)]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    svc = PlanService(calendar, store)

    with caplog.at_level(logging.WARNING):
        plan, _snapshot = await svc.read("primary", DAY)

    block = plan.blocks[0]
    assert block.t == ET.M
    assert block.p.a == "fw"
    assert block.p.st == time(13, 0)
    assert block.p.et == time(14, 0)
    assert not any(
        "block_type/timing_mode" in record.getMessage() for record in caplog.records
    )


async def test_read_an_owned_event_with_missing_properties_degrades_and_logs(
    tmp_path, caplog
):
    """An owned event (real tmbx uid) with no block_type/timing_mode at
    all: a partial write, or an event written before this round-trip
    existed. Falls back to ET.M/fw off the event's own observed times --
    deliberately, and logged, never a silently guessed mode."""
    legacy_event = CalendarEvent(
        event_id="e-legacy",
        summary="Old block",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
        etag="v1",
        uid="u-legacy",
        handle="LEG1",
        # block_type/timing_mode intentionally left unset.
    )
    calendar = FakeCalendar({"primary": [legacy_event]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    svc = PlanService(calendar, store)

    with caplog.at_level(logging.WARNING):
        plan, _snapshot = await svc.read("primary", DAY)

    block = plan.by_handle("LEG1")
    assert block.t == ET.M
    assert block.p.a == "fw"
    assert block.p.st == time(9, 0)
    assert block.p.et == time(10, 0)
    assert any(
        "e-legacy" in record.getMessage()
        and "block_type/timing_mode" in record.getMessage()
        for record in caplog.records
    )


async def test_read_an_owned_event_with_jointly_inconsistent_properties_degrades_and_logs(
    tmp_path, caplog
):
    """block_type="BG" and timing_mode="ap" each parse individually, but
    ``Block`` itself rejects that combination (BG requires fs/fw) -- the
    missing/unparseable check above never catches this since neither
    value is missing or unparseable on its own. Must still degrade to the
    same documented fallback, logged, rather than a raw ValidationError
    taking the whole read down."""
    inconsistent_event = CalendarEvent(
        event_id="e-corrupt",
        summary="Background, allegedly",
        start=datetime(2026, 8, 17, 9, 0),
        end=datetime(2026, 8, 17, 10, 0),
        etag="v1",
        uid="u-corrupt",
        handle="COR1",
        block_type="BG",
        timing_mode="ap",
    )
    calendar = FakeCalendar({"primary": [inconsistent_event]})
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    svc = PlanService(calendar, store)

    with caplog.at_level(logging.WARNING):
        plan, _snapshot = await svc.read("primary", DAY)

    block = plan.by_handle("COR1")
    assert block.t == ET.M
    assert block.p.a == "fw"
    assert any(
        "e-corrupt" in record.getMessage()
        and "block_type/timing_mode" in record.getMessage()
        for record in caplog.records
    )


async def test_commit_writes_the_resolved_type_and_mode_onto_the_calendar_event(
    empty_service,
):
    """The write side of the round trip, independent of gcal.py: the
    CalendarEvent PlanService actually hands the port carries block_type/
    timing_mode sourced from the resolved block, not left unset."""
    svc = empty_service
    _, snapshot = await svc.read("primary", DAY)
    await svc.commit(
        snapshot,
        Patch(
            ops=[
                AddBlock(
                    after=None,
                    h="DW1",
                    n="Deep Work",
                    t=ET.DW,
                    p=FixedStart(st=time(9, 0), dur=timedelta(minutes=45)),
                    anchor_source="user",
                )
            ]
        ),
    )
    live = await svc.calendar.list_day("primary", DAY, TZ)
    event = next(e for e in live if e.handle == "DW1")
    assert (event.block_type, event.timing_mode) == ("DW", "fs")


async def test_relaxing_timing_mode_alone_still_writes_to_the_calendar(empty_service):
    """A patch that only changes ``p`` (fs -> ap), leaving start/end/
    summary/description/uid/handle/slug identical, must still reach the
    calendar -- without block_type/timing_mode in ``_event_unchanged``'s
    comparison this would be (wrongly) treated as a no-op and the mode
    change would never actually be written."""
    svc = empty_service
    _, snapshot = await svc.read("primary", DAY)
    await svc.commit(
        snapshot,
        Patch(
            ops=[
                AddBlock(
                    after=None,
                    h="ANC1",
                    n="Anchor",
                    t=ET.M,
                    p=FixedWindow(st=time(8, 0), et=time(9, 0)),
                    anchor_source="user",
                )
            ]
        ),
    )
    _, snapshot1 = await svc.read("primary", DAY)
    await svc.commit(
        snapshot1,
        Patch(
            ops=[
                AddBlock(
                    after="ANC1",
                    h="RLX1",
                    n="Relax me",
                    t=ET.M,
                    p=FixedStart(st=time(9, 0), dur=timedelta(minutes=30)),
                    anchor_source="user",
                )
            ]
        ),
    )
    live_before = {
        e.handle: (e.etag, e.timing_mode)
        for e in await svc.calendar.list_day("primary", DAY, TZ)
    }
    assert live_before["RLX1"][1] == "fs"

    _, snapshot2 = await svc.read("primary", DAY)
    await svc.commit(
        snapshot2,
        Patch(ops=[UpdateBlock(h="RLX1", p=AfterPrev(dur=timedelta(minutes=30)))]),
    )

    live_after = {
        e.handle: (e.etag, e.timing_mode)
        for e in await svc.calendar.list_day("primary", DAY, TZ)
    }
    assert live_after["RLX1"][1] == "ap"
    assert live_after["RLX1"][0] != live_before["RLX1"][0]  # etag bumped: really written
