import asyncio
import os
from dataclasses import dataclass
from typing import Callable

from autogen_core import (
    AgentId,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    SingleThreadedAgentRuntime,
    default_subscription,
    message_handler,
)
from autogen_core.tool_agent import ToolAgent

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# Import agents
from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.receptionist import ReceptionistAgent, HandoffBase
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.core.config import settings
from fateforger.haunt.agents import HauntingAgent, UserChannelAgent
from fateforger.haunt.messages import UserFacingMessage
from fateforger.haunt.intervention import HauntingInterventionHandler
from fateforger.haunt.orchestrator import HauntOrchestrator
from fateforger.haunt.service import HauntingService
from fateforger.haunt.settings_store import (
    SqlAlchemyAdmonishmentSettingsStore,
    ensure_admonishment_settings_schema,
)
from fateforger.haunt.tools import build_haunting_tools
from fateforger.haunt.reconcile import McpCalendarClient, PlanningReconciler, PlanningReminder

USER_CHANNEL_AGENT_TYPE = "user_channel"
HAUNTING_AGENT_TYPE = "haunting_agent"
HAUNTING_AGENT_KEY = "default"
HAUNTING_TOOL_AGENT_TYPE = "haunter_tools"


_runtime: SingleThreadedAgentRuntime | None = None
_runtime_lock = asyncio.Lock()


async def _create_runtime() -> SingleThreadedAgentRuntime:
    """Create and start the runtime instance."""
    scheduler = AsyncIOScheduler()
    scheduler.start()

    settings_store = None
    settings_engine = None
    if settings.database_url:
        async_url = _coerce_async_database_url(settings.database_url)
        settings_engine = create_async_engine(async_url)
        await ensure_admonishment_settings_schema(settings_engine)
        sessionmaker = async_sessionmaker(settings_engine, expire_on_commit=False)
        settings_store = SqlAlchemyAdmonishmentSettingsStore(sessionmaker)

    haunting_service = HauntingService(scheduler, settings_store=settings_store)
    intervention = HauntingInterventionHandler(
        haunting_service, user_channel_type=USER_CHANNEL_AGENT_TYPE
    )

    runtime = SingleThreadedAgentRuntime(intervention_handlers=[intervention])
    haunt = HauntOrchestrator(scheduler)
    haunting_tools = build_haunting_tools(haunting_service)

    await UserChannelAgent.register(
        runtime,
        USER_CHANNEL_AGENT_TYPE,
        lambda: UserChannelAgent(USER_CHANNEL_AGENT_TYPE),
    )
    await HauntingAgent.register(
        runtime,
        HAUNTING_AGENT_TYPE,
        lambda: HauntingAgent(
            HAUNTING_AGENT_TYPE,
            service=haunting_service,
            user_channel_type=USER_CHANNEL_AGENT_TYPE,
            default_channel_key=HAUNTING_AGENT_KEY,
        ),
    )
    await ToolAgent.register(
        runtime,
        HAUNTING_TOOL_AGENT_TYPE,
        lambda: ToolAgent(
            "Haunting tool agent (deterministic)",
            tools=haunting_tools,
        ),
    )

    async def dispatch_due(due) -> None:
        await runtime.send_message(
            due,
            recipient=AgentId(HAUNTING_AGENT_TYPE, key=HAUNTING_AGENT_KEY),
        )

    haunting_service.set_dispatcher(dispatch_due)

    reconciler = None
    try:
        calendar_client = McpCalendarClient(
            server_url=os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000")
        )

        async def dispatch_planning(reminder: PlanningReminder) -> None:
            await runtime.send_message(
                UserFacingMessage(
                    content=reminder.message,
                    user_id=reminder.user_id,
                    channel_id=reminder.channel_id,
                ),
                recipient=AgentId(USER_CHANNEL_AGENT_TYPE, key=reminder.scope),
            )

        reconciler = PlanningReconciler(
            scheduler, calendar_client=calendar_client, dispatcher=dispatch_planning
        )
    except Exception:
        reconciler = None

    await PlannerAgent.register(
        runtime,
        "planner_agent",
        lambda: PlannerAgent("planner_agent", haunt=haunt),
    )
    await TimeboxingFlowAgent.register(
        runtime,
        "timeboxing_agent",
        lambda: TimeboxingFlowAgent("timeboxing_agent"),
    )
    await ReceptionistAgent.register(
        runtime,
        "receptionist_agent",
        lambda: ReceptionistAgent(
            "receptionist_agent",
            allowed_handoffs=[
                HandoffBase(
                    target="planner_agent",
                    description="Calendar planning and scheduling agent.",
                ),
                HandoffBase(
                    target="timeboxing_agent",
                    description="Timeboxing day planner that proposes a concrete schedule and iterates on it.",
                ),
            ],
            haunt=haunt,
        ),
    )
    runtime.start()
    setattr(runtime, "haunt_orchestrator", haunt)
    setattr(runtime, "haunting_service", haunting_service)
    setattr(runtime, "haunting_tools", haunting_tools)
    setattr(runtime, "haunting_settings_engine", settings_engine)
    setattr(runtime, "planning_reconciler", reconciler)
    return runtime


def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


async def initialize_runtime() -> SingleThreadedAgentRuntime:
    """Initialize the runtime with all agents, reusing the singleton instance."""
    global _runtime
    if _runtime:
        return _runtime

    async with _runtime_lock:
        if _runtime:
            return _runtime
        _runtime = await _create_runtime()
        return _runtime


# in this file we register the agents

# @dataclass
# class Message:
#     content: int


# @default_subscription
# class Modifier(RoutedAgent):
#     def __init__(self, modify_val: Callable[[int], int]) -> None:
#         super().__init__("A modifier agent.")
#         self._modify_val = modify_val

#     @message_handler
#     async def handle_message(self, message: Message, ctx: MessageContext) -> None:
#         val = self._modify_val(message.content)
#         print(f"{'-'*80}\nModifier:\nModified {message.content} to {val}")
#         await self.publish_message(Message(content=val), DefaultTopicId())  # type: ignore


# @default_subscription
# class Checker(RoutedAgent):
#     def __init__(self, run_until: Callable[[int], bool]) -> None:
#         super().__init__("A checker agent.")
#         self._run_until = run_until

#     @message_handler
#     async def handle_message(self, message: Message, ctx: MessageContext) -> None:
#         if not self._run_until(message.content):
#             print(f"{'-'*80}\nChecker:\n{message.content} passed the check, continue.")
#             await self.publish_message(Message(content=message.content), DefaultTopicId())
#         else:
#             print(f"{'-'*80}\nChecker:\n{message.content} failed the check, stopping.")

# # Create a local embedded runtime.


# await Schedular.register(
#     runtime,
#     "schedular",
#     lambda

# )

# # Register the modifier and checker agents by providing
# # their agent types, the factory functions for creating instance and subscriptions.
# await Modifier.register(
#     runtime,
#     "modifier",
#     # Modify the value by subtracting 1
#     lambda: Modifier(modify_val=lambda x: x - 1),
# )

# await Checker.register(
#     runtime,
#     "checker",
#     # Run until the value is less than or equal to 1
#     lambda: Checker(run_until=lambda x: x <= 1),
# )

# # Start the runtime and send a direct message to the checker.
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
