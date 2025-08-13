"""
# TODO: insert summary here
"""

import datetime as dt
import json
import logging
import os
from dataclasses import dataclass

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import (
    AgentId,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    default_subscription,
    message_handler,
)
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

from fateforger.debug.diag import with_timeout
from fateforger.tools.calendar_mcp import get_calendar_mcp_tools

from .models import CalendarEvent
from ...core.config import settings

# THe first planner simply handles the connection to the calendar and single event CRUD.
# So when the user sends a CalendarEvent, it will create the event in the calendar.
# however when the adresses the main calendar agent, it should decide on whether a single CRUD is enough or if it should plan a series of events.
# so it has multiple tools/routing possibilities, one is to do a single event thing, the other is to do a full planning thing.
# however a full timeboxing workflow is more complicated, requires smarter models, going back multiple times, making judgements, so the question is where to start.

# TODO: make this agent work, give it the proper mcp tools, a prompt and test it.


# class CalendarCrudAgent(RoutedAgent):
#     def __init__(self, name: str, workbench: McpWorkbench):
#         super().__init__(name=name)
#         self.workbench = workbench

#     @message_handler
#     async def handleCalendarEvent(self, msg: CalendarEvent, ctx: MessageContext):
#         result = await self.call_tool(
#             "create_event", title=msg.title, start=msg.start_iso, end=msg.end_iso
#         )
#         await ctx.send(
#             TextMessage(content=f"Created event `{msg.title}` with ID: {result.id}")
#         )


# TODO: transplant the logix from the planning.py to there
prompt = (
    f"You are PlannerAgent with Calendar MCP access. Today is {dt.date.today()}.\n"
    "Use the calendar tools for create/update/delete/list.\n"
    "Ask clarifying questions when needed."
    "\nWhen you call a tool, do not describe the plan. "
    "Call the tool, wait for its result, then answer the user directly."
)


SERVER_URL = os.getenv("CALENDAR_MCP_URL", "http://localhost:3000")


@dataclass
class MyMessage:
    content: str


class PlannerAgent(RoutedAgent):
    def __init__(self, name: str):
        super().__init__(name)
        self._delegate: AssistantAgent | None = None

    async def _ensure_initialized(self) -> None:
        if self._delegate:
            return
        # (1) wrap MCP discovery so it can't hang silently

        tools = await with_timeout(
            "mcp:get_calendar_mcp_tools",
            get_calendar_mcp_tools(SERVER_URL),
            timeout_s=5,
        )

        self._delegate = AssistantAgent(
            self.id.type,
            system_message=prompt,
            model_client=OpenAIChatCompletionClient(
                model="gpt-4o", api_key=settings.openai_api_key
            ),
            tools=tools,
            reflect_on_tool_use=True,
            max_tool_iterations=5,
        )

    @message_handler
    async def handle_message(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        logging.debug("PlannerAgent: received user message: %s", message.content)
        await self._ensure_initialized()

        # Ensure delegate is initialized
        assert self._delegate is not None, "Delegate should be initialized"

        # (2) wrap the actual LLM/tool run
        resp = await with_timeout(
            "assistant:on_messages",
            self._delegate.on_messages([message], ctx.cancellation_token),
            timeout_s=20,
        )
        return resp.chat_message


import logging

from autogen_core.tools import ToolResult
from tenacity import retry, retry_if_result, stop_after_attempt, wait_random_exponential

logger = logging.getLogger("mcp")


def _is_error(result: ToolResult) -> bool:
    return bool(getattr(result, "is_error", False))


def _log_retry(retry_state):
    # Called only when _is_error(result) is True
    logger.warning(
        "MCP create-event returned is_error=True; retrying (attempt %s)",
        retry_state.attempt_number,
    )


@retry(
    retry=retry_if_result(_is_error),  # retry ONLY when result.is_error == True
    stop=stop_after_attempt(3),  # 3 tries total
    wait=wait_random_exponential(0.5, max=4),  # tiny backoff + jitter
    before_sleep=_log_retry,
)
async def call_create_event_with_retry(workbench, payload: dict) -> ToolResult:
    # tenacity will re-call this if _is_error(result) is True
    return await workbench.call_tool("create-event", arguments=payload)


class CalendarEventWorkerAgent(RoutedAgent):
    """
    Worker agent that receives a CalendarEvent and calls the "create-event" MCP tool.
    """

    def __init__(self, name: str, server_url: str):
        super().__init__(description=name)
        params = StreamableHttpServerParams(url=server_url, timeout=5.0)
        self.workbench = McpWorkbench(params)  # auto-starts on first call if needed

    @message_handler
    async def handle_calendar_event(
        self, message: CalendarEvent, ctx: MessageContext
    ) -> ToolResult:

        payload = message.model_dump(exclude_none=True)
        result = await call_create_event_with_retry(self.workbench, payload)
        if result.is_error:
            raise RuntimeError("create-event failed after 3 attempts")
        return result
