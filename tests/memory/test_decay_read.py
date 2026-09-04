# tests/memory/test_decay_read.py
from __future__ import annotations

from datetime import date, datetime, timezone

from memory.constraint import (
    Applicability,
    Constraint,
    Necessity,
    Scope,
    Source,
    Status,
)
from memory.constraint_store import ConstraintStore
from memory.models import DecayClass, Tier
from memory.read_api import get_active_constraints, get_faded_constraints

JAN = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
AUGUST = date(2026, 8, 17)


def _c(name: str, decay: DecayClass, seen: datetime) -> Constraint:
    return Constraint(
        name=name, description=name,
        necessity=Necessity.MUST, scope=Scope.PROFILE, status=Status.LOCKED,
        source=Source.USER, tier=Tier.DURABLE, applicability=Applicability(),
        source_observation_uids=[], created_at=seen,
        decay_class=decay, last_observed_at=seen,
    )


def test_a_permanent_rule_never_fades(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep at 23:00", DecayClass.PERMANENT, JAN))
    assert len(get_active_constraints(store, AUGUST)) == 1
    assert get_faded_constraints(store, AUGUST) == []


def test_a_project_rule_unseen_for_months_fades(tmp_path):
    """The C2F case: true for a chapter, then firing forever."""
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("C2F framing cap", DecayClass.PROJECT, JAN))
    assert get_active_constraints(store, AUGUST) == []
    faded = get_faded_constraints(store, AUGUST)
    assert [v.name for v in faded] == ["C2F framing cap"]


def test_a_faded_rule_is_withheld_not_deleted(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    c = _c("C2F framing cap", DecayClass.PROJECT, JAN)
    store.upsert(c)
    assert get_active_constraints(store, AUGUST) == []
    assert store.get(c.uid) is not None, "fading must never delete"


def test_a_recently_seen_project_rule_still_applies(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    recent = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    store.upsert(_c("Current sprint cap", DecayClass.PROJECT, recent))
    assert len(get_active_constraints(store, AUGUST)) == 1


def test_faded_and_active_are_disjoint(tmp_path):
    store = ConstraintStore(str(tmp_path / "c.db"))
    store.upsert(_c("Sleep at 23:00", DecayClass.PERMANENT, JAN))
    store.upsert(_c("C2F framing cap", DecayClass.PROJECT, JAN))
    active = {v.uid for v in get_active_constraints(store, AUGUST)}
    faded = {v.uid for v in get_faded_constraints(store, AUGUST)}
    assert active and faded
    assert not (active & faded)


def test_the_read_path_is_still_model_free():
    """I1 must survive the addition."""
    import ast
    import pathlib

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src" / "memory" / "read_api.py"
    ).read_text()
    tree = ast.parse(src)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    allowed = {
        "__future__",
        "datetime",
        "memory.anchor_store",  # SQLite over system-minted uids; no model calls
        "memory.constraint",
        "memory.constraint_store",
        "memory.models",
    }
    assert imported <= allowed, f"read path imports {imported - allowed}"
    assert not [
        n for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.Await))
    ]
