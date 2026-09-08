from __future__ import annotations

import types

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.tasks.messages import (
    TaskDetailsModalRequest,
    TaskDueActionRequest,
    TaskEditTitleRequest,
)
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import register_handlers
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.task_cards import (
    FF_TASK_DETAILS_ACTION_ID,
    FF_TASK_EDIT_MODAL_CALLBACK_ID,
    FF_TASK_VIEW_ALL_ACTION_ID,
    encode_task_metadata,
)


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    async def send_message(self, message, recipient):
        self.calls.append((message, recipient))
        if isinstance(message, TaskDueActionRequest):
            return SlackBlockMessage(
                text="All due tasks",
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "Due list"}}],
            )
        if isinstance(message, TaskDetailsModalRequest):
            return {
                "ok": True,
                "error": "",
                "view": {
                    "type": "modal",
                    "callback_id": FF_TASK_EDIT_MODAL_CALLBACK_ID,
                    "title": {"type": "plain_text", "text": "Task details"},
                },
            }
        if isinstance(message, TaskEditTitleRequest):
            return {"ok": True, "message": "Updated TT-ABC12345 title to: New title"}
        return None


class _FakeClient:
    def __init__(self) -> None:
        self.updated: list[dict] = []
        self.opened: list[dict] = []
        self.posted: list[dict] = []

    async def chat_update(self, **payload):
        self.updated.append(payload)
        return {"ok": True}

    async def views_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True}

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"ok": True}


class _FakeApp:
    def __init__(self, client) -> None:
        self.client = client
        self.actions: dict[str, object] = {}
        self.views: dict[str, object] = {}

    def _register(self, bucket: dict[str, object], key: str):
        def decorator(fn):
            bucket[key] = fn
            return fn

        return decorator

    def action(self, action_id: str):
        return self._register(self.actions, action_id)

    def event(self, event_name: str):
        return self._register({}, event_name)

    def command(self, command_name: str):
        return self._register({}, command_name)

    def view(self, callback_id: str):
        return self._register(self.views, callback_id)


@pytest.mark.asyncio
async def test_task_view_all_action_updates_message() -> None:
    runtime = _FakeRuntime()
    client = _FakeClient()
    app = _FakeApp(client)
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    register_handlers(
        app=app,
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
    )
    handler = app.actions[FF_TASK_VIEW_ALL_ACTION_ID]
    ack_calls: list[bool] = []

    async def _ack():
        ack_calls.append(True)

    await handler(
        ack=_ack,
        body={
            "actions": [
                {
                    "action_id": FF_TASK_VIEW_ALL_ACTION_ID,
                    "value": encode_task_metadata(
                        {
                            "due_date": "2026-03-02",
                            "source": "ticktick",
                            "project_ids": "P1,P2",
                        }
                    ),
                }
            ],
            "channel": {"id": "C1"},
            "message": {"ts": "111.222", "thread_ts": "111.200"},
            "user": {"id": "U1"},
        },
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert ack_calls == [True]
    assert runtime.calls
    assert isinstance(runtime.calls[0][0], TaskDueActionRequest)
    assert client.updated
    assert client.updated[0]["channel"] == "C1"


@pytest.mark.asyncio
async def test_task_details_action_opens_modal() -> None:
    runtime = _FakeRuntime()
    client = _FakeClient()
    app = _FakeApp(client)
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    register_handlers(
        app=app,
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
    )
    handler = app.actions[FF_TASK_DETAILS_ACTION_ID]

    async def _ack():
        return None

    await handler(
        ack=_ack,
        body={
            "trigger_id": "TRIGGER-1",
            "actions": [
                {
                    "action_id": FF_TASK_DETAILS_ACTION_ID,
                    "value": encode_task_metadata(
                        {
                            "task_id": "T1",
                            "project_id": "P1",
                            "label": "TT-ABC12345",
                            "title": "Task title",
                            "project_name": "tasks",
                            "due_date": "2026-03-02",
                        }
                    ),
                }
            ],
            "channel": {"id": "C1"},
            "message": {"ts": "111.222", "thread_ts": "111.200"},
            "user": {"id": "U1"},
        },
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert any(isinstance(call[0], TaskDetailsModalRequest) for call in runtime.calls)
    assert client.opened
    assert client.opened[0]["trigger_id"] == "TRIGGER-1"


@pytest.mark.asyncio
async def test_task_edit_modal_submit_posts_confirmation() -> None:
    runtime = _FakeRuntime()
    client = _FakeClient()
    app = _FakeApp(client)
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    register_handlers(
        app=app,
        runtime=runtime,
        focus=focus,
        default_agent="receptionist_agent",
    )
    handler = app.views[FF_TASK_EDIT_MODAL_CALLBACK_ID]

    async def _ack():
        return None

    await handler(
        ack=_ack,
        body={
            "user": {"id": "U1"},
            "view": {
                "private_metadata": encode_task_metadata(
                    {
                        "channel_id": "C1",
                        "thread_ts": "111.200",
                        "user_id": "U1",
                        "project_id": "P1",
                        "task_id": "T1",
                        "label": "TT-ABC12345",
                    }
                ),
                "state": {
                    "values": {
                        "task_title_input": {
                            "task_title_value": {"value": "New title"}
                        }
                    }
                },
            },
        },
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert any(isinstance(call[0], TaskEditTitleRequest) for call in runtime.calls)
    assert client.posted
    assert client.posted[0]["channel"] == "C1"
    assert "Updated TT-ABC12345" in client.posted[0]["text"]
