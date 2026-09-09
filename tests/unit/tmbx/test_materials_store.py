# tests/unit/tmbx/test_materials_store.py
"""The material store: one handle per foreign id, minted and idempotent."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tmbx.journal.models import Material
from tmbx.journal.store import init_journal
from tmbx.materials import MaterialStore, mint_link_id

TICKET = "1f2a3b4c-5d6e-7f80-9a0b-1c2d3e4f5061"
OTHER_TICKET = "90ab1234-cd56-ef78-9012-3456789abcde"


@pytest.fixture
async def store(tmp_path):
    sessionmaker = await init_journal(tmp_path / "j.db")
    return MaterialStore(sessionmaker)


def test_the_handle_is_the_prefix_plus_ten_hex_characters():
    link_id = mint_link_id("notion", TICKET)

    assert link_id[0] == "m"
    assert len(link_id) == 11
    # A hex body, asserted by parsing rather than by inspecting characters.
    assert len(bytes.fromhex(link_id[1:])) == 5


def test_the_same_pair_mints_the_same_handle():
    assert mint_link_id("notion", TICKET) == mint_link_id("notion", TICKET)


def test_a_different_external_id_mints_a_different_handle():
    assert mint_link_id("notion", TICKET) != mint_link_id("notion", OTHER_TICKET)


def test_the_same_external_id_under_a_different_source_is_a_different_handle():
    assert mint_link_id("notion", TICKET) != mint_link_id("ticktick", TICKET)


def test_minting_without_an_external_id_is_loud():
    with pytest.raises(ValueError):
        mint_link_id("notion", "")


def test_minting_without_a_source_is_loud():
    with pytest.raises(ValueError):
        mint_link_id("", TICKET)


def test_first_seen_defaults_to_an_aware_utc_instant():
    material = Material(
        link_id=mint_link_id("notion", TICKET),
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket",
        label="Ship the link field",
    )

    assert material.first_seen.tzinfo is not None
    assert material.first_seen.utcoffset() == timedelta(0)


async def test_put_returns_the_minted_handle_and_the_row_round_trips(store):
    link_id = await store.put(
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket",
        label="Ship the link field",
    )

    assert link_id == mint_link_id("notion", TICKET)

    loaded = await store.get(link_id)
    assert loaded is not None
    assert loaded.source == "notion"
    assert loaded.external_id == TICKET
    assert loaded.url == "https://notion.so/ticket"
    assert loaded.label == "Ship the link field"
    assert loaded.first_seen is not None


async def test_re_put_updates_url_and_label_and_keeps_first_seen(store):
    link_id = await store.put(
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket",
        label="Ship the link field",
    )
    first = await store.get(link_id)
    assert first is not None
    first_seen = first.first_seen

    # Wide enough that a re-minted timestamp could not compare equal.
    await asyncio.sleep(0.01)

    again = await store.put(
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket-renamed",
        label="Ship the link field on blocks",
    )

    assert again == link_id
    updated = await store.get(link_id)
    assert updated is not None
    assert updated.url == "https://notion.so/ticket-renamed"
    assert updated.label == "Ship the link field on blocks"
    assert updated.first_seen == first_seen


async def test_re_put_does_not_add_a_second_row(store):
    for label in ("Ship the link field", "Ship the link field on blocks"):
        await store.put(
            source="notion",
            external_id=TICKET,
            url="https://notion.so/ticket",
            label=label,
        )

    rows = await store.get_many([mint_link_id("notion", TICKET)])
    assert len(rows) == 1


async def test_get_on_an_unknown_handle_returns_none(store):
    assert await store.get(mint_link_id("notion", OTHER_TICKET)) is None


async def test_get_many_returns_only_the_handles_that_exist(store):
    wanted = await store.put(
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket",
        label="Ship the link field",
    )
    # Stored, but not asked for — a store-wide read would wrongly return it.
    unwanted = await store.put(
        source="notion",
        external_id=OTHER_TICKET,
        url="https://notion.so/other",
        label="Something else entirely",
    )
    unknown = mint_link_id("notion", "88888888-4444-4444-4444-cccccccccccc")

    found = await store.get_many([wanted, unknown])

    assert set(found) == {wanted}
    assert unwanted not in found
    assert found[wanted].label == "Ship the link field"


async def test_get_many_of_nothing_is_an_empty_mapping(store):
    # A stored row makes this non-vacuous: asking for nothing is not "give me
    # everything".
    await store.put(
        source="notion",
        external_id=TICKET,
        url="https://notion.so/ticket",
        label="Ship the link field",
    )

    assert await store.get_many([]) == {}
