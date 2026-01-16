from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


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
        return self.channels_by_name.get(name.lstrip("#"))

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


DEFAULT_PERSONAS: dict[str, SlackPersona] = {
    "receptionist_agent": SlackPersona(username="FateForger", icon_emoji=":crystal_ball:"),
    "timeboxing_agent": SlackPersona(username="Timeboxer", icon_emoji=":spiral_calendar_pad:"),
    "planner_agent": SlackPersona(username="Planner", icon_emoji=":gear:"),
    "revisor_agent": SlackPersona(username="Revisor", icon_emoji=":mag:"),
    "tasks_agent": SlackPersona(username="Task Marshal", icon_emoji=":clipboard:"),
}


__all__ = [
    "SlackPersona",
    "WorkspaceDirectory",
    "WorkspaceRegistry",
    "DEFAULT_PERSONAS",
]
