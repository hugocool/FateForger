"""Shared submit-time baseline guard contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SubmitBaselineGuardReason = Literal[
    "ready",
    "remote_baseline_refresh_failed",
    "missing_base_snapshot",
]


@dataclass(frozen=True)
class SubmitBaselineGuard:
    """Deterministic readiness result for submit-time baseline prerequisites."""

    ready: bool
    reason: SubmitBaselineGuardReason


def evaluate_submit_baseline_guard(
    *,
    refresh_ok: bool,
    has_base_snapshot: bool,
) -> SubmitBaselineGuard:
    """Classify submit baseline readiness in a shared deterministic way."""
    if not refresh_ok:
        return SubmitBaselineGuard(
            ready=False,
            reason="remote_baseline_refresh_failed",
        )
    if not has_base_snapshot:
        return SubmitBaselineGuard(
            ready=False,
            reason="missing_base_snapshot",
        )
    return SubmitBaselineGuard(
        ready=True,
        reason="ready",
    )
