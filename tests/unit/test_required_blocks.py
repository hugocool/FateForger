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


def test_no_rule_requiring_a_kind_means_no_fact():
    assert required_blocks_value(_rows(("c2", "Work start", None))) is None
    assert required_blocks_value([]) is None
    assert required_blocks_value(None) is None


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
