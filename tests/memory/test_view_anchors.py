"""The view carries the anchors a rule attaches to, by uid and name.

The join is set membership and a lookup over uids this system minted, so the
read path stays model-free (I1). Without `anchors=` the view is exactly what it
was, so every existing caller keeps its shape.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.anchor import Anchor
from memory.anchor_store import AnchorStore
from memory.constraint import AnchorRef, Constraint, Necessity, Scope, Source, Status
from memory.constraint_store import ConstraintStore
from memory.models import Tier
from memory.read_api import get_active_constraints

DAY = date(2026, 9, 8)
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _rule(name: str) -> Constraint:
    return Constraint(
        name=name,
        description=f"{name} description",
        necessity=Necessity.SHOULD,
        scope=Scope.PROFILE,
        status=Status.PROPOSED,
        source=Source.USER,
        tier=Tier.DURABLE,
        created_at=NOW,
        last_observed_at=NOW,
    )


def _stores(tmp_path) -> tuple[ConstraintStore, AnchorStore]:
    db = str(tmp_path / "memory.db")
    return ConstraintStore(db), AnchorStore(db)


def test_a_linked_rule_carries_its_anchor_uid_and_name(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    rule = _rule("Oats before gym")
    constraints.upsert(rule)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [view] = get_active_constraints(constraints, DAY, anchors=anchors)

    assert view.anchors == [AnchorRef(uid=gym.uid, name="gym")]


def test_an_unlinked_rule_has_an_empty_list_not_a_missing_field(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    constraints.upsert(_rule("Plan at 17:00"))

    [view] = get_active_constraints(constraints, DAY, anchors=anchors)

    assert view.anchors == []


def test_without_an_anchor_store_the_view_is_unchanged(tmp_path) -> None:
    constraints, anchors = _stores(tmp_path)
    gym = Anchor(name="gym")
    anchors.upsert(gym)
    rule = _rule("Oats before gym")
    constraints.upsert(rule)
    anchors.replace_constraint_links(rule.uid, [gym.uid])

    [view] = get_active_constraints(constraints, DAY)

    assert view.anchors == []
    assert "anchors" in view.model_dump()
