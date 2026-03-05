"""Tasks agent package."""

from __future__ import annotations

from typing import Any

__all__ = ["TasksAgent"]


def __getattr__(name: str) -> Any:
    if name == "TasksAgent":
        from .agent import TasksAgent

        return TasksAgent
    raise AttributeError(name)
