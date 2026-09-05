"""Required blocks: the set arithmetic every check shares (spec §2).

A rule in memory can say a block of a registered kind must be on the day
(`requires_block`, #212). Three places act on that -- the host filing a fact,
the submit tool refusing a candidate, the kernel accepting a draft -- and each
needs the same two sets: the slugs the day requires, and the slugs a candidate
carries. Both are unions over identifiers this system minted; nothing here
reads a title or a description.
"""
from __future__ import annotations

from typing import Any, Iterable

from fateforger.agents.timeboxing.session_contracts import FactKind, PlanningFact


def required_blocks_value(constraints: Any) -> dict[str, Any] | None:
    """The fact value for a day's rules, or None when no rule requires a kind.

    `by_rule` keeps the first rule that named each slug, so the brief can say
    "(from memory: <rule name>)" without a second join.
    """
    if not isinstance(constraints, list):
        return None
    by_rule: dict[str, dict[str, str]] = {}
    for row in constraints:
        if not isinstance(row, dict):
            continue
        slug = row.get("requires_block")
        if not isinstance(slug, str) or not slug:
            continue
        by_rule.setdefault(
            slug, {"uid": str(row.get("uid") or ""), "name": str(row.get("name") or "")}
        )
    if not by_rule:
        return None
    return {"slugs": sorted(by_rule), "by_rule": by_rule}


def required_slugs(facts: Iterable[PlanningFact]) -> set[str]:
    out: set[str] = set()
    for fact in facts:
        if fact.kind is not FactKind.REQUIRED_BLOCKS or not isinstance(fact.value, dict):
            continue
        slugs = fact.value.get("slugs")
        if isinstance(slugs, list):
            out.update(s for s in slugs if isinstance(s, str) and s)
    return out


def slugs_on_candidate(payload: Any) -> set[str]:
    """Slugs a candidate carries: on its add/update ops, and on the rows tmbx
    resolved for it. Both come from the captured `plan_apply`, never from prose."""
    if not isinstance(payload, dict):
        return set()
    out: set[str] = set()
    patch = payload.get("patch")
    ops = patch.get("ops") if isinstance(patch, dict) else None
    for op in ops or []:
        if isinstance(op, dict) and op.get("op") in ("add", "update"):
            slug = op.get("slug")
            if isinstance(slug, str) and slug:
                out.add(slug)
    for row in payload.get("rows") or []:
        if isinstance(row, dict):
            slug = row.get("slug")
            if isinstance(slug, str) and slug:
                out.add(slug)
    return out


__all__ = ["required_blocks_value", "required_slugs", "slugs_on_candidate"]
