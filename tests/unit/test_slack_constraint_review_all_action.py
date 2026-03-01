from __future__ import annotations

import types
from types import SimpleNamespace

import pytest

pytest.importorskip("autogen_agentchat")

from fateforger.agents.timeboxing.preferences import ConstraintStatus
from fateforger.core.config import settings
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_REVIEW_ALL_ACTION_ID,
    CONSTRAINT_REVIEW_LIST_VIEW_CALLBACK_ID,
    encode_metadata,
)
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.handlers import register_handlers


class _FakeRuntime:
    async def send_message(self, *_args, **_kwargs):
        return None


class _FakeConstraintStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def list_constraints(
        self,
        *,
        user_id: str,
        channel_id: str | None = None,
        thread_ts: str | None = None,
    ):
        self.calls.append(
            {
                "user_id": user_id,
                "channel_id": channel_id or "",
                "thread_ts": thread_ts or "",
            }
        )
        return [
            SimpleNamespace(
                id=1,
                name="Keep lunch break",
                description="Reserve 12:00-13:00",
                necessity="must",
                status=ConstraintStatus.LOCKED,
                scope="session",
            ),
            SimpleNamespace(
                id=2,
                name="Declined rule",
                description="Should be filtered from list",
                necessity="should",
                status=ConstraintStatus.DECLINED,
                scope="session",
            ),
        ]


class _FakeClient:
    def __init__(self) -> None:
        self.opened: list[dict] = []

    async def views_open(self, **payload):
        self.opened.append(payload)
        return {"ok": True}


class _FakeApp:
    def __init__(self, client) -> None:
        self.client = client
        self.actions: dict[str, object] = {}

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
        return self._register({}, callback_id)


@pytest.mark.asyncio
async def test_constraint_review_all_action_opens_list_modal(monkeypatch) -> None:
    store = _FakeConstraintStore()

    monkeypatch.setattr(
        settings, "database_url", "sqlite+aiosqlite:///:memory:", raising=False
    )
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.create_async_engine",
        lambda *_args, **_kwargs: object(),
    )

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("fateforger.slack_bot.handlers.ensure_constraint_schema", _noop)
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.async_sessionmaker",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "fateforger.slack_bot.handlers.ConstraintStore",
        lambda _sessionmaker: store,
    )

    client = _FakeClient()
    app = _FakeApp(client)
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    register_handlers(
        app=app,
        runtime=_FakeRuntime(),
        focus=focus,
        default_agent="receptionist_agent",
    )

    handler = app.actions[CONSTRAINT_REVIEW_ALL_ACTION_ID]
    ack_calls: list[bool] = []

    async def _ack():
        ack_calls.append(True)

    await handler(
        ack=_ack,
        body={
            "actions": [
                {
                    "action_id": CONSTRAINT_REVIEW_ALL_ACTION_ID,
                    "value": encode_metadata({"thread_ts": "T1", "user_id": "U1"}),
                }
            ],
            "channel": {"id": "C1"},
            "message": {"ts": "T1"},
            "trigger_id": "TRIGGER-1",
            "user": {"id": "U1"},
        },
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert ack_calls == [True]
    assert store.calls == [{"user_id": "U1", "channel_id": "C1", "thread_ts": "T1"}]
    assert client.opened
    opened = client.opened[0]
    assert opened["trigger_id"] == "TRIGGER-1"
    assert opened["view"]["callback_id"] == CONSTRAINT_REVIEW_LIST_VIEW_CALLBACK_ID
    view_text = "\n".join(
        block.get("text", {}).get("text", "")
        for block in opened["view"]["blocks"]
        if isinstance(block, dict) and block.get("type") == "section"
    )
    assert "Keep lunch break" in view_text
    assert "Declined rule" not in view_text


@pytest.mark.asyncio
async def test_constraint_review_all_action_acks_and_noops_without_metadata(monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", "", raising=False)

    client = _FakeClient()
    app = _FakeApp(client)
    focus = FocusManager(
        ttl_seconds=3600,
        allowed_agents=["receptionist_agent", "revisor_agent", "tasks_agent"],
    )
    register_handlers(
        app=app,
        runtime=_FakeRuntime(),
        focus=focus,
        default_agent="receptionist_agent",
    )
    handler = app.actions[CONSTRAINT_REVIEW_ALL_ACTION_ID]
    ack_calls: list[bool] = []

    async def _ack():
        ack_calls.append(True)

    await handler(
        ack=_ack,
        body={
            "actions": [{"action_id": CONSTRAINT_REVIEW_ALL_ACTION_ID, "value": ""}],
            "channel": {"id": "C1"},
            "message": {"ts": "T1"},
            "user": {"id": "U1"},
        },
        client=client,
        logger=types.SimpleNamespace(info=lambda *a, **k: None),
    )

    assert ack_calls == [True]
    assert client.opened == []
