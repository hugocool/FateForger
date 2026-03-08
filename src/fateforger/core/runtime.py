import asyncio
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_core import (
    AgentId,
    SingleThreadedAgentRuntime,
)
from autogen_core.tool_agent import ToolAgent
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Import agents
from fateforger.agents.admonisher.agent import AdmonisherAgent
from fateforger.agents.receptionist import HandoffBase, ReceptionistAgent
from fateforger.agents.revisor.agent import RevisorAgent
from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.tasks import TasksAgent
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.core.config import settings
from fateforger.haunt.agents import HauntingAgent, UserChannelAgent
from fateforger.haunt.delivery import deliver_user_facing
from fateforger.haunt.event_draft_store import (
    SqlAlchemyEventDraftStore,
    ensure_event_draft_schema,
)
from fateforger.haunt.intervention import HauntingInterventionHandler
from fateforger.haunt.messages import UserFacingMessage
from fateforger.haunt.orchestrator import HauntOrchestrator
from fateforger.haunt.planning_guardian import PlanningGuardian
from fateforger.haunt.planning_session_store import (
    SqlAlchemyPlanningSessionStore,
    ensure_planning_session_schema,
)
from fateforger.haunt.planning_store import (
    SqlAlchemyPlanningAnchorStore,
    ensure_planning_anchor_schema,
)
from fateforger.haunt.reconcile import (
    McpCalendarClient,
    PlanningReconciler,
    PlanningReminder,
)
from fateforger.haunt.service import HauntingService
from fateforger.haunt.settings_store import (
    SqlAlchemyAdmonishmentSettingsStore,
    ensure_admonishment_settings_schema,
)
from fateforger.haunt.tools import build_haunting_tools

USER_CHANNEL_AGENT_TYPE = "user_channel"
HAUNTING_AGENT_TYPE = "haunting_agent"
HAUNTING_AGENT_KEY = "default"
HAUNTING_TOOL_AGENT_TYPE = "haunter_tools"

logger = logging.getLogger(__name__)

_runtime: SingleThreadedAgentRuntime | None = None
_runtime_lock = asyncio.Lock()


@dataclass(frozen=True)
class _RuntimeGitIdentity:
    branch: str
    commit: str
    tag: str
    dirty: bool


@dataclass(frozen=True)
class _McpStartupServer:
    name: str
    url: str
    headers: dict[str, str] | None = None
    timeout_s: float = 5.0
    optional: bool = False


@dataclass(frozen=True)
class _McpStartupProbeResult:
    server: _McpStartupServer
    ok: bool
    tool_count: int
    error: str | None = None


def _repo_root_for_runtime() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_git_command(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=_repo_root_for_runtime(),
        check=True,
        capture_output=True,
        text=True,
    )
    return (result.stdout or "").strip()


def _resolve_runtime_git_identity() -> _RuntimeGitIdentity:
    branch = "unknown"
    commit = "unknown"
    tag = "none"
    dirty = False

    try:
        value = _run_git_command("rev-parse", "--abbrev-ref", "HEAD")
        if value:
            branch = value
    except Exception:
        pass

    try:
        value = _run_git_command("rev-parse", "--short", "HEAD")
        if value:
            commit = value
    except Exception:
        pass

    try:
        value = _run_git_command("describe", "--tags", "--exact-match")
        if value:
            tag = value
    except Exception:
        tag = "none"

    try:
        dirty = bool(_run_git_command("status", "--porcelain"))
    except Exception:
        dirty = False

    return _RuntimeGitIdentity(
        branch=branch,
        commit=commit,
        tag=tag,
        dirty=dirty,
    )


def _runtime_mcp_servers() -> list[_McpStartupServer]:
    from fateforger.tools.notion_mcp import get_notion_mcp_headers, get_notion_mcp_url
    from fateforger.tools.ticktick_mcp import get_ticktick_mcp_url

    return [
        _McpStartupServer(
            name="calendar-mcp",
            url=settings.mcp_calendar_server_url.strip(),
            timeout_s=5.0,
        ),
        _McpStartupServer(
            name="notion-mcp",
            url=get_notion_mcp_url().strip(),
            headers=get_notion_mcp_headers(),
            timeout_s=5.0,
            optional=True,
        ),
        _McpStartupServer(
            name="ticktick-mcp",
            url=get_ticktick_mcp_url().strip(),
            timeout_s=5.0,
            optional=True,
        ),
    ]


async def _discover_mcp_tools(
    *,
    url: str,
    headers: dict[str, str] | None,
    timeout_s: float,
) -> list:
    from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

    params_kwargs: dict[str, object] = {
        "url": url,
        "headers": headers,
        "timeout": timeout_s,
    }
    try:
        params = StreamableHttpServerParams(
            **params_kwargs,
            sse_read_timeout=timeout_s,
        )
    except TypeError:
        params = StreamableHttpServerParams(**params_kwargs)
    return await asyncio.wait_for(mcp_server_tools(params), timeout=timeout_s + 0.5)


async def _probe_runtime_mcp_server(
    server: _McpStartupServer,
) -> _McpStartupProbeResult:
    try:
        tools = await _discover_mcp_tools(
            url=server.url,
            headers=server.headers,
            timeout_s=server.timeout_s,
        )
    except TimeoutError:
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=0,
            error=f"timed out after {server.timeout_s:.1f}s during tool discovery",
        )
    except Exception as exc:
        message = str(exc).strip() or repr(exc)
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=0,
            error=f"{type(exc).__name__}: {message}",
        )
    if not tools:
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=0,
            error="server returned no tools",
        )
    return _McpStartupProbeResult(
        server=server,
        ok=True,
        tool_count=len(tools),
    )


async def _assert_mcp_servers_available() -> None:
    probes = _runtime_mcp_servers()
    results = await asyncio.gather(
        *(_probe_runtime_mcp_server(server) for server in probes)
    )
    required_failures = [r for r in results if not r.ok and not r.server.optional]
    optional_failures = [r for r in results if not r.ok and r.server.optional]
    for result in optional_failures:
        logger.warning(
            "Optional MCP server unavailable (skipping): %s [%s] -> %s",
            result.server.name,
            result.server.url,
            result.error,
        )
    if required_failures:
        details = "; ".join(
            f"{result.server.name} [{result.server.url}] -> {result.error}"
            for result in required_failures
        )
        raise RuntimeError(
            "MCP startup dependency check failed. "
            f"Resolve unavailable servers before starting the app. {details}"
        )
    ok_results = [r for r in results if r.ok]
    summary = ", ".join(
        f"{result.server.name}:{result.tool_count}" for result in ok_results
    )
    logger.info("MCP startup dependency checks passed (%s)", summary)


def _graphiti_backend_required() -> bool:
    """Return whether any configured runtime path requires Graphiti availability."""
    timeboxing_backend = str(
        getattr(settings, "timeboxing_memory_backend", "") or ""
    ).strip().lower()
    tasks_backend = str(
        getattr(settings, "tasks_defaults_memory_backend", "") or ""
    ).strip().lower()
    if tasks_backend == "inherit_timeboxing":
        tasks_backend = timeboxing_backend
    return timeboxing_backend == "graphiti" or tasks_backend == "graphiti"


async def _assert_graphiti_runtime_available() -> None:
    """Fail fast when Graphiti is configured but runtime dependencies are unavailable."""
    if not _graphiti_backend_required():
        return
    try:
        from fateforger.agents.timeboxing.durable_constraint_store import (
            build_durable_constraint_store,
        )
        from fateforger.agents.timeboxing.graphiti_constraint_memory import (
            build_graphiti_client_from_settings,
        )

        user_id = (
            str(getattr(settings, "graphiti_user_id", "") or "").strip()
            or "timeboxing"
        )
        client = build_graphiti_client_from_settings(user_id=user_id)
        store = build_durable_constraint_store(client)
        info = await store.get_store_info()
        await store.query_constraints(filters={}, limit=1)
    except Exception as exc:
        raise RuntimeError(
            "Graphiti startup dependency check failed. "
            "Resolve Graphiti runtime configuration before starting the app. "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    logger.info(
        "Graphiti startup dependency check passed (backend=%s, is_cloud=%s)",
        str(info.get("backend") or "unknown"),
        bool(info.get("is_cloud")),
    )


async def _run_initial_planning_reconcile(
    *,
    planning_guardian: PlanningGuardian,
    timeout_s: float = 15.0,
) -> bool:
    """Run startup planning reconcile without aborting runtime initialization."""
    try:
        await asyncio.wait_for(planning_guardian.reconcile_all(), timeout=timeout_s)
        logger.info("Initial planning reconcile completed successfully")
        return True
    except TimeoutError:
        logger.warning(
            "Initial planning reconcile timed out after %.1fs; continuing startup.",
            timeout_s,
        )
        return False
    except Exception:
        logger.exception("Initial planning reconcile failed; continuing startup.")
        return False


def _create_scheduler(database_url: str | None) -> AsyncIOScheduler:
    """Create scheduler with in-memory jobstore.

    Jobs are re-scheduled on startup via reconcile_all(), so persistence
    is not required. This avoids pickle issues with instance methods.
    """
    # Note: We don't use SQLAlchemy jobstore because instance methods
    # referencing the scheduler can't be pickled. Instead, we rely on
    # reconcile_all() being called on every startup to re-schedule jobs.
    scheduler = AsyncIOScheduler()
    logger.info("Scheduler initialized (jobs re-scheduled on startup)")
    return scheduler


async def _create_runtime() -> SingleThreadedAgentRuntime:
    """Create and start the runtime instance."""
    git_identity = _resolve_runtime_git_identity()
    logger.info(
        "Runtime git identity branch=%s commit=%s tag=%s dirty=%s",
        git_identity.branch,
        git_identity.commit,
        git_identity.tag,
        git_identity.dirty,
    )
    await _assert_mcp_servers_available()
    await _assert_graphiti_runtime_available()
    scheduler = _create_scheduler(settings.database_url)
    scheduler.start()

    async_url = _coerce_async_database_url(settings.database_url)
    settings_engine = create_async_engine(async_url)
    await ensure_admonishment_settings_schema(settings_engine)
    sessionmaker = async_sessionmaker(settings_engine, expire_on_commit=False)
    settings_store = SqlAlchemyAdmonishmentSettingsStore(sessionmaker)
    await ensure_planning_anchor_schema(settings_engine)
    planning_anchor_store = SqlAlchemyPlanningAnchorStore(sessionmaker)
    await ensure_planning_session_schema(settings_engine)
    planning_session_store = SqlAlchemyPlanningSessionStore(sessionmaker)
    await ensure_event_draft_schema(settings_engine)
    event_draft_store = SqlAlchemyEventDraftStore(sessionmaker)

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
        lambda: UserChannelAgent(USER_CHANNEL_AGENT_TYPE, deliver=deliver_user_facing),
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

    calendar_client = McpCalendarClient(server_url=settings.mcp_calendar_server_url)

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
        scheduler,
        calendar_client=calendar_client,
        dispatcher=dispatch_planning,
        planning_session_store=planning_session_store,
    )

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
    await RevisorAgent.register(
        runtime,
        "revisor_agent",
        lambda: RevisorAgent(
            "revisor_agent",
            allowed_handoffs=[
                HandoffBase(
                    target="tasks_agent",
                    description="Task triage and sprint execution agent (ticket search/filter, relation linking, Notion sprint page patching).",
                ),
            ],
        ),
    )
    await TasksAgent.register(
        runtime,
        "tasks_agent",
        lambda: TasksAgent("tasks_agent"),
    )
    await AdmonisherAgent.register(
        runtime,
        "admonisher_agent",
        lambda: AdmonisherAgent(
            "admonisher_agent",
            allowed_handoffs=[
                HandoffBase(
                    target="timeboxing_agent",
                    description="Timeboxing day planner that proposes a concrete schedule and iterates on it.",
                ),
                HandoffBase(
                    target="planner_agent",
                    description="Calendar planning and scheduling agent.",
                ),
                HandoffBase(
                    target="tasks_agent",
                    description="Task triage and sprint execution agent (ticket search/filter, relation linking, Notion sprint page patching).",
                ),
            ],
        ),
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
                HandoffBase(
                    target="revisor_agent",
                    description="Strategic review agent for weekly reviews, long-term project management and system optimization.",
                ),
                HandoffBase(
                    target="tasks_agent",
                    description="Task triage and execution agent.",
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
    setattr(runtime, "planning_anchor_store", planning_anchor_store)
    setattr(runtime, "planning_session_store", planning_session_store)
    setattr(runtime, "event_draft_store", event_draft_store)
    planning_guardian = PlanningGuardian(
        scheduler,
        anchor_store=planning_anchor_store,
        reconciler=reconciler,
    )
    planning_guardian.schedule_daily()
    # Kick off reconcile on startup so nudges are scheduled immediately.
    # This is critical since we use in-memory scheduler (jobs lost on restart).
    await _run_initial_planning_reconcile(
        planning_guardian=planning_guardian,
        timeout_s=15.0,
    )

    setattr(runtime, "planning_guardian", planning_guardian)
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


async def shutdown_runtime() -> None:
    """Stop and release the singleton runtime and attached resources."""
    global _runtime
    async with _runtime_lock:
        runtime = _runtime
        if runtime is None:
            return
        _runtime = None

    await runtime.stop()
    await runtime.close()

    scheduler = getattr(getattr(runtime, "haunting_service", None), "_scheduler", None)
    scheduler.shutdown(wait=False)

    settings_engine = getattr(runtime, "haunting_settings_engine", None)
    await settings_engine.dispose()

    planning_reconciler = getattr(runtime, "planning_reconciler", None)
    calendar_client = getattr(planning_reconciler, "calendar_client", None)
    await calendar_client.close()


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
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
# runtime.start()
# await runtime.send_message(Message(10), AgentId("checker", "default"))
# await runtime.stop_when_idle()
