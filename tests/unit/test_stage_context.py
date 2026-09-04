"""Panel and fold builders: every assertion is over uids, anchor uids,
fact ids, counts and enum values this system minted. Nothing reads a name."""

from __future__ import annotations

from datetime import date

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.slack_bot.stage_context import group_rows, rank_rows


def _day() -> PlanningDay:
    return PlanningDay.lock_default(
        value=date(2026, 9, 8), timezone="Europe/Amsterdam", lock_revision=1
    )


def _row(uid: str, *anchors: tuple[str, str], necessity="should", fade=None) -> dict:
    return {
        "uid": uid,
        "name": f"rule {uid}",
        "necessity": necessity,
        "anchors": [{"uid": a, "name": n} for a, n in anchors],
        "fade": fade,
    }


def _suspend(uid: str) -> PlanningFact:
    return PlanningFact(
        fact_id=suspension_fact_id(uid),
        kind=FactKind.SUSPENDED_CONSTRAINT,
        value={"uid": uid, "reason": "not today"},
        source="user",
    )


def _snapshot(rows: list[dict], facts: list[PlanningFact] | None = None, **update):
    base = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=4,
        owner_user_id="U1",
        planning_day=_day(),
        applicable_constraints=rows,
        facts=list(facts or []),
    )
    return base.model_copy(update=update)


GYM, BREAKFAST, DINNER = ("a-gym", "gym"), ("a-bf", "breakfast"), ("a-din", "dinner")


def test_store_order_is_the_tiebreak() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM), _row("c3", GYM)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c1", "c2", "c3"]


def test_a_suspended_rule_ranks_first() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    ranked = rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]
    assert ranked[0].suspended_reason == "not today"
    assert ranked[0].touched is True


def test_a_rule_absent_from_the_first_draw_ranks_first() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=frozenset({"c1"}))
    assert [r.uid for r in ranked] == ["c2", "c1"]


def test_nearest_to_fading_ranks_before_the_store_order_and_none_last() -> None:
    rows = [_row("c1", GYM, fade=None), _row("c2", GYM, fade=0.2), _row("c3", GYM, fade=0.9)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c3", "c2", "c1"]


def test_touched_outranks_fading() -> None:
    rows = [_row("c1", GYM, fade=0.9), _row("c2", GYM, fade=None)]
    ranked = rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]


def test_a_rule_under_an_open_concern_ranks_before_fading(monkeypatch) -> None:
    import fateforger.slack_bot.stage_context as module

    class Matrix:
        cells = {"elicit.body.unclear": "uncovered", "elicit.fixed.unclear": "covered"}
        placement = {"a-gym": "body", "a-din": "fixed"}

    monkeypatch.setattr(module, "coverage_matrix", lambda snapshot: Matrix())
    rows = [_row("c1", DINNER, fade=0.9), _row("c2", GYM, fade=None)]
    ranked = rank_rows(_snapshot(rows), first_shown_with=None)
    assert [r.uid for r in ranked] == ["c2", "c1"]
    assert ranked[0].open_concern is True


def test_every_rule_lands_in_exactly_one_group_the_largest() -> None:
    rows = [
        _row("c1", GYM, BREAKFAST),   # gym has 2 rules, breakfast 2 -> tie by name: breakfast
        _row("c2", GYM),
        _row("c3", BREAKFAST, DINNER),
        _row("c4"),
    ]
    groups = group_rows(rank_rows(_snapshot(rows), first_shown_with=None))
    placed = [uid for g in groups for uid in g.uids]
    assert sorted(placed) == ["c1", "c2", "c3", "c4"]
    by_name = {g.name: g.uids for g in groups}
    assert by_name["breakfast"] == ["c1", "c3"]
    assert by_name["gym"] == ["c2"]
    assert by_name[None] == ["c4"]
    assert "dinner" not in by_name


def test_groups_take_the_rank_of_their_top_row() -> None:
    rows = [_row("c1", GYM), _row("c2", DINNER), _row("c3", DINNER)]
    groups = group_rows(rank_rows(_snapshot(rows, [_suspend("c2")]), first_shown_with=None))
    assert [g.name for g in groups] == ["dinner", "gym"]


def test_must_count_per_group() -> None:
    rows = [_row("c1", GYM, necessity="must"), _row("c2", GYM)]
    (group,) = group_rows(rank_rows(_snapshot(rows), first_shown_with=None))
    assert group.must_count == 1


def test_stage_context_knows_no_slack_and_no_store() -> None:
    import ast
    import inspect

    import fateforger.slack_bot.stage_context as module

    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"slack_sdk", "handlers", "timeboxing_cards", "timeboxing_commit", "memory", "sqlite3"}
    offending = {n for n in imported if any(part in forbidden for part in n.split("."))}
    assert offending == set(), offending
