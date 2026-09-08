# tests/unit/tmbx/test_block_link.py
"""A block points at a material by handle, and an unknown handle is refused.

The handle is minted by the material store from a foreign id; a patch may
only name one the store already holds. Nothing here needs a model or a
network: the store is a temp SQLite file, exactly as the store's own tests
build it.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime

import pytest

from tmbx.calendar.fake import FakeCalendar
from tmbx.calendar.port import CalendarEvent
from tmbx.core.models import (
    ET,
    AfterPrev,
    Block,
    FixedWindow,
    Plan,
    PlanViolation,
    ViolationKind,
)
from tmbx.core.ops import AddBlock, Patch, UpdateBlock, apply_ops
from tmbx.core.render import COLUMNS, plan_rows, render_plan
from tmbx.journal.models import PatchOutcome
from tmbx.journal.store import JournalStore, init_journal
from tmbx.materials import MaterialStore
from tmbx.service import PlanService

DAY = date(2026, 8, 17)
TICKET = "1f2a3b4c-5d6e-7f80-9a0b-1c2d3e4f5061"
TICKET_URL = "https://www.notion.so/Verify-VPB-2024-aangifte-1f2a3b4c"
TICKET_LABEL = "Verify VPB 2024 aangifte"
UNKNOWN_HANDLE = "m0123456789"


def _event(eid, h, start_h, end_h, uid=None, block_type="M", timing_mode="fw"):
    return CalendarEvent(
        event_id=eid,
        summary=h,
        start=datetime(2026, 8, 17, start_h, 0),
        end=datetime(2026, 8, 17, end_h, 0),
        etag="v1",
        uid=uid or f"u-{eid}",
        handle=h,
        block_type=block_type,
        timing_mode=timing_mode,
    )


class CountingMaterialStore(MaterialStore):
    """A real store that counts its own lookups.

    A spy, not a stub: every query still hits the temp database, so what the
    tests assert about the rows is the store's own behaviour. Only the call
    count is added.
    """

    def __init__(self, sessionmaker) -> None:
        super().__init__(sessionmaker)
        self.get_many_calls = 0

    async def get_many(self, link_ids):
        self.get_many_calls += 1
        return await super().get_many(link_ids)


@pytest.fixture
async def materials(tmp_path):
    """A material store over a temp file. Empty until a test stores something."""
    return CountingMaterialStore(await init_journal(tmp_path / "j.db"))


@pytest.fixture
async def known(materials):
    """The handle of one stored ticket."""
    return await materials.put(
        source="notion", external_id=TICKET, url=TICKET_URL, label=TICKET_LABEL
    )


@pytest.fixture
async def service(tmp_path, materials):
    calendar = FakeCalendar(
        {"primary": [_event("e1", "PR1", 9, 10), _event("e2", "DW1", 10, 12)]}
    )
    store = JournalStore(await init_journal(tmp_path / "j.db"))
    counter = itertools.count(1)
    return PlanService(
        calendar,
        store,
        mint_uid=lambda: f"u-new-{next(counter)}",
        materials=materials,
    )


def _plan(*blocks: Block) -> Plan:
    return Plan(date=DAY, blocks=list(blocks))


def _linked_block(handle: str, link: str | None) -> Block:
    return Block(
        uid=f"u-{handle}",
        h=handle,
        n="Deep Work",
        t=ET.DW,
        p=FixedWindow(st="10:00", et="12:00"),
        anchor_source="user",
        link=link,
    )


# --- the field itself -------------------------------------------------


def test_a_block_carries_no_link_by_default():
    assert _linked_block("DW1", None).link is None


def test_a_block_round_trips_its_link_handle():
    block = _linked_block("DW1", "mdeadbeef0")

    assert Block.model_validate(block.model_dump()).link == "mdeadbeef0"


# --- merge semantics on an update -------------------------------------


def test_an_add_carries_its_link_onto_the_new_block():
    plan = _plan(_linked_block("DW1", None))
    patch = Patch(
        ops=[
            AddBlock(
                after="DW1",
                h="SW1",
                n="Shallow work",
                t=ET.SW,
                p=AfterPrev(dur="PT30M"),
                link="mdeadbeef0",
            )
        ]
    )

    patched = apply_ops(plan, patch, mint_uid=lambda: "u-new")

    assert patched.by_handle("SW1").link == "mdeadbeef0"


def test_an_update_sets_a_link_on_a_block_that_had_none():
    plan = _plan(_linked_block("DW1", None))
    patch = Patch(ops=[UpdateBlock(h="DW1", link="mdeadbeef0")])

    patched = apply_ops(plan, patch, mint_uid=lambda: "u-new")

    assert patched.by_handle("DW1").link == "mdeadbeef0"


def test_an_update_changes_a_link():
    plan = _plan(_linked_block("DW1", "mdeadbeef0"))
    patch = Patch(ops=[UpdateBlock(h="DW1", link="mfeedface1")])

    patched = apply_ops(plan, patch, mint_uid=lambda: "u-new")

    assert patched.by_handle("DW1").link == "mfeedface1"


def test_an_explicit_null_clears_a_link():
    plan = _plan(_linked_block("DW1", "mdeadbeef0"))
    patch = Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "link": None}]})

    patched = apply_ops(plan, patch, mint_uid=lambda: "u-new")

    assert patched.by_handle("DW1").link is None


def test_an_omitted_link_leaves_the_existing_one_untouched():
    plan = _plan(_linked_block("DW1", "mdeadbeef0"))
    patch = Patch.model_validate(
        {"ops": [{"op": "update", "h": "DW1", "n": "Deeper work"}]}
    )

    patched = apply_ops(plan, patch, mint_uid=lambda: "u-new")

    assert patched.by_handle("DW1").n == "Deeper work"
    assert patched.by_handle("DW1").link == "mdeadbeef0"


# --- the store is asked before anything is written --------------------


async def test_an_add_naming_a_known_handle_applies_and_round_trips_it(
    service, known
):
    _plan_read, snapshot = await service.read("primary", DAY)
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

    result = await service.apply(snapshot, patch)

    assert result.plan.by_handle("SW1").link == known


async def test_an_add_naming_an_unknown_handle_is_refused_with_the_handle_named(
    service,
):
    _plan_read, snapshot = await service.read("primary", DAY)
    patch = Patch(
        ops=[
            AddBlock(
                after="DW1",
                h="SW1",
                n="Shallow work",
                t=ET.SW,
                p=AfterPrev(dur="PT30M"),
                link=UNKNOWN_HANDLE,
            )
        ]
    )

    with pytest.raises(PlanViolation) as excinfo:
        await service.apply(snapshot, patch)

    assert UNKNOWN_HANDLE in str(excinfo.value)
    assert excinfo.value.violation.kind is ViolationKind.UNKNOWN_LINK
    assert [b.h for b in excinfo.value.violation.blocks] == ["SW1"]


async def test_an_update_naming_an_unknown_handle_is_refused(service):
    _plan_read, snapshot = await service.read("primary", DAY)
    patch = Patch(ops=[UpdateBlock(h="DW1", link=UNKNOWN_HANDLE)])

    with pytest.raises(PlanViolation) as excinfo:
        await service.apply(snapshot, patch)

    assert UNKNOWN_HANDLE in str(excinfo.value)


async def test_a_commit_naming_an_unknown_handle_writes_nothing(service):
    _plan_read, snapshot = await service.read("primary", DAY)
    before = await service.calendar.list_day("primary", DAY, snapshot.tz)
    patch = Patch(ops=[UpdateBlock(h="DW1", link=UNKNOWN_HANDLE)])

    with pytest.raises(PlanViolation) as excinfo:
        await service.commit(snapshot, patch)

    assert UNKNOWN_HANDLE in str(excinfo.value)
    after = await service.calendar.list_day("primary", DAY, snapshot.tz)
    assert [(e.event_id, e.etag) for e in after] == [
        (e.event_id, e.etag) for e in before
    ]
    # The row is the only record of the attempt that outlives the session.
    rows = await service.store.by_day("primary", DAY)
    assert rows[-1].outcome is PatchOutcome.APPLY_FAILED
    assert UNKNOWN_HANDLE in (rows[-1].error or "")


async def test_a_forced_commit_cannot_write_past_an_unknown_handle(service):
    """``expect="force"`` overrides a drift or a day that does not fit. It
    cannot conjure a material nobody stored — there is no url to write."""
    _plan_read, snapshot = await service.read("primary", DAY)
    patch = Patch(ops=[UpdateBlock(h="DW1", link=UNKNOWN_HANDLE)])

    with pytest.raises(PlanViolation):
        await service.commit(snapshot, patch, expect="force")


async def test_one_patch_naming_one_handle_on_two_blocks_costs_one_lookup(
    service, materials, known
):
    _plan_read, snapshot = await service.read("primary", DAY)
    materials.get_many_calls = 0
    patch = Patch(
        ops=[
            UpdateBlock(h="DW1", link=known),
            AddBlock(
                after="DW1",
                h="SW1",
                n="Shallow work",
                t=ET.SW,
                p=AfterPrev(dur="PT30M"),
                link=known,
            ),
        ]
    )

    result = await service.apply(snapshot, patch)

    assert result.plan.by_handle("DW1").link == known
    assert result.plan.by_handle("SW1").link == known
    assert materials.get_many_calls == 1


async def test_clearing_a_link_asks_the_store_for_nothing(service, materials):
    """A null names no handle, so there is nothing to verify."""
    _plan_read, snapshot = await service.read("primary", DAY)
    patch = Patch.model_validate({"ops": [{"op": "update", "h": "DW1", "link": None}]})

    await service.apply(snapshot, patch)

    assert materials.get_many_calls == 0


# --- what the planner is shown ----------------------------------------


def test_the_rendered_table_names_the_link_and_its_label():
    assert COLUMNS[-2:] == ("link", "link_label")


def test_a_rendered_block_shows_its_handle_and_label_and_never_a_url():
    plan = _plan(_linked_block("DW1", "mdeadbeef0"))

    rendered = render_plan(plan, link_labels={"mdeadbeef0": TICKET_LABEL})

    assert "mdeadbeef0" in rendered
    assert TICKET_LABEL in rendered
    assert TICKET_URL not in rendered


def test_an_unlinked_block_renders_empty_link_columns():
    plan = _plan(_linked_block("DW1", None))

    row = plan_rows(plan)[0]

    assert (row["link"], row["link_label"]) == ("", "")


async def test_the_preview_shows_the_label_and_no_url(service, known):
    _plan_read, snapshot = await service.read("primary", DAY)
    patch = Patch(ops=[UpdateBlock(h="DW1", link=known)])

    result = await service.apply(snapshot, patch)

    assert known in result.rendered
    assert TICKET_LABEL in result.rendered
    assert TICKET_URL not in result.rendered
    row = next(r for r in result.rows if r["h"] == "DW1")
    assert (row["link"], row["link_label"]) == (known, TICKET_LABEL)


async def test_a_read_of_an_unlinked_day_asks_the_store_for_nothing(
    service, materials
):
    """``plan_read`` resolves labels through the store, but a day carrying no
    handles has nothing to resolve and must not pay for a query."""
    await service.read_rendered("primary", DAY)

    assert materials.get_many_calls == 0


# --- the store the service uses by default ----------------------------


async def test_the_service_defaults_to_a_store_over_the_journal_database(tmp_path):
    """No ``materials`` argument: the material store shares the journal's own
    database, which is where ``init_journal`` created the table."""
    sessionmaker = await init_journal(tmp_path / "j.db")
    handle = await MaterialStore(sessionmaker).put(
        source="notion", external_id=TICKET, url=TICKET_URL, label=TICKET_LABEL
    )
    calendar = FakeCalendar({"primary": [_event("e1", "DW1", 10, 12)]})
    service = PlanService(calendar, JournalStore(sessionmaker))
    _plan_read, snapshot = await service.read("primary", DAY)

    result = await service.apply(snapshot, Patch(ops=[UpdateBlock(h="DW1", link=handle)]))

    assert result.plan.by_handle("DW1").link == handle
    assert TICKET_LABEL in result.rendered
