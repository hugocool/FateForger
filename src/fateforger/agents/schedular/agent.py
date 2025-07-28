"""
# TODO: insert summary here
"""

import datetime as dt
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

from fateforger.tools.calendar_mcp import get_calendar_mcp_tools

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

        tools = await get_calendar_mcp_tools(SERVER_URL)
        logging.debug("PlannerAgent: received MCP tools: %s", tools)
        self._delegate = AssistantAgent(
            self.id.type,
            system_message=prompt,
            model_client=OpenAIChatCompletionClient(model="gpt-4o"),
            tools=tools,
            reflect_on_tool_use=True,
            max_tool_iterations=5,
        )

    @message_handler
    async def handle_message(
        self, message: MyMessage, ctx: MessageContext
    ) -> TextMessage:
        logging.debug("PlannerAgent: received user message: %s", message.content)
        await self._ensure_initialized()

        resp = await self._delegate.on_messages(
            [TextMessage(content=message.content, source="user")],
            ctx.cancellation_token,
        )

        return resp.chat_message
