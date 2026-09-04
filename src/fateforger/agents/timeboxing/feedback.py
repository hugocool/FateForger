"""Reader feedback from Stage 1 to the memory server: recorded, never acted on.

Every answer the user gives a probe and every rule they set aside is a signal
about whether what memory surfaced was right. #140 rules that such outcome
data may only ever be a rollback monitor, and memobase's unread `update_hits`
counter is the shape to avoid: so this port records with provenance and
nothing that plans reads it back.

The call happens after the snapshot save succeeded, never before: state
advances on durable success, not on intent.

The transport (in-process service with a judge, or the memory MCP server
over its `memory_observe` tool on a distinct channel) is not decided here; the
Slack process holds a read-only client today. `RecordingFeedbackObserver` is
what tests and the demo wire until it is.
"""
from __future__ import annotations

from typing import Protocol

from .session_contracts import FactKind, PlanningFact, PlanningSessionSnapshot

_FEEDBACK_KINDS = frozenset({FactKind.ELICITED_STATEMENT, FactKind.SUSPENDED_CONSTRAINT})


class FeedbackObserver(Protocol):
    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None: ...


def feedback_facts(
    before: PlanningSessionSnapshot, after: PlanningSessionSnapshot
) -> list[PlanningFact]:
    """User-sourced Stage 1 facts present in `after` and not in `before`, by id."""
    seen = {fact.fact_id for fact in before.facts}
    return [
        fact
        for fact in after.facts
        if fact.fact_id not in seen and fact.kind in _FEEDBACK_KINDS and fact.source == "user"
    ]


class RecordingFeedbackObserver:
    def __init__(self) -> None:
        self.observed: list[tuple[str, list[PlanningFact]]] = []

    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None:
        self.observed.append((session_key, list(facts)))


class NullFeedbackObserver:
    async def observe(self, *, session_key: str, facts: list[PlanningFact]) -> None:
        return None
