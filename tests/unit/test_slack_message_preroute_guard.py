from __future__ import annotations

import asyncio
import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.core.config import settings
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import register_handlers
from fateforger.slack_bot.workspace import WorkspaceDirectory, WorkspaceRegistry


class _FakePlanningCoordinator:
    def __init__(self, *, runtime, focus, client):
        self.runtime = runtime
        self.focus = focus
        self.client = client
        self.calls: list[tuple[str, str, str]] = []

    def attach_reconciler_dispatch(self) -> None:
        return

    async def maybe_register_user(
        self, *, user_id: str, channel_id: str, channel_type: str
    ) -> None:
        self.calls.append((user_id, channel_id, channel_type))
        await asyncio.sleep(0.05)


class _FakeRuntime:
    async def send_message(self, *_args, **_kwargs):
        return None


class _FakeClient:
    pass


class _FakeApp:
    def __init__(self):
        self.client = _FakeClient()
        self.events: dict[str, object] = {}

    def event(self, name: str):
        def decorator(fn):
            self.events[name] = fn
            return fn

        return decorator

    def action(self, _name: str):
        return self.event("__action__")

    def command(self, _name: str):
        return self.event("__command__")

    def view(self, _name: str):
        return self.event("__view__")


@pytest.mark.asyncio
async def test_message_event_routes_even_when_preregister_times_out(monkeypatch):
    app = _FakeApp()
    runtime = _FakeRuntime()
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )

    directory = WorkspaceDirectory(
        team_id="T1",
        channels_by_name={},
        channels_by_agent={},
        personas_by_agent={},
    )
    previous = WorkspaceRegistry.get_global()
    WorkspaceRegistry.set_global(directory)

    route_calls: list[dict] = []
    error_calls: list[tuple[str, str]] = []
    stage_calls: list[str] = []
    planning_instances: list[_FakePlanningCoordinator] = []

    async def _fake_route_slack_event(**kwargs):
        route_calls.append(kwargs)

    def _planning_factory(*, runtime, focus, client):
        inst = _FakePlanningCoordinator(runtime=runtime, focus=focus, client=client)
        planning_instances.append(inst)
        return inst

    monkeypatch.setattr(settings, "slack_register_user_timeout_seconds", 0.01, raising=False)
    monkeypatch.setattr("fateforger.slack_bot.handlers.PlanningCoordinator", _planning_factory)
    monkeypatch.setattr("fateforger.slack_bot.handlers.route_slack_event", _fake_route_slack_event)
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.record_error",
        lambda *, component, error_type: error_calls.append((component, error_type)),
    )
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.observe_stage_duration",
        lambda *, stage, duration_s: stage_calls.append(stage),
    )

    try:
        register_handlers(
            app=app,
            runtime=runtime,
            focus=focus,
            default_agent="receptionist_agent",
        )
        handler = app.events["message"]

        async def _say(**_kwargs):
            return {"ok": True}

        await handler(
            body={
                "event": {
                    "channel": "D123456789",
                    "channel_type": "im",
                    "user": "U1",
                    "text": "hello",
                    "ts": "1772279000.000001",
                }
            },
            say=_say,
            context={"bot_user_id": "U_BOT"},
            client=app.client,
            logger=types.SimpleNamespace(debug=lambda *a, **k: None),
        )

        assert planning_instances
        assert planning_instances[0].calls == [("U1", "D123456789", "im")]
        assert route_calls
        assert ("slack_routing", "register_timeout") in error_calls
        assert "slack_preroute_register_timeout" in stage_calls
        assert "slack_route_dispatch" in stage_calls
    finally:
        WorkspaceRegistry.set_global(previous)
