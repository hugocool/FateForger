"""Tasks specialist agent (Task Marshal)."""

from __future__ import annotations

import logging

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler

from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.tools.ticktick_mcp import TickTickMcpClient, get_ticktick_mcp_url


logger = logging.getLogger(__name__)


TASKS_PROMPT = """
You are the FateForger Tasks Agent ("Task Marshal").
Your job is to help with task triage and execution:
- capture tasks
- prioritize (now/next/later)
- define next actions
- break down work into small steps

Be direct and concrete. If needed, ask 1-2 clarifying questions then propose a plan.
If TickTick MCP tools are available, use them to list, update, or complete tasks.
""".strip()


class TasksAgent(RoutedAgent):
    def __init__(self, name: str) -> None:
        super().__init__(description=name)
        self._ticktick_client: TickTickMcpClient | None = None
        tools = None
        server_url = get_ticktick_mcp_url()
        if server_url:
            try:
                self._ticktick_client = TickTickMcpClient(server_url=server_url)
                ticktick_tools = self._ticktick_client.get_tools()
                tools = ticktick_tools if ticktick_tools else None
            except Exception:
                logger.debug("Failed to load TickTick MCP tools", exc_info=True)
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
