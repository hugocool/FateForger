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
from tmbx.calendar.gcal import MAX_PRIVATE_VALUE_CHARS, GoogleCalendarAdapter
from tmbx.calendar.port import CalendarEvent
from tmbx.core.models import ET, AfterPrev, PlanViolation, ViolationKind
from tmbx.core.models import Plan
from tmbx.core.ops import AddBlock, Patch, UpdateBlock
from tmbx.core.render import render_plan
from tmbx.journal.models import PatchOutcome
from tmbx.journal.store import JournalStore, init_journal
from tmbx.materials import MaterialStore
from tmbx.service import PlanService, _event_to_block

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
    """

    def __init__(self, events: dict[str, list[CalendarEvent]] | None = None) -> None:
        super().__init__(events)
        self.created: list[CalendarEvent] = []
        self.updated: list[CalendarEvent] = []

    async def create(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        self.created.append(event.model_copy(deep=True))
        return await super().create(calendar_id, event)

    async def update(self, calendar_id: str, event: CalendarEvent) -> CalendarEvent:
        self.updated.append(event.model_copy(deep=True))
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
    events = await calendar.list_day("primary", DAY, TZ)
    return next(event for event in events if event.handle == handle)


# ---------------------------------------------------------------------------
# the port carries both halves
# ---------------------------------------------------------------------------


def test_a_calendar_event_carries_a_link_handle_and_url():
    event = _event("e1", "DW1", 10, 12, link_id="mdeadbeef0", link_url=TICKET_URL)

    assert (event.link_id, event.link_url) == ("mdeadbeef0", TICKET_URL)


def test_a_calendar_event_carries_neither_by_default():
    event = _event("e1", "DW1", 10, 12)

    assert (event.link_id, event.link_url) == (None, None)


async def test_the_fake_calendar_round_trips_both_halves(calendar):
    """``FakeCalendar`` stores the model itself, so it carries whatever the
    port carries — asserted through a real create/list rather than assumed,
    because every service-level test below reads its evidence back out of
    it. A fake that dropped these fields would make all of them vacuous."""
    await calendar.create(
        "primary",
        _event("e9", "X1", 13, 14, link_id="mdeadbeef0", link_url=TICKET_URL),
    )

    stored = await _stored(calendar, "X1")
    assert (stored.link_id, stored.link_url) == ("mdeadbeef0", TICKET_URL)


# ---------------------------------------------------------------------------
# what a commit puts on the event
# ---------------------------------------------------------------------------


async def test_committing_a_linked_block_puts_the_handle_and_the_url_on_the_event(
    service, calendar, known
):
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    written = await _stored(calendar, "DW1")
    assert written.link_id == known
    assert written.link_url == TICKET_URL


async def test_the_url_comes_from_the_store_not_from_the_patch(
    service, calendar, known
):
    """The op names a handle and nothing else. The url on the event is the
    store's, which is the whole point of storing links by handle."""
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert (await _stored(calendar, "DW1")).link_url == TICKET_URL


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

    created = await _stored(calendar, "SW1")
    assert (created.link_id, created.link_url) == (known, TICKET_URL)


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

    cleared = await _stored(calendar, "DW1")
    assert (cleared.link_id, cleared.link_url) == (None, None)


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
    assert (await _stored(calendar, "DW1")).link_url == OTHER_URL


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
    """The provider-shaped case the fake would otherwise hide.

    A real adapter puts the url in the description and reads only the handle
    back (``gcal._event_from_payload``), so every event fetched from Google
    carries ``link_id`` and ``link_url=None``. If the no-op check compared
    ``link_url`` too, every linked block would be rewritten on every commit
    forever — an etag bump and a change notification per block, per day.
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
                    link_url=None,
                ),
            ]
        }
    )
    service = await _service(tmp_path, materials, calendar)
    _plan, snapshot = await service.read("primary", DAY)

    await service.commit(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=known)]))

    assert calendar.updated == []


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
    assert "tmbx.link" not in args["extendedProperties"]["private"]
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


async def test_an_empty_description_writes_no_property_and_reads_back_empty():
    read_back, args = await _round_trip(
        _linked_event(description="", link_id=None, link_url=None)
    )

    assert "tmbx.desc" not in args["extendedProperties"]["private"]
    assert read_back.description == ""


# ---------------------------------------------------------------------------
# the provider's own limit on a private property value
# ---------------------------------------------------------------------------


async def test_a_description_over_the_limit_refuses_naming_the_handle_and_the_limit():
    """Loud, not truncated. Silently shortening it would lose what a person
    wrote and only show up the next time the day was read back."""
    adapter, _caller = _make_adapter(
        {"create-event": [_text_result({"event": _raw_event()})]}
    )
    too_long = "x" * (MAX_PRIVATE_VALUE_CHARS + 1)

    with pytest.raises(ValueError) as excinfo:
        await adapter.create("primary", _linked_event(description=too_long))

    assert "DW1" in str(excinfo.value)
    assert str(MAX_PRIVATE_VALUE_CHARS) in str(excinfo.value)


async def test_a_description_exactly_at_the_limit_is_written():
    """The boundary belongs on the allowed side — a refusal one character early
    is a refusal nobody can explain."""
    at_limit = "x" * MAX_PRIVATE_VALUE_CHARS
    _read_back, args = await _round_trip(_linked_event(description=at_limit))

    assert args["extendedProperties"]["private"]["tmbx.desc"] == at_limit
