"""Shared reconciliation-summary contract for deterministic op buckets."""

from __future__ import annotations

from dataclasses import dataclass

from fateforger.agents.timeboxing.calendar_reconciliation import reconcile_calendar_ops
from fateforger.agents.timeboxing.tb_models import TBPlan


@dataclass(frozen=True)
class ReconciliationSummary:
    """Deterministic op-bucket counts for a desired-vs-remote snapshot pair."""

    remote_fetched: int
    matched: int
    create: int
    update: int
    noop: int
    delete: int

    @property
    def planned_mutations(self) -> int:
        return self.create + self.update + self.delete


def summarize_reconciliation(
    *,
    remote: TBPlan,
    desired: TBPlan,
    event_id_map: dict[str, str],
    remote_event_ids_by_index: list[str] | None = None,
) -> ReconciliationSummary:
    """Return deterministic reconciliation counts from canonical op planning."""
    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map=event_id_map,
        remote_event_ids_by_index=remote_event_ids_by_index,
    )
    remote_fetched = len(remote.resolve_times(validate_non_overlap=False))
    return ReconciliationSummary(
        remote_fetched=remote_fetched,
        matched=len(plan.matches),
        create=len(plan.creates),
        update=len(plan.updates),
        noop=len(plan.noops),
        delete=len(plan.deletes),
    )
