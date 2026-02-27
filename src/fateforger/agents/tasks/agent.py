"""Tasks specialist agent (Task Marshal)."""

from __future__ import annotations

import logging

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler
from autogen_core.tools import FunctionTool

from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.tools.ticktick_mcp import get_ticktick_mcp_url

from .list_tools import TickTickListManager
from .messages import PendingTaskItem, PendingTaskSnapshot, PendingTaskSnapshotRequest
from .notion_sprint_tools import NotionSprintManager


logger = logging.getLogger(__name__)

# TODO(refactor): centralize MCP client/tool loading + timeouts in shared helpers (see `fateforger.agents.timeboxing.mcp_clients`)
# TODO(refactor): replace ad-hoc response handling with a shared `parse_chat_content` helper (see `fateforger.agents.timeboxing.pydantic_parsing`)

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
For generic requests like "open tasks", never assume a list name.
Use these patterns:
- `operation="show_lists"` to retrieve available projects/lists.
- `operation="show_list_items"` with `list_id` from the previous result.
- for cross-project requests ("all open tasks", "all projects"), call
  `operation="show_list_items"` with `list_name=null` and `list_id=null`.
Never invent list names or IDs.
For multi-task requests, use `resolve_ticktick_task_mentions`:
- decompose the request into concrete task mentions
- call the tool with those mentions and optional expansion queries
- iterate until each mention is marked resolved, ambiguous, or unresolved
- if ambiguous, ask a focused disambiguation question with numbered candidates

For Notion sprint operations:
- use `find_sprint_items` to discover sprint tickets in a sprint data source
- use `link_sprint_subtasks` to link parent/child sprint records
- use `patch_sprint_page_content` for preview/apply page-content edits
- use `patch_sprint_event` for a single, opinionated sprint event patch
- use `patch_sprint_events` for list patching (search/filter first, then patch)
- when asking for open Notion tickets, call `find_sprint_items` first
  with `data_source_url=null` (it will use configured defaults when available)
- when patching content, run dry-run preview first unless user explicitly asks to apply
- strict tool schemas are enabled: pass every tool argument explicitly.
  Use `null` for optional values you are not using.

Be direct and concrete. If needed, ask 1-2 clarifying questions then propose a plan.
Never invent IDs. If the tool reports ambiguity, ask a focused follow-up question.
""".strip()


def _split_csv(raw: str | None) -> list[str]:
    return [chunk.strip() for chunk in (raw or "").split(",") if chunk.strip()]


class TasksAgent(RoutedAgent):
    def __init__(self, name: str) -> None:
        super().__init__(description=name)
        server_url = get_ticktick_mcp_url()
        self._list_manager = TickTickListManager(
            server_url=server_url,
            timeout=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)),
        )
        self._notion_sprint_manager = NotionSprintManager(
            timeout=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)),
            default_data_source_url=(settings.notion_sprint_data_source_url or "").strip(),
            default_database_id=(settings.notion_sprint_db_id or "").strip(),
            default_data_source_urls=_split_csv(
                getattr(settings, "notion_sprint_data_source_urls", "")
            ),
            default_database_ids=_split_csv(
                getattr(settings, "notion_sprint_db_ids", "")
            ),
        )
        tools = [
            FunctionTool(
                self._list_manager.manage_ticktick_lists,
                name="manage_ticktick_lists",
                description=(
                    "Manage TickTick lists and items. "
                    "Supports project-mode and explicit subtask-mode operations. "
                    "Use show_lists to enumerate projects; use show_list_items with "
                    "null list_name/list_id for an all-project open-task view."
                ),
                strict=True,
            ),
            FunctionTool(
                self._list_manager.resolve_ticktick_task_mentions,
                name="resolve_ticktick_task_mentions",
                description=(
                    "Resolve multiple task mentions across all TickTick projects with "
                    "query expansion and scored candidates."
                ),
                strict=True,
            ),
            FunctionTool(
                self._notion_sprint_manager.find_sprint_items,
                name="find_sprint_items",
                description=(
                    "Find sprint records in a Notion sprint data source with filters. "
                    "Pass data_source_url=null to use configured default sprint source."
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
            FunctionTool(
                self._notion_sprint_manager.patch_sprint_event,
                name="patch_sprint_event",
                description=(
                    "Patch a single sprint event page with a focused preview/apply workflow."
                ),
                strict=True,
            ),
            FunctionTool(
                self._notion_sprint_manager.patch_sprint_events,
                name="patch_sprint_events",
                description=(
                    "Patch multiple sprint events by explicit page IDs or by search/filter selection."
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
            max_tool_iterations=8,
        )

    @message_handler
    async def handle_pending_snapshot(
        self, message: PendingTaskSnapshotRequest, ctx: MessageContext
    ) -> PendingTaskSnapshot:
        rows = await with_timeout(
            "tasks:pending_snapshot",
            self._list_manager.list_pending_tasks(
                limit=message.limit,
                per_project_limit=message.per_project_limit,
            ),
            timeout_s=12,
        )
        query = (message.query or "").strip().lower()
        filtered = (
            [row for row in rows if query in row.title.lower()] if query else list(rows)
        )
        items = [
            PendingTaskItem(
                id=row.id,
                title=row.title,
                project_id=row.project_id,
                project_name=row.project_name,
            )
            for row in filtered
        ]
        return PendingTaskSnapshot(
            items=items,
            summary=f"Found {len(items)} pending task(s).",
        )

    @message_handler
    async def handle_text(self, message: TextMessage, ctx: MessageContext) -> TextMessage:
        response = await with_timeout(
            "tasks:on_messages",
            self._assistant.on_messages([message], ctx.cancellation_token),
            timeout_s=20,
        )
        chat_message = getattr(response, "chat_message", None)
        if isinstance(chat_message, TextMessage):
            return chat_message
        content = getattr(chat_message, "content", None) if chat_message else None
        return TextMessage(content=str(content or "(no response)"), source=self.id.type)


__all__ = ["TasksAgent"]
