import asyncio
import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from autogen_core import (
    AgentId,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    SingleThreadedAgentRuntime,
    default_subscription,
    message_handler,
)
from autogen_core.models import ChatCompletionClient
from autogen_core.tool_agent import ToolAgent
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Import agents
from fateforger.agents.admonisher.agent import AdmonisherAgent
from fateforger.agents.receptionist import HandoffBase, ReceptionistAgent
from fateforger.agents.revisor.agent import RevisorAgent
from fateforger.agents.schedular.agent import PlannerAgent
from fateforger.agents.tasks import TasksAgent
from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
from fateforger.agents.timeboxing.durable_constraint_store import (
    build_durable_constraint_store,
)
from fateforger.agents.timeboxing.kg_constraint_client import KGConstraintMemoryClient
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
    PlanningRuleConfig,
    PlanningSessionRule,
)
from fateforger.haunt.service import HauntingService
from fateforger.haunt.settings_store import (
    SqlAlchemyAdmonishmentSettingsStore,
    ensure_admonishment_settings_schema,
)
from fateforger.haunt.tools import build_haunting_tools
from fateforger.llm import build_autogen_chat_client
from fateforger.slack_bot.deepseek_timebox_planner import (
    ConstraintReader,
    DeepSeekTimeboxPlanner,
    HarnessBridgeRunner,
    UnavailableConstraintReader,
)
from fateforger.slack_bot.timeboxing_intents import TimeboxingIntentInterpreter
from fateforger.slack_bot.tmbx_client import TmbxClient
from tmbx.build_identity import BuildIdentity, current_build_identity
from tmbx.build_identity import describe as describe_build
from fateforger.slack_bot.timeboxing_session_store import (
    SqlAlchemyTimeboxingSessionRepository,
)

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
    required_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class _McpStartupProbeResult:
    server: _McpStartupServer
    ok: bool
    tool_count: int
    error: str | None = None
    # The exception behind ``error`` when discovery raised inside this
    # process rather than failing to reach the server. notion-mcp was
    # logged as "unavailable" on every startup while the real cause was a
    # TypeError in schema conversion (#257); a crash and an outage need
    # different log lines.
    internal_failure: BaseException | None = None


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

    servers = [
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

    graphiti_enabled = settings.timeboxing_memory_backend == "graphiti"
    if settings.tasks_defaults_memory_backend == "graphiti":
        graphiti_enabled = True
    elif (
        settings.tasks_defaults_memory_backend == "inherit_timeboxing"
        and settings.timeboxing_memory_backend == "graphiti"
    ):
        graphiti_enabled = True

    if graphiti_enabled:
        servers.append(
            _McpStartupServer(
                name="graphiti-mcp",
                url=settings.graphiti_mcp_server_url.strip(),
                timeout_s=5.0,
                required_tools=("add_memory", "get_episodes"),
            )
        )

    return servers


async def _discover_mcp_tools(
    *,
    url: str,
    headers: dict[str, str] | None,
    timeout_s: float,
) -> list:
    from autogen_ext.tools.mcp import StreamableHttpServerParams

    from fateforger.tools.mcp_tool_schemas import streamable_http_tools

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
    return await asyncio.wait_for(streamable_http_tools(params), timeout=timeout_s + 0.5)


def _is_connectivity_failure(exc: BaseException) -> bool:
    """Did discovery fail to reach the server, as opposed to crashing here?

    Transport errors arrive as ``OSError`` (``ConnectionError`` and
    friends), ``TimeoutError``, ``httpx`` errors, or the MCP layer's own
    ``McpError`` -- often wrapped in an ``ExceptionGroup`` by anyio's task
    groups, so the leaves are what get classified. Anything else
    (``TypeError``, ``KeyError``, a schema the converter cannot express)
    came from code running in this process and is a bug to fix, not a
    dependency to wait for.
    """

    if isinstance(exc, BaseExceptionGroup):
        return all(_is_connectivity_failure(leaf) for leaf in exc.exceptions)
    import httpx
    from mcp.shared.exceptions import McpError

    return isinstance(exc, (OSError, TimeoutError, httpx.HTTPError, McpError))


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
    except Exception as exc:  # noqa: BLE001 - optional dependency stays typed
        message = str(exc).strip() or repr(exc)
        if isinstance(exc, ExceptionGroup):
            sub_messages = [str(e) for e in exc.exceptions]
            message += " [" + ", ".join(sub_messages) + "]"
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=0,
            error=f"{type(exc).__name__}: {message}",
            internal_failure=None if _is_connectivity_failure(exc) else exc,
        )
    if not tools:
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=0,
            error="server returned no tools",
        )
    missing_required_tools = []
    available_tool_names = {
        str(getattr(tool, "name", "") or "").strip() for tool in tools
    }
    if server.required_tools:
        missing_required_tools = [
            name for name in server.required_tools if name not in available_tool_names
        ]
    if missing_required_tools:
        return _McpStartupProbeResult(
            server=server,
            ok=False,
            tool_count=len(tools),
            error=(
                "missing required tools: " + ", ".join(missing_required_tools)
            ),
        )
    return _McpStartupProbeResult(
        server=server,
        ok=True,
        tool_count=len(tools),
    )


def _log_memory_runtime_identity() -> None:
    graphiti_enabled = settings.timeboxing_memory_backend == "graphiti"
    if settings.tasks_defaults_memory_backend == "graphiti":
        graphiti_enabled = True
    elif (
        settings.tasks_defaults_memory_backend == "inherit_timeboxing"
        and settings.timeboxing_memory_backend == "graphiti"
    ):
        graphiti_enabled = True

    if not graphiti_enabled:
        logger.info(
            "Durable memory runtime identity timeboxing_backend=%s tasks_defaults_backend=%s",
            settings.timeboxing_memory_backend,
            settings.tasks_defaults_memory_backend,
        )
        return

    logger.info(
        "Durable memory runtime identity timeboxing_backend=%s tasks_defaults_backend=%s graphiti_store_backend=%s graphiti_mcp_server_url=%s graphiti_neo4j_uri=%s",
        settings.timeboxing_memory_backend,
        settings.tasks_defaults_memory_backend,
        settings.graphiti_store_backend,
        settings.graphiti_mcp_server_url,
        settings.graphiti_neo4j_uri,
    )


def tmbx_identity_verdict(
    local: BuildIdentity, remote: BuildIdentity | None
) -> tuple[int, str]:
    """What to log about the tmbx server's code against this bot's src/tmbx.

    Returns a logging level and the line. Pure, so the judgement can be pinned
    without a server.

    The comparison is over the source fingerprint, not the git sha. The sha is
    what a process happened to have checked out when it started; on a shared
    working copy with hundreds of uncommitted lines it says nothing about the
    bytes imported. Two shas with the same fingerprint are the same tmbx --
    HEAD moved, the sources did not -- and warning there would train the
    reader to skip the one warning that matters (#255).
    """
    local_line = describe_build(local)
    if remote is None:
        return (
            logging.WARNING,
            "tmbx build identity UNKNOWN: the server publishes none (it predates "
            f"#255); this bot's src/tmbx is {local_line}. Which src/tmbx answers "
            "plan_read cannot be told from here -- restart tmbx from this checkout "
            "before attributing its behaviour to the code on disk.",
        )
    remote_line = describe_build(remote)
    if remote.source_fingerprint != local.source_fingerprint:
        return (
            logging.WARNING,
            f"tmbx build identity MISMATCH: server runs {remote_line} (started "
            f"{remote.started_at} from {remote.package_root}); this bot's src/tmbx "
            f"is {local_line}. tmbx is serving code that is not the tree this bot "
            "imports -- restart it (`python scripts/demo.py start tmbx`) before "
            "attributing its behaviour to the code on disk.",
        )
    return (
        logging.INFO,
        f"tmbx build identity {remote_line} matches this bot's src/tmbx "
        f"(server started {remote.started_at})",
    )


async def _log_tmbx_build_identity(client: TmbxClient | None = None) -> None:
    """Log which src/tmbx the warm server runs, next to this bot's own.

    Sits beside `Runtime git identity` so the two shas that decide what a
    session exercised are on adjacent lines. A server that cannot be reached
    is a warning, not a startup failure: tmbx is not a required probe, and
    every call to it later fails loudly on its own.
    """
    local = current_build_identity()
    try:
        remote = await asyncio.wait_for(
            (client or TmbxClient(timeout=5.0)).build_identity(), timeout=5.5
        )
    except Exception as exc:  # noqa: BLE001 - startup diagnostics must not raise
        logger.warning(
            "tmbx build identity UNREACHABLE (%s): this bot's src/tmbx is %s; the "
            "server's could not be asked",
            type(exc).__name__,
            describe_build(local),
        )
        return
    level, message = tmbx_identity_verdict(local, remote)
    logger.log(level, message)


async def _assert_mcp_servers_available() -> None:
    probes = _runtime_mcp_servers()
    results = await asyncio.gather(
        *(_probe_runtime_mcp_server(server) for server in probes)
    )
    required_failures = [r for r in results if not r.ok and not r.server.optional]
    optional_failures = [r for r in results if not r.ok and r.server.optional]
    for result in optional_failures:
        if result.internal_failure is not None:
            logger.error(
                "Optional MCP server skipped because tool discovery raised in this "
                "process (a bug, not an outage): %s [%s] -> %s",
                result.server.name,
                result.server.url,
                result.error,
                exc_info=result.internal_failure,
            )
            continue
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


def _build_timeboxing_intent_interpreter() -> tuple[
    TimeboxingIntentInterpreter, ChatCompletionClient
]:
    """Build the runtime-owned schema interpreter and its shared client.

    No temperature pin. Two identical passes over the corpus showed no field
    disagreeing less at 0 and whole-record disagreement higher; a pin that
    looks like a guarantee invites skipping the resample (CLAUDE.md).
    """
    model_client = build_autogen_chat_client("timeboxing_agent")
    return TimeboxingIntentInterpreter(model_client), model_client


_CONSTRAINT_STORE_PROBE_TIMEOUT_SECONDS = 5.0


# The two tables that make a file the memory corpus rather than merely a
# readable database. These are identifiers the memory package minted, not user
# content. If either is ever renamed there the probe fails closed, which is the
# safe direction: a store we cannot recognise is reported unavailable.
_CONSTRAINT_STORE_CORPUS_TABLES = frozenset({"observations", "constraints"})


def _probe_constraint_store_readable(configured: str) -> None:
    """Open the configured store read-only and confirm it is the memory corpus.

    Raises on a missing path, a directory, an unreadable file, a file that is not
    a SQLite database, a database that is not the memory corpus, and one stamped
    newer than this build understands.

    Recognising the corpus is the load-bearing part. The migration ladder reads
    ``user_version = 0`` as "fresh store" and writes its whole schema in, so a
    merely-readable database — an empty file, or another of this project's own
    SQLite files sitting in the same directory — would be migrated into and then
    answer every planning query with an authoritative empty list. Checking the
    tables is what separates a legacy store the ladder may upgrade from a
    foreign one it must not touch.

    The probe issues no writes: it connects with ``mode=ro`` and only reads
    pragmas and ``sqlite_master``, so a file it rejects is left byte-identical.
    That claim is exact for the ``delete`` journal mode the memory store uses.
    A WAL database is the exception — SQLite updates a ``-shm`` sidecar merely
    to read one, and a cleanly-closed WAL database with no sidecars cannot be
    opened read-only at all, so it would be classified unavailable.
    """

    from memory.migrations import SCHEMA_VERSION

    path = Path(configured)
    if not path.is_file():
        raise OSError("configured constraint store is not a readable file")
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro",
        uri=True,
        timeout=_CONSTRAINT_STORE_PROBE_TIMEOUT_SECONDS,
    )
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise ValueError(
                "configured constraint store is stamped newer than this build"
            )
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        connection.close()
    if not _CONSTRAINT_STORE_CORPUS_TABLES <= present:
        raise ValueError("configured constraint store is not the memory corpus")


async def _build_timeboxing_constraint_store() -> ConstraintReader:
    """Build the runtime-owned, read-only durable planning context adapter.

    An existing but unusable path is classified here rather than surfacing as an
    authoritative empty context at planning time.
    """

    configured = str(getattr(settings, "memory_db_path", "") or "").strip()
    if not configured:
        logger.warning("timeboxing constraint store unavailable reason=not_configured")
        return UnavailableConstraintReader()
    try:
        # wait_for bounds startup, not the thread: to_thread cannot be
        # cancelled, so a probe stalled on a pathological filesystem keeps one
        # worker thread until it returns. Startup proceeds without it, which is
        # the property that matters here.
        await asyncio.wait_for(
            asyncio.to_thread(_probe_constraint_store_readable, configured),
            timeout=_CONSTRAINT_STORE_PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "timeboxing constraint store unavailable reason=unusable error_type=%s",
            type(exc).__name__,
        )
        return UnavailableConstraintReader()
    try:
        client = KGConstraintMemoryClient(configured)
        store = build_durable_constraint_store(client)
    except Exception as exc:
        logger.warning(
            "timeboxing constraint store unavailable error_type=%s",
            type(exc).__name__,
        )
        return UnavailableConstraintReader()
    if store is None:
        return UnavailableConstraintReader()
    return store


def _build_timeboxing_planner(
    constraint_store: ConstraintReader,
) -> tuple[DeepSeekTimeboxPlanner | None, str]:
    """Assemble the planner, or admit the host has not selected a calendar.

    Returning ``None`` keeps the adaptive route loud and inert. The alternative
    is a planner holding an invented calendar id, which reads somebody else's
    day and produces a plan that looks entirely reasonable for the wrong person.
    """

    calendar_id = str(getattr(settings, "timebox_calendar_id", "") or "").strip()
    if not calendar_id:
        logger.warning(
            "timeboxing planner unwired reason=no_calendar_selected; "
            "set TIMEBOX_CALENDAR_ID to the calendar the planner should read"
        )
        return None, ""
    planner = DeepSeekTimeboxPlanner(
        tmbx_client=TmbxClient(),
        constraint_reader=constraint_store,
        calendar_id=calendar_id,
        clock=lambda: datetime.now(UTC),
        harness_runner=HarnessBridgeRunner(),
    )
    logger.info("timeboxing planner wired calendar_selected=true")
    return planner, calendar_id


#: The checkout the profile falls back to when FF_FATEFORGER_ROOT is unset.
#: Restated from `infra/dsh/profile/cordis.patch.yml`, which cannot be imported
#: from Python -- and a drift between the two is exactly what this detects.
_DEFAULT_HARNESS_ROOT = "/Users/hugoevers/VScode-projects/admonish-1"


def harness_root_mismatch() -> str | None:
    """Say so when the stdio MCP servers will run different code than this bot.

    They are spawned by the harness with `cwd` and `PYTHONPATH` derived from
    FF_FATEFORGER_ROOT, defaulting to the main checkout. A bot started from a
    worktree without that variable therefore serves worktree code from its own
    process and main-checkout code from every stdio MCP server it spawns.

    That happened: two fixes to `planning_result_mcp.py` were committed, the
    bot was restarted four times, and neither ever ran. The unit tests passed
    against the worktree while the live server imported main's copy, so every
    signal available said the change was in.

    Returns the complaint, or None when the two agree.
    """

    package_root = Path(__file__).resolve().parents[3]
    configured = (os.environ.get("FF_FATEFORGER_ROOT") or "").strip()
    harness_root = Path(configured or _DEFAULT_HARNESS_ROOT).resolve()
    if harness_root == package_root:
        return None
    how = "FF_FATEFORGER_ROOT" if configured else "its unset default"
    return (
        f"stdio MCP servers will import {harness_root} ({how}) while this "
        f"process runs {package_root}; changes under src/fateforger that those "
        f"servers own will not take effect. Set FF_FATEFORGER_ROOT to "
        f"{package_root}."
    )


async def _create_runtime() -> SingleThreadedAgentRuntime:
    """Create and start the runtime instance."""
    git_identity = _resolve_runtime_git_identity()
    mismatch = harness_root_mismatch()
    if mismatch:
        logger.error("harness root mismatch: %s", mismatch)
    logger.info(
        "Runtime git identity branch=%s commit=%s tag=%s dirty=%s",
        git_identity.branch,
        git_identity.commit,
        git_identity.tag,
        git_identity.dirty,
    )
    _log_memory_runtime_identity()
    await asyncio.gather(
        _assert_mcp_servers_available(), _log_tmbx_build_identity()
    )
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
    timeboxing_session_store = SqlAlchemyTimeboxingSessionRepository(sessionmaker)
    timeboxing_constraint_store = await _build_timeboxing_constraint_store()
    timeboxing_planner, timeboxing_calendar_id = _build_timeboxing_planner(
        timeboxing_constraint_store
    )

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

    # The reconciler looks for the planning event on the same calendar the
    # timeboxing session writes to. Left at the rule's "primary" default it
    # evaluated a calendar the session never touched (#256).
    reconciler = PlanningReconciler(
        scheduler,
        calendar_client=calendar_client,
        dispatcher=dispatch_planning,
        planning_session_store=planning_session_store,
        rule=PlanningSessionRule(
            calendar_client=calendar_client,
            planning_session_store=planning_session_store,
            timeboxing_ledger=timeboxing_session_store,
            config=PlanningRuleConfig(
                calendar_id=timeboxing_calendar_id or "primary"
            ),
        ),
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
    timeboxing_intent_interpreter, timeboxing_intent_model_client = (
        _build_timeboxing_intent_interpreter()
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
    setattr(runtime, "timeboxing_session_store", timeboxing_session_store)
    setattr(runtime, "timeboxing_constraint_store", timeboxing_constraint_store)
    setattr(runtime, "timeboxing_planner", timeboxing_planner)
    setattr(runtime, "timeboxing_calendar_id", timeboxing_calendar_id)
    setattr(
        runtime,
        "timeboxing_intent_interpreter",
        timeboxing_intent_interpreter,
    )
    setattr(
        runtime,
        "timeboxing_intent_model_client",
        timeboxing_intent_model_client,
    )
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

    timeboxing_intent_model_client = getattr(
        runtime, "timeboxing_intent_model_client", None
    )
    if timeboxing_intent_model_client is not None:
        await timeboxing_intent_model_client.close()

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
