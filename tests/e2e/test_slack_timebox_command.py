from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from fateforger.slack_bot import handlers as handlers_mod


@pytest.mark.asyncio
async def test_timebox_command_routes_to_timeboxing_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes `/timebox` command payloads to the timeboxing agent via `route_slack_event`."""
    captured: dict[str, Any] = {}

    async def _fake_route_slack_event(
        *,
        runtime: Any,
        focus: Any,
        default_agent: str,
        event: dict[str, Any],
        bot_user_id: str,
        say: Any,
        client: Any,
        get_constraint_store: Callable[[], Awaitable[Any]],
    ) -> None:
        """Capture the routed event args."""
        captured["default_agent"] = default_agent
        captured["event"] = event

    monkeypatch.setattr(handlers_mod, "route_slack_event", _fake_route_slack_event)

    responses: list[dict[str, Any]] = []

    async def _respond(**payload: Any) -> None:
        """Capture ephemeral responses."""
        responses.append(payload)

    async def _get_constraint_store() -> Any:
        """Return no constraint store for this test."""
        return None

    await handlers_mod._handle_timebox_command(
        runtime=object(),
        focus=object(),
        default_agent="receptionist_agent",
        body={"user_id": "U1", "channel_id": "C1", "text": "tomorrow"},
        client=object(),
        respond=_respond,
        get_constraint_store=_get_constraint_store,
    )

    assert captured["default_agent"] == "timeboxing_agent"
    assert captured["event"]["text"] == "tomorrow"
    assert responses
    assert responses[0].get("response_type") == "ephemeral"


@pytest.mark.asyncio
async def test_timebox_command_sets_channel_type_for_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sets `channel_type=im` when the command is invoked from a DM channel."""
    captured: dict[str, Any] = {}

    async def _fake_route_slack_event(
        *,
        runtime: Any,
        focus: Any,
        default_agent: str,
        event: dict[str, Any],
        bot_user_id: str,
        say: Any,
        client: Any,
        get_constraint_store: Callable[[], Awaitable[Any]],
    ) -> None:
        """Capture the routed event args."""
        captured["event"] = event

    monkeypatch.setattr(handlers_mod, "route_slack_event", _fake_route_slack_event)

    async def _get_constraint_store() -> Any:
        """Return no constraint store for this test."""
        return None

    await handlers_mod._handle_timebox_command(
        runtime=object(),
        focus=object(),
        default_agent="receptionist_agent",
        body={"user_id": "U1", "channel_id": "D123", "text": "today"},
        client=object(),
        respond=None,
        get_constraint_store=_get_constraint_store,
    )

    assert captured["event"]["channel_type"] == "im"


class _FakeApp:
    """Capture the handlers `register_handlers` binds, without a Slack app."""

    def __init__(self) -> None:
        self.client = object()
        self.commands: dict[str, Any] = {}

    def command(self, name: str):
        def register(fn):
            self.commands[name] = fn
            return fn

        return register

    def action(self, _name: str):
        def register(fn):
            return fn

        return register

    def event(self, _name: str):
        def register(fn):
            return fn

        return register

    def message(self, *_args: Any, **_kwargs: Any):
        def register(fn):
            return fn

        return register

    def view(self, _name: str):
        def register(fn):
            return fn

        return register


@pytest.mark.asyncio
async def test_timebox_on_the_harness_backend_asks_for_the_day_not_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/timebox` starts a session; it does not start a planner.

    Launching the one-shot harness straight off the command is what let a
    fresh process pick its own planning day before anybody confirmed one.
    Both backends now enter through the same thread/redirect machinery.
    """
    monkeypatch.setenv("FF_TIMEBOX_BACKEND", "harness")

    timebox_calls: list[dict[str, Any]] = []

    async def _fake_timebox_command(**kwargs: Any) -> None:
        timebox_calls.append(kwargs)

    async def _forbidden_dsh(**_kwargs: Any) -> None:
        raise AssertionError("/timebox must not launch the one-shot harness")

    monkeypatch.setattr(handlers_mod, "_handle_timebox_command", _fake_timebox_command)
    monkeypatch.setattr(handlers_mod, "_handle_dsh_command", _forbidden_dsh)

    app = _FakeApp()
    handlers_mod.register_handlers(app, object(), object(), default_agent="x")

    acked: list[bool] = []

    async def _ack() -> None:
        acked.append(True)

    async def _respond(**_payload: Any) -> None:
        return None

    await app.commands["/timebox"](
        _ack,
        {"user_id": "U1", "channel_id": "C1", "text": ""},
        _respond,
        object(),
        handlers_mod.logger,
    )
    # The command dispatches in the background to stay inside Slack's ack window.
    await asyncio.sleep(0)

    assert acked
    assert len(timebox_calls) == 1
    assert timebox_calls[0]["body"]["channel_id"] == "C1"
