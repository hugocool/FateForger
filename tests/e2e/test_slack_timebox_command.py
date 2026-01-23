from __future__ import annotations

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
