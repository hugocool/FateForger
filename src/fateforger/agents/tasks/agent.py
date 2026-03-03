"""Tasks specialist agent (Task Marshal)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler
from autogen_core.tools import FunctionTool
from pydantic import TypeAdapter

from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.slack_bot.task_cards import (
    build_due_overview_blocks,
    build_task_edit_modal,
)
from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.tools.ticktick_mcp import get_ticktick_mcp_url

from .defaults_memory import TaskDefaultsMemoryStore, TaskDueDefaults
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
    TaskDetailsModalRequest,
    TaskDetailsModalResponse,
    TaskDueActionRequest,
    TaskEditTitleRequest,
    TaskEditTitleResponse,
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
_DUE_TOMORROW_HINTS = ("due tomorrow", "tomorrow due", "tasks tomorrow")
_TICKTICK_HINTS = ("ticktick", "tick tick")
_TICKTICK_ALL_LISTS_PATTERN = re.compile(
    r"^\s*tick\s*tick\s+all\s+lists\s*$|^\s*ticktick\s+all\s+lists\s*$",
    flags=re.IGNORECASE,
)
_TICKTICK_LISTS_PATTERN = re.compile(
    r"^\s*tick\s*tick\s+these\s+lists\s*:\s*(.+)$|^\s*ticktick\s+these\s+lists\s*:\s*(.+)$",
    flags=re.IGNORECASE,
)
_TASK_LABEL_PATTERN = re.compile(r"\bTT-([A-Za-z0-9]{6,})\b", flags=re.IGNORECASE)


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
        self._defaults_store = TaskDefaultsMemoryStore(
            timeout_s=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10))
        )
        self._pending_due_defaults_setup_users: set[str] = set()

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
    async def handle_due_action(
        self,
        message: TaskDueActionRequest,
        ctx: MessageContext,
    ) -> SlackBlockMessage | TextMessage:
        _ = ctx
        due_on = self._parse_iso_date(message.due_date)
        if due_on is None:
            return TextMessage(
                content="Could not parse the due date for that action.",
                source="tasks_agent",
            )
        project_ids = [item for item in (message.ticktick_project_ids or []) if item]
        tasks = await self._list_manager.list_due_tasks(
            due_on=due_on,
            project_ids=project_ids or None,
            limit=500,
        )
        return self._build_due_card(
            due_on=due_on,
            tasks=tasks,
            source_defaults=TaskDueDefaults(
                user_id=message.user_id,
                source="ticktick",
                ticktick_project_ids=project_ids,
                ticktick_project_names=[],
                configured_at=datetime.utcnow().isoformat(),
            ),
            show_all=True,
        )

    @message_handler
    async def handle_task_details_modal(
        self,
        message: TaskDetailsModalRequest,
        ctx: MessageContext,
    ) -> TaskDetailsModalResponse:
        _ = ctx
        if not (message.task_id and message.project_id and message.channel_id and message.thread_ts):
            return TaskDetailsModalResponse(
                ok=False,
                error="missing_fields",
                view=None,
            )
        view = build_task_edit_modal(
            task_id=message.task_id,
            project_id=message.project_id,
            label=message.label,
            title=message.title,
            project_name=message.project_name,
            due_date=message.due_date,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
            user_id=message.user_id,
        )
        return TaskDetailsModalResponse(ok=True, error="", view=view)

    @message_handler
    async def handle_task_edit_title(
        self,
        message: TaskEditTitleRequest,
        ctx: MessageContext,
    ) -> TaskEditTitleResponse:
        _ = ctx
        ok, error = await self._list_manager.update_task_title(
            project_id=message.project_id,
            task_id=message.task_id,
            new_title=message.new_title,
        )
        if not ok:
            return TaskEditTitleResponse(
                ok=False,
                message=f"Could not update {message.label}: {error or 'update failed'}.",
            )
        return TaskEditTitleResponse(
            ok=True,
            message=f"Updated {message.label} title to: {message.new_title.strip()}",
        )

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
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

        configured = await self._try_capture_due_defaults_from_reply(
            message=message,
            allow_loose_hint=(message.source in self._pending_due_defaults_setup_users),
        )
        if configured is not None:
            return configured

        due_reply = await self._maybe_handle_due_tomorrow_query(message=message)
        if due_reply is not None:
            return due_reply

        edit_reply = await self._maybe_handle_label_title_edit(message=message)
        if edit_reply is not None:
            return edit_reply

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

    async def _maybe_handle_due_tomorrow_query(
        self, *, message: TextMessage
    ) -> SlackBlockMessage | TextMessage | None:
        text = (message.content or "").strip()
        normalized = text.lower()
        if not self._is_due_tomorrow_query(normalized):
            return None
        defaults = await self._defaults_store.get_user_defaults(user_id=message.source)
        if defaults is None:
            self._pending_due_defaults_setup_users.add(message.source)
            return self._prompt_for_due_defaults()
        due_on = date.today() + timedelta(days=1)
        tasks = await self._list_manager.list_due_tasks(
            due_on=due_on,
            project_ids=defaults.ticktick_project_ids or None,
            limit=200,
        )
        return self._build_due_card(
            due_on=due_on,
            tasks=tasks,
            source_defaults=defaults,
            show_all=False,
        )

    async def _try_capture_due_defaults_from_reply(
        self, *, message: TextMessage, allow_loose_hint: bool = False
    ) -> SlackBlockMessage | TextMessage | None:
        content = (message.content or "").strip()
        normalized = content.lower()
        parsed = self._parse_ticktick_defaults_command(content)
        if parsed is None and not allow_loose_hint:
            return None
        if parsed is None and not any(hint in normalized for hint in _TICKTICK_HINTS):
            return None
        explicit_all_lists, explicit_list_names = parsed or (False, [])
        due_on = date.today() + timedelta(days=1)
        projects = await self._list_manager.list_projects()
        selected_projects = []
        if explicit_list_names:
            normalized_names = [self._normalize(item) for item in explicit_list_names]
            selected_projects = [
                project
                for project in projects
                if self._normalize(project.name) in normalized_names
            ]
            if not selected_projects:
                return TextMessage(
                    content=(
                        "I could not match those TickTick list names. "
                        "Use `TickTick all lists` or `TickTick these lists: <list1>, <list2>`."
                    ),
                    source="tasks_agent",
                )
        elif not explicit_all_lists:
            selected_projects = [
                project
                for project in projects
                if self._normalize(project.name) in self._normalize(content)
            ]
        if explicit_all_lists or ("all" in normalized and "list" in normalized):
            selected_projects = []
        defaults = TaskDueDefaults(
            user_id=message.source,
            source="ticktick",
            ticktick_project_ids=[project.id for project in selected_projects],
            ticktick_project_names=[project.name for project in selected_projects],
            configured_at=datetime.utcnow().isoformat(),
        )
        _ = await self._defaults_store.upsert_user_defaults(defaults)
        self._pending_due_defaults_setup_users.discard(message.source)
        tasks = await self._list_manager.list_due_tasks(
            due_on=due_on,
            project_ids=defaults.ticktick_project_ids or None,
            limit=200,
        )
        return self._build_due_card(
            due_on=due_on,
            tasks=tasks,
            source_defaults=defaults,
            show_all=False,
        )

    @staticmethod
    def _parse_ticktick_defaults_command(
        text: str,
    ) -> tuple[bool, list[str]] | None:
        stripped = (text or "").strip()
        if _TICKTICK_ALL_LISTS_PATTERN.match(stripped):
            return True, []
        match = _TICKTICK_LISTS_PATTERN.match(stripped)
        if not match:
            return None
        raw = match.group(1) or match.group(2) or ""
        names = [item.strip() for item in raw.split(",") if item.strip()]
        if not names:
            return None
        return False, names

    async def _maybe_handle_label_title_edit(
        self, *, message: TextMessage
    ) -> TextMessage | None:
        parsed = self._extract_label_title_edit(message.content or "")
        if parsed is None:
            return None
        label, new_title = parsed
        pending = await self._list_manager.list_all_pending_tasks()
        matches = [
            row
            for row in pending
            if self._task_label(row.id).lower().startswith(label.lower())
        ]
        if not matches:
            return TextMessage(
                content=f"I could not resolve `{label}` to a pending TickTick task.",
                source="tasks_agent",
            )
        if len(matches) > 1:
            hints = ", ".join(self._task_label(row.id) for row in matches[:3])
            return TextMessage(
                content=f"`{label}` is ambiguous ({hints}). Please pick one exact label.",
                source="tasks_agent",
            )
        match = matches[0]
        if not match.project_id:
            return TextMessage(
                content=f"Could not update `{label}` because project_id is missing.",
                source="tasks_agent",
            )
        ok, error = await self._list_manager.update_task_title(
            project_id=match.project_id,
            task_id=match.id,
            new_title=new_title,
        )
        if not ok:
            return TextMessage(
                content=f"Could not update `{label}`: {error or 'update failed'}.",
                source="tasks_agent",
            )
        return TextMessage(
            content=f"Updated `{label}` title to: {new_title}",
            source="tasks_agent",
        )

    @staticmethod
    def _is_due_tomorrow_query(normalized_text: str) -> bool:
        if any(phrase in normalized_text for phrase in _DUE_TOMORROW_HINTS):
            return True
        return (
            "tomorrow" in normalized_text
            and "due" in normalized_text
            and "task" in normalized_text
        )

    @staticmethod
    def _prompt_for_due_defaults() -> TextMessage:
        return TextMessage(
            content=(
                "I can remember this for next time.\n"
                "Should I use TickTick by default for due-task queries?\n"
                "Reply with either:\n"
                "- `TickTick all lists`\n"
                "- `TickTick these lists: <list1>, <list2>`"
            ),
            source="tasks_agent",
        )

    def _build_due_card(
        self,
        *,
        due_on: date,
        tasks: list[Any],
        source_defaults: TaskDueDefaults,
        show_all: bool,
    ) -> SlackBlockMessage:
        source_label = self._source_label(source_defaults)
        card_rows: list[dict[str, Any]] = []
        for row in tasks:
            card_rows.append(
                {
                    "id": getattr(row, "id", ""),
                    "title": getattr(row, "title", ""),
                    "project_id": getattr(row, "project_id", ""),
                    "project_name": getattr(row, "project_name", ""),
                    "due_date": getattr(row, "due_date", "") or due_on.isoformat(),
                    "label": self._task_label(getattr(row, "id", "")),
                }
            )
        blocks = build_due_overview_blocks(
            tasks=card_rows,
            due_date=due_on.isoformat(),
            source_label=source_label,
            show_all=show_all,
            view_all_meta={
                "action": "view_all_due",
                "source": source_defaults.source,
                "due_date": due_on.isoformat(),
                "project_ids": ",".join(source_defaults.ticktick_project_ids),
            },
        )
        return SlackBlockMessage(
            text=f"{len(card_rows)} task(s) due on {due_on.isoformat()}",
            blocks=blocks,
        )

    @staticmethod
    def _source_label(defaults: TaskDueDefaults) -> str:
        names = [item for item in defaults.ticktick_project_names if item]
        if names:
            return "TickTick (" + ", ".join(names[:3]) + (", ..." if len(names) > 3 else "") + ")"
        return "TickTick (all lists)"

    @staticmethod
    def _task_label(task_id: str) -> str:
        clean = "".join(ch for ch in (task_id or "").strip() if ch.isalnum())
        if not clean:
            return "TT-UNKNOWN"
        return "TT-" + clean[:8].upper()

    @staticmethod
    def _extract_label_title_edit(text: str) -> tuple[str, str] | None:
        normalized = " ".join((text or "").split()).strip()
        patterns = (
            r"(?i)^rename\s+(TT-[A-Za-z0-9]{6,})\s+to\s+(.+)$",
            r"(?i)^set\s+(TT-[A-Za-z0-9]{6,})\s+title\s+to\s+(.+)$",
            r"(?i)^update\s+(TT-[A-Za-z0-9]{6,})\s+to\s+(.+)$",
        )
        for pattern in patterns:
            match = re.match(pattern, normalized)
            if not match:
                continue
            label = match.group(1).strip().upper()
            title = match.group(2).strip()
            if title:
                return label, title
        return None

    @staticmethod
    def _parse_iso_date(value: str) -> date | None:
        try:
            return date.fromisoformat((value or "").strip())
        except Exception:
            return None

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join((value or "").lower().split()).strip()


__all__ = ["TasksAgent"]
