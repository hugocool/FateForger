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
    a range, a weekday against a list of weekdays — which is arithmetic, not
    a judgement about meaning.

    LIMITATION, deliberate and worth stating: this returns every durable
    constraint whose applicability window covers the day. It does NOT do
    semantic relevance — "which of these matter for a day containing hockey"
    — because that requires the anchor graph, which is a later plan. Until
    then the caller may receive more constraints than are useful. The patcher
    renders whatever it is handed by agreement, so memory owning this filter
    is what keeps the two sides from diverging as it improves.

    `stage` is accepted and currently unused; it is part of the agreed call
    shape and will select stage-relevant constraints once the graph exists.
    """
    return [c.to_view() for c in store.durable() if c.applicability.applies_on(day)]
