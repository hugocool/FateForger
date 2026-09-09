# tests/unit/tmbx/test_link_reaches_the_calendar.py
"""A linked block's url reaches the event, and its handle survives a re-commit.

Two levels, because neither can stand in for the other:

* **Against the fake calendar** — what a *commit* puts on an event. The fake
  stores a ``CalendarEvent`` verbatim, so it answers "did the service resolve
  the handle to a url and hand both to the port", and it answers the
  no-op/update questions the real provider cannot be asked offline.
* **Against the adapter** — what actually goes over the wire: the
  ``tmbx.link`` private property and the bare url on its own line at the end
  of the description. The fake never sees that shape; only ``gcal.py`` builds
  it, and the description half is the part a person clicks.

No network, no ``.env``, no model. The material store is a temp SQLite file
and the MCP transport is an in-memory fake with one method.
"""

from __future__ import annotations

import itertools
import json
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.gcal import GoogleCalendarAdapter
from tmbx.calendar.port import MAX_DESCRIPTION_CHARS, CalendarEvent
from tmbx.core.models import ET, AfterPrev, PlanViolation, ViolationKind
from tmbx.core.models import Plan
from tmbx.core.ops import AddBlock, Patch, UpdateBlock
from tmbx.core.render import render_plan
from tmbx.journal.models import PatchOutcome
from tmbx.journal.store import JournalStore, init_journal
from tmbx.materials import MaterialStore
from tmbx.service import ForeignBlockError, PlanService, _event_to_block

DAY = date(2026, 8, 17)
TZ = "Europe/Amsterdam"
TICKET = "1f2a3b4c-5d6e-7f80-9a0b-1c2d3e4f5061"
TICKET_URL = "https://www.notion.so/Verify-VPB-2024-aangifte-1f2a3b4c"
TICKET_LABEL = "Verify VPB 2024 aangifte"
OTHER_TICKET = "2a3b4c5d-6e7f-8091-a0b1-c2d3e4f50612"
OTHER_URL = "https://www.notion.so/File-the-Q3-VAT-return-2a3b4c5d"
OTHER_LABEL = "File the Q3 VAT return"
DEAD_HANDLE = "m0123456789"


# ---------------------------------------------------------------------------
# service-level scaffolding — the fake calendar, a real (temp) material store
# ---------------------------------------------------------------------------


class RecordingCalendar(FakeCalendar):
    """``FakeCalendar`` that remembers which writes it was actually asked for.

    The no-op question ("re-committing an unchanged plan performs no update")
    cannot be answered from stored state alone — an update that rewrites an
    event with identical content leaves the same content behind. Only the
    call itself is evidence, so the call is what this records.

    It is also the only place ``link_url`` can be observed at all. The fake
    answers like a provider and never hands the url back (``fake._as_fetched``),
    so "did the service resolve this handle to a url" is a question about the
    *call*, not about stored state. ``writes`` keeps creates and updates in one
    ordered log for exactly that.
    """

    def __init__(self, events: dict[str, list[CalendarEvent]] | None = None) -> None:
        super().__init__(events)
        self.created: list[CalendarEvent] = []
        self.updated: list[CalendarEvent] = []
        self.writes: list[CalendarEvent] = []

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        self.created.append(event.model_copy(deep=True))
        self.writes.append(event.model_copy(deep=True))
        return await super().create(calendar_id, event)

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        self.updated.append(event.model_copy(deep=True))
        self.writes.append(event.model_copy(deep=True))
        return await super().update(calendar_id, event)


def _event(
    eid: str,
    h: str,
    start_h: int,
    end_h: int,
    *,
    block_type: str = "M",
    timing_mode: str = "fw",
    description: str = "",
    link_id: str | None = None,
    link_url: str | None = None,
) -> CalendarEvent:
    return CalendarEvent(
        event_id=eid,
        summary=h,
        description=description,
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=f"u-{eid}",
        handle=h,
        block_type=block_type,
        timing_mode=timing_mode,
        link_id=link_id,
        link_url=link_url,
    )


@pytest.fixture
async def materials(tmp_path):
    return MaterialStore(await init_journal(tmp_path / "j.db"))


@pytest.fixture
async def known(materials):
    """The handle of one stored ticket."""
    return await materials.put(
        source="notion", external_id=TICKET, url=TICKET_URL, label=TICKET_LABEL
    )


@pytest.fixture
async def other(materials):
    """A second stored ticket, so a link can be *changed* rather than only set."""
    return await materials.put(
        source="notion", external_id=OTHER_TICKET, url=OTHER_URL, label=OTHER_LABEL
    )


async def _service(tmp_path, materials, calendar):
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    return PlanService(
        calendar,
        store,
        mint_uid=lambda: f"u-new-{next(counter)}",
        materials=materials,
    )


@pytest.fixture
async def calendar():
    return RecordingCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", "DW1", 10, 12, description="focus block"),
            ]
        }
    )


@pytest.fixture
async def service(tmp_path, materials, calendar):
    return await _service(tmp_path, materials, calendar)


async def _stored(calendar: RecordingCalendar, handle: str) -> CalendarEvent:
    """The block as the calendar hands it back — provider-shaped, no url."""
    events = await calendar.list_day("primary", DAY, TZ)
    return next(event for event in events if event.handle == handle)


def _written(calendar: RecordingCalendar, handle: str) -> CalendarEvent:
    """The last event the service actually handed the port for ``handle``.

    Where ``link_url`` is asserted, because it is write-only: it goes out on
    the call and never comes back from a read. See ``RecordingCalendar``.
    """
    return next(
        event for event in reversed(calendar.writes) if event.handle == handle
    )


# ---------------------------------------------------------------------------
# the port carries both halves
# ---------------------------------------------------------------------------


def test_a_calendar_event_carries_a_link_handle_and_url():
    event = _event("e1", "DW1", 10, 12, link_id="mdeadbeef0", link_url=TICKET_URL)

    assert (event.link_id, event.link_url) == ("mdeadbeef0", TICKET_URL)


def test_a_calendar_event_carries_neither_by_default():
    event = _event("e1", "DW1", 10, 12)

    assert (event.link_id, event.link_url) == (None, None)


async def test_the_fake_calendar_hands_back_the_handle_and_never_the_url(calendar):
    """``FakeCalendar`` answers the way a provider does: the handle comes
    back, the url does not.

    The port documents ``link_url`` as write-only — ``gcal._event_from_payload``
    reads ``tmbx.link`` and nothing else, so an event fetched from Google
    carries ``link_id`` and ``link_url is None``. A fake that echoed the url
    back gave every service-level test below a fidelity production does not
    have, and it hid a Critical: ``undo`` replayed provider-shaped events and
    stripped the url off every linked block for good. Twice now a fake more
    honest than this one would have caught the bug the day it was written.
    """
    await calendar.create(
        "primary",
        _event("e9", "X1", 13, 14, link_id="mdeadbeef0", link_url=TICKET_URL),
    )

    stored = await _stored(calendar, "X1")
    assert (stored.link_id, stored.link_url) == ("mdeadbeef0", None)


async def test_the_fake_calendar_drops_the_url_from_a_create_and_an_update_too(
    calendar,
):
    """Every way out of the fake, not only ``list_day``. ``create`` and
    ``update`` hand back the stored event, and a caller that trusted the url
    on one of those returns would be trusting something no provider gives."""
    created = await calendar.create(
        "primary",
        _event("e9", "X1", 13, 14, link_id="mdeadbeef0", link_url=TICKET_URL),
    )
    updated = await calendar.update(
        "primary",
        _event("e9", "X1", 13, 15, link_id="mdeadbeef0", link_url=TICKET_URL),
    )

    assert (created.link_id, created.link_url) == ("mdeadbeef0", None)
    assert (updated.link_id, updated.link_url) == ("mdeadbeef0", None)


# ---------------------------------------------------------------------------
# what a commit puts on the event
# ---------------------------------------------------------------------------


async def test_committing_a_linked_block_puts_the_handle_and_the_url_on_the_event(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert (await _stored(calendar, "DW1")).link_id == known
    assert _written(calendar, "DW1").link_url == TICKET_URL


async def test_the_url_comes_from_the_store_not_from_the_patch(
    service, calendar, known
):
    """The op names a handle and nothing else. The url on the event is the
    store's, which is the whole point of storing links by handle."""
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert _written(calendar, "DW1").link_url == TICKET_URL


async def test_committing_a_new_linked_block_carries_the_link_onto_the_created_event(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)
    patch = Patch(
        ops=[
            AddBlock(
                after="DW1",
                h="SW1",
                n="Shallow work",
                t=ET.SW,
                p=AfterPrev(dur="PT30M"),
                link=known,
            )
        ]
    )

    await service.commit(snapshot, patch)

    assert (await _stored(calendar, "SW1")).link_id == known
    assert _written(calendar, "SW1").link_url == TICKET_URL


async def test_a_block_with_no_link_commits_neither_half(service, calendar, known):
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    untouched = await _stored(calendar, "PR1")
    assert (untouched.link_id, untouched.link_url) == (None, None)


async def test_an_explicit_null_takes_the_link_back_off_the_event(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(
        snapshot,
        Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "link": None}]}),
    )

    assert (await _stored(calendar, "DW1")).link_id is None
    assert _written(calendar, "DW1").link_url is None


# ---------------------------------------------------------------------------
# a re-commit is a no-op; a changed ticket is not
# ---------------------------------------------------------------------------


async def test_re_committing_an_unchanged_linked_plan_performs_no_update(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))
    calendar.updated.clear()

    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert calendar.updated == []


async def test_changing_only_the_link_triggers_an_update(
    service, calendar, known, other
):
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))
    calendar.updated.clear()

    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=other)]))

    assert [event.handle for event in calendar.updated] == ["DW1"]
    assert _written(calendar, "DW1").link_url == OTHER_URL


async def test_clearing_only_the_link_triggers_an_update(service, calendar, known):
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))
    calendar.updated.clear()

    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(
        snapshot,
        Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "link": None}]}),
    )

    assert [event.handle for event in calendar.updated] == ["DW1"]


async def test_an_event_whose_url_was_never_read_back_is_still_unchanged(
    tmp_path, materials, known
):
    """A day that already carries a link, read the way a provider answers.

    A real adapter puts the url in the description and reads only the handle
    back (``gcal._event_from_payload``), so every event fetched from Google
    carries ``link_id`` and ``link_url=None``. If the no-op check compared
    ``link_url`` too, every linked block would be rewritten on every commit
    forever — an etag bump and a change notification per block, per day.

    The seeded event is handed a url on purpose: the fake drops it on the way
    out (``fake._as_fetched``), which is the point. This test used to pass
    ``link_url=None`` by hand to compensate for a fake that echoed it back —
    a test working around its own scaffolding, and the scaffolding was what
    was wrong.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event(
                    "e2",
                    "DW1",
                    10,
                    12,
                    description="focus block",
                    link_id=known,
                    link_url=TICKET_URL,
                ),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert calendar.updated == []


# ---------------------------------------------------------------------------
# both halves of a link key on the same field
# ---------------------------------------------------------------------------


def test_nothing_is_composed_into_a_description_without_a_link_id():
    """A url with no handle beside it composes nothing.

    ``_description_for`` and ``_private_properties`` must agree on what makes
    an event linked, because the read side decides on ``tmbx.link`` alone. A
    url appended without ``tmbx.link``/``tmbx.desc`` written beside it comes
    back as authored prose, is appended to a second time on the next write,
    and lands in front of a planner as text somebody typed -- the Critical
    this file has already produced once.

    Unreachable through the service, which sets both halves together. Asserted
    at the function, which is where the divergence lived.
    """
    from tmbx.calendar.gcal import _description_for, _private_properties

    orphan_url = _event(
        "e1", "DW1", 10, 12, description="focus block", link_url=TICKET_URL
    )

    assert _description_for(orphan_url) == "focus block"
    assert TICKET_URL not in _description_for(orphan_url)
    # and the two halves still agree: neither claims a link. Empty, not
    # absent -- the key is always sent, because the server merges this map
    # and an omitted key keeps its old value. `_private_str` reads an empty
    # string back as absence.
    assert _private_properties(orphan_url)["tmbx.link"] == ""
    assert _private_properties(orphan_url)["tmbx.desc"] == ""


def test_a_url_with_a_handle_beside_it_is_still_composed_in():
    """The narrowing above must not cost the ordinary case."""
    from tmbx.calendar.gcal import _description_for, _private_properties

    linked = _event(
        "e1",
        "DW1",
        10,
        12,
        description="focus block",
        link_id="mdeadbeef0",
        link_url=TICKET_URL,
    )

    assert _description_for(linked) == f"focus block\n\n{TICKET_URL}"
    assert _private_properties(linked)["tmbx.desc"] == "focus block"


def test_max_description_chars_is_exported_by_the_port():
    """The service imports it and the refusal messages quote it, so it is
    part of the port's interface whether or not ``__all__`` said so."""
    from tmbx.calendar import port

    assert "MAX_DESCRIPTION_CHARS" in port.__all__


# ---------------------------------------------------------------------------
# a foreign block's link is not tmbx's business
# ---------------------------------------------------------------------------


def _foreign_event(
    eid: str,
    start_h: int,
    end_h: int,
    *,
    link_id: str | None = None,
    description: str = "",
) -> CalendarEvent:
    """A calendar event tmbx did not write: no ``uid``, so no ownership.

    It carries a ``tmbx.link`` anyway. Contrived — but ``_event_to_block``
    reads ``link_id`` on every event, owned or not, and a day that once held
    an owned block whose uid property was later stripped is exactly this
    shape.
    """
    return CalendarEvent(
        event_id=eid,
        summary="Standup",
        description=description,
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        link_id=link_id,
    )


async def test_a_foreign_blocks_dead_handle_does_not_refuse_the_day(
    tmp_path, materials
):
    """A deadlock with no exit, until this filter.

    ``_dead_link_violation`` walked ``plan.blocks`` unfiltered, so a foreign
    block carrying a handle the store had lost refused every commit of that
    day, forever. The refusal names the one remedy — clear the link with an
    explicit null — and that null is itself refused by ``_foreign_touches``,
    because tmbx must never write a foreign event. Nothing tmbx owns is
    broken, nothing tmbx writes is affected, and the day cannot be planned.

    ``commit`` already has ``foreign_handles`` in hand a screen above.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event("f1", 9, 10, link_id=DEAD_HANDLE),
                _event("e2", "DW1", 10, 12, description="focus block"),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    result = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="DW1", d="deep focus")])
    )

    assert result.committed is True


async def test_the_only_remedy_for_a_foreign_dead_handle_is_itself_refused(
    tmp_path, materials
):
    """Why the filter, and not a message telling the caller to clear it.

    Kept as a test rather than a comment because it is the half that makes
    the deadlock a deadlock: if this ever started succeeding, the refusal
    above would have been survivable all along.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event("f1", 9, 10, link_id=DEAD_HANDLE),
                _event("e2", "DW1", 10, 12),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    plan, snapshot = await service.read("primary", DAY)
    foreign = next(block.h for block in plan.blocks if block.link == DEAD_HANDLE)

    with pytest.raises(ForeignBlockError):
        await service.commit(
            snapshot,
            Patch.model_validate(
                {"ops": [{"op": "update", "h": foreign, "link": None}]}
            ),
        )


async def test_an_owned_blocks_dead_handle_still_refuses(tmp_path, materials):
    """The filter narrows the check to what tmbx owns; it does not remove it."""
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event("f1", 9, 10),
                _event("e2", "DW1", 10, 12, link_id=DEAD_HANDLE),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    with pytest.raises(PlanViolation) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", d="focus")]))

    assert DEAD_HANDLE in str(excinfo.value)


async def test_a_foreign_blocks_long_description_does_not_refuse_the_day(
    tmp_path, materials, known
):
    """The same deadlock, one screen below the one that was fixed.

    ``_dead_link_violation`` learned to skip a foreign block; the refusal
    directly after it did not. So a foreign block carrying a *live* handle and
    an over-long description refused every commit of the day, and the two
    remedies the message offers -- shorten the description, or drop the link --
    are both writes to a foreign event, which ``_foreign_touches`` refuses.
    Nothing tmbx owns is broken and the day cannot be planned: the deadlock
    ``test_a_foreign_blocks_dead_handle_does_not_refuse_the_day`` describes,
    reached by the other door.

    Live handle, not ``DEAD_HANDLE``, so the dead-link check passes it through
    and this refusal is the only one left that can fire.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event(
                    "f1", 9, 10, link_id=known, description="x" * (MAX_DESCRIPTION_CHARS + 1)
                ),
                _event("e2", "DW1", 10, 12, description="focus block"),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    result = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="DW1", d="deep focus")])
    )

    assert result.committed is True


async def test_neither_remedy_for_a_foreign_long_description_is_available(
    tmp_path, materials, known
):
    """Why the filter, and not a message telling the caller to fix it.

    The half that makes it a deadlock rather than an inconvenience: both
    remedies the refusal names are updates to a foreign handle, and both are
    refused before they reach the calendar.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event(
                    "f1", 9, 10, link_id=known, description="x" * (MAX_DESCRIPTION_CHARS + 1)
                ),
                _event("e2", "DW1", 10, 12),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    plan, snapshot = await service.read("primary", DAY)
    foreign = next(block.h for block in plan.blocks if block.link == known)

    for remedy in ({"d": "short"}, {"link": None}):
        with pytest.raises(ForeignBlockError):
            await service.commit(
                snapshot,
                Patch.model_validate(
                    {"ops": [{"op": "update", "h": foreign, **remedy}]}
                ),
            )


async def test_an_owned_blocks_long_description_still_refuses(
    tmp_path, materials, known
):
    """The filter narrows the check to what tmbx owns; it does not remove it."""
    calendar = RecordingCalendar(
        {
            "primary": [
                _foreign_event("f1", 9, 10),
                _event(
                    "e2",
                    "DW1",
                    10,
                    12,
                    link_id=known,
                    description="x" * (MAX_DESCRIPTION_CHARS + 1),
                ),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    with pytest.raises(PlanViolation) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", n="Focus")]))

    assert excinfo.value.violation.kind is ViolationKind.DESCRIPTION_TOO_LONG


# ---------------------------------------------------------------------------
# undo puts the url back, and never refuses over a link
# ---------------------------------------------------------------------------


async def test_undo_restores_the_url_onto_a_linked_event_it_replays(
    service, calendar, known
):
    """The Critical this section exists for.

    ``before_events`` is captured at commit from ``calendar.list_day``, and a
    provider-fetched event carries ``link_url is None`` by the port's own
    contract. Replaying those rows verbatim therefore wrote every owned event
    back with no url — the composed description collapsed to the authored text
    alone and the clickable link was gone.

    And it never came back: the next commit's candidate matches the now-live
    event on summary, description, times, identity and ``link_id``, so
    ``_event_unchanged`` is True and ``_write`` skips it. The fix is here,
    where the handles are, not in ``_event_unchanged`` — which ignores
    ``link_url`` deliberately, because every provider read has none.

    Note what is being undone: a *later*, unrelated commit. The link need not
    be anywhere near the transaction to be destroyed by it.
    """
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    _plan, snapshot = await service.read("primary", DAY)
    later = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="PR1", d="prep")])
    )
    calendar.writes.clear()

    await service.undo(later.tx_id)

    assert _written(calendar, "DW1").link_url == TICKET_URL


async def test_undo_leaves_an_unlinked_event_without_a_url(service, calendar, known):
    """The restore resolves handles; it does not invent them. A block that
    carried no link before the undone commit is written back carrying none."""
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    _plan, snapshot = await service.read("primary", DAY)
    later = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="PR1", d="prep")])
    )
    calendar.writes.clear()

    await service.undo(later.tx_id)

    assert (_written(calendar, "PR1").link_id, _written(calendar, "PR1").link_url) == (
        None,
        None,
    )


async def test_one_lookup_covers_a_handle_two_blocks_share(
    tmp_path, materials, known, monkeypatch
):
    """The restore asks the store once, for the distinct handles, the way
    ``commit`` does — not once per event."""
    calendar = RecordingCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10, link_id=known),
                _event("e2", "DW1", 10, 12, link_id=known),
                _event("e3", "SW1", 13, 14),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)
    later = await service.commit(
        snapshot, Patch(ops=[UpdateBlock(h="SW1", d="admin")])
    )

    calls: list[list[str]] = []
    original = service.materials.get_many

    async def counting(link_ids):
        calls.append(list(link_ids))
        return await original(link_ids)

    monkeypatch.setattr(service.materials, "get_many", counting)
    await service.undo(later.tx_id)

    assert len(calls) == 1
    assert sorted(calls[0]) == [known]


async def test_a_handle_whose_row_is_gone_never_blocks_an_undo(
    tmp_path, materials, known
):
    """Undo has no override and no second chance: a refusal here strands the
    day in the state the commit left it in, which is the one thing undo exists
    to prevent. A dead handle degrades to the old behaviour — the event goes
    back with its handle and no url — and never raises.
    """
    calendar = RecordingCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", "DW1", 10, 12, description="focus block"),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))
    _plan, snapshot = await service.read("primary", DAY)
    later = await service.commit(snapshot, Patch(ops=[UpdateBlock(h="PR1", d="prep")]))

    # The row goes away between the commit and the undo. A commit could not
    # reach this state -- ``_dead_link_violation`` refuses first, deliberately
    # -- so the store is swapped for an empty one rather than the refusal being
    # weakened to set it up.
    service.materials = MaterialStore(await init_journal(tmp_path / "empty.db"))
    calendar.writes.clear()

    await service.undo(later.tx_id)

    restored = _written(calendar, "DW1")
    assert (restored.link_id, restored.link_url) == (known, None)


# ---------------------------------------------------------------------------
# a handle the store no longer holds refuses the commit
# ---------------------------------------------------------------------------


@pytest.fixture
async def dead_link_calendar():
    """A day already on the calendar carrying a handle nothing stores."""
    return RecordingCalendar(
        {
            "primary": [
                _event("e1", "PR1", 9, 10),
                _event("e2", "DW1", 10, 12, link_id=DEAD_HANDLE),
            ]
        }
    )


async def test_a_plan_carrying_a_handle_the_store_lost_refuses_the_commit(
    tmp_path, materials, dead_link_calendar
):
    service = await _service(tmp_path, materials, dead_link_calendar)
    _plan, snapshot = await service.read("primary", DAY)

    with pytest.raises(PlanViolation) as excinfo:
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Prep")]))

    assert DEAD_HANDLE in str(excinfo.value)
    assert excinfo.value.violation.kind is ViolationKind.UNKNOWN_LINK
    assert [b.h for b in excinfo.value.violation.blocks] == ["DW1"]


async def test_a_refused_dead_handle_writes_nothing_and_is_journalled(
    tmp_path, materials, dead_link_calendar
):
    service = await _service(tmp_path, materials, dead_link_calendar)
    _plan, snapshot = await service.read("primary", DAY)

    with pytest.raises(PlanViolation):
        await service.commit(snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Prep")]))

    assert dead_link_calendar.updated == []
    assert dead_link_calendar.created == []
    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].outcome is PatchOutcome.APPLY_FAILED
    assert DEAD_HANDLE in (rows[-1].error or "")


async def test_a_forced_commit_cannot_write_past_a_dead_handle(
    tmp_path, materials, dead_link_calendar
):
    service = await _service(tmp_path, materials, dead_link_calendar)
    _plan, snapshot = await service.read("primary", DAY)

    with pytest.raises(PlanViolation):
        await service.commit(
            snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Prep")]), expect="force"
        )


async def test_a_preview_still_shows_a_day_holding_a_dead_handle(
    tmp_path, materials, dead_link_calendar
):
    """The asymmetry is deliberate. ``apply`` writes nothing, so a dead row
    costs it nothing and refusing there would hide the whole day from the
    caller who has to fix it. ``commit`` has a url to write and none to
    write, so it refuses."""
    service = await _service(tmp_path, materials, dead_link_calendar)
    _plan, snapshot = await service.read("primary", DAY)

    result = await service.apply(snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Prep")]))

    assert result.plan.by_handle("DW1").link == DEAD_HANDLE


async def test_clearing_the_dead_handle_lets_the_day_commit_again(
    tmp_path, materials, dead_link_calendar
):
    """The stated way out of the refusal, exercised rather than promised."""
    service = await _service(tmp_path, materials, dead_link_calendar)
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(
        snapshot,
        Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "link": None}]}),
    )

    assert (await _stored(dead_link_calendar, "DW1")).link_id is None


# ---------------------------------------------------------------------------
# reading a day back returns the handle
# ---------------------------------------------------------------------------


async def test_reading_a_day_back_returns_the_handle_on_the_block(
    tmp_path, materials, known
):
    calendar = RecordingCalendar(
        {"primary": [_event("e2", "DW1", 10, 12, link_id=known)]}
    )
    service = await _service(tmp_path, materials, calendar)

    plan, _snapshot = await service.read("primary", DAY)

    assert plan.by_handle("DW1").link == known


async def test_reading_a_day_back_returns_no_handle_for_an_unlinked_block(
    tmp_path, materials
):
    calendar = RecordingCalendar({"primary": [_event("e2", "DW1", 10, 12)]})
    service = await _service(tmp_path, materials, calendar)

    plan, _snapshot = await service.read("primary", DAY)

    assert plan.by_handle("DW1").link is None


async def test_a_committed_link_survives_a_read_and_a_re_commit(
    service, calendar, known
):
    """The round trip the task is named for, end to end through the port."""
    _plan, snapshot = await service.read("primary", DAY)
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    plan, snapshot = await service.read("primary", DAY)
    assert plan.by_handle("DW1").link == known

    calendar.updated.clear()
    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="PR1", n="Prep")]))

    assert (await _stored(calendar, "DW1")).link_id == known
    assert [event.handle for event in calendar.updated] == ["PR1"]


# ---------------------------------------------------------------------------
# adapter-level scaffolding — an in-memory MCP transport, never the network
# ---------------------------------------------------------------------------


class FakeCaller:
    """Records every ``call_tool`` invocation; replays canned results in order."""

    def __init__(self, results: dict[str, list[CallToolResult]]) -> None:
        self._results = {name: list(queue) for name, queue in results.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, dict(arguments)))
        return self._results[name].pop(0)


def _text_result(payload: Any) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload))], isError=False
    )


def _raw_event(*, private: dict[str, str] | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "id": "tmb0abc123",
        "summary": "Deep Work",
        "description": "",
        "start": {"dateTime": "2026-08-17T10:00:00+02:00"},
        "end": {"dateTime": "2026-08-17T12:00:00+02:00"},
        "status": "confirmed",
        "updated": "2026-08-17T08:00:00.000Z",
    }
    if private is not None:
        event["extendedProperties"] = {"private": private}
    return event


def _make_adapter(
    results: dict[str, list[CallToolResult]],
) -> tuple[GoogleCalendarAdapter, FakeCaller]:
    caller = FakeCaller(results)

    @asynccontextmanager
    async def factory():
        yield caller

    return GoogleCalendarAdapter(tz=TZ, session_factory=factory), caller


def _linked_event(**overrides: Any) -> CalendarEvent:
    fields: dict[str, Any] = {
        "event_id": "tmb0abc123",
        "summary": "Deep Work",
        "description": "focus block",
        "start": datetime(2026, 8, 17, 10, 0),
        "end": datetime(2026, 8, 17, 12, 0),
        "uid": "u-1",
        "handle": "DW1",
        "block_type": "DW",
        "timing_mode": "fw",
        "link_id": "mdeadbeef0",
        "link_url": TICKET_URL,
    }
    fields.update(overrides)
    return CalendarEvent(**fields)


# ---------------------------------------------------------------------------
# what the adapter writes over the wire
# ---------------------------------------------------------------------------


async def test_create_writes_the_handle_into_a_private_property():
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.create("primary", _linked_event())

    _name, args = caller.calls[0]
    assert args["extendedProperties"]["private"]["tmbx.link"] == "mdeadbeef0"


async def test_create_appends_the_url_after_the_description_and_a_blank_line():
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.create("primary", _linked_event())

    _name, args = caller.calls[0]
    assert args["description"] == f"focus block\n\n{TICKET_URL}"


async def test_the_url_goes_on_its_own_line_and_is_bare():
    """Bare, because Google auto-links a bare url in a description and the
    server's ``description`` is a plain string whose HTML handling nobody
    here has verified. The anchor text a person reads is the event title."""
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.create("primary", _linked_event())

    _name, args = caller.calls[0]
    assert args["description"].splitlines()[-1] == TICKET_URL


async def test_a_block_with_no_description_gets_the_url_alone():
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.create("primary", _linked_event(description=""))

    _name, args = caller.calls[0]
    assert args["description"] == TICKET_URL


async def test_an_unlinked_event_writes_neither_the_property_nor_the_line():
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.create("primary", _linked_event(link_id=None, link_url=None))

    _name, args = caller.calls[0]
    assert args["extendedProperties"]["private"]["tmbx.link"] == ""
    assert args["description"] == "focus block"


async def test_update_writes_the_link_the_same_way_create_does():
    adapter, caller = _make_adapter(
        {"update-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.update("primary", _linked_event())

    _name, args = caller.calls[0]
    assert args["extendedProperties"]["private"]["tmbx.link"] == "mdeadbeef0"
    assert args["description"] == f"focus block\n\n{TICKET_URL}"


# ---------------------------------------------------------------------------
# what the adapter reads back
# ---------------------------------------------------------------------------


async def test_list_day_reads_the_handle_back_out_of_the_private_property():
    adapter, _caller = _make_adapter(
        {
            "list-events": [
                _text_result(
                    {
                        "events": [
                            _raw_event(
                                private={"tmbx.uid": "u-1", "tmbx.link": "mdeadbeef0"}
                            )
                        ]
                    }
                )
            ]
        }
    )

    events = await adapter.list_day("primary", DAY, TZ)

    assert events[0].link_id == "mdeadbeef0"


async def test_list_day_reports_no_handle_for_an_event_that_carries_none():
    adapter, _caller = _make_adapter(
        {
            "list-events": [
                _text_result({"events": [_raw_event(private={"tmbx.uid": "u-1"})]})
            ]
        }
    )

    events = await adapter.list_day("primary", DAY, TZ)

    assert events[0].link_id is None


async def test_the_url_is_not_reconstructed_from_the_description():
    """``link_url`` is write-only: the description is the human-visible copy,
    and the store is the one place the url is read from. Inventing one back
    off the description would be a guess about text this module did not
    mint."""
    adapter, _caller = _make_adapter(
        {
            "list-events": [
                _text_result(
                    {
                        "events": [
                            _raw_event(
                                private={"tmbx.uid": "u-1", "tmbx.link": "mdeadbeef0"}
                            )
                        ]
                    }
                )
            ]
        }
    )

    events = await adapter.list_day("primary", DAY, TZ)

    assert events[0].link_url is None


# ---------------------------------------------------------------------------
# the authored description round-trips; the composed one is display-only
# ---------------------------------------------------------------------------


def _raw_from_args(args: dict[str, Any]) -> dict[str, Any]:
    """The raw event a provider would hand back for what the adapter just sent.

    Built from the adapter's own write arguments rather than typed out again,
    so a round-trip test really is a round trip: whatever ``_write_event_args``
    decided to send is exactly what ``_event_from_payload`` is then asked to
    read. A hand-written "expected" payload would let the two drift apart and
    still pass.
    """
    raw: dict[str, Any] = {
        "id": args.get("eventId", "tmb0abc123"),
        "summary": args["summary"],
        "description": args["description"],
        "start": {"dateTime": "2026-08-17T10:00:00+02:00"},
        "end": {"dateTime": "2026-08-17T12:00:00+02:00"},
        "status": "confirmed",
        "updated": "2026-08-17T08:00:00.000Z",
    }
    if "extendedProperties" in args:
        raw["extendedProperties"] = args["extendedProperties"]
    return raw


async def _round_trip(event: CalendarEvent) -> tuple[CalendarEvent, dict[str, Any]]:
    """Write ``event``, hand the result back as a provider read, return both."""
    adapter, caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )
    await adapter.create("primary", event)
    _name, args = caller.calls[0]

    reader, _reader_caller = _make_adapter(
        {"list-events": [_text_result({"events": [_raw_from_args(args)]})]}
    )
    read_back = (await reader.list_day("primary", DAY, TZ))[0]
    return read_back, args


async def _merged_round_trip(
    first: CalendarEvent, second: CalendarEvent
) -> tuple[CalendarEvent, dict[str, Any]]:
    """Write ``first``, then ``second``, and read back what the server holds.

    **The private map is merged, not replaced** — measured against the real
    server on 2026-09-08 (see ``gcal.py``'s module docstring). A key the second
    write omits keeps the value the first write gave it. That is the whole
    point of this helper: ``_round_trip`` writes once and can never show it,
    which is why an omission was taken for a clearance for as long as it was.

    Returns the event as a provider would hand it back, and the arguments of
    the *second* write.
    """
    writer, first_caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )
    await writer.create("primary", first)
    _name, first_args = first_caller.calls[0]

    updater, second_caller = _make_adapter(
        {"update-event": [_text_result({"event": _raw_event()})]}
    )
    await updater.update("primary", second)
    _name, second_args = second_caller.calls[0]

    raw = _raw_from_args(second_args)
    merged = {
        **first_args.get("extendedProperties", {}).get("private", {}),
        **second_args.get("extendedProperties", {}).get("private", {}),
    }
    raw["extendedProperties"] = {"private": merged}

    reader, _reader_caller = _make_adapter(
        {"list-events": [_text_result({"events": [raw]})]}
    )
    return (await reader.list_day("primary", DAY, TZ))[0], second_args


async def test_a_detached_link_does_not_come_back_after_the_servers_merge():
    """Consequence 1 of the measured merge semantics.

    An explicit null clears `Block.link`, the write omitted ``tmbx.link``, the
    old handle survived on the event, the next read handed it back, and the
    next commit composed the url into the description again. A link the user
    removed came back.
    """
    read_back, args = await _merged_round_trip(
        _linked_event(), _linked_event(link_id=None, link_url=None)
    )

    assert read_back.link_id is None
    assert read_back.link_url is None
    assert TICKET_URL not in args["description"]


async def test_a_cleared_description_does_not_revert_after_the_servers_merge():
    """Consequence 2, and the reason this is Critical rather than Important.

    ``tmbx.desc`` survived the same way, so ``_authored_description`` took case
    1 and returned the stale authored text in preference to what is actually on
    the event. Content reverting under a person is worse than a resurrected
    link: nothing about the event says the words came from a write two commits
    ago.
    """
    read_back, _args = await _merged_round_trip(
        _linked_event(description="focus block"), _linked_event(description="")
    )

    assert read_back.description == ""


@pytest.mark.parametrize(
    "field",
    ["uid", "handle", "slug", "block_type", "timing_mode", "anchor_source"],
)
async def test_every_pre_existing_key_clears_under_the_servers_merge(field: str):
    """Consequence 3, and why this is not a link fix.

    The six keys that predate this branch clear by the same mechanism and were
    broken by it in the same way — ``tmbx.slug`` is the one the coordinator
    measured, and a required-kind slug removed from a block stayed on the
    event. A half-fix covering only the two keys this branch added would leave
    the identical bug in the identical file.
    """
    read_back, _args = await _merged_round_trip(
        _linked_event(**{field: "planning"}), _linked_event(**{field: None})
    )

    assert getattr(read_back, field) is None


async def test_the_authored_description_comes_back_without_the_url():
    """The composed description is for a person to read; ``tmbx.desc`` is the
    machine-readable original, exactly as identity and the structural fields
    already work."""
    read_back, args = await _round_trip(_linked_event())

    assert args["extendedProperties"]["private"]["tmbx.desc"] == "focus block"
    assert read_back.description == "focus block"


async def test_a_day_read_back_keeps_the_url_out_of_what_a_planner_sees():
    """Task 2 kept urls out of the planner's view; a day fetched back from a
    real calendar must not put one back.

    ``Block.d`` is where that is decided. The rendered table has no
    description column today — the url would reach a planner through the plan
    object and the cards built from it — so the block field is the assertion
    that carries weight, and the render check is the cheap net for a
    description column added later.
    """
    read_back, _args = await _round_trip(_linked_event())

    block = _event_to_block(read_back, 0, "u-1")

    assert block.d == "focus block"
    rendered = render_plan(
        Plan(date=DAY, tz=TZ, blocks=[block]), set(), {"mdeadbeef0": TICKET_LABEL}
    )
    assert TICKET_URL not in rendered


async def test_re_writing_a_day_read_back_carries_the_url_exactly_once():
    """The defect this fix exists to close.

    Before ``tmbx.desc``, the url came back as part of the description, became
    part of ``Block.d``, and the next write that changed anything *else* about
    the block — a retime, a rename — sent it back with the url still inside
    and had a second copy appended. So this changes the summary and leaves the
    description exactly as the provider handed it over.
    """
    read_back, _args = await _round_trip(_linked_event())
    retimed = read_back.model_copy(
        update={"summary": "Deeper Work", "link_url": TICKET_URL}
    )
    adapter, caller = _make_adapter(
        {"update-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.update("primary", retimed)

    _name, args = caller.calls[0]
    assert args["description"] == f"focus block\n\n{TICKET_URL}"
    assert args["description"].count(TICKET_URL) == 1


async def test_writing_what_was_read_back_sends_byte_identical_arguments():
    """Reversible, so a re-commit is a no-op rather than a rewrite. If the
    projection lost or added anything, the second write would differ from the
    first and every linked block would churn on every commit."""
    read_back, first = await _round_trip(_linked_event())
    adapter, caller = _make_adapter(
        {"update-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.update(
        "primary", read_back.model_copy(update={"link_url": TICKET_URL})
    )

    _name, second = caller.calls[0]
    assert second["description"] == first["description"]
    assert second["extendedProperties"] == first["extendedProperties"]


async def test_an_event_written_before_this_change_still_reads_its_description():
    """No ``tmbx.desc`` — every event already on Hugo's calendar. The composed
    description is all there is, so that is what comes back rather than an
    empty field."""
    adapter, _caller = _make_adapter(
        {
            "list-events": [
                _text_result(
                    {
                        "events": [
                            {
                                "id": "tmb0abc123",
                                "summary": "Deep Work",
                                "description": "focus block",
                                "start": {"dateTime": "2026-08-17T10:00:00+02:00"},
                                "end": {"dateTime": "2026-08-17T12:00:00+02:00"},
                                "status": "confirmed",
                                "updated": "2026-08-17T08:00:00.000Z",
                                "extendedProperties": {
                                    "private": {"tmbx.uid": "u-1"}
                                },
                            }
                        ]
                    }
                )
            ]
        }
    )

    events = await adapter.list_day("primary", DAY, TZ)

    assert events[0].description == "focus block"


async def test_an_unlinked_empty_description_writes_no_property_and_reads_back_empty():
    """The easy half: no link, so nothing is composed in and the composed and
    authored descriptions are the same empty string either way. The hard half
    — a link *and* no description — is below, and it is the case that broke.

    "No property" now means the key sent empty rather than the key left out:
    the server merges this map, so absence has to be stated to be true."""
    read_back, args = await _round_trip(
        _linked_event(description="", link_id=None, link_url=None)
    )

    assert args["extendedProperties"]["private"]["tmbx.desc"] == ""
    assert read_back.description == ""


# ---------------------------------------------------------------------------
# a linked block with no description of its own
# ---------------------------------------------------------------------------


async def test_a_linked_block_with_no_description_reads_back_empty_not_the_url():
    """The ordinary case — an add carrying a link and no description.

    The composed description is the url alone, so a read that fell back to it
    would hand the url back as authored prose. It does not: ``tmbx.link`` can
    only have been written by this change, which always writes ``tmbx.desc``
    alongside it when there is a description, so link-without-desc means the
    authored description was empty. Two keys this system minted, no reading of
    any text.
    """
    read_back, args = await _round_trip(_linked_event(description=""))

    assert args["description"] == TICKET_URL
    assert args["extendedProperties"]["private"]["tmbx.desc"] == ""
    assert read_back.description == ""


async def test_a_linked_block_with_no_description_never_doubles_its_url():
    """What the fallback used to do on the second write: compose the url onto
    a description that was already the url, and freeze the pair into
    ``tmbx.desc`` as if a person had typed it."""
    read_back, _args = await _round_trip(_linked_event(description=""))
    retimed = read_back.model_copy(
        update={"summary": "Deeper Work", "link_url": TICKET_URL}
    )
    adapter, caller = _make_adapter(
        {"update-event": [_text_result({"event": _raw_event()})]}
    )

    await adapter.update("primary", retimed)

    _name, args = caller.calls[0]
    assert args["description"] == TICKET_URL
    assert args["description"].count(TICKET_URL) == 1
    assert args["extendedProperties"]["private"]["tmbx.desc"] == ""


async def test_a_linked_block_with_no_description_gives_a_block_with_no_description():
    read_back, _args = await _round_trip(_linked_event(description=""))

    assert _event_to_block(read_back, 0, "u-1").d == ""


# ---------------------------------------------------------------------------
# the property, and its limit, belong to a linked event only
# ---------------------------------------------------------------------------


async def test_an_unlinked_event_writes_no_desc_property_and_still_round_trips():
    """Nothing is composed into an unlinked event's description, so the
    provider's own field already is the authored text. Storing a second copy
    would buy nothing and cost a limit.

    The key still goes out, empty: on an event that carried a description
    before, that empty string is what clears the old one. Omitting it left the
    stale text in place and `_authored_description` preferred it to the truth."""
    read_back, args = await _round_trip(_linked_event(link_id=None, link_url=None))

    assert args["extendedProperties"]["private"]["tmbx.desc"] == ""
    assert read_back.description == "focus block"


async def test_a_long_description_on_an_unlinked_event_is_written_unrefused():
    """``Block.d`` has no length cap, and a day of long descriptions committed
    fine before links existed. It must keep committing."""
    long_description = "x" * (MAX_DESCRIPTION_CHARS + 1)
    _read_back, args = await _round_trip(
        _linked_event(description=long_description, link_id=None, link_url=None)
    )

    assert args["description"] == long_description


# ---------------------------------------------------------------------------
# the provider's own limit on a private property value
# ---------------------------------------------------------------------------


async def test_a_too_long_description_refuses_the_commit_before_anything_is_written(
    service, calendar, known
):
    """The refusal belongs before the event loop, not inside it.

    Raised from the adapter mid-loop it would escape ``commit`` past the
    journal: some events already written, the delete sweep skipped, and no row
    recording the attempt. The service knows every block's description and
    link, so it can refuse first — and journal it, like every other refusal.
    """
    _plan, snapshot = await service.read("primary", DAY)
    patch = Patch(
        ops=[
            UpdateBlock(
                h="DW1", link=known, d="x" * (MAX_DESCRIPTION_CHARS + 1)
            )
        ]
    )

    with pytest.raises(PlanViolation) as excinfo:
        await service.commit(snapshot, patch)

    assert excinfo.value.violation.kind is ViolationKind.DESCRIPTION_TOO_LONG
    assert [b.h for b in excinfo.value.violation.blocks] == ["DW1"]
    assert "DW1" in str(excinfo.value)
    assert str(MAX_DESCRIPTION_CHARS) in str(excinfo.value)
    assert calendar.created == []
    assert calendar.updated == []


async def test_a_too_long_description_is_journalled_like_every_other_refusal(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)
    patch = Patch(
        ops=[
            UpdateBlock(
                h="DW1", link=known, d="x" * (MAX_DESCRIPTION_CHARS + 1)
            )
        ]
    )

    with pytest.raises(PlanViolation):
        await service.commit(snapshot, patch)

    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].outcome is PatchOutcome.APPLY_FAILED
    assert "DW1" in (rows[-1].error or "")


async def test_a_forced_commit_cannot_write_past_a_too_long_description(
    service, known
):
    _plan, snapshot = await service.read("primary", DAY)
    patch = Patch(
        ops=[
            UpdateBlock(
                h="DW1", link=known, d="x" * (MAX_DESCRIPTION_CHARS + 1)
            )
        ]
    )

    with pytest.raises(PlanViolation):
        await service.commit(snapshot, patch, expect="force")


async def test_a_long_description_on_an_unlinked_block_still_commits(
    service, calendar
):
    """The regression Important 1 is about, seen from the service: a day that
    committed fine before links existed must keep committing."""
    _plan, snapshot = await service.read("primary", DAY)
    long_description = "x" * (MAX_DESCRIPTION_CHARS + 1)
    patch = Patch(ops=[UpdateBlock(h="DW1", d=long_description)])

    await service.commit(snapshot, patch)

    assert (await _stored(calendar, "DW1")).description == long_description


async def test_a_description_at_the_limit_with_a_link_commits(service, calendar, known):
    _plan, snapshot = await service.read("primary", DAY)
    at_limit = "x" * MAX_DESCRIPTION_CHARS
    patch = Patch(ops=[UpdateBlock(h="DW1", link=known, d=at_limit)])

    await service.commit(snapshot, patch)

    assert (await _stored(calendar, "DW1")).description == at_limit


async def test_a_description_over_the_limit_refuses_naming_the_handle_and_the_limit():
    """Loud, not truncated. Silently shortening it would lose what a person
    wrote and only show up the next time the day was read back."""
    adapter, _caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )
    too_long = "x" * (MAX_DESCRIPTION_CHARS + 1)

    with pytest.raises(ValueError) as excinfo:
        await adapter.create("primary", _linked_event(description=too_long))

    assert "DW1" in str(excinfo.value)
    assert str(MAX_DESCRIPTION_CHARS) in str(excinfo.value)


async def test_a_description_exactly_at_the_limit_is_written():
    """The boundary belongs on the allowed side — a refusal one character early
    is a refusal nobody can explain."""
    at_limit = "x" * MAX_DESCRIPTION_CHARS
    _read_back, args = await _round_trip(_linked_event(description=at_limit))

    assert args["extendedProperties"]["private"]["tmbx.desc"] == at_limit
