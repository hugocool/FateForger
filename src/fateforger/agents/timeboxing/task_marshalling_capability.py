"""Composable task-marshalling capability for timeboxing sessions."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from autogen_agentchat.messages import TextMessage
from autogen_core import AgentId, CancellationToken
from pydantic import BaseModel

from fateforger.agents.tasks.messages import (
    PendingTaskSnapshot,
    PendingTaskSnapshotRequest,
)
from fateforger.debug.diag import with_timeout

from .contracts import TaskCandidate
from .pydantic_parsing import parse_model_list, parse_model_optional

SendMessageFn = Callable[..., Awaitable[Any]]
AppendBackgroundFn = Callable[[Any, str], None]


class SessionTaskState(Protocol):
    """Minimal session state contract required by task-marshalling capability."""

    user_id: str
    channel_id: str
    thread_ts: str
    session_key: str | None
    input_facts: dict[str, Any]
    prefetched_pending_tasks: list[TaskCandidate]
    pending_tasks_prefetch: bool


class TaskAssistRequest(BaseModel):
    """Typed request envelope for assist-turn task delegation."""

    note: str | None = None
    user_message: str

    def to_text_message(self) -> str:
        """Render one deterministic text payload for tasks_agent delegation."""
        note = (self.note or "").strip()
        message = self.user_message.strip()
        if not note:
            return message
        return f"{message}\n\nAssist context: {note}"


class TaskMarshallingCapability:
    """Encapsulates pending-task prefetch and assist-turn delegation."""

    def __init__(
        self,
        *,
        send_message: SendMessageFn,
        timeout_s: float,
        source_resolver: Callable[[], str],
    ) -> None:
        self._send_message = send_message
        self._timeout_s = timeout_s
        self._source_resolver = source_resolver

    @staticmethod
    def tasks_agent_recipient(session: SessionTaskState) -> AgentId:
        """Build the tasks-agent recipient scoped to this user thread."""
        key = session.session_key or f"{session.channel_id}:{session.thread_ts}"
        return AgentId("tasks_agent", key=key)

    @staticmethod
    def merge_prefetched_tasks(
        *,
        input_facts: dict[str, Any],
        prefetched: list[TaskCandidate],
    ) -> dict[str, Any]:
        """Inject prefetched tasks only when no explicit task list exists."""
        merged = dict(input_facts or {})
        match bool(parse_model_list(TaskCandidate, merged.get("tasks"))), bool(prefetched):
            case (True, _) | (_, False):
                return merged
            case (False, True):
                merged["tasks"] = [task.model_dump(mode="json") for task in prefetched]
                return merged
        return merged

    async def request_pending_tasks(
        self,
        *,
        session: SessionTaskState,
        query: str | None = None,
        limit: int = 12,
    ) -> list[TaskCandidate]:
        """Fetch typed pending tasks from tasks_agent."""
        request = PendingTaskSnapshotRequest(
            user_id=session.user_id,
            limit=limit,
            query=query,
        )
        try:
            raw = await with_timeout(
                "timeboxing:tasks:pending_snapshot",
                self._send_message(
                    request,
                    recipient=self.tasks_agent_recipient(session),
                    cancellation_token=CancellationToken(),
                ),
                timeout_s=self._timeout_s,
                dump_on_timeout=False,
                dump_threads_on_timeout=False,
            )
        except Exception:
            return []
        snapshot = (
            raw if isinstance(raw, PendingTaskSnapshot) else parse_model_optional(PendingTaskSnapshot, raw)
        )
        match snapshot:
            case None:
                return []
            case _:
                return [
                    TaskCandidate(title=item.title)
                    for item in snapshot.items
                    if (item.title or "").strip()
                ]

    @staticmethod
    def apply_prefetched_tasks(
        *,
        session: SessionTaskState,
        tasks: list[TaskCandidate],
    ) -> None:
        """Write prefetched tasks into session cache and capture-input facts."""
        session.prefetched_pending_tasks = tasks
        session.input_facts = TaskMarshallingCapability.merge_prefetched_tasks(
            input_facts=dict(session.input_facts or {}),
            prefetched=tasks,
        )

    def queue_prefetch(
        self,
        *,
        session: SessionTaskState,
        reason: str,
        append_background_update: AppendBackgroundFn,
    ) -> None:
        """Start non-blocking prefetch from task-marshalling."""
        if session.pending_tasks_prefetch:
            return

        async def _background() -> None:
            session.pending_tasks_prefetch = True
            try:
                tasks = await self.request_pending_tasks(session=session)
                match len(tasks):
                    case 0:
                        return
                    case n:
                        self.apply_prefetched_tasks(session=session, tasks=tasks)
                        append_background_update(
                            session,
                            f"Loaded {n} pending task candidate(s) from task-marshalling ({reason}).",
                        )
            finally:
                session.pending_tasks_prefetch = False

        asyncio.create_task(_background())

    async def assist_response(
        self,
        *,
        session: SessionTaskState,
        user_message: str,
        note: str | None,
    ) -> str | None:
        """Handle assist turn via typed delegation to tasks_agent."""
        request = TaskAssistRequest(note=note, user_message=user_message)
        if not request.user_message.strip():
            return None
        reply = await with_timeout(
            "timeboxing:assist:tasks_agent",
            self._send_message(
                TextMessage(
                    content=request.to_text_message(),
                    source=self._source_resolver(),
                ),
                recipient=self.tasks_agent_recipient(session),
                cancellation_token=CancellationToken(),
            ),
            timeout_s=self._timeout_s,
            dump_on_timeout=False,
            dump_threads_on_timeout=False,
        )
        return reply.content if isinstance(reply, TextMessage) else str(
            getattr(reply, "content", None) or reply or ""
        )


__all__ = [
    "SessionTaskState",
    "TaskAssistRequest",
    "TaskMarshallingCapability",
]
