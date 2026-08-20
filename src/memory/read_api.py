# src/memory/read_api.py
from __future__ import annotations

from datetime import date

from memory.constraint import ConstraintView
from memory.constraint_store import ConstraintStore


def get_active_constraints(
    store: ConstraintStore,
    day: date,
    stage: str | None = None,
    *,
    reachable: set[str] | None = None,
) -> list[ConstraintView]:
    """Constraints that apply on `day`, as views for the patcher.

    No model call happens here. Filtering is structural only — a date against
    a range, a weekday against a list of weekdays, a timestamp against a
    half-life threshold — which is arithmetic, not a judgement about meaning.

    A constraint that applies on `day` but has faded (no observation recent
    enough to keep it alive, per its decay class) is excluded here. It is not
    lost: `get_faded_constraints` returns exactly those, so a rule the user
    still holds can be confirmed rather than silently dropped.

    `reachable`, when given, is the set of constraint uids the day's anchors
    can reach through the taxonomy — computed by the caller with a graph walk,
    which is also model-free. Passing it narrows the result to rules that bear
    on what is actually happening that day. Omitting it returns every
    applicable rule, which is the behaviour every existing caller has.

    Set membership over system-minted uids, explicitly outside the no-matching
    rule: the judgement about which anchors the day involves happened earlier,
    at write time and at resolution time, both with a model. Nothing here
    decides what anything means.

    A constraint carrying no anchors at all is returned regardless. It is not
    unreachable, it is unanchored — a rule about the shape of the day rather
    than about a thing in it — and dropping those would silently discard every
    rule stored before the graph existed.

    `stage` is accepted and currently unused; it is part of the agreed call
    shape and will select stage-relevant constraints once stage vocabulary
    exists.
    """
    return [
        c.to_view()
        for c in store.durable()
        if c.applicability.applies_on(day)
        and not c.has_faded(day)
        and (reachable is None or c.uid in reachable)
    ]


def get_faded_constraints(
    store: ConstraintStore, day: date, stage: str | None = None
) -> list[ConstraintView]:
    """Durable rules withheld because their evidence has gone stale.

    These are not deleted and not forgotten — they are the review queue. A
    rule appears here when nothing has re-observed it within its class's
    half-life, and one new observation revives it at full weight. Surfacing
    them is what stops fading from silently losing a rule the user still
    holds.
    """
    return [
        c.to_view()
        for c in store.durable()
        if c.applicability.applies_on(day) and c.has_faded(day)
    ]
