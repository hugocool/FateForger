# src/memory/read_api.py
from __future__ import annotations

from datetime import date

from memory.constraint import ConstraintView
from memory.constraint_store import ConstraintStore


def get_active_constraints(
    store: ConstraintStore, day: date, stage: str | None = None
) -> list[ConstraintView]:
    """Constraints that apply on `day`, as views for the patcher.

    No model call happens here. Filtering is structural only — a date against
    a range, a weekday against a list of weekdays, a timestamp against a
    half-life threshold — which is arithmetic, not a judgement about meaning.

    A constraint that applies on `day` but has faded (no observation recent
    enough to keep it alive, per its decay class) is excluded here. It is not
    lost: `get_faded_constraints` returns exactly those, so a rule the user
    still holds can be confirmed rather than silently dropped.

    LIMITATION, deliberate and worth stating: this returns every durable,
    unfaded constraint whose applicability window covers the day. It does NOT
    do semantic relevance — "which of these matter for a day containing
    hockey" — because that requires the anchor graph, which is a later plan.
    Until then the caller may receive more constraints than are useful. The
    patcher renders whatever it is handed by agreement, so memory owning this
    filter is what keeps the two sides from diverging as it improves.

    `stage` is accepted and currently unused; it is part of the agreed call
    shape and will select stage-relevant constraints once the graph exists.
    """
    return [
        c.to_view()
        for c in store.durable()
        if c.applicability.applies_on(day) and not c.has_faded(day)
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
