"""Integration tests for deterministic timeboxing stage-control buttons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId

from fateforger.agents.timeboxing.messages import TimeboxingStageAction
from fateforger.slack_bot.constraint_review import encode_metadata
from fateforger.slack_bot.timeboxing_stage_actions import (
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    TimeboxingStageActionCoordinator,
    TimeboxingStageActionPayload,
)


@dataclass
class _RecordedCall:
    """Captured runtime dispatch."""

    message: Any
    recipient: AgentId


class _Runtime:
    """Minimal runtime stub that records and returns a presenter-like response."""

    def __init__(self) -> None:
        self.calls: list[_RecordedCall] = []

    async def send_message(self, message: Any, recipient: AgentId) -> Any:
        self.calls.append(_RecordedCall(message=message, recipient=recipient))
        return TextMessage(content="Stage 2/5 (CaptureInputs)", source="timeboxing_agent")


class _Client:
    """Slack client stub for chat_update assertions."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    async def chat_update(self, **payload: Any) -> dict[str, Any]:
        self.updates.append(payload)
        return {"ok": True}


def _action_payload() -> TimeboxingStageActionPayload:
    """Build stage-action payload from a Slack action body fixture."""
    meta = encode_metadata({"channel_id": "C1", "thread_ts": "T1", "user_id": "U1"})
    body = {
        "actions": [{"action_id": FF_TIMEBOX_STAGE_PROCEED_ACTION_ID, "value": meta}],
        "channel": {"id": "C1"},
        "message": {"ts": "M1"},
        "user": {"id": "U1"},
    }
    payload = TimeboxingStageActionPayload.from_action_body(body)
    assert payload is not None
    return payload


@pytest.mark.asyncio
async def test_stage_proceed_button_dispatches_and_replaces_message() -> None:
    """Proceed action should send a typed stage-action message and update the prompt."""
    runtime = _Runtime()
    client = _Client()
    coordinator = TimeboxingStageActionCoordinator(runtime=runtime, client=client)

    await coordinator.handle_action(payload=_action_payload(), action="proceed")

    assert runtime.calls
    dispatched = runtime.calls[-1].message
    assert isinstance(dispatched, TimeboxingStageAction)
    assert dispatched.action == "proceed"
    assert client.updates
    assert "CaptureInputs" in (client.updates[-1].get("text") or "")
