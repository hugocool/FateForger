from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from fateforger.core.config import settings

CHANNEL_NAME_ALIASES: dict[str, str] = {
    "timeboxing": "plan-sessions",
    "tasks": "task-marshalling",
    "strategy": "review",
    "ops": "scheduling",
}


@dataclass(frozen=True)
class SlackPersona:
    username: str
    icon_emoji: str | None = None
    icon_url: str | None = None


@dataclass(frozen=True)
class WorkspaceDirectory:
    team_id: str
    channels_by_name: Dict[str, str]
    channels_by_agent: Dict[str, str]
    personas_by_agent: Dict[str, SlackPersona]

    def channel_for_agent(self, agent_type: str) -> str | None:
        return self.channels_by_agent.get(agent_type)

    def channel_for_name(self, name: str) -> str | None:
        cleaned = name.lstrip("#")
        canonical = CHANNEL_NAME_ALIASES.get(cleaned, cleaned)
        return self.channels_by_name.get(canonical)

    def persona_for_agent(self, agent_type: str) -> SlackPersona | None:
        return self.personas_by_agent.get(agent_type)


class WorkspaceRegistry:
    _global: WorkspaceDirectory | None = None

    @classmethod
    def set_global(cls, directory: WorkspaceDirectory) -> None:
        cls._global = directory

    @classmethod
    def get_global(cls) -> WorkspaceDirectory | None:
        return cls._global


def _icon_url(filename: str) -> str | None:
    base = (getattr(settings, "slack_agent_icon_base_url", "") or "").strip()
    if not base:
        return None
    return base.rstrip("/") + "/" + filename.lstrip("/")


DEFAULT_PERSONAS: dict[str, SlackPersona] = {
    "receptionist_agent": SlackPersona(username="FateForger", icon_emoji=":crystal_ball:"),
    "timeboxing_agent": SlackPersona(
        username="The Schedular",
        icon_emoji=None,
        icon_url=_icon_url("Schedular.png"),
    ),
    "planner_agent": SlackPersona(
        username="The Schedular",
        icon_emoji=None,
        icon_url=_icon_url("Schedular.png"),
    ),
    "revisor_agent": SlackPersona(
        username="Reviewer",
        icon_emoji=None,
        icon_url=_icon_url("Revisor.png"),
    ),
    "tasks_agent": SlackPersona(
        username="TaskMarshal",
        icon_emoji=None,
        icon_url=_icon_url("TaskMarshal.png"),
    ),
    "admonisher_agent": SlackPersona(
        username="Admonisher",
        icon_emoji=None,
        icon_url=_icon_url("Admonisher.png"),
    ),
}


__all__ = [
    "SlackPersona",
    "WorkspaceDirectory",
    "WorkspaceRegistry",
    "DEFAULT_PERSONAS",
]
