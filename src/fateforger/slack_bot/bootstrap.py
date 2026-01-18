from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from fateforger.slack_bot.workspace import DEFAULT_PERSONAS, SlackPersona, WorkspaceDirectory, WorkspaceRegistry
from fateforger.slack_bot.workspace_store import SlackWorkspaceStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChannelSpec:
    name: str  # without leading '#'
    agent_type: str
    is_private: bool = False
    purpose: str | None = None


DEFAULT_CHANNEL_SPECS: list[ChannelSpec] = [
    ChannelSpec(name="plan-sessions", agent_type="timeboxing_agent", purpose="Daily schedules & timeboxes"),
    ChannelSpec(name="review", agent_type="revisor_agent", purpose="Reviews, retros, weekly planning"),
    ChannelSpec(name="task-marshalling", agent_type="tasks_agent", purpose="Task triage, planning, execution"),
    ChannelSpec(name="scheduling", agent_type="planner_agent", purpose="Calendar + operational planning"),
    ChannelSpec(name="admonishments", agent_type="admonisher_agent", purpose="Automated reminders & nudges (log)"),
]

_LEGACY_CHANNEL_ALIASES: dict[str, list[str]] = {
    "plan-sessions": ["timeboxing"],
    "task-marshalling": ["tasks"],
    "review": ["strategy"],
    "scheduling": ["ops"],
}


async def ensure_workspace_ready(
    client: AsyncWebClient,
    *,
    store: SlackWorkspaceStore | None = None,
    required_channels: Iterable[ChannelSpec] = DEFAULT_CHANNEL_SPECS,
    include_general: bool = True,
) -> WorkspaceDirectory | None:
    """
    Ensure the Slack workspace has the required channels and the bot has joined them.

    - Creates missing channels (public by default)
    - Joins channels (public)
    - Persists channel IDs to the DB (optional)
    - Publishes a "System Ready" message in each channel using persona overrides
    """

    try:
        auth = await client.auth_test()
    except Exception:
        logger.exception("Slack auth_test failed; skipping workspace bootstrap")
        return None

    team_id = auth.get("team_id") or ""
    if not team_id:
        logger.warning("Slack auth_test returned no team_id; skipping workspace bootstrap")
        return None

    try:
        existing = await _list_channels_by_name(client)
    except SlackApiError as e:
        data = getattr(e, "response", None)
        needed = None
        if data is not None:
            try:
                needed = data.get("needed")
            except Exception:
                needed = None
        logger.warning(
            "Workspace bootstrap skipped (Slack API missing scopes). needed=%s",
            needed,
        )
        return None
    channels_by_name: Dict[str, str] = {}
    if include_general and "general" in existing:
        channels_by_name["general"] = existing["general"]

    created_any = False
    for spec in required_channels:
        name = spec.name.lstrip("#")
        channel_id = existing.get(name)
        if not channel_id:
            for legacy_name in _LEGACY_CHANNEL_ALIASES.get(name, []):
                channel_id = existing.get(legacy_name)
                if channel_id:
                    # Try to rename the existing legacy channel to the canonical name.
                    await _rename_channel_best_effort(
                        client, channel_id=channel_id, new_name=name
                    )
                    break
        if not channel_id:
            channel_id = await _create_channel(client, name=name, is_private=spec.is_private)
            if channel_id:
                created_any = True
                existing[name] = channel_id
        if channel_id:
            channels_by_name[name] = channel_id
        if channel_id:
            await _join_channel(client, channel_id)

    # Always attempt to join #general if visible.
    if include_general and channels_by_name.get("general"):
        await _join_channel(client, channels_by_name["general"])

    channels_by_agent: Dict[str, str] = {}
    for spec in required_channels:
        channel_id = channels_by_name.get(spec.name.lstrip("#"))
        if channel_id:
            channels_by_agent[spec.agent_type] = channel_id

    personas_by_agent: Dict[str, SlackPersona] = dict(DEFAULT_PERSONAS)

    directory = WorkspaceDirectory(
        team_id=team_id,
        channels_by_name=channels_by_name,
        channels_by_agent=channels_by_agent,
        personas_by_agent=personas_by_agent,
    )
    WorkspaceRegistry.set_global(directory)

    if store:
        for name, channel_id in channels_by_name.items():
            agent_type = None
            for spec in required_channels:
                if spec.name.lstrip("#") == name:
                    agent_type = spec.agent_type
                    break
            await store.upsert_channel(
                team_id=team_id,
                channel_name=name,
                channel_id=channel_id,
                agent_type=agent_type,
            )

    # Post ready messages (best-effort; do not spam if nothing changed).
    if created_any:
        for spec in required_channels:
            channel_id = channels_by_name.get(spec.name.lstrip("#"))
            if not channel_id:
                continue
            persona = personas_by_agent.get(spec.agent_type)
            await _post_ready(client, channel_id=channel_id, spec=spec, persona=persona)

    logger.info(
        "Workspace bootstrap complete (team_id=%s, channels=%s)",
        team_id,
        {k: v for k, v in channels_by_name.items() if k in {s.name for s in required_channels} or k == "general"},
    )
    return directory


async def _list_channels_by_name(client: AsyncWebClient) -> Dict[str, str]:
    channels: Dict[str, str] = {}
    cursor: str | None = None
    while True:
        resp = await client.conversations_list(
            limit=1000,
            cursor=cursor,
            types="public_channel,private_channel",
            exclude_archived=True,
        )
        for ch in resp.get("channels", []):
            name = (ch.get("name") or "").strip()
            cid = (ch.get("id") or "").strip()
            if name and cid:
                channels[name] = cid
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return channels


async def _create_channel(client: AsyncWebClient, *, name: str, is_private: bool) -> str | None:
    try:
        resp = await client.conversations_create(name=name, is_private=is_private)
        channel = resp.get("channel") or {}
        return channel.get("id")
    except SlackApiError as e:
        err = (e.response or {}).get("error") if hasattr(e, "response") else None
        logger.warning("Failed to create channel #%s (error=%s)", name, err)
        return None
    except Exception:
        logger.exception("Failed to create channel #%s", name)
        return None


async def _join_channel(client: AsyncWebClient, channel_id: str) -> None:
    try:
        await client.conversations_join(channel=channel_id)
    except SlackApiError as e:
        err = (e.response or {}).get("error") if hasattr(e, "response") else None
        if err in {"method_not_supported_for_channel_type", "already_in_channel"}:
            return
        logger.debug("Failed to join channel %s (error=%s)", channel_id, err)
    except Exception:
        logger.debug("Failed to join channel %s", channel_id, exc_info=True)


async def _rename_channel_best_effort(
    client: AsyncWebClient, *, channel_id: str, new_name: str
) -> None:
    try:
        await client.conversations_rename(channel=channel_id, name=new_name)
    except SlackApiError as e:
        err = (e.response or {}).get("error") if hasattr(e, "response") else None
        # Renames are frequently restricted; treat this as best-effort.
        logger.debug(
            "Failed to rename channel %s -> #%s (error=%s)", channel_id, new_name, err
        )
    except Exception:
        logger.debug(
            "Failed to rename channel %s -> #%s", channel_id, new_name, exc_info=True
        )


async def _post_ready(
    client: AsyncWebClient,
    *,
    channel_id: str,
    spec: ChannelSpec,
    persona: SlackPersona | None,
) -> None:
    text = f":white_check_mark: System ready. ({spec.purpose or spec.agent_type})"
    payload = {"channel": channel_id, "text": text}
    if persona and persona.username:
        payload["username"] = persona.username
    if persona and persona.icon_emoji:
        payload["icon_emoji"] = persona.icon_emoji
    if persona and persona.icon_url:
        payload["icon_url"] = persona.icon_url
    try:
        await client.chat_postMessage(**payload)
    except Exception:
        logger.debug("Failed to post ready message in %s", channel_id, exc_info=True)


__all__ = ["ChannelSpec", "DEFAULT_CHANNEL_SPECS", "ensure_workspace_ready"]
