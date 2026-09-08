"""Durable timeboxing constraints served from the memory server's own store.

The `constraint_mcp` backend reads Hugo's preferences out of a Notion page that
404s, so every durable prefetch has failed for as long as anyone has looked --
loudly in the log, silently in Slack, with the flow carrying on from whatever
the current thread had extracted. These tests cover the replacement, whose one
job is to make the real corpus reachable from the same contract.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fateforger.agents.timeboxing.kg_constraint_client import KGConstraintMemoryClient
from memory.constraint import Constraint, Necessity, Scope, Source, Status
from memory.models import Tier
from memory.constraint_store import ConstraintStore


def _store_with(tmp_path, *constraints) -> str:
    db = tmp_path / "memory.db"
    store = ConstraintStore(str(db))
    for constraint in constraints:
        store.upsert(constraint)
    return str(db)


def _constraint(**overrides) -> Constraint:
    now = datetime.now(timezone.utc)
    base = dict(
        name="Work start time",
        description="Work starts at 09:00.",
        necessity=Necessity.MUST,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        created_at=now,
        last_observed_at=now,
    )
    base.update(overrides)
    return Constraint(**base)


# -- refusing to open the wrong thing ------------------------------------


def test_a_relative_path_is_refused(tmp_path, monkeypatch):
    """memory's own default is relative and resolves against the caller's cwd.

    That quietly opens an empty store, which is indistinguishable from a user
    who has never stated a rule -- and a plan seeded from nothing looks exactly
    like a plan seeded from preferences.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute"):
        KGConstraintMemoryClient("data/memory.db")


def test_a_missing_store_is_refused_rather_than_created(tmp_path):
    """sqlite would happily create an empty file and answer every read with nothing."""
    with pytest.raises(FileNotFoundError):
        KGConstraintMemoryClient(str(tmp_path / "not-here.db"))


# -- reading --------------------------------------------------------------


async def test_it_serves_the_constraints_the_store_holds(tmp_path):
    db = _store_with(tmp_path, _constraint(), _constraint(name="Commute duration"))
    client = KGConstraintMemoryClient(db)

    rows = await client.query_constraints(filters={"planned_day": "2026-08-24"})
    assert {row["name"] for row in rows} == {"Work start time", "Commute duration"}


async def test_the_enum_values_pass_straight_through(tmp_path):
    """The two vocabularies already agree; a translation table would be an
    opinion about equivalence that the types already state."""
    db = _store_with(tmp_path, _constraint())
    rows = await KGConstraintMemoryClient(db).query_constraints(filters={})

    row = rows[0]
    assert row["necessity"] == "must"
    assert row["scope"] == "profile"
    assert row["status"] == "proposed"
    assert row["source"] == "user"


async def test_a_row_carries_the_uid_reconciliation_dedupes_on(tmp_path):
    constraint = _constraint()
    db = _store_with(tmp_path, constraint)
    rows = await KGConstraintMemoryClient(db).query_constraints(filters={})
    assert rows[0]["uid"] == constraint.uid


async def test_applicability_is_left_empty_on_purpose(tmp_path):
    """The store already filtered to the day.

    Restating a window here would give the downstream filter a second chance to
    disagree with the store about the same question.
    """
    db = _store_with(tmp_path, _constraint())
    rows = await KGConstraintMemoryClient(db).query_constraints(filters={})
    assert rows[0]["days_of_week"] == []


async def test_a_bad_day_string_does_not_take_the_read_down(tmp_path):
    """A planning loop must not fail because a date arrived malformed."""
    db = _store_with(tmp_path, _constraint())
    rows = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": "not-a-date"}
    )
    assert len(rows) == 1


async def test_the_limit_is_honoured(tmp_path):
    db = _store_with(tmp_path, *[_constraint(name=f"rule {i}") for i in range(5)])
    rows = await KGConstraintMemoryClient(db).query_constraints(filters={}, limit=2)
    assert len(rows) == 2


async def test_store_info_names_the_backend_and_admits_it_cannot_write(tmp_path):
    db = _store_with(tmp_path, _constraint())
    info = await KGConstraintMemoryClient(db).get_store_info()
    assert info["backend"] == "memory_kg"
    assert info["constraint_count"] == 1
    assert info["writable"] is False


async def test_there_is_no_type_taxonomy_and_it_says_so(tmp_path):
    """anchor_edges is deliberately unpopulated -- inducing one is a gated
    taxonomy change (#140), so empty is the honest answer, not a defect."""
    db = _store_with(tmp_path, _constraint())
    assert await KGConstraintMemoryClient(db).query_types(stage="Refine") == []


# -- writing --------------------------------------------------------------


async def test_writing_is_refused_rather_than_silently_dropped(tmp_path):
    """A rule written straight to L2 has no provenance and can never be
    re-derived when a judgement improves. A no-op return would let a caller
    believe it had persisted one."""
    db = _store_with(tmp_path, _constraint())
    with pytest.raises(NotImplementedError, match="memory_observe"):
        await KGConstraintMemoryClient(db).upsert_constraint(record={"name": "x"})


# -- the seam the agent uses ---------------------------------------------


async def test_it_satisfies_the_contract_the_agent_adapts(tmp_path):
    """`build_durable_constraint_store` adapts any object exposing these four,
    which is what lets this land without touching the orchestration."""
    from fateforger.agents.timeboxing.durable_constraint_store import (
        build_durable_constraint_store,
    )

    db = _store_with(tmp_path, _constraint())
    store = build_durable_constraint_store(KGConstraintMemoryClient(db))
    rows = await store.query_constraints(filters={}, limit=10)
    assert rows and rows[0]["name"] == "Work start time"


def test_the_rows_survive_the_reconciliation_the_agent_runs(tmp_path):
    """The real integration risk: rows shaped wrongly are dropped in silence.

    `reconcile_constraint_rows` is what the agent puts the prefetch through, and
    a row it cannot read disappears without an error -- the same failure mode
    the Notion backend had, arriving from a different direction.
    """
    import asyncio

    from fateforger.agents.timeboxing.constraint_reconciliation import (
        reconcile_constraint_rows,
    )

    db = _store_with(tmp_path, _constraint(), _constraint(name="Commute duration"))
    rows = asyncio.run(KGConstraintMemoryClient(db).query_constraints(filters={}))

    result = reconcile_constraint_rows(
        rows=rows, planned_day=date(2026, 8, 24), stage="Refine"
    )
    assert result.raw_count == 2
    assert result.canonical_count == 2
    # The one that matters: they must still be applicable after reconciliation.
    assert result.applicable_count == 2


# -- anchors and suspension -------------------------------------------------


from memory.anchor import Anchor
from memory.anchor_store import AnchorStore


async def test_rows_carry_anchor_uid_and_name(tmp_path) -> None:
    """The card groups by anchor name and steers by rule uid, so both travel."""
    rule = _constraint(name="Oats before gym")
    db = _store_with(tmp_path, rule)
    anchors = AnchorStore(db)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [row] = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": date(2026, 9, 8).isoformat()}
    )

    assert row["anchors"] == [{"uid": gym.uid, "name": "gym"}]
    assert "fade" in row


async def test_suspended_rules_are_counted_not_listed(tmp_path) -> None:
    """On a vacation day every working rule is suspended; the panel shows a count."""
    from memory.constraint import Applicability

    working = _constraint(name="Commute", applicability=Applicability(day_types=["working"]))
    db = _store_with(tmp_path, working)

    client = KGConstraintMemoryClient(db)
    assert await client.count_suspended(date(2026, 9, 9).isoformat(), "vacation") == 1
    assert await client.count_suspended(date(2026, 9, 8).isoformat(), "working") == 0


async def test_an_unanchored_rule_has_an_empty_anchor_list(tmp_path) -> None:
    db = _store_with(tmp_path, _constraint(name="Plan at 17:00"))

    [row] = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": date(2026, 9, 8).isoformat()}
    )

    assert row["anchors"] == []


async def test_rows_carry_how_the_rule_applies(tmp_path) -> None:
    from memory.constraint import Applicability

    db = _store_with(tmp_path, _constraint(name="Commute", applicability=Applicability(day_types=["working"])))

    [row] = await KGConstraintMemoryClient(db).query_constraints(
        filters={"planned_day": date(2026, 9, 8).isoformat(), "day_type": "working"}
    )

    assert row["applies"] == "some_days"
