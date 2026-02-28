from __future__ import annotations

import asyncio

from fateforger.core import runtime as runtime_module


class _FakeGuardian:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def reconcile_all(self) -> None:
        self.calls += 1
        if self.mode == "ok":
            return
        if self.mode == "timeout":
            await asyncio.sleep(0.05)
            return
        raise RuntimeError("boom")


async def test_run_initial_planning_reconcile_success() -> None:
    guardian = _FakeGuardian(mode="ok")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.01,
    )
    assert result is True
    assert guardian.calls == 1


async def test_run_initial_planning_reconcile_timeout() -> None:
    guardian = _FakeGuardian(mode="timeout")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.001,
    )
    assert result is False
    assert guardian.calls == 1


async def test_run_initial_planning_reconcile_error() -> None:
    guardian = _FakeGuardian(mode="error")
    result = await runtime_module._run_initial_planning_reconcile(  # noqa: SLF001
        planning_guardian=guardian,
        timeout_s=0.01,
    )
    assert result is False
    assert guardian.calls == 1
