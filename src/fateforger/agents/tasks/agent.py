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

For Notion sprint operations:
- use `find_sprint_items` to discover sprint tickets in a sprint data source
- use `link_sprint_subtasks` to link parent/child sprint records
- use `patch_sprint_page_content` for preview/apply page-content edits
- strict tool schemas are enabled: pass every tool argument explicitly.
  Use `null` for optional values you are not using.

Be direct and concrete. If needed, ask 1-2 clarifying questions then propose a plan.
Never invent IDs. If the tool reports ambiguity, ask a focused follow-up question.
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
