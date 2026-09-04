from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId
from pydantic import ValidationError
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.tasks.messages import (
    TaskDetailsModalRequest,
    TaskDetailsModalResponse,
    TaskDueActionRequest,
    TaskEditTitleRequest,
    TaskEditTitleResponse,
)
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.agents.timeboxing.adaptive_timeboxing import (
    AdaptiveTimeboxing,
    TurnRequest,
)
from fateforger.agents.timeboxing.feedback import feedback_facts
from fateforger.agents.timeboxing.messages import StartTimeboxing, TimeboxingUserReply
from fateforger.agents.timeboxing.preferences import (
    Constraint,
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)
from fateforger.agents.timeboxing.readiness import TimeboxRequirements
from fateforger.agents.timeboxing.session_contracts import (
    ApproveArtifact,
    ArtifactKind,
    Cancelled,
    ConfirmPlanningDay,
    DayType,
    PlanningSessionSnapshot,
    TimeboxIntent,
    TurnFailed,
)
from fateforger.core.config import settings
from fateforger.core.logging_config import observe_stage_duration, record_error
from fateforger.slack_bot.bootstrap import ensure_workspace_ready
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_REVIEW_VIEW_CALLBACK_ID,
    CONSTRAINT_ROW_REVIEW_ACTION_ID,
    FF_CONSTRAINT_REVIEW_ALL_ACTION_ID,
    LEGACY_CONSTRAINT_REVIEW_ALL_ACTION_ID,
    build_constraint_review_list_view,
    build_constraint_review_view,
    build_constraint_row_blocks,
    decode_metadata,
    parse_constraint_review_submission,
)
from fateforger.slack_bot.messages import (
    SLACK_MAX_BLOCK_TEXT_CHARS,
    SLACK_MAX_BLOCKS,
    SLACK_MAX_PAYLOAD_CHARS,
    SLACK_MAX_TEXT_CHARS,
    SlackBlockMessage,
    SlackThreadStateMessage,
)
from fateforger.slack_bot.mrkdwn import to_mrkdwn
from fateforger.slack_bot.planning import (
    FF_EVENT_ADD_ACTION_ID,
    FF_EVENT_ADD_DISABLED_ACTION_ID,
    FF_EVENT_DURATION_ACTION_ID,
    FF_EVENT_EDIT_ACTION_ID,
    FF_EVENT_EDIT_MODAL_CALLBACK_ID,
    FF_EVENT_OPEN_URL_ACTION_ID,
    FF_EVENT_RETRY_ACTION_ID,
    FF_EVENT_START_AT_ACTION_ID,
    FF_EVENT_START_DATE_ACTION_ID,
    FF_EVENT_START_TIME_ACTION_ID,
    PlanningCoordinator,
    ThreadReplyOutcome,
    parse_draft_id_from_value,
)
from fateforger.slack_bot.surface_intents import SurfaceIntentError
from fateforger.slack_bot.progress import HarnessProgressCard
from fateforger.slack_bot.progress_events import (
    ProgressPhase as TimeboxProgressPhase,
)
from fateforger.slack_bot.progress_events import (
    ProgressSource,
    TimeboxProgressEvent,
)
from fateforger.slack_bot.progress_events import (
    ProgressStatus as TimeboxProgressStatus,
)
from fateforger.slack_bot.reply_guard import agent_reply_text
from fateforger.slack_bot.stage_card_registry import StageCardRegistry, receipt_body, receipt_label
from fateforger.slack_bot.stage_cards import date_stage_card
from fateforger.slack_bot.task_cards import (
    FF_TASK_DETAILS_ACTION_ID,
    FF_TASK_EDIT_MODAL_CALLBACK_ID,
    FF_TASK_VIEW_ALL_ACTION_ID,
    decode_task_metadata,
)
from fateforger.slack_bot.timeboxing_cards import (
    FF_HARNESS_APPROVE_ACTION_ID,
    FF_HARNESS_UNDO_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID,
    FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID,
    FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID,
    TIMEBOX_STALE_CHOICE_TEXT,
    TIMEBOX_TURN_FAILED_TEXT,
    HarnessApproveActionPayload,
    _undo_outcome_text,
    harness_approve_block,
    harness_undo_block,
    present_outcome,
    render_stage_card,
    timebox_failure_message,
)
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
    TimeboxCommitMeta,
    TimeboxingCommitCoordinator,
    day_type_action_id,
    format_relative_day_label,
)
from fateforger.slack_bot.timeboxing_host import (
    HostPlanningContext,
    KernelProgressSink,
    PendingCandidateCommitPort,
    derive_timebox_intent,
)
from fateforger.slack_bot.timeboxing_intents import (
    TimeboxActionEnvelope,
    intent_from_artifact_action,
    intent_from_date_action,
)
from fateforger.slack_bot.timeboxing_stage_actions import (
    FF_TIMEBOX_STAGE_BACK_ACTION_ID,
    FF_TIMEBOX_STAGE_CANCEL_ACTION_ID,
    FF_TIMEBOX_STAGE_PROCEED_ACTION_ID,
    FF_TIMEBOX_STAGE_REDO_ACTION_ID,
    TimeboxingStageActionCoordinator,
    TimeboxingStageActionPayload,
)
from fateforger.slack_bot.timeboxing_submit import (
    FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID,
    FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID,
    FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID,
    TimeboxingSubmitCoordinator,
    TimeboxSubmitActionPayload,
)

from .focus import FocusManager
from .timebox_candidate import PendingTimeboxCandidates
from .ui import link_button, open_link_blocks
from .workspace import (
    DEFAULT_PERSONAS,
    SlackPersona,
    WorkspaceDirectory,
    WorkspaceRegistry,
)
from .workspace_store import SlackWorkspaceStore, ensure_slack_workspace_schema

try:
    from autogen_agentchat.messages import HandoffMessage
except Exception:  # pragma: no cover - optional dependency wiring
    HandoffMessage = None


FF_APPHOME_WEEKLY_REVIEW_ACTION_ID = "ff_apphome_weekly_review"

_TIMEBOXING_STATE_EMOJI = {
    "pending": ":large_yellow_circle:",
    "in_progress": ":large_blue_circle:",
    "done": ":white_check_mark:",
    "canceled": ":no_entry_sign:",
}

logger = logging.getLogger(__name__)

def _timeboxing_title_from_text(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "session"
    if len(cleaned) > 80:
        return cleaned[:77].rstrip() + "…"
    return cleaned


def _truncate_slack_text(text: str | None, *, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    if max_chars <= 1:
        return "…"
    return value[: max_chars - 1].rstrip() + "…"


def _truncate_slack_block_content(value):
    if isinstance(value, str):
        return _truncate_slack_text(value, max_chars=SLACK_MAX_BLOCK_TEXT_CHARS)
    if isinstance(value, list):
        return [_truncate_slack_block_content(item) for item in value]
    if isinstance(value, dict):
        return {key: _truncate_slack_block_content(item) for key, item in value.items()}
    return value


def _delivery_fallback_text(text: str | None) -> str:
    base = _truncate_slack_text(text or "(response)", max_chars=2200)
    return (
        f"{base}\n\n"
        "_Output truncated for Slack delivery. Please continue in thread (or press Redo)._"
    )


def _compact_slack_payload(*, text: str | None, blocks=None) -> dict[str, object]:
    safe_text = _truncate_slack_text(
        text or "(no response)", max_chars=SLACK_MAX_TEXT_CHARS
    )
    safe_blocks = None
    if isinstance(blocks, list) and blocks:
        clipped = blocks
        if len(clipped) > SLACK_MAX_BLOCKS:
            clipped = list(clipped[: SLACK_MAX_BLOCKS - 1]) + [
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "_Additional details were truncated for Slack size limits._",
                        }
                    ],
                }
            ]
        safe_blocks = [_truncate_slack_block_content(block) for block in clipped]

    payload: dict[str, object] = {"text": safe_text}
    if safe_blocks:
        payload["blocks"] = safe_blocks

    if len(json.dumps(payload, ensure_ascii=False)) > SLACK_MAX_PAYLOAD_CHARS:
        return {"text": _delivery_fallback_text(safe_text)}
    return payload


def _slack_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    if isinstance(response, dict):
        return str(response.get("error") or "").strip().lower()
    getter = getattr(response, "get", None)
    if callable(getter):
        try:
            return str(getter("error") or "").strip().lower()
        except Exception:
            pass
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return str(data.get("error") or "").strip().lower()
    return ""


def _timeboxing_excerpt_from_text(text: str) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) > 200:
        return cleaned[:197].rstrip() + "…"
    return cleaned


def _timeboxing_thread_root_text(
    *, title: str, request_excerpt: str | None, state: str
) -> str:
    emoji = _TIMEBOXING_STATE_EMOJI.get(state, _TIMEBOXING_STATE_EMOJI["pending"])
    return f"{emoji} {title}"


def _build_timeboxing_thread_root_blocks(
    *,
    title: str,
    state: str,
    constraints: list[Constraint],
    thread_ts: str,
    user_id: str,
) -> list[dict[str, object]]:
    """Build the thread-root blocks with active constraints."""
    blocks: list[dict[str, object]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _timeboxing_thread_root_text(
                    title=title, request_excerpt=None, state=state
                ),
            },
        }
    ]
    active = [c for c in constraints if c.status != ConstraintStatus.DECLINED]
    if active:
        blocks.append({"type": "divider"})
        blocks.extend(
            build_constraint_row_blocks(
                active, thread_ts=thread_ts, user_id=user_id, limit=20
            )
        )
    else:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "No active constraints yet."}],
            }
        )
    return blocks


def _extract_thread_state(result) -> str | None:
    for obj in (result, getattr(result, "chat_message", None)):
        state = getattr(obj, "thread_state", None)
        if isinstance(state, str) and state.strip():
            return state.strip()
    return None


async def _maybe_update_timeboxing_thread_constraints(
    *,
    client: AsyncWebClient,
    focus: FocusManager,
    thread_key: str,
    user_id: str,
    store: ConstraintStore | None,
) -> None:
    """Update the timeboxing thread root with the latest active constraints."""
    if not store:
        return
    try:
        channel_id, thread_root_ts = thread_key.split(":", 1)
    except Exception:
        return
    if thread_root_ts == "dm":
        return
    label = focus.get_thread_label(thread_key)
    if not label:
        return
    constraints = await store.list_constraints(
        user_id=user_id,
        channel_id=channel_id,
        thread_ts=thread_root_ts,
    )
    blocks = _build_timeboxing_thread_root_blocks(
        title=label.title,
        state=label.state,
        constraints=constraints,
        thread_ts=thread_root_ts,
        user_id=user_id,
    )
    try:
        await client.chat_update(
            channel=channel_id,
            ts=thread_root_ts,
            text=_timeboxing_thread_root_text(
                title=label.title,
                request_excerpt=label.request_excerpt,
                state=label.state,
            ),
            blocks=blocks,
        )
    except Exception:
        return


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
    for name in [
        "general",
        "plan-sessions",
        "review",
        "task-marshalling",
        "scheduling",
        "admonishments",
    ]:
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
    for name in [
        "general",
        "plan-sessions",
        "review",
        "task-marshalling",
        "scheduling",
        "admonishments",
    ]:
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
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": channel_mentions}}
        )

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


# TODO: is this needed, and if so, why? it seems hacky
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
    # The model writes markdown; Slack renders mrkdwn. The agent id was a
    # debugging label that reached users as `*admonisher_agent*` on
    # 2026-09-03; the persona already names who is speaking.
    text = payload.get("text") or "(no response)"
    return {"text": to_mrkdwn(text)}


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
    # TODO(refactor,typed-errors): Replace token-prefix substring redaction with
    # structured error payload redaction at source.
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
        {
            "type": "mrkdwn",
            "text": f"*The Schedular*\n{_mention(schedular, 'not configured')}",
        },
        {
            "type": "mrkdwn",
            "text": f"*Reviewer*\n{_mention(reviews, 'not configured')}",
        },
        {
            "type": "mrkdwn",
            "text": f"*TaskMarshal*\n{_mention(task_marshal, 'not configured')}",
        },
        {
            "type": "mrkdwn",
            "text": f"*Scheduling*\n{_mention(scheduling, 'not configured')}",
        },
    ]

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Welcome to FateForger"},
        },
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


def _persona_payload(persona: SlackPersona | None) -> dict:
    """Slack persona overrides, absent rather than wrong when unconfigured.

    Every message this bot posts under an agent's name goes through here. The
    same three `if` statements had been written out at nine call sites against
    whichever local happened to be in scope, so "which fields a persona sets"
    was a fact stored nine times -- and a tenth post is written by copying
    whichever copy was nearest, not by reading the rule.
    """

    if persona is None:
        return {}
    payload: dict = {}
    if persona.username:
        payload["username"] = persona.username
    if persona.icon_emoji:
        payload["icon_emoji"] = persona.icon_emoji
    if persona.icon_url:
        payload["icon_url"] = persona.icon_url
    return payload


def _persona_payload_for(agent_type: str) -> dict:
    """The same overrides, for a caller that holds only the agent's name."""

    return _persona_payload(_persona_for_agent(agent_type))


def _channel_for_agent(agent_type: str) -> str | None:
    if agent_type == "timeboxing_agent":
        cid = (settings.slack_timeboxing_channel_id or "").strip()
        if cid:
            return cid
    if agent_type == "revisor_agent":
        cid = (getattr(settings, "slack_strategy_channel_id", "") or "").strip()
        if cid:
            return cid
    if agent_type == "tasks_agent":
        cid = (getattr(settings, "slack_tasks_channel_id", "") or "").strip()
        if cid:
            return cid
    if agent_type == "planner_agent":
        cid = (getattr(settings, "slack_ops_channel_id", "") or "").strip()
        if cid:
            return cid

    directory = WorkspaceRegistry.get_global()
    if directory:
        cid = directory.channel_for_agent(agent_type)
        if cid:
            return cid
    return None


def _agent_for_channel(channel_id: str) -> str | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        for agent_type, cid in (directory.channels_by_agent or {}).items():
            if cid == channel_id:
                return agent_type
    # Fallback: check env-configured specialist channel IDs (works without DB bootstrap).
    for agent_type in (
        "timeboxing_agent",
        "revisor_agent",
        "tasks_agent",
        "planner_agent",
    ):
        if _channel_for_agent(agent_type) == channel_id:
            return agent_type
    return None


@dataclass(frozen=True)
class _HarnessThreadContext:
    """The bounded Slack state a fresh one-shot harness process cannot retain."""

    recent_user_turns: tuple[tuple[str, str], ...] = ()
    proposed_timebox: str | None = None
    calendar_id: str | None = None
    day: str | None = None


def _contains_harness_approval(message: dict) -> bool:
    for block in message.get("blocks") or ():
        for element in block.get("elements") or ():
            if element.get("action_id") == FF_HARNESS_APPROVE_ACTION_ID:
                return True
    return False


async def _harness_thread_context(
    *,
    client,
    channel: str,
    thread_root: str,
    current_ts: str,
    owner_user_id: str,
) -> _HarnessThreadContext:
    """Recover recent user intent and the last displayed proposal from Slack.

    Harness turns are deliberately one-shot, and the in-memory candidate store
    is empty after a bot restart. Slack is already the durable user-visible
    record, so recover only the bot-authored message named by the approval
    card. Adjacency is not identity: progress and user messages may interleave
    while a turn is running. No free-form intent parsing is involved.
    """

    messages: list[dict] = []
    cursor: str | None = None
    try:
        for _page in range(10):
            request = {"channel": channel, "ts": thread_root, "limit": 100}
            if cursor:
                request["cursor"] = cursor
            response = await client.conversations_replies(**request)
            messages.extend(response.get("messages") or [])
            metadata = response.get("response_metadata") or {}
            cursor = str(metadata.get("next_cursor") or "").strip() or None
            if cursor is None:
                break
        if cursor is not None:
            return _HarnessThreadContext()
    except Exception:
        return _HarnessThreadContext()
    prior = [message for message in messages if str(message.get("ts")) != current_ts]

    user_messages = [
        ("Hugo", str(message.get("text") or "").strip())
        for message in prior
        if message.get("user") == owner_user_id
        and str(message.get("text") or "").strip()
    ][-3:]

    proposed_timebox = None
    calendar_id = None
    day = None
    proposal_message_ts = None
    approval_index = next(
        (
            index
            for index in range(len(prior) - 1, -1, -1)
            if _contains_harness_approval(prior[index])
        ),
        None,
    )
    if approval_index is not None:
        for block in prior[approval_index].get("blocks") or ():
            for element in block.get("elements") or ():
                if element.get("action_id") != FF_HARNESS_APPROVE_ACTION_ID:
                    continue
                try:
                    approval_value = json.loads(element.get("value") or "")
                except (TypeError, ValueError):
                    approval_value = {}
                if isinstance(approval_value, dict):
                    raw_calendar_id = approval_value.get("calendar_id")
                    raw_day = approval_value.get("day")
                    raw_proposal_message_ts = approval_value.get(
                        "proposal_message_ts"
                    )
                    calendar_id = (
                        raw_calendar_id if isinstance(raw_calendar_id, str) else None
                    )
                    day = raw_day if isinstance(raw_day, str) else None
                    proposal_message_ts = (
                        raw_proposal_message_ts
                        if isinstance(raw_proposal_message_ts, str)
                        and raw_proposal_message_ts
                        else None
                    )
                break
        if proposal_message_ts:
            proposal_message = next(
                (
                    message
                    for message in prior
                    if str(message.get("ts")) == proposal_message_ts
                ),
                None,
            )
            if proposal_message is not None and proposal_message.get("bot_id"):
                text = str(proposal_message.get("text") or "").strip()
                proposed_timebox = text or None

        if proposed_timebox is None:
            calendar_id = None
            day = None

    return _HarnessThreadContext(
        recent_user_turns=tuple(user_messages),
        proposed_timebox=proposed_timebox,
        calendar_id=calendar_id,
        day=day,
    )


def _general_channel_id() -> str | None:
    cid = (getattr(settings, "slack_general_channel_id", "") or "").strip()
    if cid:
        return cid
    directory = WorkspaceRegistry.get_global()
    if directory:
        cid = directory.channel_for_name("general")
        if cid:
            return cid
    return None


def _plan_sessions_channel_id() -> str | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        cid = directory.channel_for_name("plan-sessions")
        if cid:
            return cid
    return None


async def _harness_turn(
    *,
    text: str,
    thread_key: str,
    owner_user_id: str,
    on_phase,
    session_id: str | None = None,
    history: list[tuple[str, str]] | None = None,
    proposed_timebox: str | None = None,
    proposed_calendar_id: str | None = None,
    proposed_day: str | None = None,
) -> TextMessage:
    """One Slack turn through the harness, shaped like a runtime reply.

    Returned as a TextMessage so every renderer downstream -- personas, block
    compaction, thread updates -- keeps working untouched. The migration
    changes which system thinks, not how the answer reaches Slack.

    The harness call is a blocking subprocess and a planning turn runs for tens
    of seconds, so it goes to a worker thread; leaving it on the loop would
    stall every other Slack event in the workspace.
    """
    from .harness_bridge import PLANNING_MODEL, HarnessError
    from .thread_approval import approval_path, revoke

    # Prefer the exact current process-owned rendering. Slack thread recovery
    # supplies the same baseline after a restart, when this store is empty.
    previous_candidate = _pending_candidates.peek(thread_key)
    if previous_candidate is not None and previous_candidate.rendered.strip():
        proposed_timebox = previous_candidate.rendered
        raw_calendar_id = previous_candidate.snapshot.get("calendar_id")
        raw_day = previous_candidate.snapshot.get("day")
        proposed_calendar_id = (
            raw_calendar_id if isinstance(raw_calendar_id, str) else None
        )
        proposed_day = raw_day if isinstance(raw_day, str) else None

    # Any material new request invalidates the approval card it supersedes.
    _pending_candidates.invalidate(thread_key)
    revoke(thread_key)

    try:
        reply = await _owned_harness_ask(
            text,
            thread_key=thread_key,
            on_event=on_phase,
            approval_file=str(approval_path(thread_key)),
            # Without this the harness starts every turn with no idea the
            # thread has a past. `thread_key` was already threaded here for the
            # approval file; the conversation's own identity was not.
            session_id=session_id,
            history=history,
            proposed_timebox=proposed_timebox,
            proposed_calendar_id=proposed_calendar_id,
            proposed_day=proposed_day,
            # Every turn that reaches here is a planning turn: this function is
            # the timeboxing path. The receptionist and the fast conversational
            # replies do not come through it.
            model=PLANNING_MODEL,
        )
    except HarnessError as exc:
        # Surfaced, not swallowed. A harness that could not be reached and a
        # planner that declined to act must not read the same in the thread.
        return TextMessage(
            content=(f":warning: The harness did not answer.\n```{exc}```"),
            source="timeboxing_agent",
        )
    if reply.validated_candidate is not None:
        # A clean tmbx candidate is approvable whether or not the model tried
        # plan_commit. In particular, obeying "do not commit" must still show
        # the one control that can later submit this exact displayed payload.
        _pending_candidates.replace(
            thread_key, reply.validated_candidate, owner_user_id=owner_user_id
        )
    return TextMessage(content=reply.text, source="timeboxing_agent")


@dataclass
class _HarnessTurnControl:
    cancel_event: threading.Event
    on_phase: Callable[[object], None]
    finished: asyncio.Event


_harness_turn_controls: dict[str, _HarnessTurnControl] = {}
_harness_turn_handoffs: dict[str, asyncio.Lock] = {}
_thread_commit_locks: dict[str, asyncio.Lock] = {}
_approval_tasks: set[asyncio.Task[None]] = set()


def _thread_lock(registry: dict[str, asyncio.Lock], thread_key: str) -> asyncio.Lock:
    lock = registry.get(thread_key)
    if lock is None:
        lock = asyncio.Lock()
        registry[thread_key] = lock
    return lock


async def _owned_harness_ask(
    text: str,
    *,
    thread_key: str,
    on_event: Callable[[object], None],
    **ask_kwargs,
):
    """Run one cancellable child, superseding any older turn in the thread."""
    from .harness_bridge import HarnessCancelled, ask

    async with _thread_lock(_harness_turn_handoffs, thread_key):
        previous = _harness_turn_controls.get(thread_key)
        if previous is not None:
            try:
                previous.on_phase(
                    TimeboxProgressEvent(
                        session_key=thread_key,
                        sequence=0,
                        source=ProgressSource.RUNTIME,
                        phase=TimeboxProgressPhase.OTHER,
                        status=TimeboxProgressStatus.SUPERSEDED,
                    )
                )
            except Exception:
                pass
            previous.cancel_event.set()
            await previous.finished.wait()
        async with _thread_lock(_thread_commit_locks, thread_key):
            control = _HarnessTurnControl(
                cancel_event=threading.Event(),
                on_phase=on_event,
                finished=asyncio.Event(),
            )
            _harness_turn_controls[thread_key] = control
            worker = asyncio.create_task(
                asyncio.to_thread(
                    ask,
                    text,
                    on_event=on_event,
                    cancel_event=control.cancel_event,
                    **ask_kwargs,
                )
            )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        control.cancel_event.set()
        try:
            await worker
        except HarnessCancelled:
            pass
        raise
    except HarnessCancelled as exc:
        raise asyncio.CancelledError from exc
    finally:
        control.finished.set()
        if _harness_turn_controls.get(thread_key) is control:
            _harness_turn_controls.pop(thread_key, None)


def _note_harness_phase(
    card: HarnessProgressCard,
    event,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Schedule one typed progress fact from the poller onto Slack's loop."""
    # The harness tails progress on a worker thread. Scheduling against that
    # thread's nonexistent loop silently dropped every semantic update; the
    # Slack loop is captured by the async caller and is the only loop that may
    # own this coroutine.
    asyncio.run_coroutine_threadsafe(card.handle(event), loop)



#: The one stable sentence each refusal gets, named here because this is the
#: seam the route is driven and asserted through. Aliases rather than copies:
#: a second definition could drift from the sentence actually drawn.
_TIMEBOX_TURN_FAILED_TEXT = TIMEBOX_TURN_FAILED_TEXT
_TIMEBOX_STALE_CHOICE_TEXT = TIMEBOX_STALE_CHOICE_TEXT

#: Threads whose last turn had a commit refused for want of approval. Held here
#: rather than returned through the renderer because the reply travels as a
#: `TextMessage`, which carries text and a source and nothing else -- widening
#: it would touch every renderer for one boolean that only this path reads.
_pending_candidates = PendingTimeboxCandidates()

#: The card each session is currently showing, so the next turn can close it.
_stage_cards = StageCardRegistry()


def take_pending_approval(thread_key: str) -> str | None:
    """Return the opaque id of the exact candidate awaiting approval.

    Reading does not spend it; only an authorized approval click may consume
    the candidate. A material new turn invalidates it before producing a
    replacement.
    """
    candidate = _pending_candidates.peek(thread_key)
    return candidate.candidate_id if candidate is not None else None


def _approval_thread_root(
    origin_thread_root_ts: str | None, processing_message_ts: str
) -> str:
    """Put approval beside the plan and preserve its candidate thread key."""

    return origin_thread_root_ts or processing_message_ts




async def _post_pending_harness_approval(
    *,
    client,
    logger,
    channel: str,
    thread_root: str,
    thread_key: str,
    proposal_message_ts: str,
) -> bool:
    """Offer the same exact-candidate control from every harness entry path."""

    candidate_id = take_pending_approval(thread_key)
    if not candidate_id:
        return False
    candidate = _pending_candidates.peek(thread_key)
    snapshot = candidate.snapshot if candidate is not None else {}
    calendar_id = snapshot.get("calendar_id")
    day = snapshot.get("day")
    try:
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_root,
            text="Ready to commit — this needs your approval.",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":lock: *This plan has not been committed.* "
                        "Nothing reaches your calendar until you approve it.",
                    },
                },
                harness_approve_block(
                    thread_key,
                    candidate_id,
                    calendar_id=(calendar_id if isinstance(calendar_id, str) else None),
                    day=(day if isinstance(day, str) else None),
                    proposal_message_ts=proposal_message_ts,
                ),
            ],
        )
    except Exception:
        # The plan itself already landed. A missing button is recoverable by
        # asking again and must not erase the answer the user can inspect.
        logger.exception("could not offer the approval control")
        return False
    return True


async def _execute_harness_approval(
    *,
    client,
    logger,
    channel: str,
    thread_root: str,
    thread_key: str,
    candidate_id: str,
    actor_user_id: str,
) -> None:
    """Own one thread until its exact candidate has a definitive outcome."""

    from .tmbx_client import CommitOutcomeUnknown, CommitUnavailable, TmbxClient

    async with _thread_lock(_thread_commit_locks, thread_key):
        # Spend only after lifecycle ownership. A replacement harness turn
        # waits on this same lock, so no newer draft can race the calendar
        # write between candidate consumption and its definitive result.
        candidate = _pending_candidates.consume(
            thread_key,
            candidate_id,
            actor_user_id=actor_user_id,
        )
        if candidate is None:
            await client.chat_postMessage(
                channel=channel,
                thread_ts=thread_root,
                text=(
                    ":warning: That approval is stale, unavailable to this user, "
                    "or was already used. Nothing else was committed. Ask me to "
                    "show the current plan again."
                ),
            )
            return

        posted = await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_root,
            text=":white_check_mark: Approved — committing now…",
        )
        progress_card = HarnessProgressCard(
            client,
            channel=posted["channel"],
            message_ts=posted["ts"],
        )
        try:
            await progress_card.handle(
                TimeboxProgressEvent(
                    session_key=thread_key,
                    sequence=1,
                    source=ProgressSource.RUNTIME,
                    phase=TimeboxProgressPhase.COMMITTING,
                    status=TimeboxProgressStatus.STARTED,
                )
            )
            payload = await TmbxClient().commit(
                candidate.snapshot,
                candidate.patch,
                idempotency_key=candidate.digest,
            )
            committed = payload.get("committed") is True
            await progress_card.handle(
                TimeboxProgressEvent(
                    session_key=thread_key,
                    sequence=2,
                    source=ProgressSource.TMBX_MCP,
                    phase=TimeboxProgressPhase.COMMITTING,
                    status=(
                        TimeboxProgressStatus.SUCCEEDED
                        if committed
                        else TimeboxProgressStatus.FAILED
                    ),
                    refusal_code=(
                        str(payload.get("reason"))
                        if not committed and payload.get("reason")
                        else None
                    ),
                )
            )
        except CommitOutcomeUnknown:
            logger.warning(
                "approve: tmbx commit outcome unknown after idempotent reconciliation"
            )
            await progress_card.handle(
                TimeboxProgressEvent(
                    session_key=thread_key,
                    sequence=2,
                    source=ProgressSource.TMBX_MCP,
                    phase=TimeboxProgressPhase.COMMITTING,
                    status=TimeboxProgressStatus.FAILED,
                    refusal_code="outcome_unknown",
                )
            )
            payload = {
                "committed": False,
                "reason": "outcome_unknown",
            }
        except CommitUnavailable:
            logger.warning("approve: direct tmbx commit unavailable")
            await progress_card.handle(
                TimeboxProgressEvent(
                    session_key=thread_key,
                    sequence=2,
                    source=ProgressSource.TMBX_MCP,
                    phase=TimeboxProgressPhase.COMMITTING,
                    status=TimeboxProgressStatus.FAILED,
                    refusal_code="calendar_service_unavailable",
                )
            )
            payload = {
                "committed": False,
                "reason": "calendar_service_unavailable",
            }
        finally:
            await progress_card.close()

        tx_id = payload.get("tx_id") if payload.get("committed") is True else None
        if isinstance(tx_id, str) and tx_id:
            text = ":white_check_mark: Committed the plan you approved."
            if payload.get("durable") is not True:
                where = str(payload.get("calendar_backend") or "unknown")
                text = (
                    ":warning: Committed to the *"
                    f"{where}* calendar — nothing reached your real one."
                )
            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": text},
                },
                harness_undo_block(tx_id),
            ]
        else:
            reason = str(payload.get("reason") or "commit_refused")
            if reason == "outcome_unknown":
                text = (
                    ":warning: The calendar outcome is unknown — I could not "
                    "confirm whether this plan was committed. Check the calendar "
                    "before trying again."
                )
            else:
                detail = str(payload.get("message") or "")[:400]
                text = f":warning: Nothing was committed — `{reason}`."
                if detail:
                    text += f"\n```{detail}```"
            blocks = None
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_root,
            text=text,
            **({"blocks": blocks} if blocks else {}),
        )


def _track_approval_task(task: asyncio.Task[None], logger) -> None:
    """Keep a shielded commit alive and surface unexpected background errors."""

    _approval_tasks.add(task)

    def done(completed: asyncio.Task[None]) -> None:
        _approval_tasks.discard(completed)
        if completed.cancelled():
            logger.error("approval commit task was cancelled before a final outcome")
            return
        error = completed.exception()
        if error is not None:
            logger.error(
                "approval commit task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(done)


async def instant_ack(client, event: dict) -> dict | None:
    """Say "heard you" before anything slow runs.

    First visible output was 3.6s, because everything ahead of it is work the
    user cannot see: a workspace-registry ensure, a user-invite call, a
    registration guard that has been observed *timing out* at 3s, then agent
    resolution, and only then the first post. None of that is thinking. The
    user cannot tell a system that is working from one that dropped the
    message, and 3.6s of nothing is the difference between responsive and
    broken.

    So this goes first and is deliberately trivial: one API call, no lookups,
    no model, nothing that can be slow. Its `ts` is handed to the router so the
    "thinking" state edits this message rather than posting a second one --
    otherwise the fast ack buys a duplicate.

    Returns None on any failure -- an acknowledgement that could break the turn
    it is acknowledging would be a bad trade -- but it says so first.

    It used to fail in silence, and that cost a day. On 2026-09-01 a fresh
    mention got nothing for 61 seconds; the handler ran, this was its first
    statement, and no `:eyes:` ever appeared. Ruling out the causes took five
    separate eliminations from outside the process -- reproducing this exact
    `chat.postMessage` by hand (ok: true), confirming the message handler
    defers to this one, confirming there is no second listener, and proving
    from the Slack `ts` that no ack was posted and later edited away. Every one
    of those would have been unnecessary against one log line.

    Silent is not the only alternative to fatal. `logger.warning` cannot fail
    the turn and cannot be missed.
    """
    channel = event.get("channel")
    if not channel:
        logger.warning(
            "instant_ack skipped: the event names no channel (keys=%s)",
            sorted(event)[:12],
        )
        return None
    # Threaded under the message being acknowledged, including when that
    # message started no thread of its own -- a top-level ack reads as an
    # unrelated post, and the reply that follows lands in the thread anyway,
    # so the two would be separated.
    thread_ts = event.get("thread_ts") or event.get("ts")
    payload: dict = {"channel": channel, "text": ":eyes:"}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    try:
        return await client.chat_postMessage(**payload)
    except Exception as exc:
        # `exc_info` on purpose: a Slack error carries its own `error` field,
        # and "which one" is the whole question. `channel_not_found`,
        # `not_in_channel`, a rate limit and an expired token are four
        # different problems and only the traceback distinguishes them.
        logger.warning(
            "instant_ack failed for channel=%s thread_ts=%s: %s",
            channel,
            thread_ts,
            type(exc).__name__,
            exc_info=True,
        )
        return None




def _timebox_start_button_value(blocks) -> str:
    """The metadata the date card's Confirm button carries, read off the card.

    Two callers need it and neither is handed it: a day-select press arrives
    with only the dropdown's own value, and the handoff route has only the
    message it just posted. The card is the record of what was offered, so it
    is read rather than reconstructed -- a second encoding of the same day
    would be a second thing to keep in step with the button the user presses.

    At most one such button exists per message: Slack refuses a message whose
    interactive elements share an action_id, which is why the five day types
    each carry their own. So "the first one found" is "the only one there".
    """

    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        elements = block.get("elements")
        if not isinstance(elements, list):
            continue
        for element in elements:
            if (
                isinstance(element, dict)
                and element.get("type") == "button"
                and element.get("action_id") == FF_TIMEBOX_COMMIT_START_ACTION_ID
            ):
                return str(element.get("value") or "")
    return ""


def _timebox_backend() -> str:
    """Which system answers /timebox. "harness" unless told otherwise."""
    return (os.environ.get("FF_TIMEBOX_BACKEND") or "harness").strip().lower()


def _timebox_body_for_harness(body: dict) -> dict:
    """Give a bare /timebox something to plan.

    The harness handler refuses an empty message, which is right for /dsh --
    there is nothing to infer from silence. /timebox is not silence: the
    command names the intent, and the day is the obvious default. Without this
    a bare /timebox answers "Give me something to plan", which reads as the
    bot not knowing what its own command is for.

    **The day is not hardcoded, and is not decided here.** This asked for
    "today" until 2026-08-24, which is wrong every evening: at 20:00 there is
    no day left to plan and the useful answer is tomorrow. Picking a cutoff
    hour in Python would replace one wrong answer with a differently wrong one
    -- 18:00 is late for someone who plans over dinner and early for someone
    who works past it.

    So the model chooses, from the clock it already reads, and **says which day
    it chose in its first line**. That is the same discipline the standing
    instructions apply to every block: an assumption the user can see is one
    they can correct, in one reply rather than a wasted turn. It also means
    this is a default rather than a decision, which is what a bare command
    should be (#195).

    **Nothing in `src/` calls this any more.** `/timebox` goes through
    `route_slack_event`, which reaches the session kernel, and the kernel's
    host derives the planning day arithmetically in `HostPlanningContext` --
    pinned to today in the configured timezone, with no evening rollover and
    no model asked. So the paragraph above describes a behaviour that is not in
    force: read it as the argument for how a bare command should behave, not as
    a description of what one does. Two unit test files still assert this text,
    which is why it is still here rather than deleted.
    """
    text = (body.get("text") or "").strip()
    if not text:
        text = (
            "Plan a day for me. Work out which day I most likely mean from the "
            "current date and time -- the rest of today if there is usefully "
            "any left, otherwise tomorrow -- and say which day you chose and "
            "why in your first line, so I can correct it in one reply. Read "
            "that day's calendar and my active constraints before proposing "
            "anything. Do not commit without being asked."
        )
    return {**body, "text": text}








def _timeboxing_host_now() -> datetime:
    """The host clock, named so the derived planning day can be pinned."""

    return datetime.now(UTC)


def _timeboxing_kernel(
    runtime,
    *,
    session_key: str,
    actor_user_id: str,
    candidate_id: str | None = None,
) -> AdaptiveTimeboxing | None:
    """Assemble the kernel from the runtime's adapters, or admit it cannot."""

    repository = getattr(runtime, "timeboxing_session_store", None)
    planner = getattr(runtime, "timeboxing_planner", None)
    if repository is None or planner is None:
        return None
    return AdaptiveTimeboxing(
        repository=repository,
        requirements=TimeboxRequirements(),
        planner=planner,
        context=HostPlanningContext(runtime, now=_timeboxing_host_now),
        commit=PendingCandidateCommitPort(
            pending=_pending_candidates,
            session_key=session_key,
            actor_user_id=actor_user_id,
            candidate_id=candidate_id,
        ),
    )






async def _run_adaptive_timebox_turn(
    *,
    runtime,
    client,
    logger,
    session_key: str,
    actor_user_id: str,
    interaction_id: str,
    progress_channel: str,
    progress_ts: str,
    card_channel: str,
    card_thread_ts: str,
    user_text: str = "",
    action: TimeboxActionEnvelope | None = None,
    candidate_id: str | None = None,
    focus: FocusManager | None = None,
) -> SlackBlockMessage:
    """Carry one Slack turn through the planning-session kernel.

    load session -> derive typed intent -> one progress card -> one kernel turn
    -> one rendered outcome. The thread is never read back: everything this
    turn needs was written down at the moment it was decided, and rebuilding a
    session out of `conversations_replies` is what let the planning day drift
    and made an already-approved skeleton get asked for twice.
    """
    kernel = _timeboxing_kernel(
        runtime,
        session_key=session_key,
        actor_user_id=actor_user_id,
        candidate_id=candidate_id,
    )
    repository = getattr(runtime, "timeboxing_session_store", None)
    if kernel is None or repository is None:
        logger.error(
            "adaptive timeboxing is not wired session_key=%s interaction=%s",
            session_key,
            interaction_id,
        )
        return timebox_failure_message()

    # The activity tracker no longer decides whether the Admonisher nudges --
    # `dispatch_planning_reminder` reads the session store for that (#256).
    # What it still owns is the idle timer: ten quiet minutes after the last
    # turn it asks the guardian to reconcile, which is how an abandoned
    # session earns its nudge back.
    timeboxing_activity.mark_active(
        user_id=actor_user_id,
        channel_id=card_channel,
        thread_ts=card_thread_ts,
    )

    progress_card = HarnessProgressCard(
        client, channel=progress_channel, message_ts=progress_ts
    )
    snapshot: PlanningSessionSnapshot | None = None
    try:
        snapshot = await repository.load_or_create(
            session_key, owner_user_id=actor_user_id
        )
        if action is not None:
            intent: TimeboxIntent = action.intent
            expected_revision = action.expected_revision
        else:
            intent = await derive_timebox_intent(
                runtime, snapshot, user_text=user_text
            )
            expected_revision = snapshot.revision
        outcome = await kernel.turn(
            TurnRequest(
                session_key=session_key,
                interaction_id=interaction_id,
                actor_user_id=actor_user_id,
                expected_revision=expected_revision,
                intent=intent,
            ),
            progress=KernelProgressSink(progress_card, session_key=session_key),
        )
        current = await repository.load_or_create(
            session_key, owner_user_id=actor_user_id
        )
        observer = getattr(runtime, "timeboxing_feedback_observer", None)
        if observer is not None:
            new_feedback = feedback_facts(snapshot, current)
            if new_feedback:
                try:
                    await observer.observe(session_key=session_key, facts=new_feedback)
                except Exception as exc:  # noqa: BLE001 - feedback must not fail the turn
                    logger.warning(
                        "stage1 feedback not recorded error_type=%s count=%d",
                        type(exc).__name__,
                        len(new_feedback),
                    )
        if current.status != "open":
            # Committed or cancelled: the session is over, so the idle timer
            # has nothing left to watch.
            timeboxing_activity.mark_inactive(user_id=actor_user_id)
        # What the commit gate actually spends is the pending candidate, not
        # the artifact, so a turn that leaves none on offer has to disarm it.
        # Back off stage 4 drops the `validated_candidate` and receipts the
        # card, but that edit is best-effort and swallowed: a Commit button
        # that survives it reaches `_handle_timebox_candidate_approval`, finds
        # no candidate artifact, returns False, and falls through to
        # `_execute_harness_approval`, which spends the still-armed candidate
        # and commits the plan the user just went back from. Bound to what the
        # session now holds rather than to which intent ran, and before the
        # mapper, which re-arms whenever it draws a candidate card.
        if not _candidate_is_on_offer(current):
            _pending_candidates.invalidate(session_key)
    except Exception as exc:  # noqa: BLE001 - one failure shape reaches Slack
        # The message and the traceback, not the class alone. The card keeps
        # the detail out of Slack on the promise that the log has it; on
        # 2026-09-02 the log had `error_type=ValueError` and the sentence that
        # named the cause had to be reconstructed from source.
        logger.error(
            "adaptive timeboxing turn failed session_key=%s interaction=%s "
            "error_type=%s error=%s",
            session_key,
            interaction_id,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return timebox_failure_message(snapshot=snapshot)
    finally:
        await progress_card.close()

    try:
        message, card = present_outcome(
            outcome,
            pending=_pending_candidates,
            snapshot=current,
            session_key=session_key,
            actor_user_id=actor_user_id,
            channel_id=card_channel,
            thread_ts=card_thread_ts,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - a stored artifact the mapper refuses
        # The turn is saved; only its picture failed. A skeleton whose payload
        # is not a `SkeletonPayload` lands here (Task 1 refuses new ones at
        # submit, but a store older than that contract can still hold one).
        logger.error(
            "adaptive timeboxing outcome could not be presented session_key=%s "
            "revision=%s error_type=%s error=%s",
            session_key,
            current.revision,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return timebox_failure_message(snapshot=current)

    # Close the card the user just acted on, then register this one. A failed
    # turn keeps the previous card live: its Retry is the way back.
    previous = _stage_cards.shown(session_key)
    receipt_body_text = None
    if isinstance(outcome, TurnFailed):
        done = None
    elif isinstance(outcome, Cancelled):
        done = "🚫 cancelled"
    elif previous is not None:
        done = receipt_label(intent, previous.card)
        receipt_body_text = receipt_body(intent, previous.card)
    else:
        done = None

    # The panel sits above the first card, so it has to post before that
    # card's own transition; once shown, it stays put and just gets edited.
    panel_synced_first = (
        not isinstance(outcome, (TurnFailed, Cancelled))
        and _stage_cards.panel_shown(session_key) is None
    )
    if panel_synced_first:
        await _stage_cards.sync_panel(
            client,
            session_key=session_key,
            snapshot=current,
            channel=card_channel,
            thread_ts=card_thread_ts,
            logger=logger,
        )
    await _stage_cards.transition(
        client,
        session_key=session_key,
        done=done,
        body=receipt_body_text,
        new_card=card,
        channel=progress_channel,
        ts=progress_ts,
        logger=logger,
    )
    if not panel_synced_first and not isinstance(outcome, (TurnFailed, Cancelled)):
        await _stage_cards.sync_panel(
            client,
            session_key=session_key,
            snapshot=current,
            channel=card_channel,
            thread_ts=card_thread_ts,
            logger=logger,
        )

    # A typed day change never relabelled the root; only the button path did
    # (#265). One place now, for both, over the day the kernel accepted.
    if (
        isinstance(intent, ConfirmPlanningDay)
        and not isinstance(outcome, TurnFailed)
        and card_thread_ts
        and card_thread_ts not in ("dm", progress_ts)
    ):
        label = format_relative_day_label(
            planned_date=intent.planning_day.date.isoformat(),
            tz_name=intent.planning_day.timezone,
        )
        title = f"Timeboxing session for {label}"
        # The message route redraws the root from the focus label at the end
        # of every turn (`_maybe_update_timeboxing_thread_constraints`), so a
        # relabel that only wrote Slack text was overwritten with the day
        # the session *opened* on, milliseconds later. The label is the
        # source; the write below is the same text, drawn now.
        if focus is not None:
            focus.set_thread_label(
                session_key,
                title=title,
                request_excerpt=None,
                state="in_progress",
                by_user=actor_user_id,
            )
        try:
            await client.chat_update(
                channel=card_channel,
                ts=card_thread_ts,
                text=_timeboxing_thread_root_text(
                    title=title, request_excerpt=None, state="in_progress"
                ),
            )
        except Exception:
            logger.debug("could not relabel the session thread root", exc_info=True)

    return message


async def _deliver_timebox_turn(
    *,
    runtime,
    client,
    logger,
    session_key: str,
    actor_user_id: str,
    interaction_id: str,
    channel_id: str,
    thread_ts: str,
    action: TimeboxActionEnvelope,
    candidate_id: str | None = None,
    focus: FocusManager | None = None,
) -> None:
    """Run one card-driven turn in the session thread and show its result.

    A card click has no "thinking" message of its own, so this posts one and
    then edits it. The same message carries the progress card and the outcome,
    which keeps the promise that one turn produces one user-facing result.
    """
    processing_payload: dict = {
        "channel": channel_id,
        "text": ":hourglass_flowing_sand: *timeboxing_agent* is thinking...",
        **_persona_payload_for("timeboxing_agent"),
    }
    if thread_ts and thread_ts != "dm":
        processing_payload["thread_ts"] = thread_ts
    processing = await client.chat_postMessage(**processing_payload)

    message = await _run_adaptive_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logger,
        session_key=session_key,
        actor_user_id=actor_user_id,
        interaction_id=interaction_id,
        progress_channel=processing["channel"],
        progress_ts=processing["ts"],
        card_channel=channel_id,
        card_thread_ts=thread_ts,
        action=action,
        candidate_id=candidate_id,
        focus=focus,
    )
    payload = _compact_slack_payload(text=message.text, blocks=message.blocks)
    update: dict = {
        "channel": processing["channel"],
        "ts": processing["ts"],
        "text": payload.get("text", "") or "",
    }
    if payload.get("blocks"):
        update["blocks"] = payload["blocks"]
    await client.chat_update(**update)


async def _handle_timebox_date_confirmation(
    *,
    runtime,
    client,
    logger,
    value: str,
    prompt_channel_id: str,
    prompt_ts: str,
    actor_user_id: str | None,
    interaction_id: str,
    focus: FocusManager | None = None,
) -> None:
    """Lock the planning day the user picked, through the session kernel.

    The weekday and day type come from the confirmed date by arithmetic. The
    model is never asked to classify a day it can only guess at from context.
    """
    meta = TimeboxCommitMeta.from_value(value)
    envelope = intent_from_date_action(value)
    if meta is None or envelope is None:
        logger.warning("timebox date confirmation carried unreadable metadata")
        return

    display_day = format_relative_day_label(planned_date=meta.date, tz_name=meta.tz)
    try:
        await client.chat_update(
            channel=prompt_channel_id,
            ts=prompt_ts,
            text=f"Starting timeboxing for {display_day}...",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"⏳ Starting timeboxing for *{display_day}*...",
                    },
                }
            ],
        )
    except Exception:
        # The card is a convenience; losing its edit must not lose the turn.
        logger.debug("could not show the date card's loading state", exc_info=True)

    await _deliver_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logger,
        session_key=meta.session_key,
        actor_user_id=meta.user_id or (actor_user_id or ""),
        interaction_id=interaction_id,
        channel_id=meta.channel_id,
        thread_ts=meta.thread_ts,
        action=envelope,
        focus=focus,
    )


def _card_interaction_id(action: dict, action_id: str, fallback_ts: str) -> str:
    """Name one card press so it cannot replay a different turn's outcome.

    Idempotency is keyed on this string, and returning the stored outcome for a
    key that was already used is the whole point of it. Slack sends `action_ts`
    on a block action, but the fallback used to be the message timestamp --
    which is also the interaction id of the *message* turn that drew the card,
    so a press could silently return that turn's answer and commit nothing.
    Both halves here are identifiers this system or Slack minted.
    """
    action_ts = str(action.get("action_ts") or "")
    return action_ts or f"{action_id}:{fallback_ts}"


def _latest_candidate(snapshot: PlanningSessionSnapshot) -> PlanningArtifact | None:
    """The validated candidate this session would present, if it has one."""

    candidates = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.kind is ArtifactKind.VALIDATED_CANDIDATE
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda artifact: artifact.revision)


def _candidate_is_on_offer(snapshot: PlanningSessionSnapshot) -> bool:
    """Whether a Commit press could still be honoured against this session.

    An approved candidate is spent: the kernel commits it in the same turn it
    is approved, through the port that consumes the pending entry. So "on
    offer" is a candidate the session holds and has not approved.
    """

    latest = _latest_candidate(snapshot)
    if latest is None:
        return False
    return not any(
        approval.artifact_id == latest.artifact_id
        and approval.artifact_revision == latest.revision
        and approval.artifact_digest == latest.digest
        for approval in snapshot.approvals
    )


def _pending_candidate_approval(
    snapshot: PlanningSessionSnapshot,
) -> ApproveArtifact | None:
    """Bind an approval to the candidate the session currently holds.

    The button carries the opaque ownership token and the revision it was drawn
    at; the artifact's identity comes from stored state instead, because that
    is the copy the kernel will check the digest against. A press that arrives
    after a newer candidate exists fails on the revision it stamped, so binding
    late here cannot silently retarget it.
    """
    latest = _latest_candidate(snapshot)
    if latest is None:
        return None
    return ApproveArtifact(
        artifact_id=latest.artifact_id,
        artifact_revision=latest.revision,
        artifact_digest=latest.digest,
    )


async def _handle_timebox_candidate_approval(
    *,
    runtime,
    client,
    logger,
    approval: HarnessApproveActionPayload,
    channel_id: str,
    thread_ts: str,
    actor_user_id: str,
    interaction_id: str,
) -> bool:
    """Commit the approved candidate *through* the session, not around it.

    The calendar write is unchanged -- the same validated candidate, spent
    through the same owner check, with the same tmbx digest as its idempotency
    key. What changes is that the kernel decides when it may happen and stores
    the receipt, so a committed session stops offering the plan it already
    wrote. Committing behind the kernel's back left the session parked at
    `AwaitingApproval` with a calendar that disagreed with it.

    Returns False when this thread has no planning session to tell, which is
    the legacy route's answer and its cue to commit the way it always has.
    """
    repository = getattr(runtime, "timeboxing_session_store", None)
    if approval.expected_revision is None or repository is None:
        return False
    snapshot = await repository.load_or_create(
        approval.thread_key, owner_user_id=actor_user_id
    )
    intent = _pending_candidate_approval(snapshot)
    if intent is None:
        return False
    await _deliver_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logger,
        session_key=approval.thread_key,
        actor_user_id=actor_user_id,
        interaction_id=interaction_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        action=TimeboxActionEnvelope(
            session_key=approval.thread_key,
            expected_revision=approval.expected_revision,
            intent=intent,
        ),
        candidate_id=approval.candidate_id,
    )
    return True


async def _handle_timebox_date_reselect(
    *,
    client,
    logger,
    value: str,
    selected_date: str,
    prompt_channel_id: str,
    prompt_ts: str,
) -> None:
    """Redraw the date card for another day, keeping every control it had.

    The dropdown rebuilds the whole message rather than editing one field, so
    a rebuild that forgets a control loses it silently. The day-type row is the
    one that matters here: without it the user who has just navigated to Monday
    has no typed way left to say that Monday is a holiday.
    """
    meta = TimeboxCommitMeta.from_value(value)
    if meta is None:
        logger.warning("timebox day selection carried unreadable metadata")
        return
    try:
        # The override is a claim about a specific day, so it does not travel.
        reselected = meta.with_selected_date(selected_date).with_day_type(None)
    except (TypeError, ValueError, ValidationError):
        logger.warning("timebox day selection carried an unusable date")
        return

    stage_card = date_stage_card(
        session_key=reselected.session_key,
        expected_revision=reselected.expected_revision,
        user_id=reselected.user_id,
        channel_id=reselected.channel_id,
        thread_ts=reselected.thread_ts,
        planned_date=reselected.date,
        tz_name=reselected.tz,
    )
    card = render_stage_card(stage_card)
    await client.chat_update(
        channel=prompt_channel_id,
        ts=prompt_ts,
        text=card.text,
        blocks=card.blocks,
    )
    # The redraw is the card the user now sees, so it is the one the next
    # receipt has to be drawn from.
    _stage_cards.remember(
        reselected.session_key, channel=prompt_channel_id, ts=prompt_ts, card=stage_card
    )

    label = format_relative_day_label(
        planned_date=reselected.date, tz_name=reselected.tz
    )
    # Legacy cards only: sessions opened before the surface convergence
    # (2026-09-01 spec) rooted themselves at their own card, so payloads with
    # thread_ts == prompt_ts are still live in the channel. For those, this
    # relabel would land on the card it just redrew -- this handler updates
    # prompt_ts with blocks and then writes bare text to the root -- and strip
    # its controls (2026-08-31 22:57 incident). New sessions always have a
    # separate root. Delete this guard only when no pre-convergence card can
    # still be clicked.
    #
    # The typed path relabels the root in exactly one other place --
    # `_run_adaptive_timebox_turn`, for both the button and the typed day
    # change (#265) -- and its guard is not this one.
    # `_handle_timebox_date_confirmation` overwrites the card with a
    # loading placeholder before its turn runs, so by the time the relabel in
    # `_run_adaptive_timebox_turn` fires there is nothing left on that message
    # to strip. That turn's own guard -- `card_thread_ts not in ("dm",
    # progress_ts)` -- covers a DM, which has no root, and the message route's
    # fallback where card_thread_ts *is* the message this turn is drawing its
    # card into. It does not cover a pre-convergence session whose thread root
    # is a live card reached by typed text: there card_thread_ts is that card's
    # ts, progress_ts is a fresh "thinking" message, and a typed day change
    # writes bare text over the card. See the final-fix report (M3).
    root_is_this_card = reselected.thread_ts == prompt_ts
    if reselected.thread_ts and reselected.thread_ts != "dm" and not root_is_this_card:
        try:
            await client.chat_update(
                channel=reselected.channel_id,
                ts=reselected.thread_ts,
                text=f":large_yellow_circle: Timeboxing session for {label}",
            )
        except Exception:
            logger.debug("could not relabel the session thread root", exc_info=True)


async def _handle_timebox_artifact_action(
    *,
    runtime,
    client,
    logger,
    value: str,
    channel_id: str,
    thread_ts: str,
    actor_user_id: str,
    interaction_id: str,
) -> None:
    """Apply one typed artifact decision taken from a card.

    The button carries the artifact's exact identity, so an approval drawn on
    an older skeleton is rejected by the kernel rather than silently applied to
    whatever is current.
    """
    envelope = intent_from_artifact_action(value)
    if envelope is None:
        logger.warning("timebox artifact action carried unreadable metadata")
        return
    await _deliver_timebox_turn(
        runtime=runtime,
        client=client,
        logger=logger,
        session_key=envelope.session_key,
        actor_user_id=actor_user_id,
        interaction_id=interaction_id,
        channel_id=channel_id,
        thread_ts=thread_ts,
        action=envelope,
    )


async def _handle_dsh_command(*, body, client, logger) -> None:
    """Carry one Slack turn to DeepSeek Harness and the answer back.

    The harness call is a blocking subprocess, so it goes to a worker thread —
    a planning turn runs for tens of seconds and would otherwise stall every
    other Slack event on the loop.

    Failures are posted, not swallowed. A harness that could not be reached and
    a planner that declined to act must not look the same in the thread.
    """
    from .dsh_progress_hook import DONE as PROGRESS_DONE
    from .dsh_progress_hook import START as PROGRESS_START
    from .harness_bridge import HarnessError, ask
    from .progress import ProgressChannel

    channel = body.get("channel_id")
    text = (body.get("text") or "").strip()
    if not text:
        await client.chat_postMessage(
            channel=channel, text="Give me something to plan."
        )
        return

    posted = await client.chat_postMessage(
        channel=channel, text=f"*{text}*\n_asking the harness…_"
    )
    thread_ts = posted["ts"]

    progress = ProgressChannel(client, channel=channel, thread_ts=thread_ts)
    loop = asyncio.get_running_loop()

    def on_tool_call(step: str) -> None:
        """Forward one tool-call event from the tailing thread to Slack.

        Two phases, because a step that only appears once it is finished
        cannot say "this is taking a while" -- which is the one thing a person
        waiting actually wants to know. `PreToolUse` opens the step as running
        and `PostToolUse` resolves it.

        Called off the event loop, so the coroutine is scheduled rather than
        awaited. The future is deliberately dropped: `ProgressChannel` already
        swallows its own Slack errors, and blocking the tailing thread on a
        Slack round-trip would delay every step behind it.
        """
        phase, _, label = step.partition("\t")
        if not label:
            # A line from before the phase prefix existed, or a hook that could
            # not name one. Reporting it as finished is the safe reading: the
            # alternative leaves a step spinning forever.
            phase, label = PROGRESS_DONE, step
        coro = progress.step(label) if phase == PROGRESS_START else progress.done(label)
        asyncio.run_coroutine_threadsafe(coro, loop)

    try:
        reply = await asyncio.to_thread(ask, text, on_event=on_tool_call)
    except HarnessError as exc:
        logger.warning("dsh: harness failed: %s", exc)
        # Into the checklist, not just the log. It is the one message small
        # enough that Slack is still willing to edit it when something larger
        # has just failed.
        await progress.fail("harness", str(exc))
        await client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f":warning: The harness did not answer.\n```{exc}```",
        )
        return

    await progress.close()
    # The answer is an artifact: posted once, never re-edited. Rewriting it on
    # every update is what grew a message past Slack's limit elsewhere.
    # Offered only when a write actually landed. A refused commit carries no
    # transaction id, so there is nothing to reverse and no button appears --
    # which is what stops Undo implying a change that never happened.
    answer_blocks = (
        [
            {"type": "section", "text": {"type": "mrkdwn", "text": reply.text[:2900]}},
            harness_undo_block(reply.committed_tx_id),
        ]
        if reply.committed_tx_id
        else None
    )
    await client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=reply.text[:3000],
        **({"blocks": answer_blocks} if answer_blocks else {}),
    )
    elapsed = (reply.timings or {}).get("elapsed_s")
    calls = (reply.timings or {}).get("tool_calls")
    summary = f"_answered by `{reply.profile}`_"
    if elapsed is not None:
        summary += f" _· {elapsed}s · {calls} tool calls_"
    await client.chat_update(channel=channel, ts=thread_ts, text=f"*{text}*\n{summary}")


async def _route_command_as_message(
    *,
    runtime,
    focus: FocusManager,
    agent_type: str,
    body: dict,
    text: str,
    client: AsyncWebClient,
    get_constraint_store: Callable[[], Awaitable[ConstraintStore | None]],
) -> None:
    """Drive a slash command through the route a typed message already takes.

    A command arrives with no thread and no message, so it is turned into the
    event the router knows how to handle rather than given a path of its own.
    Every command that grew its own path gave the same conversation a second
    session identity -- `/timebox` once created a thread the way typing never
    did -- so the synthetic event is built in one place, and a command differs
    from a message only in which agent it names.

    `say` is a no-op: the router posts through `client`, and a command has no
    channel message to reply beneath.
    """

    channel_id = body.get("channel_id") or ""

    async def _noop_say(**_kwargs):
        return {"channel": channel_id, "ts": "unused"}

    await route_slack_event(
        runtime=runtime,
        focus=focus,
        default_agent=agent_type,
        event={
            "type": "message",
            "text": text,
            "user": body.get("user_id") or "",
            "channel": channel_id,
            "ts": f"{time.time():.6f}",
            "channel_type": "im" if channel_id.startswith(("D", "G")) else "channel",
        },
        bot_user_id=None,
        say=_noop_say,
        client=client,
        get_constraint_store=get_constraint_store,
    )


async def _handle_timebox_command(
    *,
    runtime,
    focus: FocusManager,
    default_agent: str,
    body: dict,
    client: AsyncWebClient,
    respond: Callable | None,
    get_constraint_store: Callable[[], Awaitable[ConstraintStore | None]],
) -> None:
    user_id = body.get("user_id") or ""
    channel_id = body.get("channel_id") or ""
    text = (body.get("text") or "").strip()

    if not user_id or not channel_id:
        if respond:
            await respond(
                text="Timeboxing command failed: missing user or channel.",
                response_type="ephemeral",
            )
        return

    if respond:
        await respond(
            text="Starting a timeboxing session…",
            response_type="ephemeral",
        )

    try:
        await _route_command_as_message(
            runtime=runtime,
            focus=focus,
            agent_type="timeboxing_agent",
            body=body,
            text=text,
            client=client,
            get_constraint_store=get_constraint_store,
        )
    except Exception as e:
        logger.exception("Timeboxing command route_slack_event failed")
        if respond:
            await respond(
                text=f":warning: Failed to start timeboxing: {type(e).__name__}",
                response_type="ephemeral",
            )


async def _handle_task_refine_command(
    *,
    runtime,
    focus: FocusManager,
    body: dict,
    client: AsyncWebClient,
    respond: Callable | None,
    get_constraint_store: Callable[[], Awaitable[ConstraintStore | None]],
) -> None:
    user_id = body.get("user_id") or ""
    channel_id = body.get("channel_id") or ""
    text = (body.get("text") or "").strip()

    if not user_id or not channel_id:
        if respond:
            await respond(
                text="Task refinement command failed: missing user or channel.",
                response_type="ephemeral",
            )
        return

    if respond:
        await respond(
            text="Starting guided task refinement…",
            response_type="ephemeral",
        )

    try:
        await _route_command_as_message(
            runtime=runtime,
            focus=focus,
            agent_type="tasks_agent",
            body=body,
            text=text or "start guided task refinement session",
            client=client,
            get_constraint_store=get_constraint_store,
        )
    except Exception as e:
        logger.exception("Task refinement command route_slack_event failed")
        if respond:
            await respond(
                text=f":warning: Failed to start guided task refinement: {type(e).__name__}",
                response_type="ephemeral",
            )


def _auto_recover_timeboxing_focus_for_thread(
    *, focus: FocusManager, event: dict, user_id: str
) -> None:
    """
    If the bot restarts mid-session, FocusManager's in-memory mapping is lost.
    For timeboxing sessions anchored in the dedicated channel, recover focus for
    thread replies so users can keep talking in the thread without needing a new
    handoff.
    """

    if (event.get("channel_type") or "") == "im":
        return
    channel_id = event.get("channel") or ""
    ts = event.get("ts") or ""
    thread_ts = event.get("thread_ts")
    if not (channel_id and ts and thread_ts):
        return
    key = FocusManager.thread_key(channel_id, thread_ts, ts)
    if focus.get_focus(key):
        return

    timeboxing_channel = (
        _channel_for_agent("timeboxing_agent") or _plan_sessions_channel_id()
    )
    if not timeboxing_channel or channel_id != timeboxing_channel:
        return

    try:
        focus.set_focus(
            key, "timeboxing_agent", by_user=user_id or "unknown", note="auto-recover"
        )
    except ValueError:
        return


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
    payload.update(_persona_payload(persona))
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
    get_constraint_store: Callable[[], Awaitable[ConstraintStore | None]] | None = None,
    planning: PlanningCoordinator | None = None,
    acked: dict | None = None,
) -> None:
    channel = event["channel"]
    user = event.get("user") or event.get("bot_id") or "unknown"
    text = event.get("text") or ""
    thread_ts = event.get("thread_ts")
    ts = event["ts"]
    channel_type = event.get("channel_type")
    is_dm = channel_type == "im" or str(channel).startswith("D")

    async def _update_constraints(thread_key: str) -> None:
        """Refresh timeboxing constraints in the thread root message."""
        if not get_constraint_store:
            return
        try:
            store = await get_constraint_store()
            await _maybe_update_timeboxing_thread_constraints(
                client=client,
                focus=focus,
                thread_key=thread_key,
                user_id=user,
                store=store,
            )
        except Exception as exc:
            record_error(
                component="slack_routing", error_type="constraint_refresh_error"
            )
            logger.warning(
                "Non-fatal constraint refresh failure thread_key=%s user=%s error=%s",
                thread_key,
                user,
                f"{type(exc).__name__}: {_safe_exc_summary(exc)}",
            )

    # Give the conversation a memory. Fired without awaiting: `observe` costs a
    # model round trip and this route has a 30s budget, and the task reports
    # its own failure into the thread rather than into a log line -- a judge
    # failure must stay loud, or a misconfigured bot and a user who said
    # nothing memorable look identical.
    if text.strip() and user not in (None, "unknown"):
        from .thread_memory import remember, session_id_for

        asyncio.create_task(
            remember(
                client=client,
                channel=channel,
                session_id=session_id_for(channel, thread_ts, is_dm),
                user_id=user,
                text=_strip_bot_mention(text, bot_user_id),
                thread_ts=thread_ts,
            )
        )

    # In DMs, avoid creating a new "focus thread" per message (ts changes every message).
    # Instead, keep a stable key so multi-turn conversations work without requiring threads.
    if is_dm and not thread_ts:
        origin_key = f"{channel}:dm"
    else:
        origin_key = FocusManager.thread_key(channel, thread_ts, ts)
    binding = focus.get_focus(origin_key)
    user_focus = (
        focus.get_user_focus(user) if (is_dm and user and user != "unknown") else None
    )
    channel_default_agent = _agent_for_channel(channel) if not is_dm else None
    agent_type = (
        binding.agent_type
        if binding
        else (user_focus or channel_default_agent or default_agent)
    )

    # A thread that belongs to a planning session is one whatever FocusManager
    # remembers. Focus is an in-memory TTL cache and the bot restarted twice on
    # 2026-09-03; the session store is where the thread's ownership actually
    # lives. Asked, never created (Task 1's `load`).
    if thread_ts and agent_type != "timeboxing_agent":
        session_store = getattr(runtime, "timeboxing_session_store", None)
        if session_store is not None:
            # Ordered resolvers, planning first. The DM session key names the
            # whole DM, not this thread, so a live session would otherwise
            # claim the planning card's own thread and pin focus onto it.
            claimed_by_planning = False
            if planning is not None:
                try:
                    claimed_by_planning = await planning.owns_thread(
                        channel_id=channel, thread_ts=thread_ts
                    )
                except Exception:
                    logger.exception(
                        "planning thread ownership lookup failed for %s:%s",
                        channel,
                        thread_ts,
                    )
                    record_error(
                        component="surface_intent", error_type="resolver_failure"
                    )
            if not claimed_by_planning:
                session_key = f"{channel}:dm" if is_dm else f"{channel}:{thread_ts}"
                try:
                    session = await session_store.load(session_key)
                except Exception:
                    logger.exception("session lookup failed for %s", session_key)
                    record_error(
                        component="surface_intent", error_type="resolver_failure"
                    )
                    session = None
                # `open | committed | cancelled`: only an open session is
                # still running. Committed or cancelled, the session is over.
                if session is not None and session.status == "open":
                    # The binding is the claim: an agent the focus manager
                    # refuses is not one this route may hand the reply to.
                    try:
                        focus.set_focus(
                            origin_key, "timeboxing_agent", by_user=user, note="surface"
                        )
                    except ValueError:
                        logger.warning(
                            "focus refused the timeboxing claim for %s", origin_key
                        )
                    else:
                        agent_type = "timeboxing_agent"

    cleaned_text = _strip_bot_mention(text, bot_user_id)
    # The seam below may prefix `cleaned_text` with card context meant for
    # whichever agent answers. `user_reply_text` stays the user's own words,
    # for the one place downstream that quotes the message back to a human
    # (the handoff "Incoming request" root) -- that quote must never grow
    # interpreter prose the user never typed.
    user_reply_text = cleaned_text
    # Post the "thinking" message with the active agent persona, so the eventual reply
    # (via chat.update) keeps the correct name/icon.
    # `instant_ack` replies under a top-level app mention. Its own `ts` is
    # therefore a child message, while the event `ts` remains the canonical
    # thread root used by Slack action callbacks. Keep that root all the way
    # through harness session identity and approval payload generation.
    origin_thread_root_ts = thread_ts or (ts if acked is not None and not is_dm else None)
    origin_processing_payload: dict = {
        "channel": channel,
        "text": f":hourglass_flowing_sand: *{agent_type}* is thinking...",
    }
    if origin_thread_root_ts:
        origin_processing_payload["thread_ts"] = origin_thread_root_ts
    persona = _persona_for_agent(agent_type)
    origin_processing_payload.update(_persona_payload(persona))
    if acked is not None:
        # Reuse the instant acknowledgement rather than posting beside it. The
        # ack exists to make the first frame fast; a second message would make
        # it noisy instead.
        origin_processing_msg = acked
        try:
            await client.chat_update(
                channel=acked["channel"],
                ts=acked["ts"],
                text=origin_processing_payload["text"],
            )
        except Exception:
            pass
    else:
        origin_processing_msg = await client.chat_postMessage(
            **origin_processing_payload
        )

    async def _origin_update(*, text: str, blocks=None) -> None:
        compact = _compact_slack_payload(text=text, blocks=blocks)
        payload: dict[str, object] = {
            "channel": origin_processing_msg["channel"],
            "ts": origin_processing_msg["ts"],
            "text": compact.get("text", "") or "",
        }
        if compact.get("blocks"):
            payload["blocks"] = compact["blocks"]
        try:
            await client.chat_update(**payload)
            return
        except Exception as exc:
            record_error(component="slack_routing", error_type="route_exception")
            error_code = _slack_error_code(exc)
            logger.warning(
                "Slack origin update failed channel=%s ts=%s error=%s",
                origin_processing_msg["channel"],
                origin_processing_msg["ts"],
                error_code or type(exc).__name__,
            )
            fallback_text = _delivery_fallback_text(str(compact.get("text") or text))
            try:
                await client.chat_update(
                    channel=origin_processing_msg["channel"],
                    ts=origin_processing_msg["ts"],
                    text=fallback_text,
                )
                return
            except Exception:
                thread_root = origin_thread_root_ts
                fallback_payload = {
                    "channel": origin_processing_msg["channel"],
                    "text": fallback_text,
                }
                if thread_root:
                    fallback_payload["thread_ts"] = thread_root
                await client.chat_postMessage(**fallback_payload)
                return

    async def _permalink(channel_id: str, message_ts: str) -> str | None:
        try:
            res = await client.chat_getPermalink(
                channel=channel_id, message_ts=message_ts
            )
            return res.get("permalink")
        except Exception:
            return None

    async def _origin_link_to_thread(
        *, channel_id: str, thread_ts: str, agent_label: str
    ) -> None:
        link = await _permalink(channel_id, thread_ts)
        if not link:
            await _origin_update(
                text=f":left_right_arrow: Continuing in <#{channel_id}>."
            )
            return
        blocks = open_link_blocks(
            text=f":left_right_arrow: Continuing in <#{channel_id}> (agent: *{agent_label}*).",
            url=link,
            button_text="Go to Thread",
            action_id="ff_open_thread",
        )
        await _origin_update(
            text=f":left_right_arrow: Continuing in <#{channel_id}>.", blocks=blocks
        )

    async def _begin_timeboxing_session_surface(
        *,
        target_channel: str,
        origin_key: str,
        existing_root: dict | None = None,
    ) -> None:
        """Build the one surface a timeboxing session gets: root + threaded card.

        Every session, whatever door it came through, is a dedicated root
        header with the working card as its first thread reply. The root is
        only ever a header, so relabels can never erase a control again --
        which is the failure that ate the 2026-08-31 22:57 session's card.

        `existing_root` is the origin "thinking..." ack when the session lives
        in the channel the user is already in: it is repurposed into the root
        rather than left beside a second one.
        """
        persona = _persona_for_agent("timeboxing_agent")
        try:
            await _invite_user_to_channels_best_effort(
                client, user_id=user, channel_ids=[target_channel]
            )
        except Exception:
            pass

        if existing_root is not None:
            root_ts = existing_root["ts"]
            await client.chat_update(
                channel=target_channel,
                ts=root_ts,
                text=_timeboxing_thread_root_text(
                    title="Timeboxing session",
                    request_excerpt=None,
                    state="pending",
                ),
            )
        else:
            root_payload = {
                "channel": target_channel,
                "text": _timeboxing_thread_root_text(
                    title="Timeboxing session",
                    request_excerpt=None,
                    state="pending",
                ),
            }
            root_payload.update(_persona_payload(persona))
            root = await client.chat_postMessage(**root_payload)
            root_ts = root["ts"]

        try:
            focus.set_thread_label(
                f"{target_channel}:{root_ts}",
                title="Timeboxing session",
                request_excerpt=None,
                state="pending",
                by_user=user,
            )
            redirect = focus.set_redirect(
                origin_key,
                target_channel=target_channel,
                target_thread_ts=root_ts,
                agent_type="timeboxing_agent",
                by_user=user,
                note="session-surface",
            )
            focus.set_focus(
                redirect.target_key,
                "timeboxing_agent",
                by_user=user,
                note="session-surface",
            )
            focus.set_focus(
                origin_key, "timeboxing_agent", by_user=user, note="session-surface"
            )
            focus.set_user_focus(user, "timeboxing_agent")

            if not is_dm and channel != target_channel:
                await _origin_link_to_thread(
                    channel_id=target_channel,
                    thread_ts=root_ts,
                    agent_label=(
                        persona.username if persona else "timeboxing_agent"
                    ),
                )

            processing_payload = {
                "channel": target_channel,
                "thread_ts": root_ts,
                "text": ":hourglass_flowing_sand: *timeboxing_agent* is thinking...",
            }
            processing_payload.update(_persona_payload(persona))
            processing = await client.chat_postMessage(**processing_payload)

            try:
                if _timebox_backend() != "legacy":
                    result = await _run_adaptive_timebox_turn(
                        runtime=runtime,
                        client=client,
                        logger=logger,
                        session_key=redirect.target_key,
                        actor_user_id=user,
                        interaction_id=ts,
                        progress_channel=processing["channel"],
                        progress_ts=processing["ts"],
                        card_channel=target_channel,
                        card_thread_ts=root_ts,
                        user_text=cleaned_text,
                        focus=focus,
                    )
                else:
                    handoff_msg = _build_agent_message(
                        agent_type="timeboxing_agent",
                        cleaned_text=cleaned_text,
                        user=user,
                        channel=target_channel,
                        thread_ts=root_ts,
                        ts=root_ts,
                        force_channel=target_channel,
                        force_thread_root=root_ts,
                        force_reply=False,
                    )
                    result = await runtime.send_message(
                        handoff_msg,
                        recipient=AgentId(
                            "timeboxing_agent", key=redirect.target_key
                        ),
                    )
            except asyncio.TimeoutError:
                await client.chat_update(
                    channel=target_channel,
                    ts=processing["ts"],
                    text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again.",
                )
                return
            except Exception:
                logger.exception(
                    "timeboxing session surface turn failed (key=%s)",
                    redirect.target_key,
                )
                await client.chat_update(
                    channel=target_channel,
                    ts=processing["ts"],
                    text=":warning: Something went wrong while handling that request. Check bot logs.",
                )
                return

            payload = _compact_slack_payload(**_slack_payload_from_result(result))
            update = {
                "channel": target_channel,
                "ts": processing["ts"],
                "text": payload.get("text", "") or "",
            }
            if payload.get("blocks"):
                update["blocks"] = payload["blocks"]
            await client.chat_update(**update)

            if payload.get("blocks"):
                try:
                    meta = decode_metadata(
                        _timebox_start_button_value(payload["blocks"])
                    )
                    planned_date = meta.get("date") or ""
                    tz_name = meta.get("tz") or ""
                    if planned_date and tz_name:
                        label = format_relative_day_label(
                            planned_date=planned_date, tz_name=tz_name
                        )
                        title = f"Timeboxing session for {label}"
                        focus.set_thread_label(
                            redirect.target_key,
                            title=title,
                            request_excerpt=None,
                            state="pending",
                            by_user=user,
                        )
                        await client.chat_update(
                            channel=target_channel,
                            ts=root_ts,
                            text=_timeboxing_thread_root_text(
                                title=title,
                                request_excerpt=None,
                                state="pending",
                            ),
                        )
                except Exception:
                    pass

            if channel != target_channel and payload.get("blocks"):
                try:
                    permalink = await _permalink(target_channel, root_ts)
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
                            dm_blocks.extend(
                                open_link_blocks(
                                    text="Progress is tracked in the session thread:",
                                    url=permalink,
                                    button_text="Go to Session Thread",
                                    action_id="ff_open_thread",
                                )
                            )
                        if is_dm:
                            # DM origin: the DM's own "thinking..." message
                            # becomes the card, so the DM stays the control
                            # surface instead of sitting on a spinner forever.
                            await _origin_update(
                                text=update["text"], blocks=dm_blocks
                            )
                        else:
                            dm_payload = {
                                "channel": dm_channel,
                                "text": update["text"],
                                "blocks": dm_blocks,
                            }
                            dm_payload.update(_persona_payload(persona))
                            await client.chat_postMessage(**dm_payload)
                except Exception:
                    logger.debug(
                        "Failed to DM timeboxing commit prompt", exc_info=True
                    )

            await _maybe_update_timeboxing_thread_header(
                client=client,
                focus=focus,
                thread_key=redirect.target_key,
                state=_extract_thread_state(result) or "",
            )
            await _update_constraints(redirect.target_key)
        except Exception:
            logger.exception(
                "timeboxing session surface failed after the root was posted "
                "(root=%s:%s)",
                target_channel,
                root_ts,
            )
            record_error(
                component="slack_routing", error_type="session_surface_failure"
            )
            try:
                await client.chat_update(
                    channel=target_channel,
                    ts=root_ts,
                    text=_timeboxing_thread_root_text(
                        title="Timeboxing session",
                        request_excerpt=None,
                        state="canceled",
                    ),
                )
            except Exception:
                logger.debug("could not relabel the dead root", exc_info=True)
            return

    if planning and thread_ts and cleaned_text.strip():
        try:
            reply = await planning.maybe_handle_thread_reply(
                channel_id=channel,
                thread_ts=thread_ts,
                text=cleaned_text,
                thread_respond=_origin_update,
            )
        except SurfaceIntentError:
            # Loud, by design. The 2026-09-03 cold menu was this path
            # degrading to "not mine" and routing the message anyway.
            logger.exception(
                "planning-card reply interpretation failed channel=%s thread_ts=%s",
                channel,
                thread_ts,
            )
            record_error(component="surface_intent", error_type="interpret_failure")
            await _origin_update(
                text=(
                    ":warning: I couldn't read that reply against the planning card. "
                    "Use the card's controls, or say it again."
                )
            )
            return
        except Exception:
            # The reading succeeded and the press did not. By now the seam has
            # already said it was acting, so "I couldn't read that" would name
            # the wrong failure and send the user back to rephrasing a reply
            # that was understood.
            logger.exception(
                "planning-card press failed channel=%s thread_ts=%s",
                channel,
                thread_ts,
            )
            record_error(component="surface_intent", error_type="press_failure")
            await _origin_update(
                text=(
                    ":warning: I read that as a press on the planning card but "
                    "couldn't apply it. Check the card, or use its buttons."
                )
            )
            return
        if reply.outcome is ThreadReplyOutcome.HANDLED:
            return
        if reply.outcome is ThreadReplyOutcome.NO_PRESS and reply.context:
            # Whoever answers now knows what the card is.
            cleaned_text = f"{reply.context}\n\nThe user's reply:\n{cleaned_text}"

    redirect = focus.get_redirect(origin_key)
    if redirect and agent_type == redirect.agent_type:
        focus.set_user_focus(user, redirect.agent_type)
        persona = _persona_for_agent(redirect.agent_type)
        processing_payload = {
            "channel": redirect.target_channel,
            "thread_ts": redirect.target_thread_ts,
            "text": f":hourglass_flowing_sand: *{redirect.agent_type}* is thinking...",
        }
        processing_payload.update(_persona_payload(persona))
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
            record_error(component="slack_routing", error_type="stage_compute_failure")
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
            record_error(component="slack_routing", error_type="stage_compute_failure")
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
            await _origin_update(
                text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}"
            )
            return

        payload = _compact_slack_payload(**_slack_payload_from_result(result))
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
        if redirect.agent_type == "timeboxing_agent":
            await _update_constraints(redirect.target_key)
        if not is_dm:
            await _origin_link_to_thread(
                channel_id=redirect.target_channel,
                thread_ts=redirect.target_thread_ts,
                agent_label=(persona.username if persona else redirect.agent_type),
            )
        return

    # The fresh channel start used to root the session at the origin ack and
    # then use that same message as progress card and outcome card -- the
    # aliased layout that let a root relabel erase the Stage-0 card
    # (2026-08-31 22:57). The harness path now builds the one real surface;
    # only the legacy backend still takes the fallback below.
    would_alias_root = (
        agent_type == "timeboxing_agent"
        and not is_dm
        and not thread_ts
        and not origin_thread_root_ts
    )
    if would_alias_root and _timebox_backend() != "legacy":
        session_channel = _channel_for_agent("timeboxing_agent") or channel
        await _begin_timeboxing_session_surface(
            target_channel=session_channel,
            origin_key=origin_key,
            existing_root=(
                origin_processing_msg if session_channel == channel else None
            ),
        )
        return

    forced_thread_root = (
        "dm"
        if (is_dm and agent_type == "timeboxing_agent")
        else (
            (origin_thread_root_ts or origin_processing_msg["ts"])
            if (agent_type == "timeboxing_agent" and not thread_ts)
            else None
        )
    )
    msg = _build_agent_message(
        agent_type=agent_type,
        cleaned_text=cleaned_text,
        user=user,
        channel=channel,
        thread_ts=thread_ts,
        ts=ts,
        force_thread_root=forced_thread_root,
        force_reply=(
            True
            if (is_dm and agent_type == "timeboxing_agent")
            else False
            if (agent_type == "timeboxing_agent" and not thread_ts)
            else None
        ),
    )
    recipient_key = origin_key
    if forced_thread_root:
        recipient_key = f"{channel}:{forced_thread_root}"
    # The turn behind this call runs for tens of seconds -- measured at 43-54s
    # of graph on a real Refine turn, with prefetch and stage decision before
    # it. The ack posted above then sits unchanged for that whole minute, which
    # from the thread is indistinguishable from the bot having died. A user who
    # cannot tell those apart replies again, which is how one session reached
    # nine Refine passes.
    #
    # This edits that same message rather than posting new ones, and swallows
    # every failure: a progress indicator that spams the channel, or that takes
    # down the work it is reporting on, is worse than no indicator at all. Same
    # discipline ProgressChannel follows, for the same reason.
    heartbeat_stop = asyncio.Event()

    async def _turn_heartbeat() -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            try:
                await asyncio.wait_for(heartbeat_stop.wait(), timeout=8.0)
                return
            except asyncio.TimeoutError:
                pass
            elapsed = int(loop.time() - started)
            try:
                await client.chat_update(
                    channel=origin_processing_msg["channel"],
                    ts=origin_processing_msg["ts"],
                    text=(
                        f":hourglass_flowing_sand: *{agent_type}* is working"
                        f" — {elapsed}s elapsed…"
                    ),
                )
            except Exception:
                # One failed edit means the next will probably fail too, and a
                # retry loop against Slack helps nobody. Stop quietly; the real
                # reply still goes out through _origin_update.
                return

    primary_harness_turn = (
        agent_type == "timeboxing_agent" and _timebox_backend() != "legacy"
    )
    heartbeat_task = (
        None if primary_harness_turn else asyncio.create_task(_turn_heartbeat())
    )
    try:
        if primary_harness_turn:
            # The Schedular answers through the adaptive session kernel now.
            # Same persona, same thread, different authority: the session's
            # state is the repository, and the thread is only where it is
            # shown. Reconstructing it from `conversations_replies` is what
            # let a locked Saturday become Friday between two turns.
            result = await _run_adaptive_timebox_turn(
                runtime=runtime,
                client=client,
                logger=logger,
                session_key=recipient_key,
                actor_user_id=user,
                interaction_id=ts,
                progress_channel=origin_processing_msg["channel"],
                progress_ts=origin_processing_msg["ts"],
                card_channel=channel,
                card_thread_ts=(
                    forced_thread_root
                    or origin_thread_root_ts
                    or origin_processing_msg["ts"]
                ),
                user_text=cleaned_text,
                focus=focus,
            )
        else:
            result = await runtime.send_message(
                msg, recipient=AgentId(agent_type, key=recipient_key)
            )
    except asyncio.TimeoutError:
        record_error(component="slack_routing", error_type="stage_compute_failure")
        await _origin_update(
            text=(
                ":hourglass_flowing_sand: Timed out waiting for tools/LLM. "
                "Please try again in a moment."
            )
        )
        return
    except Exception as e:
        record_error(component="slack_routing", error_type="stage_compute_failure")
        logger.exception(
            "runtime.send_message failed (agent=%s key=%s)",
            agent_type,
            recipient_key,
        )
        await _origin_update(
            text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}"
        )
        return
    finally:
        # Stop the heartbeat before anything writes the real reply, so it can
        # never overwrite the answer with a stale "still working" edit.
        heartbeat_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
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
                if handoff_target == "timeboxing_agent":
                    await _begin_timeboxing_session_surface(
                        target_channel=target_channel,
                        origin_key=origin_key,
                    )
                    return
                persona = _persona_for_agent(handoff_target)
                root_payload = {
                    "channel": target_channel,
                    "text": (
                        f"Incoming request from <@{user}> (requested in {_origin_label(event)}):\n"
                        f"> {user_reply_text}"
                    ),
                }
                root_payload.update(_persona_payload(persona))
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

                if not is_dm:
                    await _origin_link_to_thread(
                        channel_id=target_channel,
                        thread_ts=target_thread_ts,
                        agent_label=(persona.username if persona else handoff_target),
                    )
                    try:
                        await _dm_thread_link(
                            client,
                            user_id=user,
                            target_channel=target_channel,
                            thread_root_ts=target_thread_ts,
                            agent_label=(
                                persona.username if persona else handoff_target
                            ),
                        )
                    except Exception:
                        pass

                processing_payload = {
                    "channel": target_channel,
                    "thread_ts": target_thread_ts,
                    "text": f":hourglass_flowing_sand: *{handoff_target}* is thinking...",
                }
                processing_payload.update(_persona_payload(persona))
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

                # No separate approval post on the kernel route: the outcome
                # renderer already carries the one control that can commit, and
                # a second card would offer the same candidate twice.

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
        await _origin_update(
            text=f":left_right_arrow: Handing off to *{handoff_target}*..."
        )
        handoff_msg = _build_agent_message(
            agent_type=handoff_target,
            cleaned_text=cleaned_text,
            user=user,
            channel=channel,
            thread_ts=thread_ts,
            ts=ts,
            force_thread_root=(
                "dm" if (is_dm and handoff_target == "timeboxing_agent") else None
            ),
            force_reply=(
                True if (is_dm and handoff_target == "timeboxing_agent") else None
            ),
        )
        try:
            result = await runtime.send_message(
                handoff_msg, recipient=AgentId(handoff_target, key=origin_key)
            )
        except asyncio.TimeoutError:
            record_error(component="slack_routing", error_type="stage_compute_failure")
            await _origin_update(
                text=":hourglass_flowing_sand: Timed out waiting for tools/LLM. Please try again."
            )
            return
        except Exception as e:
            record_error(component="slack_routing", error_type="stage_compute_failure")
            logger.exception(
                "runtime.send_message failed (handoff agent=%s key=%s)",
                handoff_target,
                origin_key,
            )
            await _origin_update(
                text=f":warning: {type(e).__name__}: {_safe_exc_summary(e)}"
            )
            return
        payload = _with_agent_attribution(
            _compact_slack_payload(**_slack_payload_from_result(result)),
            handoff_target,
        )
        # chat.update can't change username/icon, so keep the original message as a handoff marker
        # and post the actual reply as the target agent persona.
        await _origin_update(
            text=f":left_right_arrow: Handed off to *{handoff_target}*."
        )
        reply_payload: dict = {
            "channel": channel,
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            reply_payload["blocks"] = payload["blocks"]
        if origin_thread_root_ts:
            reply_payload["thread_ts"] = origin_thread_root_ts
        reply_persona = _persona_for_agent(handoff_target)
        reply_payload.update(_persona_payload(reply_persona))
        await client.chat_postMessage(**reply_payload)
        return

    focus.set_user_focus(user, agent_type)
    payload = _with_agent_attribution(
        _compact_slack_payload(**_slack_payload_from_result(result)), agent_type
    )
    await _origin_update(text=payload.get("text", ""), blocks=payload.get("blocks"))
    if not primary_harness_turn:
        # The kernel route renders its own approval control beside the plan, so
        # posting a second one here would offer the same candidate twice.
        await _post_pending_harness_approval(
            client=client,
            logger=logger,
            channel=channel,
            thread_root=_approval_thread_root(
                origin_thread_root_ts, origin_processing_msg["ts"]
            ),
            thread_key=recipient_key,
            proposal_message_ts=origin_processing_msg["ts"],
        )
    await _maybe_update_timeboxing_thread_header(
        client=client,
        focus=focus,
        thread_key=origin_key,
        state=_extract_thread_state(result) or "",
    )
    if agent_type == "timeboxing_agent":
        await _update_constraints(origin_key)


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
    timeboxing_submit = TimeboxingSubmitCoordinator(runtime=runtime, client=app.client)
    timeboxing_stage_actions = TimeboxingStageActionCoordinator(
        runtime=runtime, client=app.client
    )
    workspace_bootstrap_attempted = False
    invited_users: set[str] = set()

    async def _ensure_workspace_registry(client: AsyncWebClient) -> None:
        nonlocal workspace_bootstrap_attempted
        if WorkspaceRegistry.get_global() is not None:
            return
        if workspace_bootstrap_attempted:
            return
        workspace_bootstrap_attempted = True
        try:
            # First try a full bootstrap (requires Slack list/join scopes).
            store = await _get_workspace_store()
            directory = await ensure_workspace_ready(client, store=store)
            if directory:
                return
        except Exception:
            logger.debug("On-demand workspace bootstrap failed", exc_info=True)

        # Fallback: reconstruct the registry from the persisted DB bindings (no Slack list scopes).
        try:
            store = await _get_workspace_store()
            if not store:
                return
            auth = await client.auth_test()
            team_id = auth.get("team_id") or ""
            if not team_id:
                return
            bindings = await store.get_channels(team_id=team_id)
            if not bindings:
                return
            channels_by_name = {name: row.channel_id for name, row in bindings.items()}
            channels_by_agent = {
                row.agent_type: row.channel_id
                for row in bindings.values()
                if row.agent_type and row.channel_id
            }
            directory = WorkspaceDirectory(
                team_id=team_id,
                channels_by_name=channels_by_name,
                channels_by_agent=channels_by_agent,
                personas_by_agent=dict(DEFAULT_PERSONAS),
            )
            WorkspaceRegistry.set_global(directory)
        except Exception:
            logger.debug("Failed to load workspace bindings from DB", exc_info=True)

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

    async def _ensure_user_invited(client: AsyncWebClient, *, user_id: str) -> None:
        if not user_id or user_id in invited_users:
            return
        directory = WorkspaceRegistry.get_global()
        if not directory:
            return
        invited_users.add(user_id)
        try:
            await _invite_user_to_channels_best_effort(
                client,
                user_id=user_id,
                channel_ids=[
                    directory.channels_by_name.get("plan-sessions", ""),
                    directory.channels_by_name.get("review", ""),
                    directory.channels_by_name.get("task-marshalling", ""),
                    directory.channels_by_name.get("scheduling", ""),
                    directory.channels_by_name.get("admonishments", ""),
                ],
            )
        except Exception:
            pass

    async def _maybe_register_user_with_guard(
        *, user_id: str, channel_id: str, channel_type: str, origin: str
    ) -> None:
        if not user_id or not channel_id:
            return
        timeout_s = float(getattr(settings, "slack_register_user_timeout_seconds", 3.0))
        started = perf_counter()
        try:
            await asyncio.wait_for(
                planning.maybe_register_user(
                    user_id=user_id,
                    channel_id=channel_id,
                    channel_type=channel_type,
                ),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            duration_s = perf_counter() - started
            observe_stage_duration(
                stage="slack_preroute_register_timeout", duration_s=duration_s
            )
            record_error(component="slack_routing", error_type="register_timeout")
            logger.warning(
                "Slack pre-route register timed out origin=%s user=%s channel=%s timeout_s=%.2f",
                origin,
                user_id,
                channel_id,
                timeout_s,
            )
        except Exception:
            duration_s = perf_counter() - started
            observe_stage_duration(
                stage="slack_preroute_register_error", duration_s=duration_s
            )
            record_error(component="slack_routing", error_type="register_error")
            logger.exception(
                "Slack pre-route register failed origin=%s user=%s channel=%s",
                origin,
                user_id,
                channel_id,
            )
        else:
            observe_stage_duration(
                stage="slack_preroute_register", duration_s=perf_counter() - started
            )
            logger.debug(
                "Slack pre-route register done origin=%s user=%s channel=%s",
                origin,
                user_id,
                channel_id,
            )

    async def _route_slack_event_with_guard(
        *,
        event: dict,
        say: Callable,
        bot_user_id: str | None,
        client: AsyncWebClient,
        origin: str,
        acked: dict | None = None,
    ) -> None:
        def _fallback_thread_root() -> str | None:
            candidate = str(event.get("thread_ts") or "").strip()
            if candidate:
                return candidate
            channel_id = str(event.get("channel") or "")
            channel_type = str(event.get("channel_type") or "")
            is_dm = channel_type == "im" or channel_id.startswith("D")
            if is_dm:
                return None
            ts = str(event.get("ts") or "").strip()
            return ts or None

        async def _post_dispatch_fallback(text: str) -> None:
            channel_id = str(event.get("channel") or "").strip()
            if not channel_id:
                return
            payload: dict[str, str] = {
                "channel": channel_id,
                "text": text,
            }
            thread_root = _fallback_thread_root()
            if thread_root:
                payload["thread_ts"] = thread_root
            await client.chat_postMessage(**payload)

        timeout_s = float(
            getattr(settings, "slack_route_dispatch_timeout_seconds", 75.0)
        )
        started = perf_counter()
        route_task = asyncio.create_task(
            route_slack_event(
                acked=acked,
                runtime=runtime,
                focus=focus,
                default_agent=default_agent,
                event=event,
                bot_user_id=bot_user_id,
                say=say,
                client=client,
                get_constraint_store=_get_constraint_store,
                planning=planning,
            )
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(route_task),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            duration_s = perf_counter() - started
            observe_stage_duration(
                stage="slack_route_dispatch_backgrounded", duration_s=duration_s
            )
            logger.info(
                "Slack route dispatch continues in background origin=%s channel=%s ts=%s timeout_s=%.2f",
                origin,
                event.get("channel"),
                event.get("ts"),
                timeout_s,
            )

            async def _observe_backgrounded_route() -> None:
                try:
                    await route_task
                except asyncio.CancelledError:
                    return
                except Exception:
                    record_error(
                        component="slack_routing",
                        error_type="background_route_exception",
                    )
                    logger.exception(
                        "Background Slack route failed origin=%s channel=%s ts=%s",
                        origin,
                        event.get("channel"),
                        event.get("ts"),
                    )
                    try:
                        await _post_dispatch_fallback(
                            ":warning: Routing failed before I could finish this reply. Please retry the message."
                        )
                    except Exception:
                        logger.exception(
                            "Failed to post background route fallback channel=%s ts=%s",
                            event.get("channel"),
                            event.get("ts"),
                        )

            asyncio.create_task(_observe_backgrounded_route())
        except asyncio.CancelledError:
            route_task.cancel()
            raise
        except Exception:
            duration_s = perf_counter() - started
            observe_stage_duration(
                stage="slack_route_dispatch_error", duration_s=duration_s
            )
            record_error(component="slack_routing", error_type="route_exception")
            logger.exception(
                "Slack route dispatch failed origin=%s channel=%s ts=%s",
                origin,
                event.get("channel"),
                event.get("ts"),
            )
            try:
                await _post_dispatch_fallback(
                    ":warning: Routing failed before I could finish this reply. Please retry the message."
                )
            except Exception:
                logger.exception(
                    "Failed to post Slack route exception fallback channel=%s ts=%s",
                    event.get("channel"),
                    event.get("ts"),
                )
        else:
            observe_stage_duration(
                stage="slack_route_dispatch", duration_s=perf_counter() - started
            )

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
                    directory.channels_by_name.get("admonishments", ""),
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

    @app.action(FF_HARNESS_UNDO_ACTION_ID)
    async def act_harness_undo(ack, body, client, logger):
        """Reverse the commit this message reported, by its own transaction id.

        Calls tmbx directly rather than asking the harness to undo. A button
        that means "reverse that" must reverse that: routing it through a
        planning turn would let a model decide whether to call plan_undo and
        with which id, which is a judgement in a mechanical path guarding the
        calendar.

        Every outcome is posted. A refusal carries its reason -- a day that has
        drifted since the commit and a transaction that never existed are
        different problems -- and an unreachable server says so rather than
        being reported as a refusal tmbx never made.
        """
        await ack()
        from .tmbx_client import TmbxClient, UndoUnavailable

        action = (body.get("actions") or [{}])[0]
        tx_id = (action.get("value") or "").strip()
        channel = (body.get("channel") or {}).get("id") or ""
        message = body.get("message") or {}
        thread_root = message.get("thread_ts") or message.get("ts") or ""
        if not (channel and tx_id):
            logger.warning("undo pressed with no transaction to reverse")
            return

        try:
            payload = await TmbxClient().undo(tx_id)
            text = _undo_outcome_text(payload)
        except UndoUnavailable as exc:
            logger.warning("undo could not reach tmbx: %s", exc)
            text = (
                ":warning: Could not reach the calendar service, so nothing was "
                f"undone and the commit still stands.\n```{exc}```"
            )
        await client.chat_postMessage(
            channel=channel, thread_ts=thread_root or None, text=text
        )

    @app.action(FF_HARNESS_APPROVE_ACTION_ID)
    async def act_harness_approve(ack, body, client, logger):
        """Atomically spend approval on the exact displayed candidate.

        The only thing that opens the commit gate. It is a button rather than
        a phrase because reading consent out of prose is a judgement about
        meaning, and a model in the path protecting the calendar is a failure
        surface exactly where failure is least acceptable.
        """
        await ack()
        channel = (body.get("channel") or {}).get("id") or ""
        message = body.get("message") or {}
        thread_root = message.get("thread_ts") or message.get("ts") or ""
        if not (channel and thread_root):
            logger.warning("approve pressed with no thread to approve for")
            return

        thread_key = f"{channel}:{thread_root}"
        actor_user_id = (body.get("user") or {}).get("id") or ""
        action = (body.get("actions") or [{}])[0]
        try:
            approval = HarnessApproveActionPayload.model_validate_json(
                action.get("value") or ""
            )
        except ValueError:
            logger.warning("approve pressed with malformed candidate identity")
            return
        if approval.thread_key != thread_key:
            logger.warning("approve candidate thread did not match message thread")
            return

        if _timebox_backend() != "legacy":
            # A planning session exists for this thread, so the commit belongs
            # inside it: the kernel is what decides a commit is allowed and
            # what stores the receipt afterwards. The write itself is the same
            # one either way -- same candidate, same idempotency digest.
            handled = await _handle_timebox_candidate_approval(
                runtime=runtime,
                client=client,
                logger=logger,
                approval=approval,
                channel_id=channel,
                thread_ts=thread_root,
                actor_user_id=actor_user_id,
                interaction_id=_card_interaction_id(
                    action, FF_HARNESS_APPROVE_ACTION_ID, thread_root
                ),
            )
            if handled:
                return

        task = asyncio.create_task(
            _execute_harness_approval(
                client=client,
                logger=logger,
                channel=channel,
                thread_root=thread_root,
                thread_key=thread_key,
                candidate_id=approval.candidate_id,
                actor_user_id=actor_user_id,
            )
        )
        _track_approval_task(task, logger)
        # Bolt may cancel the listener after its delivery window. The commit
        # owns an independent task once acked, so this waiter cannot turn a
        # real calendar write into an ambiguous cancelled callback.
        await asyncio.shield(task)

    @app.command("/dsh")
    async def cmd_dsh(ack, body, client, logger):
        """Route one turn to DeepSeek Harness.

        The harness owns the loop and the tools; this handler only carries the
        message there and the answer back. Everything runs in a thread off the
        posted message, so the thread is the unit of conversation the way it
        already is everywhere else in Slack.
        """
        await ack()
        asyncio.create_task(
            _handle_dsh_command(body=body, client=client, logger=logger)
        )

    @app.command("/timebox")
    async def cmd_timebox(ack, body, respond, client, logger):
        """Plan a day. Both backends start the same way: by asking which day.

        Neither backend launches a planner here any more. `/timebox` creates or
        reuses the plan-session thread and renders the date card; the harness
        backend then continues through the adaptive session kernel and the
        legacy backend through the five-stage machine. Forking a second thread
        creation for the harness is what once gave the two backends different
        session identities for the same conversation.

        FF_TIMEBOX_BACKEND=legacy routes back to the AutoGen flow, which stays
        wired and reachable. A migration nobody can reverse is a rewrite.
        """
        await ack()
        # Fire off in background to avoid blocking Slack's 3-second timeout
        asyncio.create_task(
            _handle_timebox_command(
                runtime=runtime,
                focus=focus,
                default_agent=default_agent,
                body=body,
                client=client,
                respond=respond,
                get_constraint_store=_get_constraint_store,
            )
        )

    @app.command("/task-refine")
    async def cmd_task_refine(ack, body, respond, client, logger):
        await ack()
        asyncio.create_task(
            _handle_task_refine_command(
                runtime=runtime,
                focus=focus,
                body=body,
                client=client,
                respond=respond,
                get_constraint_store=_get_constraint_store,
            )
        )

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

    @app.action("ff_open_admonishments_log")
    async def on_open_admonishments_log_action(ack, body, logger):
        await ack()

    @app.action(FF_EVENT_OPEN_URL_ACTION_ID)
    async def on_event_open_url_action(ack, body, logger):
        """Acknowledge planning-card URL button clicks; Slack opens the URL natively."""
        await ack()

    @app.action("open_event_url")
    async def on_event_open_url_action_legacy(ack, body, logger):
        """Backward-compatible ack for cards rendered before action-ID normalization."""
        await ack()

    @app.action(FF_EVENT_ADD_DISABLED_ACTION_ID)
    async def on_event_add_disabled_action(ack, body, logger):
        await ack()

    @app.action(FF_EVENT_START_AT_ACTION_ID)
    async def on_event_start_at_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        selected = action.get("selected_date_time")
        if channel_id and message_ts and selected is not None:
            await planning.handle_start_at_changed(
                channel_id=channel_id,
                message_ts=message_ts,
                selected_date_time=int(selected),
            )
            await planning.refresh_card_for_message(
                channel_id=channel_id, message_ts=message_ts, respond=respond
            )

    @app.action(FF_EVENT_START_DATE_ACTION_ID)
    async def on_event_start_date_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        selected = action.get("selected_date")
        if channel_id and message_ts and selected:
            await planning.handle_start_date_changed(
                channel_id=channel_id,
                message_ts=message_ts,
                selected_date=str(selected),
            )
            await planning.refresh_card_for_message(
                channel_id=channel_id, message_ts=message_ts, respond=respond
            )

    @app.action(FF_EVENT_START_TIME_ACTION_ID)
    async def on_event_start_time_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        selected = action.get("selected_time")
        if channel_id and message_ts and selected:
            await planning.handle_start_time_changed(
                channel_id=channel_id,
                message_ts=message_ts,
                selected_time=str(selected),
            )
            await planning.refresh_card_for_message(
                channel_id=channel_id, message_ts=message_ts, respond=respond
            )

    @app.action(FF_EVENT_DURATION_ACTION_ID)
    async def on_event_duration_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        selected = (
            (action.get("selected_option") or {}) if isinstance(action, dict) else {}
        ) or {}
        value = selected.get("value")
        if channel_id and message_ts and value:
            try:
                duration_min = int(value)
            except ValueError:
                return
            await planning.handle_duration_changed(
                channel_id=channel_id,
                message_ts=message_ts,
                duration_min=duration_min,
            )
            await planning.refresh_card_for_message(
                channel_id=channel_id, message_ts=message_ts, respond=respond
            )

    @app.action(FF_EVENT_ADD_ACTION_ID)
    async def on_event_add_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        draft_id = parse_draft_id_from_value(action.get("value") or "")
        if draft_id:
            await planning.start_add_to_calendar(draft_id=draft_id, respond=respond)
            return
        logger.warning(
            "on_event_add_action missing draft_id: channel=%s message_ts=%s action_value=%r",
            (body.get("channel") or {}).get("id"),
            (body.get("message") or {}).get("ts"),
            action.get("value"),
        )

    @app.action(FF_EVENT_RETRY_ACTION_ID)
    async def on_event_retry_action(ack, body, respond, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        draft_id = parse_draft_id_from_value(action.get("value") or "")
        if draft_id:
            await planning.start_add_to_calendar(draft_id=draft_id, respond=respond)
            return
        logger.warning(
            "on_event_retry_action missing draft_id: channel=%s message_ts=%s action_value=%r",
            (body.get("channel") or {}).get("id"),
            (body.get("message") or {}).get("ts"),
            action.get("value"),
        )

    @app.action(FF_EVENT_EDIT_ACTION_ID)
    async def on_event_edit_action(ack, body, client, logger):
        """Open the Edit modal (duration etc.) for a planning card."""
        await ack()
        trigger_id = body.get("trigger_id") or ""
        action = (body.get("actions") or [{}])[0]
        draft_id = parse_draft_id_from_value(action.get("value") or "")
        if not (draft_id and trigger_id):
            logger.warning(
                "on_event_edit_action: missing draft_id or trigger_id (draft_id=%r trigger_id=%r)",
                draft_id,
                trigger_id,
            )
            return
        await planning.open_edit_modal(
            draft_id=draft_id,
            trigger_id=trigger_id,
            client=client,
        )

    @app.view(FF_EVENT_EDIT_MODAL_CALLBACK_ID)
    async def on_event_edit_modal_submit(ack, body, client, logger):
        """Persist changes from the Edit modal and refresh the planning card."""
        await ack()
        import json as _json

        private_meta = (body.get("view") or {}).get("private_metadata") or "{}"
        try:
            meta = _json.loads(private_meta)
        except Exception:
            logger.warning("on_event_edit_modal_submit: invalid private_metadata")
            return
        draft_id = meta.get("draft_id") or ""
        if not draft_id:
            return
        values = ((body.get("view") or {}).get("state") or {}).get("values") or {}
        date_str = (
            (values.get("date_input") or {}).get("date_select", {}).get("selected_date")
        )
        duration_str = (
            (values.get("duration_input") or {})
            .get("duration_select", {})
            .get("selected_option", {})
            .get("value")
        )
        if not duration_str:
            return
        try:
            duration_min = int(duration_str)
        except ValueError:
            logger.warning(
                "on_event_edit_modal_submit: invalid duration_str=%r", duration_str
            )
            return
        await planning.handle_edit_modal_submit(
            draft_id=draft_id,
            duration_min=duration_min,
            date_str=date_str or None,
        )

    @app.action(FF_TASK_VIEW_ALL_ACTION_ID)
    async def on_task_view_all_due_action(ack, body, client, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        metadata = decode_task_metadata((action.get("value") or ""))
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        thread_ts = (body.get("message") or {}).get("thread_ts") or message_ts
        user_id = (body.get("user") or {}).get("id") or ""
        due_date = metadata.get("due_date") or ""
        source = metadata.get("source") or "ticktick"
        project_ids = [
            item.strip()
            for item in (metadata.get("project_ids") or "").split(",")
            if item.strip()
        ]
        if not (channel_id and message_ts and thread_ts and user_id and due_date):
            return
        try:
            result = await runtime.send_message(
                TaskDueActionRequest(
                    action="view_all_due",
                    user_id=user_id,
                    due_date=due_date,
                    source=source,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    ticktick_project_ids=project_ids,
                ),
                recipient=AgentId("tasks_agent", key=f"{channel_id}:{thread_ts}"),
            )
        except Exception as exc:
            logger.warning(
                "on_task_view_all_due_action failed (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return
        payload = _slack_payload_from_result(result)
        update: dict[str, str | list[dict]] = {
            "channel": channel_id,
            "ts": message_ts,
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)

    @app.action(FF_TASK_DETAILS_ACTION_ID)
    async def on_task_details_action(ack, body, client, logger):
        await ack()
        trigger_id = body.get("trigger_id") or ""
        action = (body.get("actions") or [{}])[0]
        metadata = decode_task_metadata((action.get("value") or ""))
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        thread_ts = (body.get("message") or {}).get("thread_ts") or message_ts
        user_id = (body.get("user") or {}).get("id") or ""
        if not (
            trigger_id
            and channel_id
            and thread_ts
            and user_id
            and metadata.get("task_id")
            and metadata.get("project_id")
        ):
            return
        try:
            response = await runtime.send_message(
                TaskDetailsModalRequest(
                    user_id=user_id,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    task_id=metadata.get("task_id", ""),
                    project_id=metadata.get("project_id", ""),
                    label=metadata.get("label", ""),
                    title=metadata.get("title", ""),
                    project_name=metadata.get("project_name", ""),
                    due_date=metadata.get("due_date", ""),
                ),
                recipient=AgentId("tasks_agent", key=f"{channel_id}:{thread_ts}"),
            )
        except Exception as exc:
            logger.warning(
                "on_task_details_action runtime call failed (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return
        modal = response if isinstance(response, TaskDetailsModalResponse) else None
        if modal is None:
            modal = TaskDetailsModalResponse.model_validate(response)
        if not (modal.ok and isinstance(modal.view, dict)):
            return
        await client.views_open(trigger_id=trigger_id, view=modal.view)

    @app.view(FF_TASK_EDIT_MODAL_CALLBACK_ID)
    async def on_task_edit_modal_submit(ack, body, client, logger):
        await ack()
        view = body.get("view") or {}
        metadata = decode_task_metadata(str(view.get("private_metadata") or ""))
        channel_id = metadata.get("channel_id") or ""
        thread_ts = metadata.get("thread_ts") or ""
        user_id = metadata.get("user_id") or (body.get("user") or {}).get("id") or ""
        project_id = metadata.get("project_id") or ""
        task_id = metadata.get("task_id") or ""
        label = metadata.get("label") or ""
        values = (view.get("state") or {}).get("values") or {}
        title_value = (
            (values.get("task_title_input") or {})
            .get("task_title_value", {})
            .get("value")
        )
        new_title = str(title_value or "").strip()
        if not (
            channel_id
            and thread_ts
            and user_id
            and project_id
            and task_id
            and new_title
        ):
            return
        try:
            result = await runtime.send_message(
                TaskEditTitleRequest(
                    user_id=user_id,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    task_id=task_id,
                    project_id=project_id,
                    label=label,
                    new_title=new_title,
                ),
                recipient=AgentId("tasks_agent", key=f"{channel_id}:{thread_ts}"),
            )
        except Exception as exc:
            logger.warning(
                "on_task_edit_modal_submit runtime call failed (%s: %s)",
                type(exc).__name__,
                exc,
            )
            return
        edit_response = result if isinstance(result, TaskEditTitleResponse) else None
        if edit_response is None:
            edit_response = TaskEditTitleResponse.model_validate(result)
        await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=edit_response.message
            or ("Task updated." if edit_response.ok else "Task update failed."),
        )

    @app.action(FF_TIMEBOX_COMMIT_START_ACTION_ID)
    async def on_timebox_commit_start_action(ack, body, client, logger):
        """Confirm the planning day, on whichever backend owns the session.

        The two backends share this one control because they share the card.
        Which one answers is the same decision `/timebox` already made, read
        again here rather than remembered in the button.
        """
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if not (channel_id and message_ts and value):
            return
        if _timebox_backend() == "legacy":
            await timeboxing_commit.handle_start_action(
                value=value,
                prompt_channel_id=channel_id,
                prompt_ts=message_ts,
                actor_user_id=actor_user_id,
            )
            return
        await _handle_timebox_date_confirmation(
            runtime=runtime,
            client=client,
            logger=logger,
            value=value,
            prompt_channel_id=channel_id,
            prompt_ts=message_ts,
            actor_user_id=actor_user_id,
            interaction_id=str(action.get("action_ts") or message_ts),
            focus=focus,
        )

    # One registration per button: Slack refuses a message whose interactive
    # elements share an action_id, so the five day types each carry their own.
    @app.action(day_type_action_id(DayType.WORKING))
    @app.action(day_type_action_id(DayType.WEEKEND))
    @app.action(day_type_action_id(DayType.VACATION))
    @app.action(day_type_action_id(DayType.HOLIDAY))
    @app.action(day_type_action_id(DayType.SICK))
    async def on_timebox_day_type_action(ack, body, client, logger):
        """Lock the day with the type the user pressed rather than derived.

        This is the same confirmation as the Confirm button; the only thing
        that differs is a typed `day_type` in the metadata, which is why it
        reaches the same handler instead of a parallel one. The row exists only
        on the kernel card, so there is no legacy branch to choose between.
        """
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id")
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if not (channel_id and message_ts and value):
            return
        await _handle_timebox_date_confirmation(
            runtime=runtime,
            client=client,
            logger=logger,
            value=value,
            prompt_channel_id=channel_id,
            prompt_ts=message_ts,
            actor_user_id=actor_user_id,
            interaction_id=str(action.get("action_ts") or message_ts),
            focus=focus,
        )

    async def _on_timebox_artifact_action(ack, body, client, logger):
        """Apply one typed artifact decision from a review card."""
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message = body.get("message") or {}
        thread_ts = message.get("thread_ts") or message.get("ts") or ""
        actor_user_id = (body.get("user") or {}).get("id") or ""
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        if not (channel_id and thread_ts and actor_user_id and value):
            return
        await _handle_timebox_artifact_action(
            runtime=runtime,
            client=client,
            logger=logger,
            value=value,
            channel_id=channel_id,
            thread_ts=thread_ts,
            actor_user_id=actor_user_id,
            interaction_id=_card_interaction_id(
                action, str(action.get("action_id") or ""), thread_ts
            ),
        )

    app.action(FF_TIMEBOX_ARTIFACT_APPROVE_ACTION_ID)(_on_timebox_artifact_action)
    app.action(FF_TIMEBOX_ARTIFACT_BACK_ACTION_ID)(_on_timebox_artifact_action)
    app.action(FF_TIMEBOX_ARTIFACT_CANCEL_ACTION_ID)(_on_timebox_artifact_action)
    app.action(FF_TIMEBOX_ARTIFACT_RETRY_ACTION_ID)(_on_timebox_artifact_action)
    # An option press is the same envelope with a different decision in it, so
    # it takes the same handler. A renderer that parsed it itself would be a
    # second place a stale press could be judged, and the kernel is the only
    # one that knows what question is open.
    app.action(FF_TIMEBOX_BLOCKER_OPTION_ACTION_ID)(_on_timebox_artifact_action)

    @app.action(FF_TIMEBOX_COMMIT_DAY_SELECT_ACTION_ID)
    async def on_timebox_commit_day_select_action(ack, body, client, logger):
        await ack()
        channel_id = (body.get("channel") or {}).get("id") or ""
        message_ts = (body.get("message") or {}).get("ts") or ""
        action = (body.get("actions") or [{}])[0]
        selected = (
            (action.get("selected_option") or {}) if isinstance(action, dict) else {}
        ) or {}
        selected_date = selected.get("value") or ""
        meta_value = _timebox_start_button_value(
            (body.get("message") or {}).get("blocks")
        )

        if not (channel_id and message_ts and selected_date and meta_value):
            return
        if _timebox_backend() == "legacy":
            await timeboxing_commit.handle_day_select_action(
                prompt_channel_id=channel_id,
                prompt_ts=message_ts,
                selected_date=selected_date,
                existing_meta_value=meta_value,
            )
            return
        await _handle_timebox_date_reselect(
            client=client,
            logger=logger,
            value=meta_value,
            selected_date=selected_date,
            prompt_channel_id=channel_id,
            prompt_ts=message_ts,
        )

    @app.action(FF_TIMEBOX_CONFIRM_SUBMIT_ACTION_ID)
    async def on_timebox_confirm_submit_action(ack, body, client, logger):
        """Handle Stage 5 confirm-submit button clicks."""
        await ack()
        payload = TimeboxSubmitActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_submit.handle_confirm_action(payload=payload)

    @app.action(FF_TIMEBOX_CANCEL_SUBMIT_ACTION_ID)
    async def on_timebox_cancel_submit_action(ack, body, client, logger):
        """Handle Stage 5 cancel-submit button clicks."""
        await ack()
        payload = TimeboxSubmitActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_submit.handle_cancel_action(payload=payload)

    @app.action(FF_TIMEBOX_UNDO_SUBMIT_ACTION_ID)
    async def on_timebox_undo_submit_action(ack, body, client, logger):
        """Handle Stage 5 undo-submit button clicks."""
        await ack()
        payload = TimeboxSubmitActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_submit.handle_undo_action(payload=payload)

    @app.action(FF_TIMEBOX_STAGE_PROCEED_ACTION_ID)
    async def on_timebox_stage_proceed_action(ack, body, client, logger):
        """Handle deterministic stage proceed button clicks."""
        await ack()
        payload = TimeboxingStageActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_stage_actions.handle_action(
            payload=payload,
            action="proceed",
        )

    @app.action(FF_TIMEBOX_STAGE_BACK_ACTION_ID)
    async def on_timebox_stage_back_action(ack, body, client, logger):
        """Handle deterministic stage back button clicks."""
        await ack()
        payload = TimeboxingStageActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_stage_actions.handle_action(
            payload=payload,
            action="back",
        )

    @app.action(FF_TIMEBOX_STAGE_REDO_ACTION_ID)
    async def on_timebox_stage_redo_action(ack, body, client, logger):
        """Handle deterministic stage redo button clicks."""
        await ack()
        payload = TimeboxingStageActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_stage_actions.handle_action(
            payload=payload,
            action="redo",
        )

    @app.action(FF_TIMEBOX_STAGE_CANCEL_ACTION_ID)
    async def on_timebox_stage_cancel_action(ack, body, client, logger):
        """Handle deterministic stage cancel button clicks."""
        await ack()
        payload = TimeboxingStageActionPayload.from_action_body(body)
        if not payload:
            return
        await timeboxing_stage_actions.handle_action(
            payload=payload,
            action="cancel",
        )

    async def _handle_constraint_review_all_action(body, client):
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        metadata = decode_metadata(value)
        thread_ts = (
            metadata.get("thread_ts")
            or (body.get("message") or {}).get("thread_ts")
            or (body.get("message") or {}).get("ts")
            or ""
        )
        user_id = metadata.get("user_id") or (body.get("user") or {}).get("id") or ""
        channel_id = (
            body.get("channel", {}).get("id") or metadata.get("channel_id") or ""
        )
        trigger_id = body.get("trigger_id") or ""
        if not (thread_ts and user_id and channel_id and trigger_id):
            return

        store = await _get_constraint_store()
        if not store:
            return
        constraints = await store.list_constraints(
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        active_constraints = [
            constraint
            for constraint in constraints
            if constraint.status != ConstraintStatus.DECLINED
        ]
        view = build_constraint_review_list_view(
            active_constraints,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=user_id,
        )
        await client.views_open(trigger_id=trigger_id, view=view)

    @app.action(FF_CONSTRAINT_REVIEW_ALL_ACTION_ID)
    async def on_constraint_review_all_action(ack, body, client, logger):
        await ack()
        await _handle_constraint_review_all_action(body, client)

    @app.action(LEGACY_CONSTRAINT_REVIEW_ALL_ACTION_ID)
    async def on_constraint_review_all_action_legacy(ack, body, client, logger):
        await ack()
        await _handle_constraint_review_all_action(body, client)

    @app.action(CONSTRAINT_ROW_REVIEW_ACTION_ID)
    async def on_constraint_review_action(ack, body, client, logger):
        await ack()
        action = (body.get("actions") or [{}])[0]
        value = action.get("value") or ""
        metadata = decode_metadata(value)
        constraint_id_raw = metadata.get("constraint_id") or ""
        thread_ts = metadata.get("thread_ts") or ""
        user_id = metadata.get("user_id") or ""
        channel_id = body.get("channel", {}).get("id") or ""
        if not (constraint_id_raw and user_id and channel_id):
            return
        try:
            constraint_id = int(constraint_id_raw)
        except ValueError:
            return

        store = await _get_constraint_store()
        if not store:
            return
        constraint = await store.get_constraint(
            user_id=user_id, constraint_id=constraint_id
        )
        if not constraint:
            return
        if thread_ts and constraint.thread_ts and constraint.thread_ts != thread_ts:
            return
        view = build_constraint_review_view(
            constraint,
            channel_id=channel_id,
            thread_ts=thread_ts or (constraint.thread_ts or ""),
            user_id=user_id,
        )
        await client.views_open(trigger_id=body["trigger_id"], view=view)

    @app.view(CONSTRAINT_REVIEW_VIEW_CALLBACK_ID)
    async def on_constraint_review_submit(ack, body, client, logger):
        await ack()
        store = await _get_constraint_store()
        if not store:
            return
        state = body.get("view", {}).get("state", {}).get("values", {})
        status, description = parse_constraint_review_submission(state)
        metadata = body.get("view", {}).get("private_metadata") or ""
        info = decode_metadata(metadata)
        constraint_id_raw = info.get("constraint_id") or ""
        user_id = info.get("user_id") or body.get("user", {}).get("id", "") or ""
        channel_id = info.get("channel_id") or ""
        thread_ts = info.get("thread_ts") or ""
        if not (constraint_id_raw and user_id):
            return
        try:
            constraint_id = int(constraint_id_raw)
        except ValueError:
            return
        await store.update_constraint(
            user_id=user_id,
            constraint_id=constraint_id,
            status=status,
            description=description,
        )
        if channel_id and thread_ts:
            await client.chat_postMessage(
                channel=channel_id,
                thread_ts=thread_ts,
                text="Saved your constraint update.",
            )
            await _maybe_update_timeboxing_thread_constraints(
                client=client,
                focus=focus,
                thread_key=f"{channel_id}:{thread_ts}",
                user_id=user_id,
                store=store,
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
        root_payload.update(_persona_payload(persona))
        root = await client.chat_postMessage(**root_payload)
        thread_root_ts = root["ts"]

        runtime_key = FocusManager.thread_key(
            channel_id, thread_ts=thread_root_ts, ts=thread_root_ts
        )
        try:
            focus.set_focus(
                runtime_key, "revisor_agent", by_user=user_id, note="apphome"
            )
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
        processing_payload.update(_persona_payload(persona))
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
        update = {
            "channel": channel_id,
            "ts": processing["ts"],
            "text": payload.get("text", "") or "",
        }
        if payload.get("blocks"):
            update["blocks"] = payload["blocks"]
        await client.chat_update(**update)

        # Refresh App Home view (focus updated)
        view = _build_app_home_view(
            user_id=user_id, focus_agent=focus.get_user_focus(user_id)
        )
        await client.views_publish(user_id=user_id, view=view)

    # --- App mention in public channels ---
    @app.event("app_mention")
    async def on_app_mention(body, say, context, client, logger):
        # Before anything else. Everything below is invisible work -- a
        # registry ensure, an invite, a registration guard observed timing out
        # at 3s -- and until one of them finishes the user cannot tell a
        # working system from a dropped message.
        acked = await instant_ack(client, body.get("event", {}))
        await _ensure_workspace_registry(client)
        event = body.get("event", {})
        user_id = event.get("user") or ""
        channel_id = event.get("channel") or ""
        channel_type = event.get("channel_type") or "channel"
        if user_id and channel_id:
            await _ensure_user_invited(client, user_id=user_id)
            await _maybe_register_user_with_guard(
                user_id=user_id,
                channel_id=channel_id,
                channel_type=channel_type,
                origin="app_mention",
            )
        await _route_slack_event_with_guard(
            acked=acked,
            event=event,
            say=say,
            bot_user_id=context.get("bot_user_id"),
            client=client,
            origin="app_mention",
        )

    @app.event("reaction_added")
    async def on_reaction_added(body, event, client, logger):
        try:
            item = event.get("item") or {}
            if (item.get("type") or "").lower() != "message":
                return
            channel_id = item.get("channel") or ""
            message_ts = item.get("ts") or ""
            user_id = event.get("user") or ""
            reaction = event.get("reaction") or ""
            if not (channel_id and message_ts and user_id and reaction):
                return
            await planning.maybe_handle_reaction(
                user_id=user_id,
                channel_id=channel_id,
                message_ts=message_ts,
                reaction=reaction,
            )
        except Exception:
            logger.exception("Failed to handle reaction_added event")

    # --- Direct messages (IM) ---
    @app.event("message")
    async def on_message_events(body, say, context, client, logger):
        await _ensure_workspace_registry(client)
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
            await _ensure_user_invited(client, user_id=user_id)
            await _maybe_register_user_with_guard(
                user_id=user_id,
                channel_id=channel_id,
                channel_type=event.get("channel_type") or "channel",
                origin="message",
            )
        thread_ts = event.get("thread_ts")
        key = FocusManager.thread_key(channel_id, thread_ts, ts)
        if event.get("channel_type") != "im":
            _auto_recover_timeboxing_focus_for_thread(
                focus=focus, event=event, user_id=user_id
            )
            # Only handle non-DMs when the thread has explicit focus (e.g., timeboxing threads).
            bot_id = context.get("bot_user_id")
            if bot_id and f"<@{bot_id}>" in (event.get("text") or ""):
                return  # app_mention handler covers this
            general_id = _general_channel_id()
            channel_agent = _agent_for_channel(channel_id)
            allow_unfocused = channel_agent is not None
            if (
                channel_id != general_id
                and not allow_unfocused
                and not focus.get_focus(key)
            ):
                logger.debug(
                    "Ignoring message in channel=%s (not general, no channel agent, no focus)",
                    channel_id,
                )
                return
        await _route_slack_event_with_guard(
            event=event,
            say=say,
            bot_user_id=context.get("bot_user_id"),
            client=client,
            origin="message",
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

    return {
        "text": agent_reply_text(chat_message if chat_message is not None else result)
    }


# TODO: this seems like something that shouldnt exist
def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url
