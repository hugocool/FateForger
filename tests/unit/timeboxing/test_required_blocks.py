"""The two set computations every required-block check shares (spec §2).

Both are arithmetic over identifiers the system minted: registry slugs on the
memory side, `slug` fields tmbx wrote on the tmbx side. Nothing here reads a
title.
"""
from __future__ import annotations

from fateforger.agents.timeboxing.required_blocks import (
    required_blocks_value,
    required_slugs,
    slugs_on_candidate,
)
from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact


def _rows(*pairs):
    return [{"uid": uid, "name": name, "requires_block": rb} for uid, name, rb in pairs]


def test_the_fact_value_lists_each_required_slug_once_with_its_rule():
    value = required_blocks_value(_rows(
        ("c1", "Daily planning", "planning"),
        ("c2", "Work start", None),
        ("c3", "End of day planning", "planning"),
    ))
    assert value == {
        "slugs": ["planning"],
        "by_rule": {"planning": {"uid": "c1", "name": "Daily planning"}},
    }


def test_no_rule_requiring_a_kind_means_an_empty_value():
    """Empty, not None: the fact is filed on every resolve so that it can clear
    a previous turn's requirement, and `_merge_facts` only ever overwrites."""
    empty = {"slugs": [], "by_rule": {}}
    assert required_blocks_value(_rows(("c2", "Work start", None))) == empty
    assert required_blocks_value([]) == empty
    assert required_blocks_value(None) == empty


def test_required_slugs_reads_only_the_required_blocks_fact():
    facts = [
        PlanningFact(fact_id="f1", kind=FactKind.REQUIRED_BLOCKS,
                     value={"slugs": ["planning", "sleep"], "by_rule": {}}, source="constraint_memory"),
        PlanningFact(fact_id="f2", kind=FactKind.CALENDAR_SNAPSHOT,
                     value={"fetched": True, "blocks": 3}, source="calendar"),
    ]
    assert required_slugs(facts) == {"planning", "sleep"}
    assert required_slugs([facts[1]]) == set()


def test_slugs_on_a_candidate_come_from_ops_and_rows():
    payload = {
        "patch": {"ops": [
            {"op": "add", "h": "PLN1", "slug": "planning"},
            {"op": "update", "h": "DW1", "slug": "deep-work"},
            {"op": "remove", "h": "X1"},
            {"op": "add", "h": "Y1"},
        ]},
        "rows": [{"h": "SLP1", "slug": "sleep"}, {"h": "DW1", "slug": ""}, {"h": "Z1"}],
    }
    assert slugs_on_candidate(payload) == {"planning", "deep-work", "sleep"}
    assert slugs_on_candidate({}) == set()
    assert slugs_on_candidate(None) == set()


def test_an_op_carries_the_slug_when_the_capture_has_no_rows():
    """A capture written before tmbx resolved the patch has ops and nothing
    else; reading rows alone would report the block absent and refuse a
    candidate that places it."""
    payload = {"patch": {"ops": [{"op": "add", "h": "PLN1", "slug": "planning"}]}, "rows": []}
    assert slugs_on_candidate(payload) == {"planning"}


def test_the_rows_carry_the_slug_when_no_op_touched_the_block():
    """The ordinary case for a block already on the day: the patch never names
    it, and only the post-patch rows say it is there."""
    payload = {
        "patch": {"ops": [{"op": "add", "h": "DW1", "n": "Deep work"}]},
        "rows": [{"h": "PLN1", "slug": "planning"}, {"h": "DW1"}],
    }
    assert slugs_on_candidate(payload) == {"planning"}


def test_two_rules_requiring_one_kind_attribute_it_to_the_same_rule_either_way():
    """The brief names the rule behind each required kind, and memory does not
    promise an order. First-seen made that attribution a coin flip: the same
    day, read twice, told the user a different rule was asking. The lowest uid
    is arbitrary but stable, which is what the brief needs."""
    pair = (("c9", "End of day planning", "planning"), ("c1", "Daily planning", "planning"))
    one = required_blocks_value(_rows(*pair))
    other = required_blocks_value(_rows(*reversed(pair)))
    assert one == other
    assert one["by_rule"]["planning"] == {"uid": "c1", "name": "Daily planning"}
