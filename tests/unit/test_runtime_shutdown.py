from __future__ import annotations

from fateforger.core import runtime as runtime_module


class _FakeScheduler:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def shutdown(self, *, wait: bool) -> None:
        self.calls.append(wait)


class _FakeHauntingService:
    def __init__(self, scheduler: _FakeScheduler) -> None:
        self._scheduler = scheduler


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeRuntime:
    def __init__(
        self,
        service: _FakeHauntingService,
        engine: _FakeEngine,
        planning_reconciler: "_FakePlanningReconciler",
    ) -> None:
        self.haunting_service = service
        self.haunting_settings_engine = engine
        self.planning_reconciler = planning_reconciler
        self.stop_calls = 0
        self.close_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class _FakeCalendarClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePlanningReconciler:
    def __init__(self, calendar_client: _FakeCalendarClient) -> None:
        self.calendar_client = calendar_client


async def test_shutdown_runtime_releases_resources() -> None:
    scheduler = _FakeScheduler()
    service = _FakeHauntingService(scheduler)
    engine = _FakeEngine()
    calendar_client = _FakeCalendarClient()
    planning_reconciler = _FakePlanningReconciler(calendar_client)
    fake_runtime = _FakeRuntime(service, engine, planning_reconciler)
    original_runtime = runtime_module._runtime
    runtime_module._runtime = fake_runtime
    try:
        await runtime_module.shutdown_runtime()
    finally:
        runtime_module._runtime = original_runtime

    assert fake_runtime.stop_calls == 1
    assert fake_runtime.close_calls == 1
    assert scheduler.calls == [False]
    assert engine.disposed is True
    assert calendar_client.closed is True
    assert runtime_module._runtime is original_runtime


async def test_shutdown_runtime_is_noop_when_uninitialized() -> None:
    original_runtime = runtime_module._runtime
    runtime_module._runtime = None
    try:
        await runtime_module.shutdown_runtime()
    finally:
        runtime_module._runtime = original_runtime
