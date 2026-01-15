from __future__ import annotations

from typing import Callable

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from autogen_core import AgentId
from autogen_agentchat.messages import TextMessage
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.core.config import settings
from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.agents.timeboxing.preferences import (
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_REVIEW_ACTION_ID,
    CONSTRAINT_REVIEW_VIEW_CALLBACK_ID,
    build_constraint_review_view,
    decode_metadata,
    parse_constraint_decisions,
)
from fateforger.slack_bot.messages import SlackBlockMessage

from .focus import FocusManager

try:
    from autogen_agentchat.messages import HandoffMessage
except Exception:  # pragma: no cover - optional dependency wiring
    HandoffMessage = None


def _strip_bot_mention(text: str, bot_user_id: str | None) -> str:
    cleaned = (text or "").strip()
    if not bot_user_id:
        return cleaned
    prefix = f"<@{bot_user_id}>"
    if not cleaned.startswith(prefix):
        return cleaned
    remainder = cleaned[len(prefix) :].lstrip()
    if remainder.startswith((",", ":", "-")):
        remainder = remainder[1:].lstrip()
    return remainder.strip()


def _extract_handoff_target(chat_message) -> str | None:
    if HandoffMessage and isinstance(chat_message, HandoffMessage):
        target = getattr(chat_message, "target", None)
    else:
        target = getattr(chat_message, "target", None)
    if not target:
        return None
    return (
        getattr(target, "name", None)
        or getattr(target, "agent_type", None)
        or getattr(target, "type", None)
    )


def _build_timeboxing_message(
    *,
    cleaned_text: str,
    user: str,
    channel: str,
    thread_ts: str | None,
    ts: str,
) -> StartTimeboxing | TimeboxingUserReply:
    if thread_ts:
        return TimeboxingUserReply(
            thread_ts=thread_ts,
            channel_id=channel,
            user_id=user,
            text=cleaned_text,
        )
    return StartTimeboxing(
        thread_ts=ts,
        channel_id=channel,
        user_id=user,
        user_input=cleaned_text,
    )


def _build_agent_message(
    *,
    agent_type: str,
    cleaned_text: str,
    user: str,
    channel: str,
    thread_ts: str | None,
    ts: str,
) -> object:
    if agent_type == "timeboxing_agent":
        return _build_timeboxing_message(
            cleaned_text=cleaned_text,
            user=user,
            channel=channel,
            thread_ts=thread_ts,
            ts=ts,
        )
    return TextMessage(content=cleaned_text, source=user)


async def route_slack_event(
    *,
    runtime,
    focus: FocusManager,
    default_agent: str,
    event: dict,
    bot_user_id: str | None,
    say: Callable,
) -> None:
    channel = event["channel"]
    user = event.get("user") or event.get("bot_id") or "unknown"
    text = event.get("text") or ""
    thread_ts = event.get("thread_ts")
    ts = event["ts"]

    key = FocusManager.thread_key(channel, thread_ts, ts)
    binding = focus.get_focus(key)
    agent_type = binding.agent_type if binding else default_agent

    cleaned_text = _strip_bot_mention(text, bot_user_id)
    msg = _build_agent_message(
        agent_type=agent_type,
        cleaned_text=cleaned_text,
        user=user,
        channel=channel,
        thread_ts=thread_ts,
        ts=ts,
    )

    result = await runtime.send_message(msg, recipient=AgentId(agent_type, key=key))
    chat_message = getattr(result, "chat_message", result)
    handoff_target = _extract_handoff_target(chat_message)
    if handoff_target and handoff_target != agent_type:
        try:
            focus.set_focus(key, handoff_target, by_user=user, note="handoff")
        except ValueError:
            handoff_target = None

    if handoff_target:
        handoff_msg = _build_agent_message(
            agent_type=handoff_target,
            cleaned_text=cleaned_text,
            user=user,
            channel=channel,
            thread_ts=thread_ts,
            ts=ts,
        )
        result = await runtime.send_message(
            handoff_msg, recipient=AgentId(handoff_target, key=key)
        )

    payload = _slack_payload_from_result(result)
    await say(thread_ts=thread_ts or ts, **payload)


def register_handlers(
    app: AsyncApp,
    runtime,
    focus: FocusManager,
    *,
    default_agent: str = "planner_agent",
):
    """
    Registers:
      - /ff-focus            : set focus to an agent for this thread
      - /ff-clear            : clear focus binding for this thread
      - /ff-status           : show current focus / allowed agents
      - App mention handler  : route @mentions via focus→agent
      - DM handler           : route DMs via focus→agent
    """
    constraint_store: ConstraintStore | None = None

    async def _get_constraint_store() -> ConstraintStore | None:
        nonlocal constraint_store
        if constraint_store:
            return constraint_store
        if not settings.database_url:
            return None
        engine = create_async_engine(_coerce_async_database_url(settings.database_url))
        await ensure_constraint_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        constraint_store = ConstraintStore(sessionmaker)
        return constraint_store

    # --- Slash Commands ---

    @app.command("/ff-focus")
    async def cmd_focus(ack, body, respond, logger):
        await ack()
        user_id = body["user_id"]
        channel_id = body["channel_id"]
        text = (body.get("text") or "").strip()
        if not text:
            await respond(
                text=f"Usage: `/ff-focus <agent_type> [note]`\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )
            return

        parts = text.split(maxsplit=1)
        agent_type = parts[0].strip()
        note = parts[1].strip() if len(parts) > 1 else None

        # For slash commands, there’s no thread_ts; bind to the next messages in this channel root
        key = FocusManager.thread_key(
            channel_id,
            thread_ts=None,
            ts=(
                body["container"]["message_ts"]
                if "container" in body and "message_ts" in body["container"]
                else body["trigger_id"]
            ),
        )
        try:
            binding = focus.set_focus(key, agent_type, by_user=user_id, note=note)
            await respond(
                text=f"Focus set to *{binding.agent_type}* for this thread (TTL active). {f'Note: {binding.note}' if binding.note else ''}",
                response_type="ephemeral",
            )
        except ValueError as e:
            await respond(text=str(e), response_type="ephemeral")

    @app.command("/ff-clear")
    async def cmd_clear(ack, body, respond):
        await ack()
        channel_id = body["channel_id"]
        key = FocusManager.thread_key(
            channel_id, thread_ts=None, ts=body.get("trigger_id", "root")
        )
        removed = focus.clear_focus(key)
        msg = (
            "Focus cleared for this thread."
            if removed
            else "No focus was set for this thread."
        )
        await respond(text=msg, response_type="ephemeral")

    @app.command("/ff-status")
    async def cmd_status(ack, body, respond):
        await ack()
        channel_id = body["channel_id"]
        key = FocusManager.thread_key(
            channel_id, thread_ts=None, ts=body.get("trigger_id", "root")
        )
        binding = focus.get_focus(key)
        if binding:
            note = f"\n• note: {binding.note}" if binding.note else ""
            await respond(
                text=f"Focus for this thread: *{binding.agent_type}* (set by <@{binding.set_by_user}>){note}\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )
        else:
            await respond(
                text=f"No focus set for this thread.\nAllowed: `{', '.join(focus.allowed_agents())}`",
                response_type="ephemeral",
            )

    # --- Routing helpers ---

    @app.action(CONSTRAINT_REVIEW_ACTION_ID)
    async def on_constraint_review_action(ack, body, client, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        metadata = decode_metadata(value)
        thread_ts = metadata.get("thread_ts")
        user_id = metadata.get("user_id")
        if not thread_ts or not user_id:
            return
        channel_id = body.get("channel", {}).get("id")
        if not channel_id:
            return

        store = await _get_constraint_store()
        if not store:
            return
        constraints = await store.list_constraints(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            status=ConstraintStatus.PROPOSED,
        )
        if not constraints:
            await client.chat_postMessage(
                channel=channel_id,
                text="No pending constraints to review.",
                thread_ts=thread_ts,
            )
            return

        view = build_constraint_review_view(
            constraints, channel_id=channel_id, thread_ts=thread_ts
        )
        await client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view(CONSTRAINT_REVIEW_VIEW_CALLBACK_ID)
    async def on_constraint_review_submit(ack, body, client, logger):
        await ack()
        store = await _get_constraint_store()
        if not store:
            return
        state = body.get("view", {}).get("state", {}).get("values", {})
        decisions = parse_constraint_decisions(state)
        if decisions:
            await store.update_constraint_statuses(
                user_id=body.get("user", {}).get("id", ""),
                decisions=decisions,
            )
        metadata = body.get("view", {}).get("private_metadata") or ""
        info = decode_metadata(metadata)
        channel_id = info.get("channel_id")
        thread_ts = info.get("thread_ts")
        if channel_id and thread_ts:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Saved your constraint decisions.",
            )

    # --- App mention in public channels ---
    @app.event("app_mention")
    async def on_app_mention(body, say, context, client, logger):
        await route_slack_event(
            runtime=runtime,
            focus=focus,
            default_agent=default_agent,
            event=body["event"],
            bot_user_id=context.get("bot_user_id"),
            say=say,
        )

    # --- Direct messages (IM) ---
    @app.event("message")
    async def on_message_events(body, say, context, client, logger):
        event = body.get("event", {})
        if event.get("channel_type") != "im":
            return  # ignore non-DMs; public channels handled by app_mention
        # Ignore bot messages to avoid loops
        if event.get("subtype") == "bot_message":
            return
        await route_slack_event(
            runtime=runtime,
            focus=focus,
            default_agent=default_agent,
            event=event,
            bot_user_id=context.get("bot_user_id"),
            say=say,
        )


def _slack_payload_from_result(result) -> dict:
    if isinstance(result, SlackBlockMessage):
        return {"text": result.text, "blocks": result.blocks}
    chat_message = getattr(result, "chat_message", None)
    if isinstance(chat_message, SlackBlockMessage):
        return {"text": chat_message.text, "blocks": chat_message.blocks}
    content = getattr(chat_message, "content", None)
    if content is None and isinstance(result, TextMessage):
        content = result.content
    return {"text": content or "(no response)"}


def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
