from __future__ import annotations

import logging
from typing import Callable
import asyncio
import re

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
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.planning import (
    FF_PLANNING_PICK_TIME_ACTION_ID,
    FF_PLANNING_SCHEDULE_ACTION_ID,
    FF_PLANNING_TIME_MODAL_CALLBACK_ID,
    PlanningCoordinator,
)
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID,
    FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    TimeboxingCommitCoordinator,
)

from .focus import FocusManager
from .ui import link_button, open_link_blocks
from .workspace import DEFAULT_PERSONAS, SlackPersona, WorkspaceRegistry
from .workspace_store import SlackWorkspaceStore, ensure_slack_workspace_schema

try:
    from autogen_agentchat.messages import HandoffMessage
except Exception:  # pragma: no cover - optional dependency wiring
    HandoffMessage = None


FF_APPHOME_WEEKLY_REVIEW_ACTION_ID = "ff_apphome_weekly_review"

_TIMEBOXING_STATE_EMOJI = {
    "active": ":large_yellow_circle:",
    "done": ":white_check_mark:",
    "canceled": ":no_entry_sign:",
}

logger = logging.getLogger(__name__)


def _timeboxing_title_from_text(text: str) -> str:
    cleaned = re.sub(r"\\s+", " ", (text or "")).strip()
    if not cleaned:
        return "session"
    if len(cleaned) > 80:
        return cleaned[:77].rstrip() + "…"
    return cleaned


def _timeboxing_excerpt_from_text(text: str) -> str:
    cleaned = re.sub(r"\\s+", " ", (text or "")).strip()
    if len(cleaned) > 200:
        return cleaned[:197].rstrip() + "…"
    return cleaned


def _timeboxing_thread_root_text(*, title: str, request_excerpt: str | None, state: str) -> str:
    emoji = _TIMEBOXING_STATE_EMOJI.get(state, _TIMEBOXING_STATE_EMOJI["active"])
    header = f"{emoji} Timeboxing — {title}"
    if request_excerpt:
        return header + "\n" + f"> {request_excerpt}"
    return header


def _extract_thread_state(result) -> str | None:
    for obj in (result, getattr(result, "chat_message", None)):
        state = getattr(obj, "thread_state", None)
        if isinstance(state, str) and state.strip():
            return state.strip()
    return None


async def _maybe_update_timeboxing_thread_header(
    *,
    client: AsyncWebClient,
    focus: FocusManager,
    thread_key: str,
    state: str,
) -> None:
    if state not in {"done", "canceled"}:
        return
    label = focus.update_thread_state(thread_key, state=state)
    if not label:
        return
    try:
        channel_id, thread_root_ts = thread_key.split(":", 1)
    except Exception:
        return
    try:
        await client.chat_update(
            channel=channel_id,
            ts=thread_root_ts,
            text=_timeboxing_thread_root_text(
                title=label.title,
                request_excerpt=label.request_excerpt,
                state=label.state,
            ),
        )
    except Exception:
        return


async def _invite_user_to_channels_best_effort(
    client: AsyncWebClient, *, user_id: str, channel_ids: list[str]
) -> None:
    if not user_id:
        return
    for channel_id in channel_ids:
        if not channel_id:
            continue
        try:
            await client.conversations_invite(channel=channel_id, users=[user_id])
        except Exception:
            # Slack workspaces vary: bots may be blocked from inviting users, or scopes may be missing.
            # This is best-effort; the user can always join manually.
            continue


def _format_workspace_ready_response(directory) -> str:
    team_id = getattr(directory, "team_id", None) or ""
    channels_by_name = getattr(directory, "channels_by_name", {}) or {}

    def _line(name: str) -> str | None:
        cid = channels_by_name.get(name)
        if not cid:
            return None
        return f"• <#{cid}> (`{cid}`)"

    lines = []
    for name in ["general", "plan-sessions", "review", "task-marshalling", "scheduling", "admonishments"]:
        line = _line(name)
        if line:
            lines.append(line)

    hint = (
        "Note: FateForger can *try* to invite you to these channels, but some workspaces block apps from doing this. "
        "If you don’t see them, click the channel mentions above (or Slack → Browse channels) and *join*, then optionally pin."
    )
    return "Workspace ready.\n" + "\n".join(lines + ["", hint])


def _workspace_ready_blocks(directory) -> list[dict]:
    team_id = getattr(directory, "team_id", None) or ""
    channels_by_name = getattr(directory, "channels_by_name", {}) or {}

    channels = []
    for name in ["general", "plan-sessions", "review", "task-marshalling", "scheduling", "admonishments"]:
        cid = channels_by_name.get(name)
        if cid:
            channels.append((name, cid))

    channel_mentions = "\n".join([f"• <#{cid}> (`{cid}`)" for _name, cid in channels])
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Workspace ready.\nOpen and join these channels:",
            },
        }
    ]
    if channel_mentions:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": channel_mentions}})

    if team_id:
        buttons = [
            link_button(
                text=f"#{name}",
                url=f"https://app.slack.com/client/{team_id}/{cid}",
                action_id=f"ff_open_channel_{name}",
            )
            for name, cid in channels
        ]
        for i in range(0, len(buttons), 5):
            blocks.append({"type": "actions", "elements": buttons[i : i + 5]})

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "Note: FateForger can *try* to invite you to these channels, but some workspaces block apps from doing this. "
                    "If you don’t see them, click the buttons above (or Slack → Browse channels) and *join*, then optionally pin."
                ),
            },
        }
    )
    return blocks


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


def _safe_exc_summary(exc: Exception) -> str:
    msg = " ".join(str(exc).split())
    if not msg:
        return type(exc).__name__
    # Avoid leaking tokens via headers/URLs etc.
    for needle in ("sk-", "or-", "xoxb-", "xapp-"):
        if needle in msg:
            msg = msg.replace(needle, f"{needle}***")
    return (msg[:240] + "…") if len(msg) > 240 else msg


def _build_app_home_view(*, user_id: str, focus_agent: str | None) -> dict:
    directory = WorkspaceRegistry.get_global()
    schedular = _channel_for_agent("timeboxing_agent")
    reviews = _channel_for_agent("revisor_agent")
    task_marshal = _channel_for_agent("tasks_agent")
    scheduling = _channel_for_agent("planner_agent")

    def _mention(cid: str | None, fallback: str) -> str:
        if cid:
            return f"<#{cid}>"
        return fallback

    fields = [
        {"type": "mrkdwn", "text": f"*The Schedular*\n{_mention(schedular, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*Reviewer*\n{_mention(reviews, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*TaskMarshal*\n{_mention(task_marshal, 'not configured')}"},
        {"type": "mrkdwn", "text": f"*Scheduling*\n{_mention(scheduling, 'not configured')}"},
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


def _agent_for_channel(channel_id: str) -> str | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        for agent_type, cid in (directory.channels_by_agent or {}).items():
            if cid == channel_id:
                return agent_type
    # Fallback: check env-configured specialist channel IDs (works without DB bootstrap).
    for agent_type in ("timeboxing_agent", "revisor_agent", "tasks_agent", "planner_agent"):
        if _channel_for_agent(agent_type) == channel_id:
            return agent_type
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
        *open_link_blocks(
            text=f"Handed off to *{agent_label}*.\nOpen the thread to continue:",
            url=permalink,
            button_text="Go to Thread",
            action_id="ff_open_thread",
        ),
    ]
    payload = {"channel": dm_channel, "text": permalink, "blocks": blocks}
    persona = _persona_for_agent("receptionist_agent")
    if persona and persona.username:
        payload["username"] = persona.username
    if persona and persona.icon_emoji:
        payload["icon_emoji"] = persona.icon_emoji
    if persona and persona.icon_url:
        payload["icon_url"] = persona.icon_url
    await client.chat_postMessage(**payload)


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
    channel_type = event.get("channel_type")
    is_dm = channel_type == "im" or str(channel).startswith("D")

    # In DMs, avoid creating a new "focus thread" per message (ts changes every message).
    # Instead, keep a stable key so multi-turn conversations work without requiring threads.
    if is_dm and not thread_ts:
        origin_key = f"{channel}:dm"
    else:
        origin_key = FocusManager.thread_key(channel, thread_ts, ts)
    binding = focus.get_focus(origin_key)
    user_focus = focus.get_user_focus(user) if (is_dm and user and user != "unknown") else None
    channel_default_agent = _agent_for_channel(channel) if not is_dm else None
    agent_type = binding.agent_type if binding else (user_focus or channel_default_agent or default_agent)

    cleaned_text = _strip_bot_mention(text, bot_user_id)
    # Post the "thinking" message with the active agent persona, so the eventual reply
    # (via chat.update) keeps the correct name/icon.
    origin_thread_root_ts = (thread_ts or ts) if (thread_ts or (not is_dm)) else None
    origin_processing_payload: dict = {
        "channel": channel,
        "text": f":hourglass_flowing_sand: *{agent_type}* is thinking...",
    }
    if origin_thread_root_ts:
        origin_processing_payload["thread_ts"] = origin_thread_root_ts
    persona = _persona_for_agent(agent_type)
    if persona and persona.username:
        origin_processing_payload["username"] = persona.username
    if persona and persona.icon_emoji:
        origin_processing_payload["icon_emoji"] = persona.icon_emoji
    if persona and persona.icon_url:
        origin_processing_payload["icon_url"] = persona.icon_url
    origin_processing_msg = await client.chat_postMessage(**origin_processing_payload)

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

    async def _origin_link_to_thread(*, channel_id: str, thread_ts: str, agent_label: str) -> None:
        link = await _permalink(channel_id, thread_ts)
        if not link:
            await _origin_update(text=f":left_right_arrow: Continuing in <#{channel_id}>.")
            return
        blocks = open_link_blocks(
            text=f":left_right_arrow: Continuing in <#{channel_id}> (agent: *{agent_label}*).",
            url=link,
            button_text="Go to Thread",
            action_id="ff_open_thread",
        )
        await _origin_update(text=f":left_right_arrow: Continuing in <#{channel_id}>.", blocks=blocks)

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
        try:
            result = await runtime.send_message(
                msg, recipient=AgentId(redirect.agent_type, key=redirect.target_key)
            )
        except asyncio.TimeoutError:
            await client.chat_update(
                channel=redirect.target_channel,
                ts=processing["ts"],
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again.",
            )
            await _origin_update(
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again."
            )
            return
        except Exception as e:
            logger.exception(
                "runtime.send_message failed (redirect agent=%s key=%s)",
                redirect.agent_type,
                redirect.target_key,
            )
            await client.chat_update(
                channel=redirect.target_channel,
                ts=processing["ts"],
                text=":warning: Something went wrong while handling that request. Check bot logs.",
            )
            await _origin_update(text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}")
            return

        payload = _slack_payload_from_result(result)
        update = {
            "channel": redirect.target_channel,
            "ts": processing["ts"],
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)
        await _maybe_update_timeboxing_thread_header(
            client=client,
            focus=focus,
            thread_key=redirect.target_key,
            state=_extract_thread_state(result) or "",
        )
        if not is_dm:
            await _origin_link_to_thread(
                channel_id=redirect.target_channel,
                thread_ts=redirect.target_thread_ts,
                agent_label=(persona.username if persona else redirect.agent_type),
            )
        return

    msg = _build_agent_message(
        agent_type=agent_type,
        cleaned_text=cleaned_text,
        user=user,
        channel=channel,
        thread_ts=thread_ts,
        ts=ts,
        force_thread_root=("dm" if (is_dm and agent_type == "timeboxing_agent") else None),
        force_reply=(True if (is_dm and agent_type == "timeboxing_agent") else None),
    )
    try:
        result = await runtime.send_message(
            msg, recipient=AgentId(agent_type, key=origin_key)
        )
    except asyncio.TimeoutError:
        await _origin_update(
            text=(
                ":hourglass_flowing_sand: Timed out waiting for tools/LLM. "
                "Please try again in a moment."
            )
        )
        return
    except Exception as e:
        logger.exception("runtime.send_message failed (agent=%s key=%s)", agent_type, origin_key)
        await _origin_update(text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}")
        return
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
        # For timeboxing, always anchor the session in the dedicated channel thread (when configured),
        # even if the user started in a DM. The DM becomes the control surface (buttons/modals),
        # and the channel thread becomes the durable workspace/log.
        should_redirect = bool(target_channel and target_channel != channel) and (
            (not is_dm) or handoff_target == "timeboxing_agent"
        )
        if should_redirect:
            try:
                persona = _persona_for_agent(handoff_target)
                tb_title = None
                tb_excerpt = None
                if handoff_target == "timeboxing_agent":
                    try:
                        await _invite_user_to_channels_best_effort(
                            client, user_id=user, channel_ids=[target_channel]
                        )
                    except Exception:
                        pass
                    tb_title = _timeboxing_title_from_text(cleaned_text)
                    tb_excerpt = _timeboxing_excerpt_from_text(cleaned_text)
                root_payload = {
                    "channel": target_channel,
                    "text": (
                        _timeboxing_thread_root_text(
                            title=tb_title,
                            request_excerpt=tb_excerpt,
                            state="active",
                        )
                        if handoff_target == "timeboxing_agent"
                        else (
                            f"Incoming request from <@{user}> (requested in {_origin_label(event)}):\n"
                            f"> {cleaned_text}"
                        )
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
                if handoff_target == "timeboxing_agent" and tb_title:
                    focus.set_thread_label(
                        f"{target_channel}:{target_thread_ts}",
                        title=tb_title,
                        request_excerpt=tb_excerpt,
                        state="active",
                        by_user=user,
                    )

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

                if not is_dm:
                    await _origin_link_to_thread(
                        channel_id=target_channel,
                        thread_ts=target_thread_ts,
                        agent_label=(persona.username if persona else handoff_target),
                    )
                    if handoff_target != "timeboxing_agent":
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
                try:
                    result = await runtime.send_message(
                        handoff_msg,
                        recipient=AgentId(handoff_target, key=redirect.target_key),
                    )
                except asyncio.TimeoutError:
                    await client.chat_update(
                        channel=target_channel,
                        ts=processing["ts"],
                        text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again.",
                    )
                    return
                except Exception as e:
                    logger.exception(
                        "runtime.send_message failed (handoff redirect agent=%s key=%s)",
                        handoff_target,
                        redirect.target_key,
                    )
                    await client.chat_update(
                        channel=target_channel,
                        ts=processing["ts"],
                        text=":warning: Something went wrong while handling that request. Check bot logs.",
                    )
                    return
                payload = _slack_payload_from_result(result)
                update = {
                    "channel": target_channel,
                    "ts": processing["ts"],
                    "text": payload.get("text", "") or "",
                }
                if payload.get("blocks"):
                    update["blocks"] = payload["blocks"]
                await client.chat_update(**update)

                # Timeboxing stage-0: DM the commit prompt (with a thread deep-link button).
                if handoff_target == "timeboxing_agent" and payload.get("blocks"):
                    try:
                        permalink = await _permalink(target_channel, target_thread_ts)
                    except Exception:
                        permalink = None
                    try:
                        dm_channel = channel if is_dm else ""
                        if not dm_channel:
                            dm = await client.conversations_open(users=[user])
                            dm_channel = (dm.get("channel") or {}).get("id") or ""
                        if dm_channel:
                            dm_blocks = list(payload["blocks"])
                            if permalink:
                                # Append the "Go to Thread" button to the first actions block if possible.
                                appended = False
                                for block in dm_blocks:
                                    if block.get("type") == "actions":
                                        elems = block.get("elements") or []
                                        if isinstance(elems, list) and len(elems) < 5:
                                            elems.append(
                                                link_button(
                                                    text="Go to Thread",
                                                    url=permalink,
                                                    action_id="ff_open_thread",
                                                )
                                            )
                                            block["elements"] = elems
                                            appended = True
                                            break
                                if not appended:
                                    dm_blocks.append(
                                        {
                                            "type": "actions",
                                            "elements": [
                                                link_button(
                                                    text="Go to Thread",
                                                    url=permalink,
                                                    action_id="ff_open_thread",
                                                )
                                            ],
                                        }
                                    )
                            dm_payload = {
                                "channel": dm_channel,
                                "text": update["text"],
                                "blocks": dm_blocks,
                            }
                            if persona and persona.username:
                                dm_payload["username"] = persona.username
                            if persona and persona.icon_emoji:
                                dm_payload["icon_emoji"] = persona.icon_emoji
                            if persona and persona.icon_url:
                                dm_payload["icon_url"] = persona.icon_url
                            await client.chat_postMessage(**dm_payload)
                    except Exception:
                        logger.debug("Failed to DM timeboxing commit prompt", exc_info=True)

                await _maybe_update_timeboxing_thread_header(
                    client=client,
                    focus=focus,
                    thread_key=redirect.target_key,
                    state=_extract_thread_state(result) or "",
                )
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
            force_thread_root=("dm" if (is_dm and handoff_target == "timeboxing_agent") else None),
            force_reply=(True if (is_dm and handoff_target == "timeboxing_agent") else None),
        )
        try:
            result = await runtime.send_message(
                handoff_msg, recipient=AgentId(handoff_target, key=origin_key)
            )
        except asyncio.TimeoutError:
            await _origin_update(
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again."
            )
            return
        except Exception as e:
            logger.exception(
                "runtime.send_message failed (handoff agent=%s key=%s)",
                handoff_target,
                origin_key,
            )
            await _origin_update(
                text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}"
            )
            return
        payload = _with_agent_attribution(_slack_payload_from_result(result), handoff_target)
        # chat.update can't change username/icon, so keep the original message as a handoff marker
        # and post the actual reply as the target agent persona.
        await _origin_update(text=f":left_right_arrow: Handed off to *{handoff_target}*.")
        reply_payload: dict = {
            "channel": channel,
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            reply_payload["blocks"] = payload["blocks"]
        if origin_thread_root_ts:
            reply_payload["thread_ts"] = origin_thread_root_ts
        reply_persona = _persona_for_agent(handoff_target)
        if reply_persona and reply_persona.username:
            reply_payload["username"] = reply_persona.username
        if reply_persona and reply_persona.icon_emoji:
            reply_payload["icon_emoji"] = reply_persona.icon_emoji
        if reply_persona and reply_persona.icon_url:
            reply_payload["icon_url"] = reply_persona.icon_url
        await client.chat_postMessage(**reply_payload)
        return

    focus.set_user_focus(user, agent_type)
    payload = _with_agent_attribution(_slack_payload_from_result(result), agent_type)
    await _origin_update(text=payload.get("text", ""), blocks=payload.get("blocks"))
    await _maybe_update_timeboxing_thread_header(
        client=client,
        focus=focus,
        thread_key=origin_key,
        state=_extract_thread_state(result) or "",
    )


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
    timeboxing_commit = TimeboxingCommitCoordinator(runtime=runtime, client=app.client)

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

    async def _run_setup(respond, client, *, user_id: str | None) -> None:
        store = await _get_workspace_store()
        directory = await ensure_workspace_ready(client, store=store)
        if not directory:
            await respond(
                text="Setup failed. Check the bot logs and Slack scopes.",
                response_type="ephemeral",
            )
            return
        try:
            await _invite_user_to_channels_best_effort(
                client,
                user_id=(user_id or ""),
                channel_ids=[
                    directory.channels_by_name.get("plan-sessions", ""),
                    directory.channels_by_name.get("review", ""),
                    directory.channels_by_name.get("task-marshalling", ""),
                    directory.channels_by_name.get("scheduling", ""),
                ],
            )
        except Exception:
            pass
        await respond(
            text=_format_workspace_ready_response(directory),
            blocks=_workspace_ready_blocks(directory),
            response_type="ephemeral",
        )

    @app.command("/setup")
    async def cmd_setup(ack, body, respond, client, logger):
        await ack()
        await _run_setup(respond, client, user_id=body.get("user_id"))

    @app.command("/ff-setup")
    async def cmd_ff_setup(ack, body, respond, client, logger):
        await ack()
        await _run_setup(respond, client, user_id=body.get("user_id"))

    # --- Routing helpers ---

    # Slack sends `block_actions` even for url buttons; ack them to avoid 404s.
    @app.action("ff_open_thread")
    async def on_open_thread_action(ack, body, logger):
        await ack()

    @app.action("ff_open_link")
    async def on_open_link_action(ack, body, logger):
        await ack()

    @app.action("ff_open_google_calendar_event")
    async def on_open_google_calendar_event_action(ack, body, logger):
        await ack()

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
        trigger_id = body.get("trigger_id") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if channel_id and message_ts and trigger_id and value:
            await planning.handle_pick_time_modal(
                trigger_id=trigger_id,
                value=value,
                channel_id=channel_id,
                thread_ts=message_ts,
                actor_user_id=actor_user_id,
            )

    @app.action(FF_TIMEBOX_COMMIT_START_ACTION_ID)
    async def on_timebox_commit_start_action(ack, body, client, logger):
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if channel_id and message_ts and value:
            await timeboxing_commit.handle_start_action(
                value=value,
                prompt_channel_id=channel_id,
                prompt_ts=message_ts,
                actor_user_id=actor_user_id,
            )

    @app.action(FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID)
    async def on_timebox_commit_pick_day_action(ack, body, client, logger):
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        trigger_id = body.get("trigger_id") or ""
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if channel_id and message_ts and trigger_id and value:
            await timeboxing_commit.handle_pick_day_action(
                trigger_id=trigger_id,
                value=value,
                prompt_channel_id=channel_id,
                prompt_ts=message_ts,
            )

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

    @app.view(FF_PLANNING_TIME_MODAL_CALLBACK_ID)
    async def on_planning_time_modal_submit(ack, body, client, logger):
        await ack()
        view = body.get("view") or {}
        state = (view.get("state") or {}).get("values") or {}
        private_metadata = view.get("private_metadata") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        await planning.handle_time_modal_submission(
            private_metadata=private_metadata,
            state_values=state,
            actor_user_id=actor_user_id,
        )

    @app.view(FF_TIMEBOX_COMMIT_MODAL_CALLBACK_ID)
    async def on_timebox_commit_modal_submit(ack, body, client, logger):
        await ack()
        view = body.get("view") or {}
        state = (view.get("state") or {}).get("values") or {}
        private_metadata = view.get("private_metadata") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        await timeboxing_commit.handle_commit_modal_submission(
            private_metadata=private_metadata,
            state_values=state,
            actor_user_id=actor_user_id,
        )

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

        try:
            result = await runtime.send_message(
                TextMessage(content="Start a weekly review.", source=user_id),
                recipient=AgentId("revisor_agent", key=runtime_key),
            )
        except asyncio.TimeoutError:
            await client.chat_update(
                channel=channel_id,
                ts=processing["ts"],
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again.",
            )
            return
        except Exception:
            await client.chat_update(
                channel=channel_id,
                ts=processing["ts"],
                text=":warning: Something went wrong while handling that request. Check bot logs.",
            )
            return
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
        # Ignore bot messages / non-user subtypes to avoid loops and empty "message_changed" events.
        subtype = event.get("subtype")
        if subtype == "bot_message":
            return
        if subtype and subtype not in {"file_share", "me_message"}:
            return
        channel_id = event.get("channel")
        ts = event.get("ts")
        if not channel_id or not ts:
            return
        text = event.get("text") or ""
        if not text.strip() and subtype != "file_share":
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
                text=text,
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
            channel_agent = _agent_for_channel(channel_id)
            allow_unfocused = channel_agent in {
                "timeboxing_agent",
                "tasks_agent",
                "revisor_agent",
                "planner_agent",
            }
            if channel_id != general_id and not allow_unfocused and not focus.get_focus(key):
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
    if isinstance(result, SlackThreadStateMessage):
        payload = {"text": result.text}
        if result.blocks:
            payload["blocks"] = result.blocks
        return payload
    if isinstance(result, SlackBlockMessage):
        return {"text": result.text, "blocks": result.blocks}
    chat_message = getattr(result, "chat_message", None)
    if isinstance(chat_message, SlackThreadStateMessage):
        payload = {"text": chat_message.text}
        if chat_message.blocks:
            payload["blocks"] = chat_message.blocks
        return payload
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
