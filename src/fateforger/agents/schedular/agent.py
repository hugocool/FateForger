"""
# TODO: insert summary here
"""

import asyncio
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

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
from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams
from dateutil import parser as date_parser

from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.haunt.mixins import HauntAwareAgentMixin
from fateforger.haunt.models import FollowUpPlan, HauntTone
from fateforger.haunt.orchestrator import HauntOrchestrator, HauntTicket
from fateforger.llm import build_autogen_chat_client
from fateforger.slack_bot.messages import SlackBlockMessage
from fateforger.tools.calendar_mcp import get_calendar_mcp_tools

from .messages import (
    SuggestedSlot,
    SuggestNextSlot,
    UpsertCalendarEvent,
    UpsertCalendarEventResult,
)
from .models import CalendarEvent

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
    "Use the calendar tools for create/update/delete/list/get.\n"
    "Ask clarifying questions when needed."
    "\nWhen you call a tool, do not describe the plan. "
    "Call the tool, wait for its result, then answer the user directly."
    "\nIf the user provides an explicit `eventId`, you MUST preserve it and use it for get/update operations."
)


SERVER_URL = os.getenv("CALENDAR_MCP_URL", "http://localhost:3000")
SERVER_URL = os.getenv("MCP_CALENDAR_SERVER_URL", SERVER_URL)


@dataclass
class MyMessage:
    content: str


class PlannerAgent(HauntAwareAgentMixin, RoutedAgent):
    def __init__(self, name: str, *, haunt: HauntOrchestrator):
        RoutedAgent.__init__(self, name)
        HauntAwareAgentMixin.__init__(
            self,
            haunt_orchestrator=haunt,
            haunt_agent_id=f"planner::{name}",
            default_channel="planner-thread",
        )
        self._delegate: AssistantAgent | None = None
        self._workbench: McpWorkbench | None = None

    def _ensure_workbench(self) -> McpWorkbench:
        if self._workbench:
            return self._workbench
        params = StreamableHttpServerParams(url=SERVER_URL, timeout=10.0)
        self._workbench = McpWorkbench(params)
        return self._workbench

    @staticmethod
    def _extract_tool_payload(result: object) -> object:
        if isinstance(result, dict):
            return result
        payload = getattr(result, "content", None)
        if payload is not None:
            return payload
        payload = getattr(result, "result", None)
        if payload is not None:
            return payload
        return {}

    @staticmethod
    def _normalize_event(payload: object) -> dict | None:
        if isinstance(payload, dict):
            if "id" in payload or "summary" in payload:
                return payload
            item = payload.get("item")
            if isinstance(item, dict):
                return item
            event = payload.get("event")
            if isinstance(event, dict):
                return event
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    return item
        return None

    @staticmethod
    def _normalize_events(payload: object) -> list[dict]:
        # TODO(refactor): Replace dict filtering with Pydantic CalendarEvent parsing.
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _parse_event_dt(raw: dict | str | None, *, tz: ZoneInfo) -> dt.datetime | None:
        # TODO(refactor): Use a Pydantic date-time field parser instead of try/except.
        if not raw:
            return None
        if isinstance(raw, str):
            try:
                parsed = date_parser.isoparse(raw)
            except Exception:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)
        if isinstance(raw, dict):
            if raw.get("dateTime"):
                parsed = date_parser.isoparse(raw["dateTime"])
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=tz)
                return parsed.astimezone(tz)
            if raw.get("date"):
                day = date_parser.isoparse(raw["date"]).date()
                return dt.datetime.combine(day, dt.time(0, 0), tz)
        return None

    @classmethod
    def _format_event_when(cls, event: dict, *, tz: ZoneInfo) -> str | None:
        start = cls._parse_event_dt(event.get("start"), tz=tz)
        end = cls._parse_event_dt(event.get("end"), tz=tz)
        if not start or not end:
            return None
        if start.date() == end.date():
            return (
                f"{start.strftime('%a %d %b %H:%M')}–{end.strftime('%H:%M')} ({tz.key})"
            )
        return f"{start.strftime('%a %d %b %H:%M')}–{end.strftime('%a %d %b %H:%M')} ({tz.key})"

    @classmethod
    def _event_blocks(
        cls,
        *,
        title: str,
        event: dict,
        calendar_id: str,
        tz: ZoneInfo,
        note: str | None = None,
    ) -> SlackBlockMessage:
        summary = event.get("summary") or title
        html_link = event.get("htmlLink")
        when = cls._format_event_when(event, tz=tz) or "(time unavailable)"
        event_id = event.get("id") or ""

        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": f"📅 {summary}"}},
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*When*\n{when}"},
                    {"type": "mrkdwn", "text": f"*Calendar*\n`{calendar_id}`"},
                ],
            },
        ]
        if note:
            blocks.append(
                {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]}
            )
        if html_link:
            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "action_id": "ff_open_google_calendar_event",
                            "text": {
                                "type": "plain_text",
                                "text": "Open in Google Calendar",
                            },
                            "url": html_link,
                            "style": "primary",
                        }
                    ],
                }
            )
        if event_id:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"Event id: `{event_id}`"}],
                }
            )
        return SlackBlockMessage(text=f"{summary}\n{when}", blocks=blocks)

    @message_handler
    async def handle_suggest_next_slot(
        self, message: SuggestNextSlot, ctx: MessageContext
    ) -> SuggestedSlot:
        workbench = self._ensure_workbench()
        try:
            tz = ZoneInfo(message.time_zone)
        except Exception:
            return SuggestedSlot(
                ok=False, error=f"Unknown time zone: {message.time_zone}"
            )

        now = dt.datetime.now(dt.timezone.utc).astimezone(tz)
        duration = dt.timedelta(minutes=message.duration_min)

        def _busy_intervals(
            events: list[dict],
        ) -> list[tuple[dt.datetime, dt.datetime]]:
            intervals: list[tuple[dt.datetime, dt.datetime]] = []
            for event in events:
                if (event.get("status") or "").lower() == "cancelled":
                    continue
                start = self._parse_event_dt(event.get("start"), tz=tz)
                end = self._parse_event_dt(event.get("end"), tz=tz)
                if start and end and end > start:
                    intervals.append((start, end))
            intervals.sort(key=lambda pair: pair[0])
            merged: list[tuple[dt.datetime, dt.datetime]] = []
            for start, end in intervals:
                if not merged or start > merged[-1][1]:
                    merged.append((start, end))
                else:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            return merged

        def _first_gap(
            window_start: dt.datetime,
            window_end: dt.datetime,
            busy: list[tuple[dt.datetime, dt.datetime]],
        ) -> dt.datetime | None:
            cursor = window_start
            for start, end in busy:
                if start - cursor >= duration:
                    return cursor
                cursor = max(cursor, end)
                if cursor >= window_end:
                    return None
            if window_end - cursor >= duration:
                return cursor
            return None

        horizon_days = max(int(message.horizon_days), 1)
        for day_offset in range(0, horizon_days):
            day = (now + dt.timedelta(days=day_offset)).date()
            window_start = dt.datetime.combine(
                day, dt.time(message.work_start_hour, 0), tz
            )
            window_end = dt.datetime.combine(day, dt.time(message.work_end_hour, 0), tz)
            if day_offset == 0:
                window_start = max(window_start, now + dt.timedelta(minutes=5))
            if window_end <= window_start:
                continue

            result = await workbench.call_tool(
                "list-events",
                arguments={
                    "calendarId": message.calendar_id,
                    "timeMin": window_start.astimezone(dt.timezone.utc).isoformat(),
                    "timeMax": window_end.astimezone(dt.timezone.utc).isoformat(),
                    "singleEvents": True,
                    "orderBy": "startTime",
                },
            )
            events = self._normalize_events(self._extract_tool_payload(result))
            start = _first_gap(window_start, window_end, _busy_intervals(events))
            if start:
                end = start + duration
                return SuggestedSlot(
                    ok=True,
                    start_utc=start.astimezone(dt.timezone.utc).isoformat(),
                    end_utc=end.astimezone(dt.timezone.utc).isoformat(),
                    time_zone=tz.key,
                )

        return SuggestedSlot(ok=False, error="No free slot found")

    @message_handler
    async def handle_upsert_calendar_event(
        self, message: UpsertCalendarEvent, ctx: MessageContext
    ) -> UpsertCalendarEventResult:
        logger.info(
            "Upserting calendar event: calendar=%s, event_id=%s, summary=%s",
            message.calendar_id,
            message.event_id,
            message.summary,
        )
        workbench = self._ensure_workbench()
        tz = ZoneInfo(message.time_zone or "UTC")

        # Prefer deterministic upsert (get → update|create) over LLM tool-routing.
        exists = False
        try:
            fetched = await workbench.call_tool(
                "get-event",
                arguments={
                    "calendarId": message.calendar_id,
                    "eventId": message.event_id,
                },
            )
            event = self._normalize_event(self._extract_tool_payload(fetched))
            exists = bool(event)
        except Exception:
            exists = False

        try:
            if exists:
                await workbench.call_tool(
                    "update-event",
                    arguments={
                        "calendarId": message.calendar_id,
                        "eventId": message.event_id,
                        "summary": message.summary,
                        "start": message.start,
                        "end": message.end,
                        "timeZone": message.time_zone,
                        "colorId": message.color_id,
                        "description": message.description,
                    },
                )
            else:
                await workbench.call_tool(
                    "create-event",
                    arguments={
                        "calendarId": message.calendar_id,
                        "eventId": message.event_id,
                        "summary": message.summary,
                        "start": message.start,
                        "end": message.end,
                        "timeZone": message.time_zone,
                        "colorId": message.color_id,
                        "description": message.description,
                    },
                )
        except Exception as e:
            logger.error("Failed to upsert calendar event: %s", e, exc_info=True)
            return UpsertCalendarEventResult(
                ok=False,
                calendar_id=message.calendar_id,
                event_id=message.event_id,
                error=str(e),
            )

        try:
            fetched = await workbench.call_tool(
                "get-event",
                arguments={
                    "calendarId": message.calendar_id,
                    "eventId": message.event_id,
                },
            )
            event = self._normalize_event(self._extract_tool_payload(fetched)) or {}
        except Exception:
            event = {"id": message.event_id, "summary": message.summary}

        event_url = event.get("htmlLink") or event.get("html_link") or event.get("url")
        logger.info(
            "Calendar event upserted successfully: event_id=%s, url=%s",
            event.get("id") or message.event_id,
            event_url,
        )
        return UpsertCalendarEventResult(
            ok=True,
            calendar_id=message.calendar_id,
            event_id=event.get("id") or message.event_id,
            event_url=str(event_url) if event_url else None,
        )

    async def _ensure_initialized(self) -> None:
        if self._delegate:
            return
        # (1) wrap MCP discovery so it can't hang silently

        tools = await with_timeout(
            "mcp:get_calendar_mcp_tools",
            get_calendar_mcp_tools(SERVER_URL),
            timeout_s=settings.agent_mcp_discovery_timeout_seconds,
        )

        self._delegate = AssistantAgent(
            self.id.type,
            system_message=prompt,
            model_client=build_autogen_chat_client(
                "planner_agent", parallel_tool_calls=False
            ),
            tools=tools,
            reflect_on_tool_use=False,
            max_tool_iterations=3,
        )

    @message_handler
    async def handle_message(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        logging.debug("PlannerAgent: received user message: %s", message.content)

        session_id = self._session_id(ctx)
        await self._log_inbound(
            session_id=session_id,
            content=message.content,
            core_intent=self._summarize(message.content),
        )
        await self._ensure_initialized()

        # Ensure delegate is initialized
        assert self._delegate is not None, "Delegate should be initialized"

        # (2) wrap the actual LLM/tool run
        try:
            resp = await with_timeout(
                "assistant:on_messages",
                self._delegate.on_messages([message], ctx.cancellation_token),
                timeout_s=settings.agent_on_messages_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return TextMessage(
                content=(
                    "I'm still waiting on the calendar tools. "
                    "Please try again in a moment (or ask a narrower question like "
                    '"what\'s on my calendar on Sunday between 9 and 12?").'
                ),
                source=self.id.type,
            )

        chat_message = getattr(resp, "chat_message", None)
        content = getattr(chat_message, "content", None)
        if content is None:
            content = getattr(resp, "content", None)

        # AutoGen can return other BaseTextChatMessage variants (e.g. ToolCallSummaryMessage)
        # when tools were called. The RoutedAgent return type must remain stable (TextMessage),
        # so coerce to TextMessage for downstream Slack handlers.
        if type(chat_message) is not TextMessage:
            source = getattr(chat_message, "source", None)
            if not source:
                try:
                    source = self.id.type
                except Exception:
                    source = "planner_agent"
            chat_message = TextMessage(
                content=str(
                    getattr(chat_message, "content", None) or content or "(no response)"
                ),
                source=source,
            )

        follow_up = self._follow_up_plan(chat_message.content)
        await self._log_outbound(
            session_id=session_id,
            content=chat_message.content,
            core_intent=self._summarize(chat_message.content),
            follow_up=follow_up,
            tone=HauntTone.SUPPORTIVE,
        )

        return chat_message

    @staticmethod
    def _summarize(text: str) -> str:
        clean = " ".join(text.split())
        return clean[:160]

    @staticmethod
    def _session_id(ctx: MessageContext) -> str:
        topic = getattr(ctx, "topic_id", None)
        if topic:
            return str(topic)
        convo = getattr(ctx, "conversation_id", None)
        if convo:
            return str(convo)
        return "planner-session"

    @staticmethod
    def _follow_up_plan(content: str) -> FollowUpPlan:
        if "?" in content:
            return FollowUpPlan(required=True, delay_minutes=10)
        return FollowUpPlan(required=False)

    async def on_haunt_follow_up(self, ticket: HauntTicket) -> None:
        reminder = f"👻 Planner reminder: {ticket.payload.core_intent}"
        await self.publish_message(
            TextMessage(content=reminder, source=self._haunt_agent_id),
            DefaultTopicId(),
        )


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
