from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from fateforger.agents.timeboxing.session_contracts import (
    Advance,
    PlanningDay,
    PlanningSessionSnapshot,
)
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
        intent_model_client: "_FakeIntentModelClient | None" = None,
        judge_model_client: "_FakeIntentModelClient | None" = None,
        lifecycle: list[str] | None = None,
    ) -> None:
        self.haunting_service = service
        self.haunting_settings_engine = engine
        self.planning_reconciler = planning_reconciler
        self.timeboxing_intent_model_client = intent_model_client
        self.timeboxing_judge_model_client = judge_model_client
        self.lifecycle = lifecycle if lifecycle is not None else []
        self.stop_calls = 0
        self.close_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1
        self.lifecycle.append("runtime.stop")

    async def close(self) -> None:
        self.close_calls += 1
        self.lifecycle.append("runtime.close")


class _FakeIntentModelClient:
    def __init__(self, lifecycle: list[str] | None = None, name: str = "intent_client") -> None:
        self.close_calls = 0
        self.create_calls = 0
        self.name = name
        self.lifecycle = lifecycle if lifecycle is not None else []

    async def create(self, _messages, *, json_output):  # noqa: ANN001
        self.create_calls += 1
        return SimpleNamespace(
            content=json.dumps({"decision": "advance", "facts": []})
        )

    async def close(self) -> None:
        self.close_calls += 1
        self.lifecycle.append(f"{self.name}.close")


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


def test_runtime_builds_one_intent_client_from_the_interpreter_site(monkeypatch) -> None:
    """Catches per-turn model-client construction, and the wrong construction site.

    One client, built once, from the interpreter's own row -- and the object
    the interpreter holds is the one the runtime owns and closes.

    It no longer sees the client's kwargs: build_intent_interpreter_client()
    takes none, so the no-temperature-pin rule (CLAUDE.md) is asserted where
    those kwargs exist -- tests/unit/test_intent_interpreter_client.py,
    test_the_default_row_is_the_flash_pin_at_minimal_and_capped.
    """

    created: list[dict[str, object]] = []
    client = _FakeIntentModelClient()

    def _build(**kwargs: object) -> _FakeIntentModelClient:
        created.append(kwargs)
        return client

    # The interpreter's own row, not its host agent's client (#336).
    monkeypatch.setattr(runtime_module, "build_intent_interpreter_client", _build)

    interpreter, owned_client = (
        runtime_module._build_timeboxing_intent_interpreter()
    )

    assert created == [{}]
    assert owned_client is client
    assert interpreter.model_client is client


async def test_runtime_interpreter_reuses_owned_client_across_turns(
    monkeypatch,
) -> None:
    """Catches interpreter turns silently allocating their own model clients."""

    client = _FakeIntentModelClient()
    monkeypatch.setattr(
        runtime_module,
        "build_intent_interpreter_client",
        lambda **_kwargs: client,
    )
    interpreter, owned_client = (
        runtime_module._build_timeboxing_intent_interpreter()
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=1,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 8, 29), timezone="Europe/Amsterdam", lock_revision=1
        ),
        # Stage 1 has proposed to close; this test is about client reuse, not the gate.
        stage1="proposed",
    )

    first = await interpreter.interpret("plan it", snapshot)
    second = await interpreter.interpret("continue", snapshot)

    assert first == Advance()
    assert second == Advance()
    assert owned_client is client
    assert client.create_calls == 2


async def test_shutdown_closes_shared_intent_client_once_after_runtime_stop() -> None:
    """Catches a reused interpreter client leaking or closing before agent stop."""

    lifecycle: list[str] = []
    scheduler = _FakeScheduler()
    intent_client = _FakeIntentModelClient(lifecycle)
    judge_client = _FakeIntentModelClient(lifecycle, name="judge_client")
    fake_runtime = _FakeRuntime(
        _FakeHauntingService(scheduler),
        _FakeEngine(),
        _FakePlanningReconciler(_FakeCalendarClient()),
        intent_model_client=intent_client,
        judge_model_client=judge_client,
        lifecycle=lifecycle,
    )
    original_runtime = runtime_module._runtime
    runtime_module._runtime = fake_runtime
    try:
        await runtime_module.shutdown_runtime()
        await runtime_module.shutdown_runtime()
    finally:
        runtime_module._runtime = original_runtime

    assert intent_client.close_calls == 1
    assert judge_client.close_calls == 1
    assert lifecycle == [
        "runtime.stop",
        "runtime.close",
        "intent_client.close",
        "judge_client.close",
    ]
