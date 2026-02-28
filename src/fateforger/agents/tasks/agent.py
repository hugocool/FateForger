"""Tasks specialist agent (Task Marshal)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler
from autogen_core.tools import FunctionTool
from pydantic import TypeAdapter

from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.tools.ticktick_mcp import get_ticktick_mcp_url

from .list_tools import TickTickListManager
from .messages import (
    GuidedRefinementPhase,
    GuidedRefinementRecap,
    GuidedRefinementRecapRequest,
    GuidedRefinementRecapResponse,
    GuidedRefinementTurn,
    PendingTaskItem,
    PendingTaskSnapshot,
    PendingTaskSnapshotRequest,
)
from .notion_sprint_tools import NotionSprintManager

logger = logging.getLogger(__name__)

_GUIDED_SESSION_START_COMMANDS = {
    "start guided task refinement session",
    "start task refinement session",
    "start scrum refinement session",
    "/task-refine",
}
_GUIDED_SESSION_CANCEL_COMMANDS = {
    "cancel task refinement session",
    "stop task refinement session",
    "exit task refinement session",
}
_PHASE_ORDER = (
    GuidedRefinementPhase.SCOPE,
    GuidedRefinementPhase.SCAN,
    GuidedRefinementPhase.REFINE,
    GuidedRefinementPhase.CLOSE,
)
_PHASE_LABELS = {
    GuidedRefinementPhase.SCOPE: "Scope",
    GuidedRefinementPhase.SCAN: "Scan",
    GuidedRefinementPhase.REFINE: "Refine",
    GuidedRefinementPhase.CLOSE: "Close",
}


@dataclass
class GuidedRefinementSessionState:
    """In-memory v0 state for one guided refinement session."""

    phase: GuidedRefinementPhase = GuidedRefinementPhase.SCOPE
    phase_summaries: dict[GuidedRefinementPhase, list[str]] = field(
        default_factory=dict
    )
    turns: int = 0
    user_id: str = ""

TASKS_PROMPT = """
You are the FateForger Tasks Agent ("Task Marshal").
Your job is to help with task triage and execution:
- capture tasks
- prioritize (now/next/later)
- define next actions
- break down work into small steps

For TickTick list management requests, always call the `manage_ticktick_lists` tool.
Use model="project" by default. Use model="subtask" only when the user provides
explicit parent-task context.

For Notion sprint operations:
- use `find_sprint_items` to discover sprint tickets in a sprint data source
- use `link_sprint_subtasks` to link parent/child sprint records
- use `patch_sprint_page_content` for preview/apply page-content edits
- strict tool schemas are enabled: pass every tool argument explicitly.
  Use `null` for optional values you are not using.

Be direct and concrete. If needed, ask 1-2 clarifying questions then propose a plan.
Never invent IDs. If the tool reports ambiguity, ask a focused follow-up question.
""".strip()

GUIDED_REFINEMENT_PROMPT = """
You run a quick, gated scrum-master refinement session (v0, 15 min style).

Rules:
- Keep responses concise and stepwise.
- Do not auto-advance unless gate is met.
- Ask for missing fields directly when gate is not met.
- This flow is refinement, not scheduling.
- Distinguish work state strictly:
  - hypothesis (what must be true)
  - research (what we need to learn)
  - engineering (what to build/test)
  - blocked (cannot progress now)

Output contract:
- Return only a GuidedRefinementTurn structured response.
- Respect the current phase from the provided context.
- `gate_met=true` only when phase requirements are satisfied.
- On close phase, provide recap and set `session_complete=true`.

Phase requirements:
1) scope:
   - selected project areas and board/list scope.
2) scan:
   - at least 3 active items reviewed (or explicit none).
3) refine:
   - refined items include state, acceptance criteria, binary DoD, size, dependencies, next action.
4) close:
   - concise recap with stuck/postponed signals and one intention line.
""".strip()


class TasksAgent(RoutedAgent):
    def __init__(self, name: str) -> None:
        super().__init__(description=name)
        server_url = get_ticktick_mcp_url()
        self._list_manager = TickTickListManager(
            server_url=server_url,
            timeout=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)),
        )
        self._notion_sprint_manager = NotionSprintManager(
            timeout=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10))
        )
        tools = [
            FunctionTool(
                self._list_manager.manage_ticktick_lists,
                name="manage_ticktick_lists",
                description=(
                    "Manage TickTick lists and items. "
                    "Supports project-mode and explicit subtask-mode operations."
                ),
                strict=True,
            ),
            FunctionTool(
                self._notion_sprint_manager.find_sprint_items,
                name="find_sprint_items",
                description=(
                    "Find sprint records in a Notion sprint data source with filters."
                ),
                strict=True,
            ),
            FunctionTool(
                self._notion_sprint_manager.link_sprint_subtasks,
                name="link_sprint_subtasks",
                description=(
                    "Link or unlink parent-child relations between Notion sprint pages."
                ),
                strict=True,
            ),
            FunctionTool(
                self._notion_sprint_manager.patch_sprint_page_content,
                name="patch_sprint_page_content",
                description=(
                    "Preview or apply patch-style edits to Notion sprint page content."
                ),
                strict=True,
            ),
        ]
        self._assistant = AssistantAgent(
            name=f"{name}_assistant",
            system_message=TASKS_PROMPT,
            model_client=build_autogen_chat_client("tasks_agent"),
            tools=tools,
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )
        self._guided_assistant = AssistantAgent(
            name=f"{name}_guided_refinement_assistant",
            system_message=GUIDED_REFINEMENT_PROMPT,
            model_client=build_autogen_chat_client("tasks_agent"),
            output_content_type=GuidedRefinementTurn,
            reflect_on_tool_use=False,
            max_tool_iterations=1,
        )
        self._guided_session: GuidedRefinementSessionState | None = None
        self._latest_guided_recap_by_user: dict[str, GuidedRefinementRecap] = {}

    @message_handler
    async def handle_pending_snapshot(
        self,
        message: PendingTaskSnapshotRequest,
        ctx: MessageContext,
    ) -> PendingTaskSnapshot:
        """Return a bounded snapshot of pending tasks for planning assistance.

        Delegates to :meth:`TickTickListManager.list_pending_tasks` and maps
        the raw vendor objects to typed :class:`PendingTaskItem` records.
        """
        tasks = await self._list_manager.list_pending_tasks(
            limit=message.limit,
            per_project_limit=message.per_project_limit,
        )
        items = [
            PendingTaskItem(
                id=t.id,
                title=t.title,
                project_id=getattr(t, "project_id", None),
                project_name=getattr(t, "project_name", None),
            )
            for t in tasks
        ]
        return PendingTaskSnapshot(
            items=items,
            summary=f"Found {len(items)} pending task(s).",
        )

    @message_handler
    async def handle_guided_recap_request(
        self, message: GuidedRefinementRecapRequest, ctx: MessageContext
    ) -> GuidedRefinementRecapResponse:
        _ = ctx
        recap = self._latest_guided_recap_by_user.get(message.user_id)
        if recap is None:
            return GuidedRefinementRecapResponse(found=False, recap=None)
        return GuidedRefinementRecapResponse(found=True, recap=recap)

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        content = (message.content or "").strip()
        normalized = content.lower()
        if normalized in _GUIDED_SESSION_CANCEL_COMMANDS and self._guided_session:
            self._guided_session = None
            return TextMessage(
                content="Guided task refinement session canceled.",
                source="tasks_agent",
            )

        if normalized in _GUIDED_SESSION_START_COMMANDS:
            self._guided_session = GuidedRefinementSessionState(user_id=message.source)
            return self._render_phase_intro(self._guided_session)

        if self._guided_session is not None:
            return await self._handle_guided_session_turn(message=message, ctx=ctx)

        response = await with_timeout(
            "tasks:on_messages",
            self._assistant.on_messages([message], ctx.cancellation_token),
            timeout_s=float(getattr(settings, "agent_on_messages_timeout_seconds", 20)),
        )
        chat_message = getattr(response, "chat_message", None)
        if isinstance(chat_message, TextMessage):
            return chat_message
        fallback = getattr(chat_message, "content", None) if chat_message else None
        return TextMessage(
            content=str(fallback or "(no response)"),
            source="tasks_agent",
        )

    async def _handle_guided_session_turn(
        self, *, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        assert self._guided_session is not None
        session = self._guided_session
        phase = session.phase
        context = self._build_guided_context(session=session, user_message=message.content)
        try:
            response = await with_timeout(
                "tasks:guided_refinement",
                self._guided_assistant.on_messages(
                    [TextMessage(content=context, source=message.source)],
                    ctx.cancellation_token,
                ),
                timeout_s=float(getattr(settings, "agent_on_messages_timeout_seconds", 20)),
            )
            turn = self._extract_guided_turn(response)
        except Exception as exc:
            logger.exception("Guided task refinement turn failed")
            return TextMessage(
                content=(
                    f"*Guided Refinement — Phase {self._phase_position(phase)}/{len(_PHASE_ORDER)} "
                    f"({self._phase_label(phase)})*\n"
                    "Gate: ❌ not met\n"
                    f"Error: {type(exc).__name__}. Please rephrase briefly and try again."
                ),
                source="tasks_agent",
            )

        session.turns += 1
        if turn.phase_summary:
            existing = list(session.phase_summaries.get(phase, []))
            existing.extend(turn.phase_summary)
            session.phase_summaries[phase] = existing[-8:]

        if turn.gate_met:
            if phase == GuidedRefinementPhase.CLOSE or turn.session_complete:
                if turn.recap is not None and session.user_id:
                    self._latest_guided_recap_by_user[session.user_id] = turn.recap
                self._guided_session = None
            else:
                session.phase = self._next_phase(phase)

        return self._render_guided_turn(phase=phase, turn=turn)

    def _build_guided_context(
        self, *, session: GuidedRefinementSessionState, user_message: str
    ) -> str:
        prior = []
        for phase in _PHASE_ORDER:
            lines = session.phase_summaries.get(phase, [])
            if not lines:
                continue
            prior.append(f"{self._phase_label(phase)}: " + " | ".join(lines[-3:]))
        prior_text = "\n".join(f"- {line}" for line in prior) if prior else "- none yet"
        return (
            "Guided task refinement context\n"
            f"Current phase: {session.phase.value}\n"
            f"Session turns so far: {session.turns}\n"
            "Prior phase summaries:\n"
            f"{prior_text}\n\n"
            f"User message:\n{user_message}\n"
        )

    @staticmethod
    def _extract_guided_turn(response: Any) -> GuidedRefinementTurn:
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, GuidedRefinementTurn):
            return content
        return TypeAdapter(GuidedRefinementTurn).validate_python(content)

    @staticmethod
    def _phase_label(phase: GuidedRefinementPhase) -> str:
        return _PHASE_LABELS.get(phase, phase.value.title())

    @staticmethod
    def _phase_position(phase: GuidedRefinementPhase) -> int:
        return _PHASE_ORDER.index(phase) + 1

    @staticmethod
    def _next_phase(phase: GuidedRefinementPhase) -> GuidedRefinementPhase:
        idx = _PHASE_ORDER.index(phase)
        if idx + 1 >= len(_PHASE_ORDER):
            return GuidedRefinementPhase.CLOSE
        return _PHASE_ORDER[idx + 1]

    def _render_phase_intro(self, session: GuidedRefinementSessionState) -> TextMessage:
        phase = session.phase
        intro = (
            f"*Guided Refinement — Phase {self._phase_position(phase)}/{len(_PHASE_ORDER)} "
            f"({self._phase_label(phase)})*\n"
            "Gate: ⏳ pending\n"
            "Quick start: name the boards/lists and 2-3 project areas to refine "
            "(include work + personal if relevant)."
        )
        return TextMessage(content=intro, source="tasks_agent")

    def _render_guided_turn(
        self, *, phase: GuidedRefinementPhase, turn: GuidedRefinementTurn
    ) -> TextMessage:
        gate_line = "✅ met" if turn.gate_met else "❌ not met"
        lines = [
            f"*Guided Refinement — Phase {self._phase_position(phase)}/{len(_PHASE_ORDER)} ({self._phase_label(phase)})*",
            f"Gate: {gate_line}",
            turn.assistant_message.strip(),
        ]
        if turn.phase_summary:
            lines.append("*Captured so far:*")
            lines.extend(f"- {item}" for item in turn.phase_summary[:5])
        if turn.missing_fields and not turn.gate_met:
            lines.append("*Still needed:*")
            lines.extend(f"- {item}" for item in turn.missing_fields[:5])
        if turn.recap:
            lines.append("*Session recap:*")
            lines.append(f"- {turn.recap.summary or 'No summary provided.'}")
            if turn.recap.user_intention:
                lines.append(f"- Intention: {turn.recap.user_intention}")
            if turn.recap.stuck_or_postponed_signals:
                lines.extend(
                    f"- Stuck signal: {item}"
                    for item in turn.recap.stuck_or_postponed_signals[:3]
                )
        return TextMessage(content="\n".join(lines), source="tasks_agent")


__all__ = ["TasksAgent"]
