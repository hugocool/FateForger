"""The one surface a timeboxing session gets: a root header with the working
card threaded under it. Every door -- a message, a slash command, the planning
event's own start -- opens a session through this function."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .focus import FocusManager
from .workspace import DEFAULT_PERSONAS, SlackPersona, WorkspaceRegistry

logger = logging.getLogger(__name__)

_TIMEBOXING_STATE_EMOJI = {
    "pending": ":large_yellow_circle:",
    "in_progress": ":large_blue_circle:",
    "done": ":white_check_mark:",
    "canceled": ":no_entry_sign:",
    #: The session ran out of time with nothing planned (#164).
    "missed": ":alarm_clock:",
}

#: States whose emoji alone does not say what happened to the session, so the
#: header spells it out. A live session needs no word -- the card under it says
#: where it is -- but a dead one leaves only this line behind.
_TIMEBOXING_STATE_WORD = {
    "canceled": "canceled",
    "missed": "missed",
}


def persona_for_agent(agent_type: str) -> SlackPersona | None:
    directory = WorkspaceRegistry.get_global()
    if directory:
        persona = directory.persona_for_agent(agent_type)
        if persona:
            return persona
    return DEFAULT_PERSONAS.get(agent_type)


def persona_payload(persona: SlackPersona | None) -> dict:
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


def timeboxing_thread_root_text(
    *, title: str, request_excerpt: str | None, state: str
) -> str:
    emoji = _TIMEBOXING_STATE_EMOJI.get(state, _TIMEBOXING_STATE_EMOJI["pending"])
    word = _TIMEBOXING_STATE_WORD.get(state)
    return f"{emoji} {title} — {word}" if word else f"{emoji} {title}"


async def invite_user_to_channels_best_effort(
    client, *, user_id: str, channel_ids: list[str]
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


@dataclass(frozen=True)
class SessionSurface:
    channel_id: str
    root_ts: str
    session_key: str


async def open_session_surface(
    client,
    focus: FocusManager,
    *,
    user_id: str,
    target_channel: str,
    origin_key: str | None = None,
    existing_root: dict | None = None,
) -> SessionSurface:
    """Build the one surface a timeboxing session gets: root + threaded card.

    Every session, whatever door it came through, is a dedicated root header
    with the working card as its first thread reply. The root is only ever a
    header, so relabels can never erase a control again -- which is the failure
    that ate the 2026-08-31 22:57 session's card.

    `existing_root` is the origin "thinking..." ack when the session lives in
    the channel the user is already in: it is repurposed into the root rather
    than left beside a second one.
    """

    persona = persona_for_agent("timeboxing_agent")
    try:
        await invite_user_to_channels_best_effort(
            client, user_id=user_id, channel_ids=[target_channel]
        )
    except Exception:
        logger.debug("invite failed for %s", user_id, exc_info=True)

    root_text = timeboxing_thread_root_text(
        title="Timeboxing session", request_excerpt=None, state="pending"
    )
    if existing_root is not None:
        root_ts = existing_root["ts"]
        await client.chat_update(channel=target_channel, ts=root_ts, text=root_text)
    else:
        root_payload = {"channel": target_channel, "text": root_text}
        root_payload.update(persona_payload(persona))
        root = await client.chat_postMessage(**root_payload)
        root_ts = root["ts"]

    session_key = f"{target_channel}:{root_ts}"
    focus.set_thread_label(
        session_key,
        title="Timeboxing session",
        request_excerpt=None,
        state="pending",
        by_user=user_id,
    )
    if origin_key is not None:
        redirect = focus.set_redirect(
            origin_key,
            target_channel=target_channel,
            target_thread_ts=root_ts,
            agent_type="timeboxing_agent",
            by_user=user_id,
            note="session-surface",
        )
        focus.set_focus(
            redirect.target_key,
            "timeboxing_agent",
            by_user=user_id,
            note="session-surface",
        )
        focus.set_focus(
            origin_key, "timeboxing_agent", by_user=user_id, note="session-surface"
        )
    else:
        focus.set_focus(
            session_key, "timeboxing_agent", by_user=user_id, note="session-surface"
        )
    focus.set_user_focus(user_id, "timeboxing_agent")
    return SessionSurface(
        channel_id=target_channel, root_ts=root_ts, session_key=session_key
    )
