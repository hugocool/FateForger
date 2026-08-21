# tests/memory/test_anchor_graph.py
"""The anchor graph: identity, traversal, and semantic relevance (#137, #141).

What this closes: get_active_constraints returned every applicable rule,
because relevance needed a graph that did not exist. Measured earlier, that is
~34 genuinely standing rules on any given day — a flood that decay (1
constraint) and applicability (2) could not meaningfully reduce, because it is
structural rather than stale.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import pytest

from memory.anchor import Anchor, EdgeKind
from memory.anchor_store import AnchorStore
from memory.anchoring import resolve_anchors
from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.constraint_store import ConstraintStore
from memory.judge import StubJudge
from memory.models import Channel, DecayClass, Tier
from memory.service import MemoryService

DAY = date(2026, 8, 20)
WHEN = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)


def _constraint(store, name, uid_hint=None):
    constraint = Constraint(
        name=name,
        description=name,
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        applicability=Applicability(),
        source_observation_uids=[],
        created_at=WHEN,
        decay_class=DecayClass.PERMANENT,
        last_observed_at=WHEN,
    )
    store.upsert(constraint)
    return constraint


# ── traversal ──────────────────────────────────────────────────────────────


def test_a_rule_stated_generally_reaches_a_specific_activity(tmp_path):
    """The map's destination case, in miniature.

    "Oats two hours before sport" must surface on a day containing hockey,
    without anyone restating it and without a model in the read path.
    """
    store = AnchorStore(str(tmp_path / "a.db"))
    sport, hockey = Anchor(name="sport"), Anchor(name="hockey")
    for anchor in (sport, hockey):
        store.upsert(anchor)
    store.add_edge(sport.uid, hockey.uid, EdgeKind.IS_A)
    store.replace_constraint_links("c-oats", [sport.uid])

    assert store.constraints_reachable_from([hockey.uid]) == {"c-oats"}


def test_an_unrelated_anchor_reaches_nothing(tmp_path):
    """The half that makes it a filter rather than a formality."""
    store = AnchorStore(str(tmp_path / "a.db"))
    sport, hockey, admin = (
        Anchor(name="sport"),
        Anchor(name="hockey"),
        Anchor(name="admin"),
    )
    for anchor in (sport, hockey, admin):
        store.upsert(anchor)
    store.add_edge(sport.uid, hockey.uid, EdgeKind.IS_A)
    store.replace_constraint_links("c-oats", [sport.uid])

    assert store.constraints_reachable_from([admin.uid]) == set()


def test_the_walk_is_depth_bounded_and_survives_a_cycle(tmp_path):
    """No gate exists yet to stop a bad induction writing a cycle (#140).

    The bound lives in the query rather than in the caller, so a cycle costs a
    few wasted hops instead of hanging a planning loop.
    """
    store = AnchorStore(str(tmp_path / "a.db"))
    a, b = Anchor(name="a"), Anchor(name="b")
    for anchor in (a, b):
        store.upsert(anchor)
    store.add_edge(a.uid, b.uid, EdgeKind.IS_A)
    store.add_edge(b.uid, a.uid, EdgeKind.IS_A)   # cycle
    store.replace_constraint_links("c", [a.uid])

    assert store.constraints_reachable_from([b.uid]) == {"c"}


def test_an_edge_into_an_unknown_anchor_is_refused(tmp_path):
    store = AnchorStore(str(tmp_path / "a.db"))
    real = Anchor(name="sport")
    store.upsert(real)
    with pytest.raises(ValueError, match="not a known anchor"):
        store.add_edge(real.uid, "deadbeef" * 4, EdgeKind.IS_A)


def test_a_self_edge_is_refused(tmp_path):
    store = AnchorStore(str(tmp_path / "a.db"))
    anchor = Anchor(name="sport")
    store.upsert(anchor)
    with pytest.raises(ValueError, match="self-edge"):
        store.add_edge(anchor.uid, anchor.uid, EdgeKind.IS_A)


def test_constraint_links_are_replaced_not_appended(tmp_path):
    """Re-projection can drop an anchor as well as add one; an add-only table
    would keep serving a rule from an anchor it no longer belongs to."""
    store = AnchorStore(str(tmp_path / "a.db"))
    first, second = Anchor(name="gym"), Anchor(name="admin")
    for anchor in (first, second):
        store.upsert(anchor)
    store.replace_constraint_links("c", [first.uid])
    store.replace_constraint_links("c", [second.uid])

    assert store.anchors_for("c") == [second.uid]
    assert store.constraints_reachable_from([first.uid]) == set()


# ── identity ───────────────────────────────────────────────────────────────


async def test_three_spellings_of_one_anchor_resolve_to_one_identity(tmp_path):
    """The judgement CLAUDE.md reserves for a model.

    Jaccard merged `Work Window` with `Deep Work Block Duration` on this
    project's own data. String comparison cannot do this job; the stub stands
    in for the model that can.
    """
    store = AnchorStore(str(tmp_path / "a.db"))
    judge = StubJudge()
    first = await resolve_anchors(["gym"], store, judge)

    judge = StubJudge(anchor_uids={"the gym": first[0], "gym session": first[0]})
    again = await resolve_anchors(["the gym", "gym session"], store, judge)

    assert again == [first[0], first[0]]
    assert len(store.all()) == 1


async def test_a_hallucinated_anchor_uid_raises(tmp_path):
    """Never act on a model-supplied identifier.

    An invented uid attaches the rule to nothing, and a walk that traverses
    nothing is indistinguishable from a rule that legitimately does not apply.
    """
    store = AnchorStore(str(tmp_path / "a.db"))
    judge = StubJudge(anchor_uids={"gym": "deadbeef" * 4})
    with pytest.raises(ValueError, match="unknown anchor_uid"):
        await resolve_anchors(["gym"], store, judge)


async def test_concurrent_observations_do_not_mint_the_anchor_twice(tmp_path):
    """The span reads known anchors, asks a model, then writes.

    Without the lock two observations mentioning the same new anchor are each
    told "this is new" and each mint one — duplicated identity in the layer
    whose whole job is to unify it.
    """
    store = AnchorStore(str(tmp_path / "a.db"))

    class SlowJudge(StubJudge):
        async def resolve_anchors(self, names, candidates):
            await asyncio.sleep(0.01)          # widen the window
            known = {c.name: c.uid for c in candidates}
            from memory.judge import AnchorResolution, AnchorResolutions

            return AnchorResolutions(
                resolutions=[
                    AnchorResolution(name=n, anchor_uid=known.get(n)) for n in names
                ]
            )

    judge = SlowJudge()
    await asyncio.gather(
        resolve_anchors(["gym"], store, judge),
        resolve_anchors(["gym"], store, judge),
    )

    assert len(store.all()) == 1


# ── end to end ─────────────────────────────────────────────────────────────


async def test_relevance_narrows_the_flood_without_a_model(tmp_path):
    """What the graph is for.

    Three standing rules, one day. Without anchors every rule comes back —
    which is the measured behaviour on the real store, ~34 of them. With the
    day's anchors, only what bears on the day does.
    """
    db = str(tmp_path / "m.db")
    judge = StubJudge(
        tiers={
            "eat oats two hours before sport": Tier.DURABLE,
            "no meetings on gym days": Tier.DURABLE,
            "invoices go out on the last friday": Tier.DURABLE,
        },
        anchors={
            "eat oats two hours before sport": ["sport"],
            "no meetings on gym days": ["gym"],
            "invoices go out on the last friday": ["invoicing"],
        },
    )
    service = MemoryService(db, judge)
    for text in judge._tiers:
        await service.observe(
            text, channel=Channel.PLANNING, session_id=text, observed_at=WHEN
        )

    assert len(service.get_active_constraints(DAY)) == 3   # today's flood

    # The taxonomy: hockey is a sport. One edge, inserted directly — inducing
    # it is a gated taxonomy change (#140), not this ticket's job.
    by_name = {a.name: a.uid for a in service._anchors.all()}
    hockey = Anchor(name="hockey")
    service._anchors.upsert(hockey)
    service._anchors.add_edge(by_name["sport"], hockey.uid, EdgeKind.IS_A)

    narrowed = service.get_active_constraints(DAY, anchor_uids=[hockey.uid])

    assert [v.name for v in narrowed] == ["eat oats two hours before sport"]


async def test_an_unanchored_rule_is_always_returned(tmp_path):
    """Unreachable and unanchored are different.

    A rule about the shape of the day rather than a thing in it has no anchor,
    and every rule stored before the graph existed has none either. Dropping
    them on a walk would silently discard the whole legacy store.
    """
    db = str(tmp_path / "m.db")
    judge = StubJudge(
        tiers={"keep the calendar tidy": Tier.DURABLE},   # no anchors extracted
    )
    service = MemoryService(db, judge)
    await service.observe(
        "keep the calendar tidy",
        channel=Channel.PLANNING,
        session_id="s",
        observed_at=WHEN,
    )
    anchor = Anchor(name="hockey")
    service._anchors.upsert(anchor)

    views = service.get_active_constraints(DAY, anchor_uids=[anchor.uid])

    assert [v.name for v in views] == ["keep the calendar tidy"]


async def test_narrowing_never_reaches_a_model(tmp_path):
    """Callers hold this inside a planning loop.

    A model here would buy them the host's latency and make the same day, read
    twice, answer differently. The AST guard covers read_api; this covers the
    service method that does the walk.
    """
    db = str(tmp_path / "m.db")
    judge = StubJudge(
        tiers={"eat oats before sport": Tier.DURABLE},
        anchors={"eat oats before sport": ["sport"]},
    )
    service = MemoryService(db, judge)
    await service.observe(
        "eat oats before sport",
        channel=Channel.PLANNING,
        session_id="s",
        observed_at=WHEN,
    )
    uid = service._anchors.all()[0].uid

    class Exploding(StubJudge):
        async def resolve_anchors(self, names, candidates):
            raise AssertionError("the read path must not sample")

        async def tier(self, observation):
            raise AssertionError("the read path must not sample")

    service._judge = Exploding()
    assert len(service.get_active_constraints(DAY, anchor_uids=[uid])) == 1


def test_the_walk_drives_from_the_reachable_set_not_the_edge_table(tmp_path):
    """Guards a keyword no behavioural test can protect.

    CROSS JOIN and JOIN return identical results here, so every other test
    passes either way — while the plain form is linear in total store size:
    85 ms against 0.43 ms at 100x the real store, because SQLite has no
    cardinality estimate for a recursive co-routine, assumes it is large, and
    scans the whole edge table to probe a handful of anchors.

    Asserting the plan rather than a timing, because a timing threshold on CI
    is a flake. The strings matched are SQLite's own plan output and the
    index name this module created — system-minted identifiers, not user
    content.
    """
    import json

    from memory.anchor_store import _WALK

    store = AnchorStore(str(tmp_path / "a.db"))
    anchor = Anchor(name="sport")
    store.upsert(anchor)
    store.replace_constraint_links("c", [anchor.uid])

    plan = "\n".join(
        row[-1]
        for row in store._conn.execute(
            "EXPLAIN QUERY PLAN " + _WALK, (json.dumps([anchor.uid]), 3)
        )
    )

    assert "ix_ca_anchor" in plan, f"walk stopped using the anchor index:\n{plan}"
    assert "SCAN ca" not in plan, (
        f"walk is scanning constraint_anchors instead of driving from the "
        f"reachable set — the join order inverted:\n{plan}"
    )


def test_a_rule_beyond_the_depth_bound_is_not_reached(tmp_path):
    """The bound has to bite on a chain, not only on a cycle.

    A cycle terminates anyway because the CTE is a UNION, so a cycle test
    passes with the bound removed entirely. A chain deeper than the bound is
    what actually distinguishes them.
    """
    from memory.anchor_store import MAX_WALK_DEPTH

    depth = MAX_WALK_DEPTH
    store = AnchorStore(str(tmp_path / "a.db"))
    # level0 is the root; each level is a child of the one above it, so
    # climbing from levelN reaches level(N-1), level(N-2), ...
    chain = [Anchor(name=f"level{i}") for i in range(depth * 2 + 2)]
    for anchor in chain:
        store.upsert(anchor)
    for parent, child in zip(chain, chain[1:]):
        store.add_edge(parent.uid, child.uid, EdgeKind.IS_A)

    leaf = len(chain) - 1
    store.replace_constraint_links("within", [chain[leaf - depth].uid])
    store.replace_constraint_links("beyond", [chain[0].uid])

    reachable = store.constraints_reachable_from([chain[leaf].uid])

    assert "within" in reachable, "a rule exactly at the bound must be reached"
    assert "beyond" not in reachable, (
        f"a rule {leaf} hops up was reached with the bound at {depth}"
    )


async def test_minting_is_bounded_so_a_careless_caller_cannot_flood_it(tmp_path):
    """Nothing refused before this.

    Every unresolved name became a permanent anchor, so a caller passing raw
    calendar titles — or a test harness passing junk — grew the taxonomy
    without bound, and every anchor is a node the gate must later reason
    about. Counting is arithmetic; nothing here judges the names.
    """
    from memory.anchoring import MAX_NEW_ANCHORS_PER_CALL

    store = AnchorStore(str(tmp_path / "a.db"))
    too_many = [f"thing {i}" for i in range(MAX_NEW_ANCHORS_PER_CALL + 3)]

    with pytest.raises(ValueError, match="refusing to mint more than"):
        await resolve_anchors(
            too_many, store, StubJudge(), max_new=MAX_NEW_ANCHORS_PER_CALL
        )

    # A day's worth of activities is well inside the bound.
    fine = await resolve_anchors(
        ["hockey", "dinner", "school run"], store, StubJudge()
    )
    assert len(fine) == 3
