from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.planning import PlanningCoordinator


class _SlowGuardian:
    async def reconcile_user(self, *, user_id: str) -> None:
        await asyncio.sleep(0.2)


@pytest.mark.asyncio
async def test_maybe_register_user_records_cancelled_reconcile(monkeypatch):
    stage_calls: list[str] = []
    error_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "fateforger.slack_bot.planning.observe_stage_duration",
        lambda *, stage, duration_s: stage_calls.append(stage),
    )
    monkeypatch.setattr(
        "fateforger.slack_bot.planning.record_error",
        lambda *, component, error_type: error_calls.append((component, error_type)),
    )

    runtime = SimpleNamespace(planning_guardian=_SlowGuardian())
    focus = FocusManager(ttl_seconds=3600, allowed_agents=["receptionist_agent"])
    coordinator = PlanningCoordinator(runtime=runtime, focus=focus, client=SimpleNamespace())

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            coordinator.maybe_register_user(
                user_id="U1",
                channel_id="D1",
                channel_type="im",
            ),
            timeout=0.01,
        )

    assert "planning_register_ensure_anchor" in stage_calls
    assert "planning_guardian_reconcile_cancelled" in stage_calls
    assert ("planning_guardian", "reconcile_cancelled") in error_calls
