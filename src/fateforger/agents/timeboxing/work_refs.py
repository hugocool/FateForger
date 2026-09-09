"""The `WORK_REFS` fact: what it is called, and how to read one.

The host mints these facts, the brief renders them and the planning card will
name them. That is three readers, so the shape lives here rather than in any
one of them -- beside `required_blocks.required_slugs`, which is the same
arrangement for the same reason: a fact whose *value* is read needs one place
that knows the value's shape, and the brief renderer must not have to import
the Slack host to build a prompt.

A ref is `{"link": <material handle>, "label": <ticket name>, "task": <board
number or None>}`. There is deliberately no url: the planner has no use for one
and must not learn to write one, which is the whole reason a handle exists.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .session_contracts import FactKind, PlanningFact


def work_refs_fact_id(day: str) -> str:
    """One id per day, so a re-resolve rewrites rather than accumulates.

    Facts merge by id and are never deleted, so a stable id is what keeps a
    second lookup on the same day from stacking a second list beside the first.
    """
    return f"work-refs:{day}"


def work_refs_on(facts: Iterable[PlanningFact]) -> list[dict[str, Any]]:
    """The refs carried by the `WORK_REFS` facts among these, in fact order.

    A reader rather than a field lookup at each call site: this fact's value is
    read, not merely present, and one place that knows its shape is one place
    to change when it grows.
    """
    refs: list[dict[str, Any]] = []
    for fact in facts:
        if fact.kind is not FactKind.WORK_REFS or not isinstance(fact.value, list):
            continue
        refs.extend(entry for entry in fact.value if isinstance(entry, dict))
    return refs


__all__ = ["work_refs_fact_id", "work_refs_on"]
