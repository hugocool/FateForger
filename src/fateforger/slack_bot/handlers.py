from __future__ import annotations

from typing import Callable

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.agents.timeboxing.preferences import (
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)
from fateforger.core.config import settings
from fateforger.slack_bot.bootstrap import ensure_workspace_ready
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_REVIEW_ACTION_ID,
    CONSTRAINT_REVIEW_VIEW_CALLBACK_ID,
    build_constraint_review_view,
    decode_metadata,
    parse_constraint_decisions,
)
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.planning import (
    FF_PLANNING_PICK_TIME_ACTION_ID,
    FF_PLANNING_SCHEDULE_ACTION_ID,
    PlanningCoordinator,
)

from .focus import FocusManager
from .workspace import DEFAULT_PERSONAS, SlackPersona, WorkspaceRegistry
from .workspace_store import SlackWorkspaceStore, ensure_slack_workspace_schema

try:
    from autogen_agentchat.messages import HandoffMessage
except Exception:  # pragma: no cover - optional dependency wiring
    HandoffMessage = None


FF_APPHOME_WEEKLY_REVIEW_ACTION_ID = "ff_apphome_weekly_review"


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
    if isinstance(target, str):
        return target
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
    force_channel: str | None = None,
    force_thread_root: str | None = None,
    force_reply: bool | None = None,
) -> StartTimeboxing | TimeboxingUserReply:
    resolved_channel = force_channel or channel
    resolved_thread_root = force_thread_root or (thread_ts or ts)
    is_reply = force_reply if force_reply is not None else bool(thread_ts)

    if is_reply:
        return TimeboxingUserReply(
            thread_ts=resolved_thread_root,
            channel_id=resolved_channel,
            user_id=user,
            text=cleaned_text,
        )
    return StartTimeboxing(
        thread_ts=resolved_thread_root,
        channel_id=resolved_channel,
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
    force_channel: str | None = None,
    force_thread_root: str | None = None,
    force_reply: bool | None = None,
) -> object:
    if agent_type == "timeboxing_agent":
        return _build_timeboxing_message(
            cleaned_text=cleaned_text,
            user=user,
            channel=channel,
            thread_ts=thread_ts,
            ts=ts,
            force_channel=force_channel,
            force_thread_root=force_thread_root,
            force_reply=force_reply,
        )
    return TextMessage(content=cleaned_text, source=user)


def _with_agent_attribution(payload: dict, agent_type: str) -> dict:
    blocks = payload.get("blocks")
    if blocks:
        decorated = list(blocks)
        decorated.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"_agent: *{agent_type}*_"}],
            }
        )
        return {"text": payload.get("text") or "", "blocks": decorated}
    text = payload.get("text") or "(no response)"
    return {"text": f"*{agent_type}*\n{text}"}


def _origin_label(event: dict) -> str:
    if event.get("channel_type") == "im":
        return "a DM"
    channel = event.get("channel")
    if channel:
        return f"<#{channel}>"
    return "Slack"


def _build_app_home_view(*, user_id: str, focus_agent: str | None) -> dict:
    directory = WorkspaceRegistry.get_global()
    timeboxing = _channel_for_agent("timeboxing_agent")
    strategy = _channel_for_agent("revisor_agent")
    tasks = _channel_for_agent("tasks_agent")
    ops = _channel_for_agent("planner_agent")

    def _mention(cid: str | None, fallback: str) -> str:
        if cid:
            return f"<#{cid}>"
        return fallback

    fields = [
        {"type": "mrkdwn", "text": f"*Timeboxer*\n{_mention(timeboxing, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*Revisor*\n{_mention(strategy, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*Task Marshal*\n{_mention(tasks, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*Planner*\n{_mention(ops, 'not configured')}"},
    ]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Welcome to FateForger"}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Your focus:* `{focus_agent or 'none'}`",
            },
        },
        {"type": "divider"},
        {"type": "section", "fields": fields},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Start Weekly Review"},
                    "action_id": FF_APPHOME_WEEKLY_REVIEW_ACTION_ID,
                    "value": user_id,
                }
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": "Tip: run `/setup` to auto-provision channels if needed.",
                }
            ],
        },
    ]

    return {"type": "home", "blocks": blocks}


def _persona_for_agent(agent_type: str) -> SlackPersona | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        persona = directory.persona_for_agent(agent_type)
        if persona:
            return persona
    return DEFAULT_PERSONAS.get(agent_type)


def _channel_for_agent(agent_type: str) -> str | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        cid = directory.channel_for_agent(agent_type)
        if cid:
            return cid
    if agent_type == "timeboxing_agent":
        return (settings.slack_timeboxing_channel_id or "").strip() or None
    if agent_type == "revisor_agent":
        return (getattr(settings, "slack_strategy_channel_id", "") or "").strip() or None
    if agent_type == "tasks_agent":
        return (getattr(settings, "slack_tasks_channel_id", "") or "").strip() or None
    if agent_type == "planner_agent":
        return (getattr(settings, "slack_ops_channel_id", "") or "").strip() or None
    return None


def _general_channel_id() -> str | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        cid = directory.channel_for_name("general")
        if cid:
            return cid
    return (getattr(settings, "slack_general_channel_id", "") or "").strip() or None


async def _dm_thread_link(
    client: AsyncWebClient,
    *,
    user_id: str,
    target_channel: str,
    thread_root_ts: str,
    agent_label: str,
) -> None:
    try:
        permalink_res = await client.chat_getPermalink(
            channel=target_channel, message_ts=thread_root_ts
        )
        permalink = permalink_res.get("permalink")
    except Exception:
        permalink = None
    if not permalink:
        return
    dm = await client.conversations_open(users=[user_id])
    dm_channel = (dm.get("channel") or {}).get("id")
    if not dm_channel:
        return
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Handed off to *{agent_label}*.\nOpen the thread to continue:",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Go to Thread"},
                    "url": permalink,
                    "action_id": "ff_open_thread",
                }
            ],
        },
    ]
    await client.chat_postMessage(channel=dm_channel, text=permalink, blocks=blocks)


async def route_slack_event(
    *,
    runtime,
    focus: FocusManager,
    default_agent: str,
    event: dict,
    bot_user_id: str | None,
    say: Callable,
    client: AsyncWebClient,
) -> None:
    channel = event["channel"]
    user = event.get("user") or event.get("bot_id") or "unknown"
    text = event.get("text") or ""
    thread_ts = event.get("thread_ts")
    ts = event["ts"]

    origin_key = FocusManager.thread_key(channel, thread_ts, ts)
    binding = focus.get_focus(origin_key)
    agent_type = binding.agent_type if binding else default_agent

    cleaned_text = _strip_bot_mention(text, bot_user_id)
    origin_processing_msg = await say(
        text=f":hourglass_flowing_sand: *{agent_type}* is thinking...",
        thread_ts=thread_ts or ts,
    )

    async def _origin_update(*, text: str, blocks=None) -> None:
        payload = {
            "channel": origin_processing_msg["channel"],
            "ts": origin_processing_msg["ts"],
            "text": text,
        }
        if blocks:
            payload["blocks"] = blocks
        await client.chat_update(**payload)

    async def _permalink(channel_id: str, message_ts: str) -> str | None:
        try:
            res = await client.chat_getPermalink(channel=channel_id, message_ts=message_ts)
            return res.get("permalink")
        except Exception:
            return None

    redirect = focus.get_redirect(origin_key)
    if redirect and agent_type == redirect.agent_type:
        focus.set_user_focus(user, redirect.agent_type)
        persona = _persona_for_agent(redirect.agent_type)
        processing_payload = {
            "channel": redirect.target_channel,
            "thread_ts": redirect.target_thread_ts,
            "text": f":hourglass_flowing_sand: *{redirect.agent_type}* is thinking...",
        }
        if persona and persona.username:
            processing_payload["username"] = persona.username
        if persona and persona.icon_emoji:
            processing_payload["icon_emoji"] = persona.icon_emoji
        if persona and persona.icon_url:
            processing_payload["icon_url"] = persona.icon_url
        processing = await client.chat_postMessage(**processing_payload)

        msg = _build_agent_message(
            agent_type=redirect.agent_type,
            cleaned_text=cleaned_text,
            user=user,
            channel=redirect.target_channel,
            thread_ts=redirect.target_thread_ts,
            ts=redirect.target_thread_ts,
            force_channel=redirect.target_channel,
            force_thread_root=redirect.target_thread_ts,
            force_reply=True,
        )
        result = await runtime.send_message(
            msg, recipient=AgentId(redirect.agent_type, key=redirect.target_key)
        )
        payload = _slack_payload_from_result(result)
        update = {
            "channel": redirect.target_channel,
            "ts": processing["ts"],
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)
        link = await _permalink(redirect.target_channel, redirect.target_thread_ts)
        await _origin_update(
            text=(
                f":left_right_arrow: Continuing in <#{redirect.target_channel}>."
                + (f" {link}" if link else "")
            )
        )
        return

    msg = _build_agent_message(
        agent_type=agent_type,
        cleaned_text=cleaned_text,
        user=user,
        channel=channel,
        thread_ts=thread_ts,
        ts=ts,
    )
    result = await runtime.send_message(msg, recipient=AgentId(agent_type, key=origin_key))
    chat_message = getattr(result, "chat_message", result)
    handoff_target = _extract_handoff_target(chat_message)

    if handoff_target and handoff_target != agent_type:
        try:
            focus.set_focus(origin_key, handoff_target, by_user=user, note="handoff")
        except ValueError:
            handoff_target = None

    if handoff_target:
        focus.set_user_focus(user, handoff_target)
        target_channel = _channel_for_agent(handoff_target)
        if target_channel and target_channel != channel:
            try:
                persona = _persona_for_agent(handoff_target)
                root_payload = {
                    "channel": target_channel,
                    "text": (
                        f"Incoming request from <@{user}> (requested in {_origin_label(event)}):\n"
                        f"> {cleaned_text}"
                    ),
                }
                if persona and persona.username:
                    root_payload["username"] = persona.username
                if persona and persona.icon_emoji:
                    root_payload["icon_emoji"] = persona.icon_emoji
                if persona and persona.icon_url:
                    root_payload["icon_url"] = persona.icon_url
                root = await client.chat_postMessage(**root_payload)
                target_thread_ts = root["ts"]

                redirect = focus.set_redirect(
                    origin_key,
                    target_channel=target_channel,
                    target_thread_ts=target_thread_ts,
                    agent_type=handoff_target,
                    by_user=user,
                    note="auto-redirect",
                )
                focus.set_focus(
                    redirect.target_key,
                    handoff_target,
                    by_user=user,
                    note="auto-redirect",
                )
                focus.set_focus(
                    origin_key,
                    handoff_target,
                    by_user=user,
                    note="auto-redirect",
                )

                link = await _permalink(target_channel, target_thread_ts)
                await _origin_update(
                    text=(
                        f":left_right_arrow: Continuing in <#{target_channel}>."
                        + (f" {link}" if link else "")
                    )
                )
                try:
                    await _dm_thread_link(
                        client,
                        user_id=user,
                        target_channel=target_channel,
                        thread_root_ts=target_thread_ts,
                        agent_label=(persona.username if persona else handoff_target),
                    )
                except Exception:
                    pass

                processing_payload = {
                    "channel": target_channel,
                    "thread_ts": target_thread_ts,
                    "text": f":hourglass_flowing_sand: *{handoff_target}* is thinking...",
                }
                if persona and persona.username:
                    processing_payload["username"] = persona.username
                if persona and persona.icon_emoji:
                    processing_payload["icon_emoji"] = persona.icon_emoji
                if persona and persona.icon_url:
                    processing_payload["icon_url"] = persona.icon_url
                processing = await client.chat_postMessage(**processing_payload)

                handoff_msg = _build_agent_message(
                    agent_type=handoff_target,
                    cleaned_text=cleaned_text,
                    user=user,
                    channel=target_channel,
                    thread_ts=target_thread_ts,
                    ts=target_thread_ts,
                    force_channel=target_channel,
                    force_thread_root=target_thread_ts,
                    force_reply=False,
                )
                result = await runtime.send_message(
                    handoff_msg,
                    recipient=AgentId(handoff_target, key=redirect.target_key),
                )
                payload = _slack_payload_from_result(result)
                update = {
                    "channel": target_channel,
                    "ts": processing["ts"],
                    "text": payload.get("text", "") or "",
                }
                if payload.get("blocks"):
                    update["blocks"] = payload["blocks"]
                await client.chat_update(**update)
                return
            except Exception:
                # Fall back to in-thread handling if the target channel isn't accessible.
                pass

    if handoff_target:
        await _origin_update(text=f":left_right_arrow: Handing off to *{handoff_target}*...")
        handoff_msg = _build_agent_message(
            agent_type=handoff_target,
            cleaned_text=cleaned_text,
            user=user,
            channel=channel,
            thread_ts=thread_ts,
            ts=ts,
        )
        result = await runtime.send_message(
            handoff_msg, recipient=AgentId(handoff_target, key=origin_key)
        )
        payload = _with_agent_attribution(_slack_payload_from_result(result), handoff_target)
        await _origin_update(text=payload.get("text", ""), blocks=payload.get("blocks"))
        return

    focus.set_user_focus(user, agent_type)
    payload = _with_agent_attribution(_slack_payload_from_result(result), agent_type)
    await _origin_update(text=payload.get("text", ""), blocks=payload.get("blocks"))


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
    workspace_store: SlackWorkspaceStore | None = None
    planning = PlanningCoordinator(runtime=runtime, focus=focus, client=app.client)
    planning.attach_reconciler_dispatch()

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

    async def _get_workspace_store() -> SlackWorkspaceStore | None:
        nonlocal workspace_store
        if workspace_store:
            return workspace_store
        if not settings.database_url:
            return None
        engine = create_async_engine(_coerce_async_database_url(settings.database_url))
        await ensure_slack_workspace_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        workspace_store = SlackWorkspaceStore(sessionmaker)
        return workspace_store

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

    async def _run_setup(respond, client) -> None:
        store = await _get_workspace_store()
        directory = await ensure_workspace_ready(client, store=store)
        if not directory:
            await respond(
                text="Setup failed. Check the bot logs and Slack scopes.",
                response_type="ephemeral",
            )
            return
        await respond(
            text=(
                "Workspace ready.\n"
                + "\n".join(
                    [
                        f"• #{name}: `{cid}`"
                        for name, cid in sorted(directory.channels_by_name.items())
                        if name in {"general", "timeboxing", "strategy", "tasks", "ops"}
                    ]
                )
            ),
            response_type="ephemeral",
        )

    @app.command("/setup")
    async def cmd_setup(ack, body, respond, client, logger):
        await ack()
        await _run_setup(respond, client)

    @app.command("/ff-setup")
    async def cmd_ff_setup(ack, body, respond, client, logger):
        await ack()
        await _run_setup(respond, client)

    # --- Routing helpers ---

    @app.action(FF_PLANNING_SCHEDULE_ACTION_ID)
    async def on_planning_schedule_action(ack, body, client, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        if channel_id and message_ts and value:
            await planning.handle_schedule_action(
                value=value,
                channel_id=channel_id,
                thread_ts=message_ts,
                actor_user_id=actor_user_id,
            )

    @app.action(FF_PLANNING_PICK_TIME_ACTION_ID)
    async def on_planning_pick_time_action(ack, body, client, logger):
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        if channel_id and message_ts:
            await planning.handle_pick_time_action(channel_id=channel_id, thread_ts=message_ts)

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

    # --- App Home (Command Center) ---
    @app.event("app_home_opened")
    async def on_app_home_opened(body, event, client, logger):
        user_id = event.get("user")
        if not user_id:
            return
        focus_agent = focus.get_user_focus(user_id)
        view = _build_app_home_view(user_id=user_id, focus_agent=focus_agent)
        await client.views_publish(user_id=user_id, view=view)

    @app.action(FF_APPHOME_WEEKLY_REVIEW_ACTION_ID)
    async def on_apphome_weekly_review(ack, body, client, logger):
        await ack()
        user_id = (body.get("user") or {}).get("id") or ""
        if not user_id:
            return
        channel_id = _channel_for_agent("revisor_agent")
        if not channel_id:
            dm = await client.conversations_open(users=[user_id])
            dm_channel = (dm.get("channel") or {}).get("id")
            if dm_channel:
                await client.chat_postMessage(
                    channel=dm_channel,
                    text="Revisor channel not configured. Run `/setup` first.",
                )
            return

        persona = _persona_for_agent("revisor_agent")
        root_payload = {
            "channel": channel_id,
            "text": f"Initiating Weekly Review for <@{user_id}>...",
        }
        if persona and persona.username:
            root_payload["username"] = persona.username
        if persona and persona.icon_emoji:
            root_payload["icon_emoji"] = persona.icon_emoji
        if persona and persona.icon_url:
            root_payload["icon_url"] = persona.icon_url
        root = await client.chat_postMessage(**root_payload)
        thread_root_ts = root["ts"]

        runtime_key = FocusManager.thread_key(channel_id, thread_ts=thread_root_ts, ts=thread_root_ts)
        try:
            focus.set_focus(runtime_key, "revisor_agent", by_user=user_id, note="apphome")
        except Exception:
            pass
        focus.set_user_focus(user_id, "revisor_agent")

        await _dm_thread_link(
            client,
            user_id=user_id,
            target_channel=channel_id,
            thread_root_ts=thread_root_ts,
            agent_label=(persona.username if persona else "Revisor"),
        )

        processing_payload = {
            "channel": channel_id,
            "thread_ts": thread_root_ts,
            "text": ":hourglass_flowing_sand: *revisor_agent* is thinking...",
        }
        if persona and persona.username:
            processing_payload["username"] = persona.username
        if persona and persona.icon_emoji:
            processing_payload["icon_emoji"] = persona.icon_emoji
        if persona and persona.icon_url:
            processing_payload["icon_url"] = persona.icon_url
        processing = await client.chat_postMessage(**processing_payload)

        result = await runtime.send_message(
            TextMessage(content="Start a weekly review.", source=user_id),
            recipient=AgentId("revisor_agent", key=runtime_key),
        )
        payload = _slack_payload_from_result(result)
        update = {"channel": channel_id, "ts": processing["ts"], "text": payload.get("text", "") or ""}
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)

        # Refresh App Home view (focus updated)
        view = _build_app_home_view(user_id=user_id, focus_agent=focus.get_user_focus(user_id))
        await client.views_publish(user_id=user_id, view=view)

    # --- App mention in public channels ---
    @app.event("app_mention")
    async def on_app_mention(body, say, context, client, logger):
        event = body.get("event", {})
        user_id = event.get("user") or ""
        channel_id = event.get("channel") or ""
        channel_type = event.get("channel_type") or "channel"
        if user_id and channel_id:
            await planning.maybe_register_user(
                user_id=user_id, channel_id=channel_id, channel_type=channel_type
            )
        await route_slack_event(
            runtime=runtime,
            focus=focus,
            default_agent=default_agent,
            event=event,
            bot_user_id=context.get("bot_user_id"),
            say=say,
            client=client,
        )

    # --- Direct messages (IM) ---
    @app.event("message")
    async def on_message_events(body, say, context, client, logger):
        event = body.get("event", {})
        # Ignore bot messages to avoid loops
        if event.get("subtype") == "bot_message":
            return
        channel_id = event.get("channel")
        ts = event.get("ts")
        if not channel_id or not ts:
            return
        user_id = event.get("user") or ""
        if user_id:
            await planning.maybe_register_user(
                user_id=user_id,
                channel_id=channel_id,
                channel_type=event.get("channel_type") or "channel",
            )
            thread_ts = event.get("thread_ts")
            if await planning.maybe_handle_time_reply(
                user_id=user_id,
                channel_id=channel_id,
                thread_ts=thread_ts,
                text=event.get("text") or "",
            ):
                return
        thread_ts = event.get("thread_ts")
        key = FocusManager.thread_key(channel_id, thread_ts, ts)
        if event.get("channel_type") != "im":
            # Only handle non-DMs when the thread has explicit focus (e.g., timeboxing threads).
            bot_id = context.get("bot_user_id")
            if bot_id and f"<@{bot_id}>" in (event.get("text") or ""):
                return  # app_mention handler covers this
            general_id = _general_channel_id()
            if channel_id != general_id and not focus.get_focus(key):
                return
        await route_slack_event(
            runtime=runtime,
            focus=focus,
            default_agent=default_agent,
            event=event,
            bot_user_id=context.get("bot_user_id"),
            say=say,
            client=client,
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
