"""Constants and validated defaults for the timeboxing workflow.

This module exists to keep `agent.py` focused on orchestration logic by extracting:
- timeouts (LLM calls, background IO, Slack UX gates)
- concurrency limits (background tasks / semaphores)
- small deterministic defaults (fallback skeleton settings)

These values are *not* user configuration. User configuration lives in `fateforger.core.config`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimeboxingTimeouts:
    """Timeout configuration for timeboxing orchestration."""

    stage_gate_s: float = 35.0
    stage_decision_s: float = 20.0
    constraint_intent_s: float = 10.0
    constraint_interpret_s: float = 20.0
    constraint_extract_s: float = 25.0
    planning_date_interpret_s: float = 10.0
    skeleton_draft_s: float = 90.0
    summary_s: float = 20.0
    review_commit_s: float = 20.0
    notion_extract_s: float = 25.0
    notion_upsert_s: float = 20.0
    calendar_prefetch_wait_s: float = 2.0
    pending_constraints_wait_s: float = 2.0
    durable_prefetch_wait_s: float = 20.0
    tasks_snapshot_s: float = 12.0
    graph_turn_s: float = 120.0
    slow_turn_warn_s: float = 30.0


@dataclass(frozen=True, slots=True)
class TimeboxingLimits:
    """Concurrency / size limits for background work."""

    durable_upsert_concurrency: int = 1
    durable_prefetch_concurrency: int = 3
    constraint_extract_concurrency: int = 2

    durable_task_key_len: int = 16
    durable_task_queue_limit: int = 10

    durable_constraint_type_ids_limit: int = 12
    durable_constraint_query_limit: int = 50


@dataclass(frozen=True, slots=True)
class FallbackSkeletonDefaults:
    """Defaults used when skeleton drafting fails or times out."""

    focus_block_minutes: int = 90


TIMEBOXING_TIMEOUTS = TimeboxingTimeouts()
TIMEBOXING_LIMITS = TimeboxingLimits()
TIMEBOXING_FALLBACK = FallbackSkeletonDefaults()
