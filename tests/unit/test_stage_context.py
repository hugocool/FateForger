"""Panel and fold builders: every assertion is over uids, anchor uids,
fact ids, counts and enum values this system minted. Nothing reads a name."""

from __future__ import annotations

from datetime import date

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
    suspension_fact_id,
)
from fateforger.slack_bot.messages import SLACK_MAX_MODAL_BLOCKS
from fateforger.slack_bot.stage_context import (
    context_fold,
    context_panel,
    fold_block_count,
    group_rows,
    rank_rows,
    shown_with_of,
)


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


def test_the_panel_counts_and_groups() -> None:
    rows = [_row("c1", GYM, necessity="must"), _row("c2", GYM), _row("c3")]
    panel = context_panel(
        _snapshot(rows, [_suspend("c3")], suspended_constraint_count=3),
        first_shown_with=None,
    )
    assert panel.day == "2026-09-08"
    assert (panel.rule_count, panel.must_count, panel.off_today_count) == (3, 1, 3)
    assert panel.off_today_reason == "working"
    assert [g.name for g in panel.groups] == [None, "gym"]  # suspended c3 ranks first
    assert [s.uid for s in panel.suspended] == ["c3"]
    assert panel.suspended[0].reason == "not today"


def test_shown_with_is_row_uids_plus_suspension_fact_ids() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    snapshot = _snapshot(rows, [_suspend("c2")])
    assert shown_with_of(snapshot) == frozenset({"c1", "c2", suspension_fact_id("c2")})
    panel = context_panel(snapshot, first_shown_with=None)
    assert panel.shown_with == shown_with_of(snapshot)


def test_first_draw_seeds_first_shown_with_and_later_draws_keep_it() -> None:
    first = context_panel(_snapshot([_row("c1", GYM)]), first_shown_with=None)
    assert first.first_shown_with == frozenset({"c1"})
    later = context_panel(
        _snapshot([_row("c1", GYM), _row("c2", GYM)]), first_shown_with=first.first_shown_with
    )
    assert later.first_shown_with == frozenset({"c1"})
    assert [g.uids for g in later.groups] == [["c2", "c1"]]


def test_the_fold_lists_every_rule_once_with_its_other_anchors() -> None:
    rows = [_row("c1", GYM, BREAKFAST), _row("c2", GYM), _row("c3", BREAKFAST, DINNER)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    by_uid = {r.uid: (g.name, r.also) for g in fold.groups for r in g.rows}
    assert by_uid["c1"] == ("breakfast", ["gym"])
    assert by_uid["c3"] == ("breakfast", ["dinner"])
    assert by_uid["c2"] == ("gym", [])


def test_verbs_depend_on_whether_the_row_is_suspended() -> None:
    rows = [_row("c1", GYM), _row("c2", GYM)]
    fold = context_fold(_snapshot(rows, [_suspend("c2")]), first_shown_with=None)
    verbs = {r.uid: r.verbs for g in fold.groups for r in g.rows}
    assert verbs["c1"] == ["steer_not_today", "steer_wrong"]
    assert verbs["c2"] == ["restore"]


def test_the_fold_truncates_whole_groups_past_the_modal_cap() -> None:
    # 60 groups of 2 rows = 60 headings + 120 rows + 1 footer, far past 100.
    rows = [_row(f"c{i}", (f"a{i // 2}", f"anchor{i // 2}")) for i in range(120)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold_block_count(fold) <= SLACK_MAX_MODAL_BLOCKS
    kept = sum(len(g.rows) for g in fold.groups)
    assert fold.truncated == (120 - kept, 60 - len(fold.groups))
    assert fold.groups[0].rows[0].uid == "c0"  # the top-ranked group survives


def test_a_fitting_fold_is_not_truncated() -> None:
    rows = [_row(f"c{i}", GYM) for i in range(41)]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold.truncated is None
    assert fold_block_count(fold) == 1 + 41 + 1


def test_groups_that_fit_exactly_at_the_cap_are_all_kept() -> None:
    # 49-row group + 48-row group + 1 footer = 100 blocks exactly: no "+N" needed.
    rows = [_row(f"a{i}", ("anchor-a", "AAA")) for i in range(49)] + [
        _row(f"b{i}", ("anchor-b", "BBB")) for i in range(48)
    ]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold.truncated is None
    assert {g.name for g in fold.groups} == {"AAA", "BBB"}
    assert sum(len(g.rows) for g in fold.groups) == 97
    assert fold_block_count(fold) <= SLACK_MAX_MODAL_BLOCKS
    assert fold_block_count(fold) == 100


def test_a_panel_needs_a_locked_day() -> None:
    snapshot = _snapshot([_row("c1", GYM)], planning_day=None)
    with pytest.raises(ValueError):
        context_panel(snapshot, first_shown_with=None)


def test_a_fold_needs_a_locked_day() -> None:
    snapshot = _snapshot([_row("c1", GYM)], planning_day=None)
    with pytest.raises(ValueError):
        context_fold(snapshot, first_shown_with=None)


def test_an_oversized_top_group_is_kept_partially_under_the_cap() -> None:
    # One group of 150 rows dwarfs the whole cap; a second, small group follows it.
    rows = [_row(f"c{i}", ("anchor-c", "CCC")) for i in range(150)] + [
        _row(f"d{i}", ("anchor-d", "DDD")) for i in range(2)
    ]
    fold = context_fold(_snapshot(rows), first_shown_with=None)
    assert fold_block_count(fold) <= SLACK_MAX_MODAL_BLOCKS
    (first_group,) = fold.groups  # the second group is dropped entirely
    kept_rows = len(first_group.rows)
    assert kept_rows < 150
    assert fold.truncated == (150 - kept_rows + 2, 1)
