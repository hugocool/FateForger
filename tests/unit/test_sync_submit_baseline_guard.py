"""Unit tests for shared submit baseline guard contract."""

from fateforger.sync_core.submit_baseline_guard import (
    SubmitBaselineGuardReason,
    evaluate_submit_baseline_guard,
)


def test_evaluate_submit_baseline_guard_ready() -> None:
    result = evaluate_submit_baseline_guard(
        refresh_ok=True,
        has_base_snapshot=True,
    )
    assert result.ready is True
    assert result.reason == "ready"


def test_evaluate_submit_baseline_guard_refresh_failure() -> None:
    result = evaluate_submit_baseline_guard(
        refresh_ok=False,
        has_base_snapshot=True,
    )
    assert result.ready is False
    assert result.reason == "remote_baseline_refresh_failed"


def test_evaluate_submit_baseline_guard_missing_base_snapshot() -> None:
    result = evaluate_submit_baseline_guard(
        refresh_ok=True,
        has_base_snapshot=False,
    )
    assert result.ready is False
    assert result.reason == "missing_base_snapshot"


def test_submit_baseline_guard_reason_type_alias_contains_expected_values() -> None:
    values: set[SubmitBaselineGuardReason] = {
        "ready",
        "remote_baseline_refresh_failed",
        "missing_base_snapshot",
    }
    assert len(values) == 3
