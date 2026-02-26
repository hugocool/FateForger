"""Coordinator agent that runs a stage-gated timeboxing flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Type, TypeVar
from zoneinfo import ZoneInfo

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.teams import GraphFlow
from autogen_core import (
    CancellationToken,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    message_handler,
)
from autogen_core.tools import FunctionTool
from dateutil import parser as date_parser
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.constraint_search_tool import (
    search_constraints,
)
from fateforger.agents.timeboxing.notion_constraint_extractor import (
    NotionConstraintExtractor,
)
from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.llm import build_autogen_chat_client
from fateforger.llm import assert_strict_tools_for_structured_output
from fateforger.llm.toon import toon_encode
from fateforger.slack_bot.constraint_review import (
    build_constraint_row_blocks,
    encode_metadata,
)
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.timeboxing_commit import build_timebox_commit_prompt_message
from fateforger.slack_bot.timeboxing_stage_actions import build_stage_actions_block
from fateforger.slack_bot.timeboxing_submit import (
    build_markdown_block,
    build_review_submit_actions_block,
    build_text_section_block,
    build_undo_submit_actions_block,
)
from fateforger.tools.constraint_mcp import get_constraint_mcp_tools
from fateforger.tools.ticktick_mcp import TickTickMcpClient, get_ticktick_mcp_url

from .actions import TimeboxAction
from .constants import TIMEBOXING_FALLBACK, TIMEBOXING_LIMITS, TIMEBOXING_TIMEOUTS
from .constraint_retriever import ConstraintRetriever
from .constraint_retriever import STARTUP_PREFETCH_TAG
from .contracts import (
    BlockPlan,
    CaptureInputsContext,
    CollectConstraintsContext,
    DailyOneThing,
    Immovable,
    SkeletonContext,
    SleepTarget,
    TaskCandidate,
    WorkWindow,
)
from .flow_graph import build_timeboxing_graphflow
from .mcp_clients import ConstraintMemoryClient, McpCalendarClient
from .messages import (
    StartTimeboxing,
    TimeboxingCancelSubmit,
    TimeboxingCommitDate,
    TimeboxingConfirmSubmit,
    TimeboxingFinalResult,
    TimeboxingStageAction,
    TimeboxingUndoSubmit,
    TimeboxingUpdate,
    TimeboxingUserReply,
)
from .nlu import (
    ConstraintInterpretation,
    PlannedDateResult,
    build_constraint_interpreter,
    build_planned_date_interpreter,
)
from .patching import TimeboxPatcher
from .planning_policy import QUALITY_RUBRIC_PROMPT
from .preferences import (
    Constraint,
    ConstraintBase,
    ConstraintBatch,
    ConstraintDayOfWeek,
    ConstraintNecessity,
    ConstraintScope,
    ConstraintSource,
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)
from .prompt_rendering import render_skeleton_draft_system_prompt
from .pydantic_parsing import parse_chat_content, parse_model_list, parse_model_optional
from .stage_gating import (
    CAPTURE_INPUTS_PROMPT,
    COLLECT_CONSTRAINTS_PROMPT,
    DECISION_PROMPT,
    REVIEW_COMMIT_PROMPT,
    TIMEBOX_SUMMARY_PROMPT,
    StageDecision,
    StageGateOutput,
    TimeboxingStage,
)
from .submitter import CalendarSubmitter
from .sync_engine import FFTB_PREFIX, SyncTransaction, gcal_response_to_tb_plan_with_identity
from .tb_models import TBEvent, TBPlan
from .timebox import Timebox, tb_plan_to_timebox, timebox_to_tb_plan
from .toon_views import (
    constraints_rows,
    immovables_rows,
    tasks_rows,
    timebox_events_rows,
)

logger = logging.getLogger(__name__)
TEnum = TypeVar("TEnum", bound=Enum)


class _ConstraintInterpretationPayload(BaseModel):
    """Input payload for constraint interpretation (multilingual, structured output)."""

    text: str
    is_initial: bool
    planned_date: str | None = None
    timezone: str | None = None
    stage_id: str | None = None


class _ConstraintOverviewView(BaseModel):
    """Typed Stage 1 overview payload rendered in presenter output."""

    durable_applies: list[str] = PydanticField(default_factory=list)
    day_specific_applies: list[str] = PydanticField(default_factory=list)
    unresolved: list[str] = PydanticField(default_factory=list)


class _ConstraintTemplateView(BaseModel):
    """Typed Stage 1 template-coverage payload rendered in presenter output."""

    filled_fields: list[str] = PydanticField(default_factory=list)
    useful_next_fields: list[str] = PydanticField(default_factory=list)
    notes: str | None = None


class RefineQualityFacts(BaseModel):
    """Typed Stage 4 quality payload generated by LLM summaries."""

    quality_level: int = PydanticField(ge=0, le=4)
    quality_label: Literal["Insufficient", "Minimal", "Okay", "Detailed", "Ultra"]
    missing_for_next: list[str] = PydanticField(default_factory=list)
    next_suggestion: str


@dataclass
class Session:
    """State container for an active timeboxing run."""

    thread_ts: str
    channel_id: str
    user_id: str
    last_user_message: str | None = None
    last_response: str | None = None
    start_message: str | None = None
    completed: bool = False
    committed: bool = False
    planned_date: str | None = None
    tz_name: str = "UTC"
    prefetched_immovables_by_date: Dict[str, List[Dict[str, str]]] = field(
        default_factory=dict
    )
    prefetched_remote_snapshots_by_date: Dict[str, TBPlan] = field(default_factory=dict)
    prefetched_event_id_maps_by_date: Dict[str, Dict[str, str]] = field(
        default_factory=dict
    )
    prefetched_remote_event_ids_by_date: Dict[str, List[str]] = field(
        default_factory=dict
    )
    active_constraints: List[Constraint] = field(default_factory=list)
    durable_constraints_by_stage: Dict[str, List[Constraint]] = field(
        default_factory=dict
    )
    durable_constraints_loaded_stages: set[str] = field(default_factory=set)
    durable_constraints_date: str | None = None
    pending_durable_constraints: bool = False
    pending_durable_stages: set[str] = field(default_factory=set)
    durable_constraints_failed_stages: Dict[str, str] = field(default_factory=dict)
    pending_calendar_prefetch: bool = False
    background_updates: List[str] = field(default_factory=list)
    timebox: Timebox | None = None
    pre_generated_skeleton: Timebox | None = None
    pre_generated_skeleton_plan: TBPlan | None = None
    pre_generated_skeleton_markdown: str | None = None
    pre_generated_skeleton_fingerprint: str | None = None
    pre_generated_skeleton_task: asyncio.Task | None = None
    pending_skeleton_pre_generation: bool = False
    skeleton_overview_markdown: str | None = None
    tb_plan: TBPlan | None = None
    base_snapshot: TBPlan | None = None
    event_id_map: Dict[str, str] = field(default_factory=dict)
    remote_event_ids_by_index: List[str] = field(default_factory=list)
    pending_submit: bool = False
    last_sync_transaction: SyncTransaction | None = None
    last_sync_event_id_map: Dict[str, str] | None = None
    pending_presenter_blocks: List[dict[str, Any]] | None = None
    stage: TimeboxingStage = TimeboxingStage.COLLECT_CONSTRAINTS
    frame_facts: Dict[str, Any] = field(default_factory=dict)
    input_facts: Dict[str, Any] = field(default_factory=dict)
    stage_ready: bool = False
    stage_missing: List[str] = field(default_factory=list)
    stage_question: str | None = None
    suppressed_durable_uids: set[str] = field(default_factory=set)
    collect_defaults_applied: List[str] = field(default_factory=list)
    last_quality_level: int | None = None
    last_quality_label: str | None = None
    last_quality_next_step: str | None = None
    constraints_prefetched: bool = False
    pending_constraint_extractions: set[str] = field(default_factory=set)
    last_extraction_task: asyncio.Task | None = None
    graphflow: GraphFlow | None = None
    thread_state: str | None = None
    session_key: str | None = None
    debug_log_path: str | None = None


@dataclass
class RefinePreflight:
    """Structured Stage 4 preflight outcome."""

    plan_issues: list[str] = field(default_factory=list)
    snapshot_issues: list[str] = field(default_factory=list)

    @property
    def has_plan_issues(self) -> bool:
        return bool(self.plan_issues)

    @property
    def has_snapshot_issues(self) -> bool:
        return bool(self.snapshot_issues)


class TimeboxingFlowAgent(RoutedAgent):
    """Entry point for the GraphFlow-driven timeboxing workflow."""

    def __init__(self, name: str) -> None:
        """Initialize the timeboxing agent and supporting clients."""
        super().__init__(description=name)
        self._sessions: Dict[str, Session] = {}
        self._model_client = build_autogen_chat_client(
            "timeboxing_agent", parallel_tool_calls=False
        )
        self._constraint_model_client = build_autogen_chat_client(
            "timeboxing_agent_background", parallel_tool_calls=False
        )
        self._draft_model_client = build_autogen_chat_client(
            "timeboxing_draft", parallel_tool_calls=False
        )
        self._calendar_client: McpCalendarClient | None = None
        self._constraint_memory_client: ConstraintMemoryClient | None = None
        self._ticktick_client: TickTickMcpClient | None = None
        self._constraint_store: ConstraintStore | None = None
        self._constraint_engine = None
        self._constraint_agent = self._build_constraint_agent()
        self._constraint_retriever = ConstraintRetriever()
        self._timebox_patcher = TimeboxPatcher()
        self._calendar_submitter = CalendarSubmitter()
        self._constraint_mcp_tools: list | None = None
        self._notion_extractor: NotionConstraintExtractor | None = None
        self._constraint_extractor_tool = None
        self._constraint_search_tool: FunctionTool | None = None
        self._durable_constraint_task_keys: set[str] = set()
        self._durable_constraint_semaphore = asyncio.Semaphore(
            TIMEBOXING_LIMITS.durable_upsert_concurrency
        )
        self._durable_constraint_prefetch_tasks: dict[str, asyncio.Task] = {}
        self._durable_constraint_prefetch_semaphore = asyncio.Semaphore(
            TIMEBOXING_LIMITS.durable_prefetch_concurrency
        )
        self._constraint_extraction_tasks: dict[str, asyncio.Task] = {}
        self._constraint_extraction_semaphore = asyncio.Semaphore(
            TIMEBOXING_LIMITS.constraint_extract_concurrency
        )
        self._constraint_interpreter_agent: AssistantAgent | None = None
        self._planning_date_interpreter_agent: AssistantAgent | None = None
        self._stage_agents: Dict[TimeboxingStage, AssistantAgent] = {}
        self._decision_agent: AssistantAgent | None = None
        self._summary_agent: AssistantAgent | None = None
        self._review_commit_agent: AssistantAgent | None = None
        self._session_debug_loggers: dict[str, logging.Logger] = {}

    # region helpers

    def _session_key(self, ctx: MessageContext, *, fallback: str | None = None) -> str:
        """Return a stable session key for the current routing context."""
        topic_key = ctx.topic_id.source if ctx.topic_id else None
        if topic_key:
            return topic_key
        if fallback:
            return fallback
        agent = ctx.sender if ctx.sender else None
        return agent.key if agent else "default"

    def _agent_source(self) -> str:
        """Return a safe message source identifier for TextMessage outputs."""
        try:
            return self.id.type
        except Exception:
            return "timeboxing_agent"

    def _default_tz_name(self) -> str:
        """Return the default timezone name for planning."""
        return getattr(settings, "planning_timezone", "") or "Europe/Amsterdam"

    def _default_planned_date(self, *, now: datetime, tz: ZoneInfo) -> str:
        """Return a deterministic default planned date.

        This is a fallback used only when the user did not specify a date.
        We avoid any "workday" logic here (no weekday/weekend assumptions).

        Rule:
        - Before 09:00 local time → use today.
        - At/after 09:00 local time → use tomorrow.
        """
        local_now = now.astimezone(tz)
        planned = local_now.date()
        if (local_now.hour, local_now.minute) >= (9, 0):
            planned = planned + timedelta(days=1)
        return planned.isoformat()

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        """Interpret common truthy env values."""
        if value is None:
            return False
        return value.strip().lower() in {"1", "true", "yes", "on", "debug"}

    def _session_debug_enabled(self) -> bool:
        """Return whether per-session debug log files should be written."""
        explicit = os.getenv("TIMEBOX_SESSION_DEBUG_LOG")
        if explicit is None:
            try:
                return self._is_truthy_env(
                    os.getenv("DEBUG") or os.getenv("FATEFORGER_DEBUG")
                ) or (sys.gettrace() is not None)
            except Exception:
                return self._is_truthy_env(
                    os.getenv("DEBUG") or os.getenv("FATEFORGER_DEBUG")
                )
        return self._is_truthy_env(explicit)

    @staticmethod
    def _safe_session_log_key(raw: str) -> str:
        """Convert a session key into a filename-safe token."""
        allowed = set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
        )
        normalized = "".join(ch if ch in allowed else "_" for ch in raw).strip("._")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized or "session"

    def _ensure_session_debug_logger(self, session: Session) -> logging.Logger | None:
        """Create or reuse a dedicated per-session logger."""
        if not self._session_debug_enabled():
            return None
        session_loggers = getattr(self, "_session_debug_loggers", None)
        if session_loggers is None:
            session_loggers = {}
            setattr(self, "_session_debug_loggers", session_loggers)
        session_key = session.session_key or f"{session.channel_id}:{session.thread_ts}"
        existing = session_loggers.get(session_key)
        if existing:
            return existing
        safe_key = self._safe_session_log_key(session_key)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_dir = Path(os.getenv("TIMEBOX_SESSION_LOG_DIR", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_dir / f"timeboxing_session_{ts}_{safe_key}_{os.getpid()}.log"
        logger_name = (
            f"fateforger.agents.timeboxing.session.{safe_key}.{ts}.{os.getpid()}"
        )
        session_logger = logging.getLogger(logger_name)
        session_logger.setLevel(logging.DEBUG)
        session_logger.propagate = False
        if not any(
            getattr(h, "_fftb_session_file", False) for h in session_logger.handlers
        ):
            handler = logging.FileHandler(file_path, encoding="utf-8")
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            )
            setattr(handler, "_fftb_session_file", True)
            session_logger.addHandler(handler)
        session.debug_log_path = str(file_path)
        session_loggers[session_key] = session_logger
        logger.info("Timeboxing session debug logging enabled: %s", file_path)
        return session_logger

    def _session_debug(self, session: Session, event: str, **payload: Any) -> None:
        """Write a JSON debug event to the session log when enabled."""
        session_logger = self._ensure_session_debug_logger(session)
        if not session_logger:
            return
        data: dict[str, Any] = {
            "event": event,
            "session_key": session.session_key,
            "thread_ts": session.thread_ts,
            "channel_id": session.channel_id,
            "planned_date": session.planned_date,
            "stage": session.stage.value if session.stage else None,
        }
        data.update(payload)
        session_logger.info(json.dumps(data, ensure_ascii=False, default=str))

    # TODO: remove all these if statements
    def _close_session_debug_logger(self, session_key: str) -> None:
        """Close and detach file handlers for a session logger."""
        session_loggers = getattr(self, "_session_debug_loggers", None)
        if session_loggers is None:
            return
        session_logger = session_loggers.pop(session_key, None)
        if not session_logger:
            return
        for handler in list(session_logger.handlers):
            session_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    # TODO: remove this fallback, it should just already be there
    def _ensure_graphflow(self, session: Session) -> GraphFlow:
        """Return the per-session GraphFlow instance, building it if needed."""
        if session.graphflow is not None:
            return session.graphflow
        session.graphflow = build_timeboxing_graphflow(
            orchestrator=self, session=session
        )
        return session.graphflow

    # TODO: remove this, we need to rely on the autogen framework to handle this
    async def _run_graph_turn(self, *, session: Session, user_text: str) -> TextMessage:
        """Run one GraphFlow turn and return the presenter text message."""
        self._session_debug(
            session,
            "graph_turn_start",
            user_text=(user_text or "")[:500],
        )
        flow = self._ensure_graphflow(session)
        presenter: TextMessage | None = None
        try:
            async for item in flow.run_stream(
                task=TextMessage(content=user_text, source="user")
            ):
                if isinstance(item, TextMessage) and item.source == "PresenterNode":
                    presenter = item
        except Exception as exc:
            self._session_debug(
                session,
                "graph_turn_error",
                error_type=type(exc).__name__,
                error=str(exc)[:2000],
            )
            raise
        content = (
            presenter.content
            if presenter
            else "Timed out waiting for tools/LLM. Please try again in a moment."
        )
        self._session_debug(
            session,
            "graph_turn_end",
            presenter_found=presenter is not None,
            output_preview=content[:500],
        )
        return TextMessage(content=content, source=self.id.type)

    # TODO: why is this even here?!?
    async def _ensure_planning_date_interpreter_agent(self) -> None:
        """Initialize the multilingual planned-date interpreter agent if needed."""
        if self._planning_date_interpreter_agent:
            return
        self._planning_date_interpreter_agent = build_planned_date_interpreter(
            model_client=self._model_client
        )

    # TODO: this should be build into the agent itself using the autogen message in that stage, not bolted on like this
    async def _interpret_planned_date(
        self, text: str, *, now: datetime, tz_name: str
    ) -> str:
        """Interpret the user's intended planning date using structured multilingual parsing."""
        tz = ZoneInfo(tz_name)
        if not (text or "").strip():
            return self._default_planned_date(now=now, tz=tz)
        await self._ensure_planning_date_interpreter_agent()
        assert self._planning_date_interpreter_agent is not None
        payload = {
            "text": text,
            "now_utc": now.isoformat(),
            "timezone": tz_name,
        }
        try:
            response = await with_timeout(
                "timeboxing:planning-date",
                self._planning_date_interpreter_agent.on_messages(
                    [
                        TextMessage(
                            content=json.dumps(payload, ensure_ascii=False),
                            source="user",
                        )
                    ],
                    CancellationToken(),
                ),
                timeout_s=TIMEBOXING_TIMEOUTS.planning_date_interpret_s,
            )
            result = parse_chat_content(PlannedDateResult, response)
            if result.planned_date:
                return result.planned_date
        except Exception:
            logger.debug(
                "Planned date interpretation failed; using default.", exc_info=True
            )
        return self._default_planned_date(now=now, tz=tz)

    # TODO:  this should be handled by the mcpworkbench, not a re-implementation
    def _ensure_calendar_client(self) -> McpCalendarClient | None:
        """Return the calendar MCP client, initializing it if needed."""
        if self._calendar_client:
            return self._calendar_client
        server_url = settings.mcp_calendar_server_url.strip()
        if not server_url:
            return None
        try:
            self._calendar_client = McpCalendarClient(
                server_url=server_url,
                timeout=float(
                    getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)
                ),
            )
        except Exception:
            logger.error("Failed to initialize MCP calendar client", exc_info=True)
            return None
        return self._calendar_client

    # TODO:  this should be handled by the mcpworkbench, not a re-implementation
    def _ensure_ticktick_client(self) -> TickTickMcpClient | None:
        """Return the TickTick MCP client, initializing it if needed."""
        if self._ticktick_client:
            return self._ticktick_client
        server_url = get_ticktick_mcp_url()
        if not server_url:
            return None
        try:
            self._ticktick_client = TickTickMcpClient(
                server_url=server_url,
                timeout=float(
                    getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)
                ),
            )
        except Exception:
            logger.error("Failed to initialize TickTick MCP client", exc_info=True)
            return None
        return self._ticktick_client

    # TODO:  this should be handled by the mcpworkbench, not a re-implementation
    def _ensure_constraint_memory_client(self) -> ConstraintMemoryClient | None:
        """Return the constraint-memory MCP client, initializing it if needed."""
        if self._constraint_memory_client:
            return self._constraint_memory_client
        if not settings.notion_timeboxing_parent_page_id:
            return None
        try:
            timeout = float(
                getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)
            )
            # Constraint-memory Notion queries often traverse multiple relations; allow
            # a slightly longer read timeout than generic MCP discovery.
            timeout = max(timeout, TIMEBOXING_TIMEOUTS.durable_prefetch_wait_s)
            self._constraint_memory_client = ConstraintMemoryClient(timeout=timeout)
        except Exception:
            logger.error(
                "Failed to initialize constraint memory MCP client", exc_info=True
            )
            return None
        return self._constraint_memory_client

    # TODO: thia should be a tool, not a bolted on method
    async def _fetch_durable_constraints(
        self, session: Session, *, stage: TimeboxingStage
    ) -> List[Constraint]:
        """Fetch durable constraints for a stage from Notion via the MCP server."""
        client = self._ensure_constraint_memory_client()
        if not client:
            return []
        try:
            planned_day = date.fromisoformat(
                session.planned_date or datetime.utcnow().date().isoformat()
            )
        except Exception:
            planned_day = datetime.utcnow().date()

        work_window = parse_model_optional(
            WorkWindow, session.frame_facts.get("work_window")
        )
        sleep_target = parse_model_optional(
            SleepTarget, session.frame_facts.get("sleep_target")
        )
        immovables = parse_model_list(Immovable, session.frame_facts.get("immovables"))
        block_plan = parse_model_optional(
            BlockPlan, session.input_facts.get("block_plan")
        )

        try:
            _plan, records = await self._constraint_retriever.retrieve(
                client=client,
                stage=stage,
                planned_day=planned_day,
                work_window=work_window,
                sleep_target=sleep_target,
                immovables=immovables,
                block_plan=block_plan,
                frame_facts=dict(session.frame_facts or {}),
            )
        except Exception as exc:
            # This is a background prefetch and we want failures to be visible in Slack.
            msg = f"Durable constraints failed to load: {type(exc).__name__}: {exc}"
            session.background_updates.append(msg)
            logger.error(msg, exc_info=True)
            raise
        return _constraints_from_memory(records, user_id=session.user_id)

    async def _ensure_constraint_interpreter_agent(self) -> None:
        """Initialize the structured constraint interpreter agent if needed."""
        if self._constraint_interpreter_agent:
            return
        self._constraint_interpreter_agent = build_constraint_interpreter(
            model_client=self._constraint_model_client
        )

    # TODO: this should be leveraging the autogen framework by having a constraints agent that has Constraint as it message type rather than a bolted on message..
    async def _interpret_constraints(
        self, session: Session, *, text: str, is_initial: bool
    ) -> ConstraintInterpretation:
        """Interpret constraints + scope from user text using a single structured LLM call."""
        await self._ensure_constraint_interpreter_agent()
        assert self._constraint_interpreter_agent is not None
        payload = _ConstraintInterpretationPayload(
            text=text,
            is_initial=is_initial,
            planned_date=session.planned_date,
            timezone=session.tz_name,
            stage_id=session.stage.value if session.stage else None,
        )
        response = await with_timeout(
            "timeboxing:constraint-interpret",
            self._constraint_interpreter_agent.on_messages(
                [TextMessage(content=payload.model_dump_json(), source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.constraint_interpret_s,
        )
        return parse_chat_content(ConstraintInterpretation, response)

    # TODO: this should be part of a tool, not bolted onto an agent
    def _constraint_task_key(self, session: Session, text: str) -> str:
        """Return a stable hash for deduping constraint extraction tasks."""
        payload = {
            "user_id": session.user_id,
            "channel_id": session.channel_id,
            "thread_ts": session.thread_ts,
            "text": text.strip(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[: TIMEBOXING_LIMITS.durable_task_key_len]

    # TODO: this should be part of a tool, not bolted onto an agent
    def _durable_prefetch_key(self, session: Session) -> str:
        """Return a stable key for deduping durable constraint prefetch tasks."""
        planned_date = session.planned_date or "unknown"
        return f"{session.user_id}:{session.thread_ts}:{planned_date}"

    def _durable_prefetch_stage_key(
        self, session: Session, *, stage: TimeboxingStage
    ) -> str:
        """Return a stable key for one stage-scoped durable prefetch task."""
        return f"{self._durable_prefetch_key(session)}:{stage.value}"

    @staticmethod
    def _durable_prefetch_stages(
        *, include_secondary: bool = True
    ) -> tuple[TimeboxingStage, ...]:
        """Return deterministic durable-prefetch stage groups."""
        if include_secondary:
            return (
                TimeboxingStage.COLLECT_CONSTRAINTS,
                TimeboxingStage.SKELETON,
                TimeboxingStage.REFINE,
            )
        return (TimeboxingStage.COLLECT_CONSTRAINTS,)

    @staticmethod
    def _is_collect_stage_loaded(session: Session) -> bool:
        """Return whether Stage 1 durable constraints are loaded for the current date."""
        planned_date = session.planned_date or ""
        return bool(
            session.durable_constraints_date == planned_date
            and TimeboxingStage.COLLECT_CONSTRAINTS.value
            in session.durable_constraints_loaded_stages
        )

    def _reset_durable_prefetch_state(self, session: Session) -> None:
        """Clear cached durable-prefetch state when planned date changes."""
        session.durable_constraints_by_stage = {}
        session.durable_constraints_loaded_stages = set()
        session.pending_durable_stages = set()
        session.pending_durable_constraints = False
        session.durable_constraints_failed_stages = {}
        session.durable_constraints_date = None

    def _queue_durable_prefetch_stage(
        self,
        *,
        session: Session,
        stage: TimeboxingStage,
        reason: str,
    ) -> asyncio.Task | None:
        """Queue one stage-scoped durable prefetch task with in-flight dedupe."""
        if not settings.notion_timeboxing_parent_page_id:
            return None
        planned_date = session.planned_date or ""
        if (
            session.durable_constraints_date == planned_date
            and stage.value in session.durable_constraints_loaded_stages
        ):
            return None
        task_key = self._durable_prefetch_stage_key(session, stage=stage)
        existing = self._durable_constraint_prefetch_tasks.get(task_key)
        if existing:
            return existing

        async def _background() -> None:
            """Fetch durable constraints for one stage."""
            acquired = False
            stage_label = stage.value
            session.pending_durable_stages.add(stage_label)
            session.pending_durable_constraints = True
            task_planned_date = planned_date
            try:
                await self._durable_constraint_prefetch_semaphore.acquire()
                acquired = True
                constraints = await self._fetch_durable_constraints(session, stage=stage)
                # Ignore stale results when the session date changed while fetching.
                if (session.planned_date or "") != task_planned_date:
                    return
                session.durable_constraints_by_stage[stage_label] = constraints
                session.durable_constraints_loaded_stages.add(stage_label)
                session.durable_constraints_failed_stages.pop(stage_label, None)
                session.durable_constraints_date = task_planned_date
                if constraints:
                    self._append_background_update_once(
                        session,
                        f"Loaded {len(constraints)} saved constraint(s) for {stage_label}.",
                    )
                await self._sync_durable_constraints_to_store(
                    session, constraints=constraints
                )
                await self._collect_constraints(session)
            except Exception as exc:
                if (session.planned_date or "") != task_planned_date:
                    return
                details = str(exc).strip()
                if len(details) > 240:
                    details = details[:237] + "..."
                msg = (
                    f"Durable constraint prefetch failed (stage={stage_label}, reason={reason})"
                )
                if details:
                    msg = f"{msg}: {details}"
                session.durable_constraints_failed_stages[stage_label] = msg
                self._append_background_update_once(session, msg)
                logger.error(msg, exc_info=True)
            finally:
                if acquired:
                    self._durable_constraint_prefetch_semaphore.release()
                session.pending_durable_stages.discard(stage_label)
                session.pending_durable_constraints = bool(session.pending_durable_stages)
                self._durable_constraint_prefetch_tasks.pop(task_key, None)

        task = asyncio.create_task(_background())
        self._durable_constraint_prefetch_tasks[task_key] = task
        return task

    def _queue_constraint_prefetch(self, session: Session) -> None:
        """Prefetch session-scoped constraints and durable constraints in background."""
        self._queue_durable_constraint_prefetch(
            session=session, reason="prefetch", include_secondary=True
        )
        if session.constraints_prefetched:
            return
        if not settings.database_url:
            return

        async def _background() -> None:
            """Fetch session constraints on a background task."""
            acquired = False
            try:
                await self._constraint_extraction_semaphore.acquire()
                acquired = True
                await self._ensure_constraint_store()
                await self._collect_constraints(session)
            except Exception:
                logger.debug("Constraint prefetch failed", exc_info=True)
            finally:
                if acquired:
                    self._constraint_extraction_semaphore.release()
                session.constraints_prefetched = True

        asyncio.create_task(_background())

    def _queue_durable_constraint_prefetch(
        self,
        *,
        session: Session,
        reason: str,
        include_secondary: bool = True,
    ) -> None:
        """Start background durable constraint prefetch if needed."""
        if not settings.notion_timeboxing_parent_page_id:
            return
        planned_date = session.planned_date or ""
        if session.durable_constraints_date and session.durable_constraints_date != planned_date:
            self._reset_durable_prefetch_state(session)

        stages = self._durable_prefetch_stages(include_secondary=include_secondary)
        for stage in stages:
            self._queue_durable_prefetch_stage(
                session=session,
                stage=stage,
                reason=reason,
            )

    def _queue_constraint_extraction(
        self,
        *,
        session: Session,
        text: str,
        reason: str,
        is_initial: bool,
    ) -> asyncio.Task | None:
        """Queue background extraction for session constraints and optional durable upsert."""
        if not (text or "").strip():
            return None
        task_key = self._constraint_task_key(session, text)
        if task_key in self._constraint_extraction_tasks:
            return None

        async def _background() -> ConstraintBatch | None:
            """Extract constraints from user text on a background task."""
            acquired = False
            try:
                await self._constraint_extraction_semaphore.acquire()
                acquired = True
                interpretation = await self._interpret_constraints(
                    session, text=text, is_initial=is_initial
                )
                if not interpretation.should_extract:
                    return None

                scope = ConstraintScope(interpretation.scope)
                # Persist extracted constraints to the session store (non-blocking UX).
                await self._ensure_constraint_store()
                constraints = list(interpretation.constraints or [])
                if constraints:
                    for constraint in constraints:
                        if constraint.scope is None:
                            constraint.scope = scope
                        if scope == ConstraintScope.DATESPAN:
                            if (
                                interpretation.start_date
                                and constraint.start_date is None
                            ):
                                # TODO(refactor): Parse dates via a Pydantic schema.
                                try:
                                    constraint.start_date = date.fromisoformat(
                                        interpretation.start_date
                                    )
                                except Exception:
                                    pass
                            if interpretation.end_date and constraint.end_date is None:
                                # TODO(refactor): Parse dates via a Pydantic schema.
                                try:
                                    constraint.end_date = date.fromisoformat(
                                        interpretation.end_date
                                    )
                                except Exception:
                                    pass
                    if self._constraint_store:
                        await self._constraint_store.add_constraints(
                            user_id=session.user_id,
                            channel_id=session.channel_id,
                            thread_ts=session.thread_ts,
                            constraints=constraints,
                        )
                        await self._collect_constraints(session)
                    if scope in (ConstraintScope.PROFILE, ConstraintScope.DATESPAN):
                        self._queue_durable_constraint_upsert(
                            session=session,
                            text=text,
                            reason=reason,
                            decision_scope=scope.value,
                            constraints=constraints,
                        )
                    return ConstraintBatch(constraints=constraints)
                return None
            except Exception:
                logger.debug(
                    "Constraint extraction failed (reason=%s task_key=%s)",
                    reason,
                    task_key,
                    exc_info=True,
                )
                return None
            finally:
                if acquired:
                    self._constraint_extraction_semaphore.release()
                session.pending_constraint_extractions.discard(task_key)
                self._constraint_extraction_tasks.pop(task_key, None)

        session.pending_constraint_extractions.add(task_key)
        task = asyncio.create_task(_background())
        self._constraint_extraction_tasks[task_key] = task
        return task

    def _queue_durable_constraint_upsert(
        self,
        *,
        session: Session,
        text: str,
        reason: str,
        decision_scope: str | None,
        constraints: list[ConstraintBase] | None = None,
    ) -> None:
        """Queue durable constraint upserts into Notion for profile/datespan rules."""
        if not settings.notion_timeboxing_parent_page_id:
            return
        if not (text or "").strip():
            return

        serialized_constraints = [
            constraint.model_dump(mode="json") for constraint in (constraints or [])
        ]
        payload = {
            "planned_date": session.planned_date or "",
            "timezone": session.tz_name or "UTC",
            "stage_id": session.stage.value,
            "user_utterance": text,
            "triggering_suggestion": reason,
            "impacted_event_types": [],
            "suggested_tags": [],
            "decision_scope": decision_scope,
            "constraints": serialized_constraints,
        }
        task_key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[: TIMEBOXING_LIMITS.durable_task_key_len]
        if task_key in self._durable_constraint_task_keys:
            return
        if (
            len(self._durable_constraint_task_keys)
            >= TIMEBOXING_LIMITS.durable_task_queue_limit
        ):
            return
        self._durable_constraint_task_keys.add(task_key)

        async def _background() -> None:
            """Upsert a durable constraint to Notion on a background task."""
            acquired = False
            try:
                await self._durable_constraint_semaphore.acquire()
                acquired = True
                persisted = await self._upsert_constraints_to_durable_store(
                    session=session,
                    constraints=constraints or [],
                    user_utterance=payload["user_utterance"],
                    triggering_suggestion=payload["triggering_suggestion"] or None,
                    decision_scope=payload["decision_scope"],
                )
                if persisted <= 0:
                    await self._ensure_constraint_mcp_tools()
                    if not self._notion_extractor:
                        self._append_background_update_once(
                            session,
                            "Could not initialize Notion durable constraint upsert.",
                        )
                        return
                    await with_timeout(
                        f"notion:constraint-upsert:{task_key}",
                        self._notion_extractor.extract_and_upsert_constraint(
                            planned_date=payload["planned_date"],
                            timezone=payload["timezone"],
                            stage_id=payload["stage_id"] or None,
                            user_utterance=payload["user_utterance"],
                            triggering_suggestion=payload["triggering_suggestion"] or None,
                            impacted_event_types=payload["impacted_event_types"],
                            suggested_tags=payload["suggested_tags"],
                            decision_scope=payload["decision_scope"],
                        ),
                        timeout_s=TIMEBOXING_TIMEOUTS.notion_upsert_s,
                    )
                    self._append_background_update_once(
                        session,
                        "Saved durable constraint to Notion from your latest message.",
                    )
                else:
                    self._append_background_update_once(
                        session,
                        f"Saved {persisted} durable constraint(s) to Notion.",
                    )
                self._reset_durable_prefetch_state(session)
                self._queue_durable_constraint_prefetch(
                    session=session, reason="post_upsert"
                )
            except Exception:
                logger.warning(
                    "Durable constraint upsert failed (task_key=%s)",
                    task_key,
                    exc_info=True,
                )
                self._append_background_update_once(
                    session,
                    "Failed to save durable constraint(s) to Notion; continuing with local session constraints.",
                )
            finally:
                if acquired:
                    self._durable_constraint_semaphore.release()
                self._durable_constraint_task_keys.discard(task_key)

        asyncio.create_task(_background())

    async def _upsert_constraints_to_durable_store(
        self,
        *,
        session: Session,
        constraints: list[ConstraintBase],
        user_utterance: str,
        triggering_suggestion: str | None,
        decision_scope: str | None,
    ) -> int:
        """Upsert extracted constraints deterministically into the durable MCP store."""
        if not constraints:
            return 0
        client = self._ensure_constraint_memory_client()
        if client is None:
            return 0
        persisted = 0
        for constraint in constraints:
            try:
                record = self._build_durable_constraint_record(
                    session=session,
                    constraint=constraint,
                    decision_scope=decision_scope,
                )
                event = {
                    "user_utterance": user_utterance,
                    "triggering_suggestion": triggering_suggestion,
                    "stage": session.stage.value if session.stage else None,
                    "event_types": record.get("applies_event_types") or [],
                    "decision_scope": decision_scope,
                    "action": "upsert",
                    "overrode_planner": False,
                    "extracted_type_id": None,
                }
                result = await client.upsert_constraint(record=record, event=event)
                if result.get("uid") or result.get("page_id"):
                    persisted += 1
            except Exception:
                logger.debug(
                    "Deterministic durable upsert failed for constraint=%s",
                    constraint.name,
                    exc_info=True,
                )
        return persisted

    def _build_durable_constraint_record(
        self,
        *,
        session: Session,
        constraint: ConstraintBase,
        decision_scope: str | None,
    ) -> dict[str, Any]:
        """Map a local extracted constraint to a Notion MCP upsert payload."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        selector = constraint.selector if isinstance(constraint.selector, dict) else {}
        scope = constraint.scope.value if constraint.scope else (decision_scope or "profile")
        uid = self._build_durable_constraint_uid(
            session=session,
            constraint=constraint,
            scope=scope,
        )
        rule_kind = self._resolve_rule_kind(hints=hints, selector=selector)
        scalar_params = self._extract_scalar_params(hints=hints, selector=selector)
        windows = self._extract_windows(hints=hints, selector=selector)
        topics = [
            str(tag).strip()
            for tag in (constraint.tags or [])
            if isinstance(tag, str) and str(tag).strip()
        ]
        if self._should_mark_startup_prefetch(constraint=constraint, rule_kind=rule_kind):
            topics.append(STARTUP_PREFETCH_TAG)
        topics = list(dict.fromkeys(topics))
        return {
            "constraint_record": {
                "name": constraint.name,
                "description": constraint.description,
                "necessity": constraint.necessity.value,
                "status": (
                    constraint.status.value
                    if constraint.status is not None
                    else ConstraintStatus.PROPOSED.value
                ),
                "source": (
                    constraint.source.value
                    if constraint.source is not None
                    else ConstraintSource.USER.value
                ),
                "confidence": constraint.confidence,
                "scope": scope,
                "applicability": {
                    "start_date": (
                        constraint.start_date.isoformat()
                        if constraint.start_date is not None
                        else None
                    ),
                    "end_date": (
                        constraint.end_date.isoformat()
                        if constraint.end_date is not None
                        else None
                    ),
                    "days_of_week": [day.value for day in (constraint.days_of_week or [])],
                    "timezone": constraint.timezone or session.tz_name,
                    "recurrence": constraint.recurrence,
                },
                "lifecycle": {
                    "uid": uid,
                    "supersedes_uids": list(constraint.supersedes or []),
                    "ttl_days": constraint.ttl_days,
                },
                "payload": {
                    "rule_kind": rule_kind,
                    "scalar_params": scalar_params,
                    "windows": windows,
                },
                "applies_stages": self._default_durable_applies_stages(),
                "applies_event_types": self._default_durable_event_types(),
                "topics": topics,
            }
        }

    def _build_durable_constraint_uid(
        self,
        *,
        session: Session,
        constraint: ConstraintBase,
        scope: str,
    ) -> str:
        """Build a stable idempotency key for durable upserts."""
        material = {
            "user_id": session.user_id,
            "scope": scope,
            "name": (constraint.name or "").strip().lower(),
            "description": (constraint.description or "").strip().lower(),
            "start_date": (
                constraint.start_date.isoformat()
                if constraint.start_date is not None
                else None
            ),
            "end_date": (
                constraint.end_date.isoformat() if constraint.end_date is not None else None
            ),
            "days_of_week": [day.value for day in (constraint.days_of_week or [])],
            "timezone": constraint.timezone or session.tz_name,
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return f"tb_{digest[:40]}"

    def _resolve_rule_kind(
        self,
        *,
        hints: dict[str, Any],
        selector: dict[str, Any],
    ) -> str | None:
        """Extract a supported durable rule_kind from hint/selector metadata."""
        allowed = {
            "prefer_window",
            "avoid_window",
            "fixed_bedtime",
            "min_sleep",
            "buffer",
            "sequencing",
            "capacity",
        }
        for source in (hints, selector):
            value = source.get("rule_kind")
            if isinstance(value, str) and value in allowed:
                return value
        return None

    def _extract_scalar_params(
        self,
        *,
        hints: dict[str, Any],
        selector: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract scalar payload fields from hint/selector metadata."""
        merged: dict[str, Any] = {}
        for source in (hints.get("scalar_params"), selector.get("scalar_params")):
            if isinstance(source, dict):
                merged.update(source)
        allowed = {"duration_min", "duration_max", "contiguity"}
        return {k: v for k, v in merged.items() if k in allowed}

    def _extract_windows(
        self,
        *,
        hints: dict[str, Any],
        selector: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Extract optional durable window definitions from metadata."""
        for source in (hints, selector):
            windows = source.get("windows")
            if isinstance(windows, list):
                valid_windows = []
                for item in windows:
                    if not isinstance(item, dict):
                        continue
                    kind = item.get("kind")
                    start = item.get("start_time_local")
                    end = item.get("end_time_local")
                    if (
                        isinstance(kind, str)
                        and isinstance(start, str)
                        and isinstance(end, str)
                    ):
                        valid_windows.append(
                            {
                                "kind": kind,
                                "start_time_local": start,
                                "end_time_local": end,
                            }
                        )
                return valid_windows
        return []

    def _default_durable_applies_stages(self) -> list[str]:
        """Return default stage routing for durable constraints."""
        return [
            TimeboxingStage.COLLECT_CONSTRAINTS.value,
            TimeboxingStage.CAPTURE_INPUTS.value,
            TimeboxingStage.SKELETON.value,
            TimeboxingStage.REFINE.value,
            TimeboxingStage.REVIEW_COMMIT.value,
        ]

    def _default_durable_event_types(self) -> list[str]:
        """Return default event-type routing for durable constraints."""
        return ["M", "C", "DW", "SW", "H", "R", "BU", "BG", "PR"]

    def _should_mark_startup_prefetch(
        self, *, constraint: ConstraintBase, rule_kind: str | None
    ) -> bool:
        """Return whether a durable constraint should be startup-prefetched in Stage 1."""
        rk = str(rule_kind or "").strip().lower()
        if rk in {"fixed_bedtime", "min_sleep"}:
            return True
        tags = [str(tag).strip().lower() for tag in (constraint.tags or []) if tag]
        if STARTUP_PREFETCH_TAG in tags:
            return True
        text = f"{constraint.name or ''} {constraint.description or ''} {' '.join(tags)}".lower()
        tokens = (
            "sleep",
            "bed",
            "wake",
            "work window",
            "work hours",
            "availability",
            "commute",
            "routine",
        )
        return any(token in text for token in tokens)

    async def _await_pending_constraint_extractions(
        self,
        session: Session,
        timeout_s: float = TIMEBOXING_TIMEOUTS.pending_constraints_wait_s,
    ) -> None:
        """Wait briefly for any pending constraint extraction tasks."""
        if not session.pending_constraint_extractions:
            return
        tasks = [
            self._constraint_extraction_tasks.get(key)
            for key in session.pending_constraint_extractions
        ]
        tasks = [task for task in tasks if task]
        if not tasks:
            return
        try:
            await asyncio.wait(tasks, timeout=timeout_s)
        except Exception:
            return

    async def _await_pending_durable_constraint_prefetch(
        self,
        session: Session,
        timeout_s: float = TIMEBOXING_TIMEOUTS.durable_prefetch_wait_s,
        *,
        stage: TimeboxingStage = TimeboxingStage.COLLECT_CONSTRAINTS,
    ) -> None:
        """Wait for one stage-scoped durable prefetch, capped with timeout."""
        self._queue_durable_constraint_prefetch(
            session=session,
            reason="await_prefetch",
            include_secondary=True,
        )
        task_key = self._durable_prefetch_stage_key(session, stage=stage)
        task = self._durable_constraint_prefetch_tasks.get(task_key)
        if not task:
            return
        self._append_background_update_once(session, "Loading saved constraints...")
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
        except asyncio.TimeoutError:
            msg = (
                f"Saved constraints timed out after {int(timeout_s)}s for {stage.value}. "
                "You can continue now and use Redo to retry after a moment."
            )
            session.durable_constraints_failed_stages[stage.value] = msg
            self._append_background_update_once(session, msg)
            logger.error(msg)
        except Exception:
            return

    async def _prefetch_calendar_immovables(
        self, session: Session, planned_date: str
    ) -> None:
        """Fetch calendar immovables + remote identity for the planned date."""
        if (
            planned_date in session.prefetched_immovables_by_date
            and planned_date in session.prefetched_remote_snapshots_by_date
        ):
            self._session_debug(
                session,
                "calendar_prefetch_skip_cached",
                planned_date=planned_date,
            )
            return
        client = self._ensure_calendar_client()
        if not client:
            self._append_background_update_once(
                session,
                "Calendar integration is unavailable right now; share fixed events manually.",
            )
            self._session_debug(
                session,
                "calendar_prefetch_no_client",
                planned_date=planned_date,
            )
            return
        session.pending_calendar_prefetch = True
        self._session_debug(
            session,
            "calendar_prefetch_start",
            planned_date=planned_date,
            timezone=session.tz_name,
        )
        try:
            tz = ZoneInfo(session.tz_name or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        # TODO(refactor): Validate planned_date with Pydantic before calendar prefetch.
        try:
            diagnostics: dict[str, Any] = {}
            snapshot = await client.list_day_snapshot(
                calendar_id="primary",
                day=date.fromisoformat(planned_date),
                tz=tz,
                diagnostics=diagnostics,
            )
            immovables = snapshot.immovables
            session.prefetched_immovables_by_date[planned_date] = immovables
            remote_plan, event_id_map, event_ids_by_index = (
                gcal_response_to_tb_plan_with_identity(
                    snapshot.response,
                    plan_date=date.fromisoformat(planned_date),
                    tz_name=session.tz_name or "UTC",
                )
            )
            session.prefetched_remote_snapshots_by_date[planned_date] = remote_plan
            session.prefetched_event_id_maps_by_date[planned_date] = dict(event_id_map)
            session.prefetched_remote_event_ids_by_date[planned_date] = list(
                event_ids_by_index
            )
            self._session_debug(
                session,
                "calendar_prefetch_success",
                planned_date=planned_date,
                immovable_count=len(immovables),
                remote_identity_count=len(event_ids_by_index),
                diagnostics=diagnostics,
            )
            if immovables:
                session.background_updates.append(
                    f"Loaded {len(immovables)} calendar immovable(s)."
                )
            if event_ids_by_index:
                session.background_updates.append(
                    f"Loaded {len(event_ids_by_index)} remote calendar event identity record(s)."
                )
        except Exception:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)
            self._session_debug(
                session,
                "calendar_prefetch_error",
                planned_date=planned_date,
                error="list_day_snapshot_failed",
            )
            self._append_background_update_once(
                session,
                "Couldn't load calendar events yet; share fixed anchors manually or click Redo.",
            )
        finally:
            session.pending_calendar_prefetch = False
            self._session_debug(
                session,
                "calendar_prefetch_end",
                planned_date=planned_date,
                pending=session.pending_calendar_prefetch,
            )

    async def _ensure_calendar_immovables(
        self, session: Session, *, timeout_s: float = 4.0
    ) -> None:
        """Ensure calendar immovables are fetched and applied to frame facts."""
        planned_date = session.planned_date
        if not planned_date:
            return
        if (
            session.prefetched_immovables_by_date.get(planned_date)
            and session.prefetched_remote_snapshots_by_date.get(planned_date)
        ):
            self._apply_prefetched_calendar_immovables(session)
            self._session_debug(
                session,
                "calendar_ensure_used_prefetch",
                planned_date=planned_date,
            )
            return
        prefetch_task = asyncio.create_task(
            self._prefetch_calendar_immovables(session, planned_date)
        )
        self._session_debug(
            session,
            "calendar_ensure_wait_start",
            planned_date=planned_date,
            timeout_s=timeout_s,
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(prefetch_task),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.debug("Calendar prefetch timed out for %s", planned_date)
            self._session_debug(
                session,
                "calendar_ensure_timeout",
                planned_date=planned_date,
                timeout_s=timeout_s,
            )
            self._append_background_update_once(
                session,
                "Calendar fetch timed out; share fixed anchors manually or click Redo.",
            )
        except Exception:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)
            if not prefetch_task.done():
                prefetch_task.cancel()
            self._session_debug(
                session,
                "calendar_ensure_error",
                planned_date=planned_date,
                error="prefetch_task_failed",
            )
            self._append_background_update_once(
                session,
                "Couldn't load calendar events yet; share fixed anchors manually or click Redo.",
            )
        self._apply_prefetched_calendar_immovables(session)
        self._session_debug(
            session,
            "calendar_ensure_done",
            planned_date=planned_date,
            immovable_count=len(
                session.prefetched_immovables_by_date.get(planned_date) or []
            ),
        )

    def _apply_prefetched_calendar_immovables(self, session: Session) -> None:
        """Apply prefetched immovables to frame facts when available."""
        planned_date = session.planned_date
        if not planned_date:
            return
        immovables = session.prefetched_immovables_by_date.get(planned_date) or []
        if not immovables:
            return
        existing = session.frame_facts.get("immovables")
        if isinstance(existing, list) and existing:
            return
        session.frame_facts["immovables"] = immovables

    def _build_commit_prompt_blocks(self, *, session: Session) -> SlackBlockMessage:
        """Build the Stage 0 commit prompt Slack blocks."""
        tz_name = session.tz_name or "UTC"
        planned_date = session.planned_date or ""
        meta = encode_metadata(
            {
                "channel_id": session.channel_id,
                "thread_ts": session.thread_ts,
                "user_id": session.user_id,
                "date": planned_date,
                "tz": tz_name,
            }
        )
        return build_timebox_commit_prompt_message(
            planned_date=planned_date,
            tz_name=tz_name,
            meta_value=meta,
        )

    async def _ensure_stage_agents(self) -> None:
        """Initialize stage-gating agents and their shared model clients."""
        if (
            self._stage_agents
            and self._decision_agent
            and self._summary_agent
            and self._review_commit_agent
        ):
            return

        # Build the constraint search tool for optional non-Stage-1 lookups.
        if self._constraint_search_tool is None and settings.notion_timeboxing_parent_page_id:
            self._constraint_search_tool = self._build_constraint_search_tool()

        optional_constraint_tools: list | None = (
            [self._constraint_search_tool] if self._constraint_search_tool else None
        )

        def build(
            name: str,
            prompt: str,
            out_type,
            *,
            tools: list[FunctionTool] | None = None,
            max_tool_iterations: int = 2,
        ) -> AssistantAgent:
            """Construct a stage helper agent with shared configuration."""
            assert_strict_tools_for_structured_output(
                tools=tools,
                output_content_type=out_type,
                agent_name=name,
            )
            return AssistantAgent(
                name=name,
                model_client=self._model_client,
                tools=tools,
                output_content_type=out_type,
                system_message=prompt,
                reflect_on_tool_use=False,
                max_tool_iterations=max_tool_iterations,
            )

        self._stage_agents = {
            TimeboxingStage.COLLECT_CONSTRAINTS: build(
                "StageCollectConstraints",
                COLLECT_CONSTRAINTS_PROMPT,
                StageGateOutput,
                tools=optional_constraint_tools,
                max_tool_iterations=2,
            ),
            TimeboxingStage.CAPTURE_INPUTS: build(
                "StageCaptureInputs",
                CAPTURE_INPUTS_PROMPT,
                StageGateOutput,
                tools=optional_constraint_tools,
                max_tool_iterations=3,
            ),
        }
        self._decision_agent = build("StageDecision", DECISION_PROMPT, StageDecision)
        self._summary_agent = build(
            "StageTimeboxSummary", TIMEBOX_SUMMARY_PROMPT, StageGateOutput
        )
        self._review_commit_agent = build(
            "StageReviewCommit", REVIEW_COMMIT_PROMPT, StageGateOutput
        )

    async def _run_stage_gate(
        self,
        *,
        stage: TimeboxingStage,
        user_message: str,
        context: dict[str, Any],
    ) -> StageGateOutput:
        """Run the stage-gating LLM for the current stage."""
        await self._ensure_stage_agents()
        agent = self._stage_agents.get(stage)
        if not agent:
            raise ValueError(f"Unsupported stage: {stage}")

        task = self._format_stage_gate_input(stage=stage, context=context)
        response = await with_timeout(
            f"timeboxing:stage:{stage.value}",
            agent.on_messages(
                [TextMessage(content=task, source="user")], CancellationToken()
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.stage_gate_s,
        )
        try:
            return parse_chat_content(StageGateOutput, response)
        except Exception as exc:
            error = (
                f"Stage gate parse failed for {stage.value}: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.error(error, exc_info=True)
            fallback_facts = dict(context.get("facts") or {})
            fallback_facts["_stage_gate_error"] = error
            return StageGateOutput(
                stage_id=stage,
                ready=False,
                summary=[
                    "I hit an internal response-format error while processing this stage.",
                    "I kept your known facts and can continue once you confirm or retry.",
                ],
                missing=["stage response parse failure"],
                question="Reply `Redo` to retry this stage, or provide any updates and continue.",
                facts=fallback_facts,
            )

    @staticmethod
    def _constraint_uid(constraint: ConstraintBase) -> str | None:
        """Return a durable constraint UID when present."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        uid = hints.get("uid")
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
        return None

    def _collect_stage_durable_constraints(
        self, session: Session, *, stage: TimeboxingStage
    ) -> list[Constraint]:
        """Return durable constraints for a stage, excluding session-suppressed UIDs."""
        durable = session.durable_constraints_by_stage.get(stage.value, [])
        out: list[Constraint] = []
        for constraint in durable or []:
            uid = self._constraint_uid(constraint)
            if uid and uid in session.suppressed_durable_uids:
                continue
            out.append(constraint)
        return out

    @staticmethod
    def _extract_clock_times(text: str) -> list[str]:
        """Extract HH:MM values in stable order from arbitrary text."""
        if not text:
            return []
        return [
            f"{int(hour):02d}:{minute}"
            for hour, minute in re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
        ]

    def _extract_collect_default_value(
        self, *, domain: str, constraint: Constraint
    ) -> dict[str, Any] | None:
        """Derive a deterministic default value for one collect-stage domain."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        text = f"{constraint.name or ''} {constraint.description or ''}".strip()
        times = self._extract_clock_times(text)

        hint_start = str(
            hints.get("start_time") or hints.get("bed_time") or hints.get("bedtime") or ""
        ).strip()
        hint_end = str(
            hints.get("end_time") or hints.get("wake_time") or hints.get("wake") or ""
        ).strip()

        if domain == "sleep_target":
            start = hint_start or (times[0] if len(times) > 0 else "")
            end = hint_end or (times[1] if len(times) > 1 else "")
            if not start or not end:
                return None
            try:
                start_dt = datetime.strptime(start, "%H:%M")
                end_dt = datetime.strptime(end, "%H:%M")
            except Exception:
                return {"start": start, "end": end, "hours": None}
            minutes = int((end_dt - start_dt).total_seconds() // 60)
            if minutes <= 0:
                minutes += 24 * 60
            hours = round(minutes / 60.0, 2)
            return {"start": start, "end": end, "hours": hours}

        if domain == "work_window":
            start = hint_start or (times[0] if len(times) > 0 else "")
            end = hint_end or (times[1] if len(times) > 1 else "")
            if not start or not end:
                return None
            return {"start": start, "end": end}

        return None

    def _classify_collect_default_domain(self, constraint: Constraint) -> str | None:
        """Classify collect-stage default domains from durable constraint metadata."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        rule_kind = str(hints.get("rule_kind") or "").strip().lower()
        tags = [str(tag).strip().lower() for tag in (constraint.tags or [])]
        text = (
            f"{constraint.name or ''} {constraint.description or ''} "
            f"{' '.join(tags)} {rule_kind}"
        ).lower()
        if rule_kind in {"fixed_bedtime", "min_sleep"} or any(
            token in text for token in ("sleep", "bed", "wake")
        ):
            return "sleep_target"
        if any(
            token in text
            for token in ("work window", "start work", "work hours", "availability")
        ):
            return "work_window"
        return None

    @staticmethod
    def _collect_default_priority_key(constraint: Constraint) -> tuple[int, int, int, str]:
        """Stable sorting key for selecting one default per domain."""
        status_rank = 0 if constraint.status == ConstraintStatus.LOCKED else 1
        necessity_rank = 0 if constraint.necessity == ConstraintNecessity.MUST else 1
        scope_rank = 0 if constraint.scope == ConstraintScope.PROFILE else 1
        name = (constraint.name or "").strip().lower()
        return (status_rank, necessity_rank, scope_rank, name)

    def _derive_collect_defaults_from_durable(
        self, constraints: list[Constraint]
    ) -> dict[str, Any]:
        """Derive deterministic collect-stage defaults from durable constraints."""
        by_domain: dict[str, list[Constraint]] = {"sleep_target": [], "work_window": []}
        for constraint in constraints or []:
            domain = self._classify_collect_default_domain(constraint)
            if domain in by_domain:
                by_domain[domain].append(constraint)

        domain_values: dict[str, dict[str, Any]] = {}
        domain_uids: dict[str, list[str]] = {}
        domain_lines: dict[str, str] = {}
        durable_applies: list[str] = []

        for domain, items in by_domain.items():
            if not items:
                continue
            ordered = sorted(items, key=self._collect_default_priority_key)
            chosen = ordered[0]
            value = self._extract_collect_default_value(domain=domain, constraint=chosen)
            if value:
                domain_values[domain] = value
            uids = [uid for uid in (self._constraint_uid(item) for item in ordered) if uid]
            if uids:
                domain_uids[domain] = uids
            line = (chosen.name or domain).strip()
            description = (chosen.description or "").strip()
            if description:
                line = f"{line} — {description}"
            domain_lines[domain] = line
            durable_applies.append(line)

        return {
            "domain_values": domain_values,
            "domain_uids": domain_uids,
            "domain_lines": domain_lines,
            "durable_applies": durable_applies,
        }

    @staticmethod
    def _merge_unique_lines(base: list[str], extra: list[str]) -> list[str]:
        """Return first-seen unique lines from two lists."""
        out: list[str] = []
        seen: set[str] = set()
        for raw in (base or []) + (extra or []):
            line = (raw or "").strip()
            if not line or line in seen:
                continue
            seen.add(line)
            out.append(line)
        return out

    def _apply_collect_defaults_to_facts(
        self,
        *,
        session: Session,
        facts: dict[str, Any],
        defaults: dict[str, Any],
    ) -> list[str]:
        """Apply deterministic collect defaults into stage facts when missing."""
        domain_values: dict[str, dict[str, Any]] = defaults.get("domain_values", {})
        domain_lines: dict[str, str] = defaults.get("domain_lines", {})
        applied_domains: list[str] = []

        if domain_values.get("sleep_target") and not parse_model_optional(
            SleepTarget, facts.get("sleep_target")
        ):
            facts["sleep_target"] = domain_values["sleep_target"]
            applied_domains.append("sleep_target")

        if domain_values.get("work_window") and not parse_model_optional(
            WorkWindow, facts.get("work_window")
        ):
            facts["work_window"] = domain_values["work_window"]
            applied_domains.append("work_window")

        overview = dict(facts.get("constraint_overview") or {})
        durable_existing = list(overview.get("durable_applies") or [])
        overview["durable_applies"] = self._merge_unique_lines(
            durable_existing, list(defaults.get("durable_applies") or [])
        )
        facts["constraint_overview"] = overview

        session.collect_defaults_applied = [
            domain_lines[d]
            for d in applied_domains
            if isinstance(domain_lines.get(d), str)
        ]
        facts["defaults_applied"] = list(session.collect_defaults_applied)
        return applied_domains

    @staticmethod
    def _facts_conflict_with_default(
        *, domain: str, value: dict[str, Any], default: dict[str, Any]
    ) -> bool:
        """Return whether a fact value conflicts with a derived default for a domain."""
        if domain == "sleep_target":
            cur = parse_model_optional(SleepTarget, value)
            ref = parse_model_optional(SleepTarget, default)
            if cur is None or ref is None:
                return False
            return (cur.start, cur.end, cur.hours) != (ref.start, ref.end, ref.hours)
        if domain == "work_window":
            cur = parse_model_optional(WorkWindow, value)
            ref = parse_model_optional(WorkWindow, default)
            if cur is None or ref is None:
                return False
            return (cur.start, cur.end) != (ref.start, ref.end)
        return False

    def _normalize_collect_constraints_gate(
        self,
        *,
        session: Session,
        gate: StageGateOutput,
        user_message: str,
    ) -> StageGateOutput:
        """Post-process Stage 1 gate output with deterministic defaults + suppression policy."""
        durable = self._collect_stage_durable_constraints(
            session, stage=TimeboxingStage.COLLECT_CONSTRAINTS
        )
        defaults = self._derive_collect_defaults_from_durable(durable)
        facts = dict(session.frame_facts or {})
        facts.update(gate.facts or {})
        self._apply_collect_defaults_to_facts(session=session, facts=facts, defaults=defaults)

        suppressed_domains: list[str] = []
        if user_message.strip():
            for domain, default_value in (defaults.get("domain_values") or {}).items():
                current_value = facts.get(domain)
                if not isinstance(current_value, dict):
                    continue
                if not self._facts_conflict_with_default(
                    domain=domain,
                    value=current_value,
                    default=default_value,
                ):
                    continue
                for uid in (defaults.get("domain_uids") or {}).get(domain, []):
                    if uid in session.suppressed_durable_uids:
                        continue
                    session.suppressed_durable_uids.add(uid)
                    suppressed_domains.append(domain)

        if suppressed_domains:
            durable = self._collect_stage_durable_constraints(
                session, stage=TimeboxingStage.COLLECT_CONSTRAINTS
            )
            defaults = self._derive_collect_defaults_from_durable(durable)
            self._apply_collect_defaults_to_facts(
                session=session,
                facts=facts,
                defaults=defaults,
            )
            unique_domains = sorted(set(suppressed_domains))
            override_line = (
                "Session override applied for "
                + ", ".join(unique_domains)
                + "; matching saved defaults were hidden for this session."
            )
            gate.summary = self._merge_unique_lines(list(gate.summary or []), [override_line])

        sleep_known = parse_model_optional(SleepTarget, facts.get("sleep_target")) is not None
        if sleep_known and gate.missing:
            gate.missing = [
                item
                for item in gate.missing
                if "sleep" not in item.lower() and "routine" not in item.lower()
            ]
        if not gate.missing:
            gate.ready = True

        defaults_applied = list(session.collect_defaults_applied or [])
        collect_stage = TimeboxingStage.COLLECT_CONSTRAINTS.value
        collect_loaded = collect_stage in session.durable_constraints_loaded_stages
        collect_pending = collect_stage in session.pending_durable_stages
        collect_failure = session.durable_constraints_failed_stages.get(collect_stage)

        if not collect_loaded:
            gate.summary = [
                line
                for line in list(gate.summary or [])
                if "no existing durable constraints found" not in line.lower()
            ]
            if collect_pending:
                gate.summary = self._merge_unique_lines(
                    gate.summary,
                    [
                        "Saved constraints are still loading; I have not confirmed durable defaults yet."
                    ],
                )
            elif collect_failure:
                gate.summary = self._merge_unique_lines(
                    gate.summary,
                    [f"Saved constraints could not be confirmed yet: {collect_failure}"],
                )
            else:
                gate.summary = self._merge_unique_lines(
                    gate.summary,
                    ["Saved constraints are not loaded yet; I will keep checking in the background."],
                )

        if gate.ready and defaults_applied:
            gate.summary = self._merge_unique_lines(
                list(gate.summary or []),
                [f"Using your saved defaults: {', '.join(defaults_applied)}."],
            )
            gate.question = (
                "Using your saved defaults. Reply to override for this session, or proceed."
            )

        if isinstance(facts.get("_stage_gate_error"), str):
            self._append_background_update_once(session, facts["_stage_gate_error"])

        gate.facts = facts
        return gate

    async def _refresh_collect_constraints_durable(
        self, session: Session, *, reason: str
    ) -> None:
        """Force a targeted Stage 1 durable refresh after new user hints."""
        if not settings.notion_timeboxing_parent_page_id:
            return
        stage = TimeboxingStage.COLLECT_CONSTRAINTS
        session.durable_constraints_loaded_stages.discard(stage.value)
        task = self._queue_durable_prefetch_stage(
            session=session,
            stage=stage,
            reason=reason,
        )
        if not task:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=TIMEBOXING_TIMEOUTS.durable_prefetch_wait_s,
            )
        except asyncio.TimeoutError:
            msg = (
                "Saved constraints refresh timed out while processing your latest "
                "Stage 1 input. Continue now or use Redo to retry."
            )
            session.durable_constraints_failed_stages[stage.value] = msg
            self._append_background_update_once(session, msg)
            logger.error(msg)

    def _format_stage_gate_input(
        self, *, stage: TimeboxingStage, context: dict[str, Any]
    ) -> str:
        """Format stage-gate input with TOON tables for list data."""
        user_message = str(context.get("user_message") or "")
        if stage == TimeboxingStage.COLLECT_CONSTRAINTS:
            facts = dict(context.get("facts") or {})
            immovables = parse_model_list(Immovable, context.get("immovables"))
            durable_constraints = list(context.get("durable_constraints") or [])
            facts_json = json.dumps(facts, ensure_ascii=False, sort_keys=True)
            immovables_toon = toon_encode(
                name="immovables",
                rows=immovables_rows(immovables),
                fields=["title", "start", "end"],
            )
            durable_toon = toon_encode(
                name="durable_constraints",
                rows=constraints_rows(durable_constraints),
                fields=[
                    "name",
                    "necessity",
                    "scope",
                    "status",
                    "source",
                    "description",
                ],
            )
            return (
                "The following lists are in TOON format: name[N]{keys}: defines the schema, and each line below is a record with values in that exact order.\n"
                f"user_message: {user_message}\n"
                f"facts_json: {facts_json}\n"
                f"{immovables_toon}\n"
                f"{durable_toon}\n"
            )

        if stage == TimeboxingStage.CAPTURE_INPUTS:
            frame_facts = dict(context.get("frame_facts") or {})
            input_facts = dict(context.get("input_facts") or {})
            tasks = parse_model_list(TaskCandidate, input_facts.get("tasks"))
            daily_one_thing = parse_model_optional(
                DailyOneThing, input_facts.get("daily_one_thing")
            )
            frame_facts_json = json.dumps(
                frame_facts, ensure_ascii=False, sort_keys=True
            )
            scrubbed_input = dict(input_facts)
            scrubbed_input.pop("tasks", None)
            scrubbed_input.pop("daily_one_thing", None)
            input_facts_json = json.dumps(
                scrubbed_input, ensure_ascii=False, sort_keys=True
            )
            tasks_toon = toon_encode(
                name="tasks",
                rows=tasks_rows(tasks),
                fields=["title", "block_count", "duration_min", "due", "importance"],
            )
            daily_toon = toon_encode(
                name="daily_one_thing",
                rows=(
                    [
                        {
                            "title": daily_one_thing.title,
                            "block_count": daily_one_thing.block_count or "",
                            "duration_min": daily_one_thing.duration_min or "",
                        }
                    ]
                    if daily_one_thing
                    else []
                ),
                fields=["title", "block_count", "duration_min"],
            )
            return (
                "The following lists are in TOON format: name[N]{keys}: defines the schema, and each line below is a record with values in that exact order.\n"
                f"user_message: {user_message}\n"
                f"frame_facts_json: {frame_facts_json}\n"
                f"input_facts_json: {input_facts_json}\n"
                f"{tasks_toon}\n"
                f"{daily_toon}\n"
            )

        return json.dumps(context, ensure_ascii=False, sort_keys=True)

    def _build_collect_constraints_context(
        self, session: Session, *, user_message: str
    ) -> dict[str, Any]:
        """Build the injected context payload for the CollectConstraints stage."""
        normalized = parse_model_list(Immovable, session.frame_facts.get("immovables"))
        durable = self._collect_stage_durable_constraints(
            session, stage=TimeboxingStage.COLLECT_CONSTRAINTS
        )
        facts = dict(session.frame_facts or {})
        defaults = self._derive_collect_defaults_from_durable(list(durable or []))
        self._apply_collect_defaults_to_facts(
            session=session,
            facts=facts,
            defaults=defaults,
        )
        return CollectConstraintsContext(
            user_message=user_message,
            facts=facts,
            immovables=normalized,
            durable_constraints=list(durable or []),
        ).model_dump(mode="json")

    def _build_capture_inputs_context(
        self, session: Session, *, user_message: str
    ) -> dict[str, Any]:
        """Build the injected context payload for the CaptureInputs stage."""
        return CaptureInputsContext(
            user_message=user_message,
            frame_facts=dict(session.frame_facts or {}),
            input_facts=dict(session.input_facts or {}),
        ).model_dump(mode="json")

    def _quality_snapshot_for_prompt(self, session: Session) -> dict[str, Any]:
        """Return previously captured quality facts for refine-stage prompt context."""
        if session.last_quality_level is None or not session.last_quality_label:
            return {}
        payload: dict[str, Any] = {
            "quality_level": session.last_quality_level,
            "quality_label": session.last_quality_label,
        }
        if session.last_quality_next_step:
            payload["next_suggestion"] = session.last_quality_next_step
        return payload

    async def _run_refine_quality_assessment(self, *, timebox: Timebox) -> RefineQualityFacts:
        """Ask a typed LLM helper to assess refine-stage quality facts."""
        events_toon = toon_encode(
            name="events",
            rows=timebox_events_rows(timebox.events or []),
            fields=["type", "summary", "ST", "ET", "DT", "AP", "location"],
        )
        quality_prompt = (
            "You assess refine-stage planning quality from a timeboxed schedule.\n"
            "Return STRICT JSON matching RefineQualityFacts.\n"
            f"{QUALITY_RUBRIC_PROMPT}\n"
            "Guidance:\n"
            "- Base the score on schedule structure and flow quality.\n"
            "- missing_for_next should be concrete and actionable.\n"
            "- next_suggestion should be one practical next refinement step.\n"
        )
        quality_agent = AssistantAgent(
            name="StageRefineQualityAssessor",
            model_client=self._model_client,
            tools=None,
            output_content_type=RefineQualityFacts,
            system_message=quality_prompt,
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )
        response = await with_timeout(
            "timeboxing:summary:RefineQuality",
            quality_agent.on_messages(
                [TextMessage(content=events_toon, source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.summary_s,
        )
        return parse_chat_content(RefineQualityFacts, response)

    async def _enrich_refine_quality_feedback(
        self,
        *,
        session: Session,
        gate: StageGateOutput,
        timebox: Timebox,
    ) -> StageGateOutput:
        """Ensure refine-stage gate contains typed quality facts and session carry state."""
        quality = parse_model_optional(RefineQualityFacts, gate.facts)
        if quality is None:
            quality = await self._run_refine_quality_assessment(timebox=timebox)
        gate.facts = quality.model_dump(mode="json")
        session.last_quality_level = quality.quality_level
        session.last_quality_label = quality.quality_label
        session.last_quality_next_step = quality.next_suggestion
        quality_line = f"Quality: {quality.quality_label} ({quality.quality_level}/4)."
        if quality_line not in gate.summary:
            gate.summary.append(quality_line)
        if quality.quality_level < 4:
            next_line = f"Next upgrade: {quality.next_suggestion}"
            if next_line not in gate.summary:
                gate.summary.append(next_line)
            gate.question = (
                f"{gate.question or 'Want another refine pass?'} "
                f"Next suggested step: {quality.next_suggestion}"
            )
        return gate

    async def _run_timebox_summary(
        self,
        *,
        stage: TimeboxingStage,
        timebox: Timebox,
        session: Session | None = None,
    ) -> StageGateOutput:
        """Generate a summary for a timebox draft via the summary agent."""
        await self._ensure_stage_agents()
        assert self._summary_agent is not None
        events_toon = toon_encode(
            name="events",
            rows=timebox_events_rows(timebox.events or []),
            fields=["type", "summary", "ST", "ET", "DT", "AP", "location"],
        )
        payload = f"stage_id: {stage.value}\n{events_toon}\n"
        response = await with_timeout(
            f"timeboxing:summary:{stage.value}",
            self._summary_agent.on_messages(
                [TextMessage(content=payload, source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.summary_s,
        )
        gate = parse_chat_content(StageGateOutput, response)
        if stage == TimeboxingStage.REFINE and session is not None:
            gate = await self._enrich_refine_quality_feedback(
                session=session,
                gate=gate,
                timebox=timebox,
            )
        return gate

    async def _run_review_commit(self, *, timebox: Timebox) -> StageGateOutput:
        """Generate the final review/commit response."""
        await self._ensure_stage_agents()
        assert self._review_commit_agent is not None
        events_toon = toon_encode(
            name="events",
            rows=timebox_events_rows(timebox.events or []),
            fields=["type", "summary", "ST", "ET", "DT", "AP", "location"],
        )
        payload = f"{events_toon}\n"
        response = await with_timeout(
            "timeboxing:review-commit",
            self._review_commit_agent.on_messages(
                [TextMessage(content=payload, source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.review_commit_s,
        )
        return parse_chat_content(StageGateOutput, response)

    async def _run_skeleton_draft(
        self, session: Session
    ) -> tuple[None, str, TBPlan | None]:
        """Draft a Stage 3 markdown overview and a TBPlan draft."""
        await self._ensure_stage_agents()
        context = await self._build_skeleton_context(session)
        try:
            markdown_overview = await self._run_skeleton_overview_markdown(
                context=context
            )
            session.skeleton_overview_markdown = markdown_overview
            seed_plan = self._build_skeleton_seed_plan(session)
            return None, markdown_overview, seed_plan
        except Exception as exc:
            logger.warning(
                "Skeleton overview draft failed; using deterministic fallback.",
                exc_info=True,
            )
            session.background_updates.append(
                "Skeleton overview draft failed; using deterministic fallback."
            )
            fallback_plan = self._build_skeleton_seed_plan(session)
            fallback_markdown = self._tb_plan_overview_markdown(fallback_plan)
            session.skeleton_overview_markdown = fallback_markdown
            return None, fallback_markdown, fallback_plan

    def _tb_plan_overview_markdown(self, plan: TBPlan) -> str:
        """Render concise Stage 3 markdown from TBPlan."""
        lines = ["## Day Overview"]
        try:
            resolved = plan.resolve_times()
        except Exception:
            resolved = []
        if not resolved:
            lines.append("- No blocks drafted yet.")
            return "\n".join(lines)

        sections: dict[str, list[str]] = {
            "Night": [],
            "Morning": [],
            "Midday": [],
            "Afternoon": [],
            "Evening": [],
        }
        for index, event in enumerate(resolved):
            title = str(event.get("n") or "Untitled")
            start_time = event.get("start_time")
            end_time = event.get("end_time")
            placement = plan.events[index].p.a if index < len(plan.events) else ""
            anchored = placement in {"fs", "fw"}
            if anchored and start_time is not None and end_time is not None:
                entry = (
                    f"- {start_time.isoformat(timespec='minutes')}-"
                    f"{end_time.isoformat(timespec='minutes')} **{title}**"
                )
            else:
                duration = self._coarse_duration_label(
                    start_time=start_time,
                    end_time=end_time,
                )
                entry = f"- **{title}** — {duration}" if duration else f"- **{title}**"

            if start_time is None:
                bucket = "Morning"
            else:
                hour = start_time.hour
                if hour < 6:
                    bucket = "Night"
                elif hour < 12:
                    bucket = "Morning"
                elif hour < 14:
                    bucket = "Midday"
                elif hour < 18:
                    bucket = "Afternoon"
                elif hour < 22:
                    bucket = "Evening"
                else:
                    bucket = "Night"
            sections[bucket].append(entry)

        for heading in ("Night", "Morning", "Midday", "Afternoon", "Evening"):
            entries = sections[heading]
            if not entries:
                continue
            lines.append(f"### {heading}")
            lines.extend(entries)
        return "\n".join(lines)

    def _coarse_duration_label(self, *, start_time: time | None, end_time: time | None) -> str:
        """Return a rough, glanceable duration label for flexible Stage 3 blocks."""
        if start_time is None or end_time is None:
            return ""
        start_dt = datetime.combine(date.today(), start_time)
        end_dt = datetime.combine(date.today(), end_time)
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        if minutes <= 0:
            return ""
        hours, remainder = divmod(minutes, 60)
        if hours and remainder:
            return f"~{hours}h{remainder}m"
        if hours:
            return f"~{hours}h"
        return f"~{minutes}m"

    def _patcher_context_payload(
        self,
        *,
        session: Session,
        stage: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build structured context injected into patcher requests."""
        frame = session.frame_facts or {}
        payload: dict[str, Any] = {
            "stage": stage,
            "planned_date": session.planned_date,
            "timezone": session.tz_name,
            "frame_facts": {
                "work_window": frame.get("work_window"),
                "sleep_target": frame.get("sleep_target"),
                "immovables": frame.get("immovables"),
            },
            "input_facts": session.input_facts or {},
        }
        if extra:
            payload["extra"] = extra
        return payload

    def _compose_patcher_message(
        self,
        *,
        base_message: str,
        session: Session,
        stage: str,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Attach structured planning context to patcher instructions."""
        if stage != TimeboxingStage.REFINE.value:
            raise ValueError(
                "Patcher messages are restricted to Stage 4 Refine. "
                f"Received stage={stage!r}."
            )
        context_json = json.dumps(
            self._patcher_context_payload(session=session, stage=stage, extra=extra),
            ensure_ascii=False,
            default=str,
            sort_keys=True,
        )
        return (
            f"{base_message.strip()}\n\n"
            "Planning context:\n"
            f"```json\n{context_json}\n```"
        )

    async def _run_skeleton_overview_markdown(self, *, context: SkeletonContext) -> str:
        """Generate the Stage 3 markdown overview for Slack rendering."""
        system_prompt = render_skeleton_draft_system_prompt(context=context)
        draft_agent = AssistantAgent(
            name="StageDraftSkeletonOverview",
            model_client=self._draft_model_client,
            tools=None,
            system_message=system_prompt,
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )
        response = await with_timeout(
            "timeboxing:skeleton-overview",
            draft_agent.on_messages(
                [
                    TextMessage(
                        content="Draft the Stage 3 day overview in markdown now.",
                        source="user",
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, str) and content.strip():
            return content.strip()
        raise ValueError("Skeleton overview response did not contain markdown text.")

    def _build_skeleton_seed_plan(self, session: Session) -> TBPlan:
        """Build the seed plan that the patcher expands into a full skeleton."""
        planning_date = self._resolve_planning_date(session)
        tz_name = session.tz_name or "UTC"
        immovables = self._normalize_calendar_events(
            session.frame_facts.get("immovables")
        )
        seed_events = immovables or [self._build_focus_block_event(timezone=tz_name)]
        seed_plan = self._tb_plan_from_calendar_events(
            events=seed_events,
            planning_date=planning_date,
            tz_name=tz_name,
        )
        if seed_plan.events:
            return seed_plan
        if immovables:
            fallback = self._build_focus_block_event(timezone=tz_name)
            return self._tb_plan_from_calendar_events(
                events=[fallback],
                planning_date=planning_date,
                tz_name=tz_name,
            )
        return seed_plan

    def _tb_plan_from_calendar_events(
        self,
        *,
        events: list[CalendarEvent],
        planning_date: date,
        tz_name: str,
    ) -> TBPlan:
        """Convert calendar events into a TBPlan while skipping unmappable entries."""
        tb_events: list[TBEvent] = []
        skipped: list[str] = []
        for event in events:
            seed_timebox = Timebox.model_construct(
                events=[event],
                date=planning_date,
                timezone=tz_name,
            )
            try:
                candidate = timebox_to_tb_plan(seed_timebox, validate=False)
            except Exception as exc:
                label = str(
                    getattr(event, "summary", None)
                    or getattr(event, "eventId", None)
                    or "event"
                ).strip()
                skipped.append(f"{label}: {str(exc) or type(exc).__name__}")
                continue
            if candidate.events:
                tb_events.extend(candidate.events)
        if skipped:
            logger.warning(
                "Skipped %s unmappable calendar event(s) while building TBPlan: %s",
                len(skipped),
                "; ".join(skipped[:5]),
            )
        return TBPlan.model_construct(events=tb_events, date=planning_date, tz=tz_name)

    def _fallback_skeleton_markdown(self, fallback: Timebox) -> str:
        """Render a deterministic markdown overview for fallback drafts."""
        lines = ["## Day Overview"]
        if not fallback.events:
            lines.append("- No events drafted yet.")
            return "\n".join(lines)
        lines.append("### Planned")
        for event in fallback.events:
            start = (
                event.start_time.isoformat(timespec="minutes")
                if event.start_time
                else ""
            )
            end = event.end_time.isoformat(timespec="minutes") if event.end_time else ""
            if start and end:
                lines.append(f"- {start}-{end} {event.summary}")
            else:
                lines.append(f"- {event.summary}")
        return "\n".join(lines)

    def _skeleton_pregeneration_fingerprint(self, session: Session) -> str:
        """Build a deterministic fingerprint for current skeleton draft inputs."""
        payload = {
            "planned_date": session.planned_date or "",
            "tz_name": session.tz_name or "UTC",
            "frame_facts": session.frame_facts or {},
            "input_facts": session.input_facts or {},
            "constraints": [
                c.model_dump(mode="json")
                for c in (
                    session.durable_constraints_by_stage.get(
                        TimeboxingStage.SKELETON.value, []
                    )
                    or []
                )
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _can_pre_generate_skeleton(self, session: Session) -> bool:
        """Return whether Stage 2 has enough context to pre-generate skeleton."""
        immovables = parse_model_list(Immovable, session.frame_facts.get("immovables"))
        has_immovables = bool(immovables)
        durable = session.durable_constraints_by_stage.get(
            TimeboxingStage.SKELETON.value, []
        )
        has_constraints = bool(durable or session.active_constraints)
        return has_immovables and has_constraints

    def _queue_skeleton_pre_generation(self, session: Session) -> None:
        """Queue a background skeleton draft during Stage 2."""
        if not self._can_pre_generate_skeleton(session):
            return
        fingerprint = self._skeleton_pregeneration_fingerprint(session)
        if (
            session.pre_generated_skeleton_plan is not None
            and session.pre_generated_skeleton_fingerprint == fingerprint
        ):
            return
        active_task = session.pre_generated_skeleton_task
        if active_task and not active_task.done():
            if session.pre_generated_skeleton_fingerprint == fingerprint:
                return
            active_task.cancel()
        session.pre_generated_skeleton = None
        session.pre_generated_skeleton_plan = None
        session.pre_generated_skeleton_markdown = None
        session.pre_generated_skeleton_fingerprint = fingerprint
        session.pending_skeleton_pre_generation = True

        async def _background() -> None:
            """Run skeleton pre-generation without blocking user responses."""
            try:
                draft, markdown, drafted_plan = await self._run_skeleton_draft(session)
                if (
                    session.pre_generated_skeleton_fingerprint == fingerprint
                    and drafted_plan is not None
                ):
                    session.pre_generated_skeleton = draft
                    session.pre_generated_skeleton_plan = drafted_plan
                    session.pre_generated_skeleton_markdown = markdown
                    session.background_updates.append(
                        "Prepared a skeleton draft in the background."
                    )
            except asyncio.CancelledError:
                logger.debug("Skeleton pre-generation task canceled.")
            except Exception:
                logger.debug("Skeleton pre-generation failed.", exc_info=True)
            finally:
                if session.pre_generated_skeleton_fingerprint == fingerprint:
                    session.pending_skeleton_pre_generation = False
                    session.pre_generated_skeleton_task = None

        session.pre_generated_skeleton_task = asyncio.create_task(_background())

    async def _consume_pre_generated_skeleton(
        self, session: Session
    ) -> tuple[None, str, TBPlan | None]:
        """Return a pre-generated skeleton when valid, else draft synchronously."""
        fingerprint = self._skeleton_pregeneration_fingerprint(session)
        active_task = session.pre_generated_skeleton_task
        if (
            active_task
            and not active_task.done()
            and session.pre_generated_skeleton_fingerprint == fingerprint
        ):
            try:
                await asyncio.wait_for(
                    asyncio.shield(active_task),
                    timeout=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timed out waiting for in-flight skeleton pre-generation; "
                    "falling back to synchronous draft."
                )
            except asyncio.CancelledError:
                logger.debug("Skeleton pre-generation task was canceled before consume.")
            except Exception:
                logger.debug(
                    "Skeleton pre-generation task failed before consume.",
                    exc_info=True,
                )
        if (
            session.pre_generated_skeleton_plan is not None
            and session.pre_generated_skeleton_fingerprint == fingerprint
        ):
            drafted_plan = session.pre_generated_skeleton_plan
            markdown = (
                session.pre_generated_skeleton_markdown
                or self._tb_plan_overview_markdown(drafted_plan)
            )
            session.pre_generated_skeleton = None
            session.pre_generated_skeleton_plan = None
            session.pre_generated_skeleton_markdown = None
            session.pre_generated_skeleton_task = None
            session.pending_skeleton_pre_generation = False
            return None, markdown, drafted_plan
        return await self._run_skeleton_draft(session)

    def _ensure_refine_plan_state(self, session: Session) -> RefinePreflight:
        """Ensure Stage 4 has a TBPlan and remote baseline snapshot ready for sync.

        Returns:
            Structured preflight diagnostics used to seed repair patching.
        """
        result = RefinePreflight()
        if session.timebox is None and session.tb_plan is None:
            return result
        if session.tb_plan is None:
            try:
                session.tb_plan = timebox_to_tb_plan(session.timebox)
            except Exception as exc:
                issue = str(exc).strip() or type(exc).__name__
                result.plan_issues.append(f"timebox_to_tb_plan: {issue}")
                session.tb_plan = timebox_to_tb_plan(session.timebox, validate=False)
                self._session_debug(
                    session,
                    "refine_plan_prepared_unvalidated",
                    source="timebox_to_tb_plan",
                    issue=issue,
                    event_count=len(session.tb_plan.events),
                )
            self._session_debug(
                session,
                "refine_plan_prepared",
                source="timebox_to_tb_plan",
                event_count=len(session.tb_plan.events),
            )
        if session.base_snapshot is None:
            try:
                session.base_snapshot = self._build_remote_snapshot_plan(session)
                self._session_debug(
                    session,
                    "refine_base_snapshot_prepared",
                    source="calendar_immovables",
                    event_count=len(session.base_snapshot.events),
                )
            except Exception as exc:
                issue = str(exc).strip() or type(exc).__name__
                result.snapshot_issues.append(f"remote_snapshot: {issue}")
                self._session_debug(
                    session,
                    "refine_base_snapshot_failed",
                    source="calendar_immovables",
                    issue=issue,
                )
        return result

    def _build_timeboxing_action_value(self, session: Session) -> str:
        """Encode Slack metadata for timeboxing submit/undo buttons."""
        return encode_metadata(
            {
                "channel_id": session.channel_id,
                "thread_ts": session.thread_ts,
                "user_id": session.user_id,
            }
        )

    def _build_stage_action_value(self, session: Session) -> str:
        """Encode Slack metadata for deterministic stage-control buttons."""
        return encode_metadata(
            {
                "channel_id": session.channel_id,
                "thread_ts": session.thread_ts,
                "user_id": session.user_id,
            }
        )

    def _render_stage_action_blocks(self, *, session: Session) -> list[dict[str, Any]]:
        """Render deterministic stage-control actions for the current session stage."""
        if session.completed or session.thread_state in {"done", "canceled"}:
            return []
        can_go_back = session.stage != TimeboxingStage.COLLECT_CONSTRAINTS
        can_proceed = (
            session.stage != TimeboxingStage.REVIEW_COMMIT and session.stage_ready
        )
        meta_value = self._build_stage_action_value(session)
        return [
            build_stage_actions_block(
                meta_value=meta_value,
                can_proceed=can_proceed,
                can_go_back=can_go_back,
                include_cancel=True,
            )
        ]

    def _event_key_from_summary_and_start(
        self, *, summary: str, start: str, tz_name: str
    ) -> str | None:
        """Build the canonical ``summary|start_time`` key used by sync mapping."""
        try:
            tz = ZoneInfo(tz_name or "UTC")
            parsed = date_parser.isoparse(start)
            if parsed.tzinfo:
                start_time = parsed.astimezone(tz).time().replace(tzinfo=None)
            else:
                start_time = parsed.time()
        except Exception:
            return None
        return f"{summary}|{start_time.isoformat()}"

    def _update_event_id_map_after_submit(
        self,
        *,
        session: Session,
        transaction: SyncTransaction,
    ) -> Dict[str, str]:
        """Update ``session.event_id_map`` after a submit transaction."""
        previous = dict(session.event_id_map)
        op_key_to_id: Dict[str, str] = {}
        for index, op in enumerate(transaction.ops):
            if transaction.results:
                result = (
                    transaction.results[index]
                    if index < len(transaction.results)
                    else {}
                )
                if not bool(result.get("ok", False)):
                    continue
            payload = op.after_payload or {}
            summary = str(payload.get("summary") or "").strip()
            start = str(payload.get("start") or "").strip()
            event_id = str(payload.get("eventId") or op.gcal_event_id or "").strip()
            if not (summary and start and event_id):
                continue
            key = self._event_key_from_summary_and_start(
                summary=summary,
                start=start,
                tz_name=session.tz_name,
            )
            if key:
                op_key_to_id[key] = event_id
        updated: Dict[str, str] = {
            key: value
            for key, value in previous.items()
            if not value.startswith(FFTB_PREFIX)
        }
        if session.tb_plan is None:
            return updated
        for resolved in session.tb_plan.resolve_times():
            key = f"{resolved['n']}|{resolved['start_time'].isoformat()}"
            event_id = op_key_to_id.get(key) or previous.get(key)
            if event_id:
                updated[key] = event_id
        return updated

    def _render_submit_prompt_blocks(
        self, *, session: Session, text: str
    ) -> list[dict[str, Any]]:
        """Render Slack blocks for Stage 5 confirm/cancel flow."""
        _ = text
        action_value = self._build_timeboxing_action_value(session)
        return [build_review_submit_actions_block(meta_value=action_value)]

    def _render_markdown_summary_blocks(self, *, text: str) -> list[dict[str, Any]]:
        """Render a markdown summary block for Slack output."""
        return [build_markdown_block(text=text)]

    def _render_submit_result_blocks(
        self, *, session: Session, text: str, include_undo: bool
    ) -> list[dict[str, Any]]:
        """Render Slack blocks for post-submit result messages."""
        blocks: list[dict[str, Any]] = [build_text_section_block(text=text)]
        if include_undo:
            action_value = self._build_timeboxing_action_value(session)
            blocks.append(build_undo_submit_actions_block(meta_value=action_value))
        return blocks

    def _build_remote_snapshot_plan(self, session: Session) -> TBPlan:
        """Build the calendar baseline snapshot used by the sync engine."""
        planning_date = self._resolve_planning_date(session)
        tz_name = session.tz_name or "UTC"
        planned_date_key = planning_date.isoformat()
        prefetched = session.prefetched_remote_snapshots_by_date.get(planned_date_key)
        if prefetched is not None:
            prefetched_map = (
                session.prefetched_event_id_maps_by_date.get(planned_date_key) or {}
            )
            prefetched_ids = (
                session.prefetched_remote_event_ids_by_date.get(planned_date_key) or []
            )
            session.event_id_map = dict(prefetched_map)
            session.remote_event_ids_by_index = list(prefetched_ids)
            return prefetched.model_copy(deep=True)

        immovables = self._normalize_calendar_events(
            session.frame_facts.get("immovables")
        )
        session.remote_event_ids_by_index = []
        return self._tb_plan_from_calendar_events(
            events=immovables,
            planning_date=planning_date,
            tz_name=tz_name,
        )

    async def _submit_current_plan(self, session: Session) -> str | None:
        """Sync the current TBPlan to Google Calendar and return a short status line."""
        if session.tb_plan is None:
            return "Calendar sync skipped: plan is not ready yet."
        if session.base_snapshot is None:
            return "Calendar sync skipped: baseline snapshot unavailable. Click Redo to retry sync."

        previous_map = dict(session.event_id_map)
        try:
            tx = await self._calendar_submitter.submit_plan(
                desired=session.tb_plan,
                remote=session.base_snapshot,
                event_id_map=session.event_id_map,
                remote_event_ids_by_index=session.remote_event_ids_by_index,
            )
        except Exception:
            logger.exception("Calendar sync failed during refine stage.")
            return "Calendar sync failed; keep refining and try again."
        session.committed = True
        session.last_sync_transaction = tx
        session.last_sync_event_id_map = previous_map
        session.event_id_map = self._update_event_id_map_after_submit(
            session=session,
            transaction=tx,
        )
        session.base_snapshot = session.tb_plan.model_copy(deep=True)
        session.remote_event_ids_by_index = []
        if tx.status == "committed":
            return "Synced to Google Calendar."
        if tx.status == "partial":
            return "Synced with partial calendar failures."
        return f"Calendar sync finished with status `{tx.status}`."

    async def _build_skeleton_context(self, session: Session) -> SkeletonContext:
        """Assemble the injected context for the skeleton drafter."""
        await self._ensure_calendar_immovables(
            session, timeout_s=TIMEBOXING_TIMEOUTS.calendar_prefetch_wait_s
        )
        constraints = await self._collect_constraints(session)
        planned = self._resolve_planning_date(session)
        tz_name = session.tz_name or "UTC"

        immovables = parse_model_list(Immovable, session.frame_facts.get("immovables"))
        work_window = parse_model_optional(
            WorkWindow, session.frame_facts.get("work_window")
        )
        sleep_target = parse_model_optional(
            SleepTarget, session.frame_facts.get("sleep_target")
        )
        block_plan = parse_model_optional(
            BlockPlan, session.input_facts.get("block_plan")
        )
        daily_one_thing = parse_model_optional(
            DailyOneThing, session.input_facts.get("daily_one_thing")
        )
        tasks = parse_model_list(TaskCandidate, session.input_facts.get("tasks"))

        return SkeletonContext(
            date=planned,
            timezone=tz_name,
            work_window=work_window,
            sleep_target=sleep_target,
            immovables=immovables,
            block_plan=block_plan,
            daily_one_thing=daily_one_thing,
            tasks=tasks,
            constraints_snapshot=list(constraints or []),
        )

    def _build_fallback_skeleton_timebox(self, session: Session) -> Timebox:
        """Build a minimal timebox when skeleton drafting fails or times out."""
        planning_date = self._resolve_planning_date(session)
        tz_name = session.tz_name or "UTC"
        immovables = self._normalize_calendar_events(
            session.frame_facts.get("immovables")
        )
        events = immovables or [self._build_focus_block_event(timezone=tz_name)]
        try:
            return Timebox(events=events, date=planning_date, timezone=tz_name)
        except Exception:
            logger.debug("Fallback timebox failed; returning focus block only.")
            focus_block = self._build_focus_block_event(timezone=tz_name)
            return Timebox(events=[focus_block], date=planning_date, timezone=tz_name)

    def _resolve_planning_date(self, session: Session) -> date:
        """Resolve the planning date from session state or default to today."""
        if session.planned_date:
            # TODO(refactor): Parse planned_date via a Pydantic schema.
            try:
                return date.fromisoformat(session.planned_date)
            except ValueError:
                logger.debug(
                    "Invalid planned_date=%s; defaulting to today.",
                    session.planned_date,
                )
        return date.today()

    def _normalize_calendar_events(self, immovables: Any | None) -> list[CalendarEvent]:
        """Normalize immovable payloads into CalendarEvent instances."""
        events = parse_model_list(CalendarEvent, immovables)
        return self._sort_calendar_events(events)

    def _sort_calendar_events(self, events: list[CalendarEvent]) -> list[CalendarEvent]:
        """Sort events by their scheduled times for deterministic ordering."""
        return sorted(events, key=self._calendar_event_sort_key)

    def _calendar_event_sort_key(self, event: CalendarEvent) -> time:
        """Build a stable sort key for calendar events."""
        if event.start_time:
            return event.start_time
        if event.end_time:
            return event.end_time
        return time.max

    def _build_focus_block_event(self, *, timezone: str) -> CalendarEvent:
        """Return a default focus block event for fallback timeboxes."""
        return CalendarEvent(
            summary="Focus Block",
            event_type=EventType.DEEP_WORK,
            start_time=time(9, 0),
            duration=timedelta(minutes=TIMEBOXING_FALLBACK.focus_block_minutes),
            calendarId="primary",
            timeZone=timezone,
        )

    async def _decide_next_action(
        self, session: Session, *, user_message: str
    ) -> StageDecision:
        """Decide how to advance the timeboxing stage based on user input."""
        await self._ensure_stage_agents()
        assert self._decision_agent is not None
        decision_ctx = toon_encode(
            name="decision_ctx",
            rows=[
                {
                    "current_stage": session.stage.value,
                    "stage_ready": session.stage_ready,
                    "stage_question": session.stage_question or "",
                    "user_message": user_message,
                }
            ],
            fields=["current_stage", "stage_ready", "stage_question", "user_message"],
        )
        missing = toon_encode(
            name="stage_missing",
            rows=[{"item": item} for item in (session.stage_missing or [])],
            fields=["item"],
        )
        payload = f"{decision_ctx}\n{missing}\n"
        response = await with_timeout(
            "timeboxing:stage-decision",
            self._decision_agent.on_messages(
                [TextMessage(content=payload, source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.stage_decision_s,
        )
        return parse_chat_content(StageDecision, response)

    def _format_constraints_section(
        self, constraints: list[Constraint], limit: int = 6
    ) -> list[str]:
        """Format active constraints for display in stage responses."""
        lines: list[str] = []
        for constraint in constraints[:limit]:
            name = (constraint.name or "Constraint").strip()
            description = (constraint.description or "").strip()
            if description:
                lines.append(f"{name} — {description}")
            else:
                lines.append(name)
        remaining = len(constraints) - len(lines)
        if remaining > 0:
            lines.append(f"...and {remaining} more")
        return lines

    def _build_collect_constraint_template_section(
        self, *, gate: StageGateOutput, constraints: list[Constraint] | None
    ) -> list[str] | None:
        """Build the Stage 1 template coverage section from typed gate facts."""
        _ = (gate, constraints)
        return None

    def _append_background_update_once(self, session: Session, note: str) -> None:
        """Append a background note if it is not already queued."""
        if note in session.background_updates:
            return
        session.background_updates.append(note)

    def _sanitize_stage_summary_lines(
        self,
        *,
        gate: StageGateOutput,
        immovables: list[dict[str, str]] | None,
    ) -> list[str]:
        """Deduplicate stage-summary lines without post-hoc phrase filtering."""
        lines: list[str] = []
        seen: set[str] = set()
        for raw in gate.summary or []:
            line = (raw or "").strip()
            if not line:
                continue
            if line in seen:
                continue
            seen.add(line)
            lines.append(line)
        return lines

    def _format_immovables_section(
        self, immovables: list[dict[str, str]], limit: int = 6
    ) -> list[str]:
        """Format calendar immovables for display in stage responses."""
        lines: list[str] = []
        for item in immovables[:limit]:
            title = (item.get("title") or "Busy").strip()
            start = (item.get("start") or "").strip()
            end = (item.get("end") or "").strip()
            if start and end:
                lines.append(f"{start}-{end} {title}")
            else:
                lines.append(title)
        remaining = len(immovables) - len(lines)
        if remaining > 0:
            lines.append(f"...and {remaining} more")
        return lines

    def _format_stage_message(
        self,
        gate: StageGateOutput,
        *,
        background_notes: list[str] | None = None,
        constraints: list[Constraint] | None = None,
        immovables: list[dict[str, str]] | None = None,
    ) -> str:
        """Render a human-readable stage update message."""
        stage_order = {
            TimeboxingStage.COLLECT_CONSTRAINTS: "Stage 1/5 (CollectConstraints)",
            TimeboxingStage.CAPTURE_INPUTS: "Stage 2/5 (CaptureInputs)",
            TimeboxingStage.SKELETON: "Stage 3/5 (Skeleton)",
            TimeboxingStage.REFINE: "Stage 4/5 (Refine)",
            TimeboxingStage.REVIEW_COMMIT: "Stage 5/5 (ReviewCommit)",
        }
        header = stage_order.get(gate.stage_id, f"Stage ({gate.stage_id.value})")
        summary_lines = self._sanitize_stage_summary_lines(
            gate=gate, immovables=immovables
        )
        bullets = (
            "\n".join([f"- {b}" for b in summary_lines])
            if summary_lines
            else "- (none)"
        )
        missing = (
            "\n".join([f"- {m}" for m in gate.missing]) if gate.missing else "- (none)"
        )
        if gate.stage_id == TimeboxingStage.COLLECT_CONSTRAINTS:
            defaults_raw = []
            if isinstance(gate.facts, dict):
                defaults_raw = list(gate.facts.get("defaults_applied") or [])
            defaults = [
                f"- {line.strip()}"
                for line in defaults_raw
                if isinstance(line, str) and line.strip()
            ]
            defaults_block = "\n".join(defaults) if defaults else "- (none)"
            parts = [
                header,
                "Confirmed Defaults:",
                defaults_block,
                "Still Missing:",
                missing,
            ]
            if gate.question:
                parts.append(f"Question: {gate.question}")
            parts.extend(["What I Have So Far:", bullets])
            if not gate.ready:
                parts.append(
                    "Stage criteria not met yet. Share the missing inputs, then use Redo."
                )
            else:
                parts.append(
                    "Stage criteria met. I can auto-proceed; click Proceed or share adjustments."
                )
            if constraints:
                constraint_lines = self._format_constraints_section(constraints)
                parts.extend(
                    [
                        "Constraints:",
                        "\n".join([f"- {line}" for line in constraint_lines]),
                    ]
                )
            if immovables:
                immovable_lines = self._format_immovables_section(immovables)
                parts.extend(
                    [
                        "Calendar:",
                        "\n".join([f"- {line}" for line in immovable_lines]),
                    ]
                )
            if background_notes:
                notes = "\n".join([f"- {note}" for note in background_notes])
                parts.extend(["Background:", notes])
            return "\n".join(parts)

        parts = [header]
        if not gate.ready:
            parts.extend(
                [
                    "Need Before Proceeding:",
                    missing,
                ]
            )
            parts.append(
                "Stage criteria not met yet. Share the missing inputs, then use Redo."
            )
            if gate.question:
                parts.append(f"Question: {gate.question}")
            parts.extend(["What I Have So Far:", bullets])
        else:
            parts.extend(["Summary:", bullets])
            if gate.question:
                parts.append(f"Question: {gate.question}")
            parts.append(
                "Stage criteria met. I can auto-proceed; click Proceed or share adjustments."
            )
        if constraints:
            constraint_lines = self._format_constraints_section(constraints)
            parts.extend(
                [
                    "Constraints:",
                    "\n".join([f"- {line}" for line in constraint_lines]),
                ]
            )
        if immovables:
            immovable_lines = self._format_immovables_section(immovables)
            parts.extend(
                [
                    "Calendar:",
                    "\n".join([f"- {line}" for line in immovable_lines]),
                ]
            )
        if background_notes:
            notes = "\n".join([f"- {note}" for note in background_notes])
            parts.extend(["Background:", notes])
        return "\n".join(parts)

    def _collect_background_notes(self, session: Session) -> list[str] | None:
        """Assemble background status notes to include in stage responses."""
        notes: list[str] = []

        def _add(note: str) -> None:
            if note in notes:
                return
            notes.append(note)

        if session.pending_durable_constraints:
            _add("Loading saved constraints...")
        if session.pending_calendar_prefetch:
            _add("Loading calendar immovables for the day.")
        if session.pending_constraint_extractions:
            _add(
                "Syncing your preferences in the background so we can keep moving."
            )
        if session.pending_skeleton_pre_generation:
            _add("Pre-drafting your skeleton in the background.")
        if session.background_updates:
            for note in session.background_updates:
                _add(note)
            session.background_updates.clear()
        return notes or None

    async def _advance_stage(
        self, session: Session, *, next_stage: TimeboxingStage
    ) -> None:
        """Advance the session to the next stage and reset stage state."""
        session.stage = next_stage
        session.stage_ready = False
        session.stage_missing = []
        session.stage_question = None
        if next_stage != TimeboxingStage.REVIEW_COMMIT:
            session.pending_submit = False
            session.pending_presenter_blocks = None
        if next_stage in (
            TimeboxingStage.COLLECT_CONSTRAINTS,
            TimeboxingStage.CAPTURE_INPUTS,
        ):
            session.timebox = None
            session.skeleton_overview_markdown = None
            task = session.pre_generated_skeleton_task
            if task and not task.done():
                task.cancel()
            session.pre_generated_skeleton = None
            session.pre_generated_skeleton_markdown = None
            session.pre_generated_skeleton_fingerprint = None
            session.pre_generated_skeleton_task = None
            session.pending_skeleton_pre_generation = False

    @staticmethod
    def _previous_stage(stage: TimeboxingStage) -> TimeboxingStage:
        """Return the previous stage for the timeboxing flow."""
        prev_map = {
            TimeboxingStage.CAPTURE_INPUTS: TimeboxingStage.COLLECT_CONSTRAINTS,
            TimeboxingStage.SKELETON: TimeboxingStage.CAPTURE_INPUTS,
            TimeboxingStage.REFINE: TimeboxingStage.SKELETON,
            TimeboxingStage.REVIEW_COMMIT: TimeboxingStage.REFINE,
        }
        return prev_map.get(stage, TimeboxingStage.COLLECT_CONSTRAINTS)

    async def _proceed(self, session: Session) -> None:
        """Advance the session to the next stage."""
        next_map = {
            TimeboxingStage.COLLECT_CONSTRAINTS: TimeboxingStage.CAPTURE_INPUTS,
            TimeboxingStage.CAPTURE_INPUTS: TimeboxingStage.SKELETON,
            TimeboxingStage.SKELETON: TimeboxingStage.REFINE,
            TimeboxingStage.REFINE: TimeboxingStage.REVIEW_COMMIT,
            TimeboxingStage.REVIEW_COMMIT: TimeboxingStage.REVIEW_COMMIT,
        }
        next_stage = next_map.get(session.stage, session.stage)
        await self._advance_stage(session, next_stage=next_stage)

    def _build_constraint_agent(self) -> "AssistantAgent":
        """Build the LLM agent that extracts local constraints."""
        model_client = getattr(self, "_constraint_model_client", None) or getattr(
            self, "_model_client", None
        )
        if model_client is None:
            raise RuntimeError("Constraint model client is not configured.")
        return AssistantAgent(
            name="ConstraintExtractor",
            model_client=model_client,
            output_content_type=ConstraintBatch,
            system_message=(
                "Extract ONLY explicit scheduling preferences or constraints that the USER personally stated. "
                "Examples of valid constraints:\n"
                "- 'I have a meeting at 2pm' -> fixed appointment\n"
                "- 'I don't work before 9am' -> work window preference\n"
                "- 'I want 2 deep-work blocks' -> block allocation preference\n"
                "- 'I need 2 hours for deep work' -> duration requirement\n"
                "- 'I want to exercise in the morning' -> activity preference\n"
                "- 'No calls after 5pm' -> availability rule\n\n"
                "DO NOT extract:\n"
                "- Generic statements about timeboxing or scheduling methodology\n"
                "- Definitions or explanations of what timeboxing means\n"
                "- Bot/system messages or instructions\n"
                "- Anything the user did NOT explicitly state as their own preference\n\n"
                "If no valid user constraints are found, return an empty constraints list.\n"
                "Return ONLY a JSON object with a list of constraints. Each constraint needs "
                "name, description, necessity (must/should), and any useful hints/selector "
                "metadata. Use source=user and status=proposed unless explicitly locked."
            ),
            reflect_on_tool_use=False,
            max_tool_iterations=1,
        )

    def _build_constraint_search_tool(self) -> FunctionTool:
        """Build a FunctionTool that lets stage-gating LLMs search durable constraints.

        The tool closes over ``self`` so it can lazily initialise the MCP client.
        """
        agent_ref = self  # prevent gc issues with the closure

        async def _search_constraints_wrapper(
            queries: list[dict[str, Any]],
            planned_date: str | None,
            stage: str | None,
        ) -> str:
            """Search the durable constraint store with one or more query facets.

            Use this tool to find saved scheduling preferences and constraints
            from the user's Notion preference store. You can search by:
            - text (free-text match on constraint name or description)
            - event type codes (M, DW, SW, H, R, C, BU, BG, PR)
            - topic tags
            - status (locked / proposed)
            - scope (session / profile / datespan)
            - necessity (must / should)

            Args:
                queries: List of search facets. Each facet is a dict with keys:
                    - label (str): Short description of this query.
                    - text_query (str): Free-text search on Name/Description.
                    - event_types (list[str]): Event-type codes.
                    - tags (list[str]): Topic tag names.
                    - statuses (list[str]): 'locked' and/or 'proposed'.
                    - scopes (list[str]): 'session', 'profile', 'datespan'.
                    - necessities (list[str]): 'must' and/or 'should'.
                    - limit (int): Max results per facet (default 20).
                planned_date: ISO date (YYYY-MM-DD), or null to use today.
                stage: Current timeboxing stage, or null for no stage filter.

            Returns:
                Formatted summary of matching constraints.
            """
            if (
                stage == TimeboxingStage.COLLECT_CONSTRAINTS.value
                and (not queries or all(not isinstance(q, dict) or not q for q in queries))
            ):
                return (
                    "Skipped search_constraints for Stage 1 because no concrete query "
                    "facet was provided. Using deterministic saved-default prefetch."
                )
            client = agent_ref._ensure_constraint_memory_client()
            return await search_constraints(
                queries=queries,
                planned_date=planned_date,
                stage=stage,
                _client=client,
            )

        return FunctionTool(
            _search_constraints_wrapper,
            name="search_constraints",
            description=(
                "Search the durable constraint/preference store in Notion. "
                "Accepts one or more search facets (text, event types, tags, "
                "status, scope, necessity) and returns a formatted summary of "
                "matching constraints. Use this to find the user's saved "
                "scheduling preferences before making planning decisions."
            ),
            strict=True,
        )

    async def _ensure_constraint_store(self) -> None:
        """Initialize the SQLite constraint store if needed."""
        if self._constraint_store or not settings.database_url:
            return
        async_url = _coerce_async_database_url(settings.database_url)
        engine = create_async_engine(async_url)
        await ensure_constraint_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        self._constraint_store = ConstraintStore(sessionmaker)
        self._constraint_engine = engine

    async def _ensure_constraint_mcp_tools(self) -> None:
        """Initialize constraint MCP tools for durable preference upserts."""
        if self._constraint_mcp_tools:
            return
        if not settings.notion_timeboxing_parent_page_id:
            return
        model_client = getattr(self, "_constraint_model_client", None) or getattr(
            self, "_model_client", None
        )
        if model_client is None:
            return
        try:
            raw_tools = await get_constraint_mcp_tools()
            raw_by_name = {
                str(getattr(tool, "name", "")).strip(): tool for tool in raw_tools
            }
            required_tool_names = (
                "constraint_query_types",
                "constraint_query_constraints",
                "constraint_upsert_constraint",
                "constraint_log_event",
            )
            missing = [name for name in required_tool_names if name not in raw_by_name]
            if missing:
                raise RuntimeError(
                    f"Constraint MCP server missing required tools: {', '.join(missing)}"
                )

            async def _run_constraint_tool_json(
                tool_name: str, arguments: dict[str, Any]
            ) -> Any:
                tool = raw_by_name[tool_name]
                result = await tool.run_json(arguments, CancellationToken())
                return ConstraintMemoryClient._decode_tool_result(tool_name, result)

            async def constraint_query_types(
                stage: str | None,
                event_types: list[str] | None,
            ) -> list[dict[str, Any]]:
                payload = await _run_constraint_tool_json(
                    "constraint_query_types",
                    {
                        "stage": stage,
                        "event_types": event_types,
                    },
                )
                if not isinstance(payload, list):
                    raise RuntimeError(
                        "constraint_query_types returned non-list JSON payload"
                    )
                return [item for item in payload if isinstance(item, dict)]

            async def constraint_query_constraints(
                filters: dict[str, Any],
                type_ids: list[str] | None,
                tags: list[str] | None,
                sort: list[list[str]] | None,
                limit: int,
            ) -> list[dict[str, Any]]:
                payload = await _run_constraint_tool_json(
                    "constraint_query_constraints",
                    {
                        "filters": filters,
                        "type_ids": type_ids,
                        "tags": tags,
                        "sort": sort,
                        "limit": limit,
                    },
                )
                if not isinstance(payload, list):
                    raise RuntimeError(
                        "constraint_query_constraints returned non-list JSON payload"
                    )
                return [item for item in payload if isinstance(item, dict)]

            async def constraint_upsert_constraint(
                record: dict[str, Any],
                event: dict[str, Any] | None,
            ) -> dict[str, Any]:
                payload = await _run_constraint_tool_json(
                    "constraint_upsert_constraint",
                    {
                        "record": record,
                        "event": event,
                    },
                )
                if not isinstance(payload, dict):
                    raise RuntimeError(
                        "constraint_upsert_constraint returned non-dict JSON payload"
                    )
                if not payload.get("uid"):
                    raise RuntimeError(
                        "constraint_upsert_constraint returned missing uid"
                    )
                return payload

            async def constraint_log_event(event: dict[str, Any]) -> dict[str, Any]:
                payload = await _run_constraint_tool_json(
                    "constraint_log_event",
                    {"event": event},
                )
                if not isinstance(payload, dict):
                    raise RuntimeError("constraint_log_event returned non-dict JSON payload")
                return payload

            self._constraint_mcp_tools = [
                FunctionTool(
                    constraint_query_types,
                    name="constraint_query_types",
                    description="Query ranked constraint types from durable memory.",
                    strict=True,
                ),
                FunctionTool(
                    constraint_query_constraints,
                    name="constraint_query_constraints",
                    description="Query durable constraints using filters, tags, and type ids.",
                    strict=True,
                ),
                FunctionTool(
                    constraint_upsert_constraint,
                    name="constraint_upsert_constraint",
                    description="Create or update one durable constraint record.",
                    strict=True,
                ),
                FunctionTool(
                    constraint_log_event,
                    name="constraint_log_event",
                    description="Log a durable-memory extraction/upsert event.",
                    strict=True,
                ),
            ]
            self._notion_extractor = NotionConstraintExtractor(
                model_client=model_client,
                tools=self._constraint_mcp_tools,
            )

            async def extract_and_upsert_constraint(
                planned_date: str,
                timezone: str,
                stage_id: str,
                user_utterance: str,
                triggering_suggestion: str,
                impacted_event_types: list[str],
                suggested_tags: list[str],
                decision_scope: str,
            ) -> dict[str, Any] | None:
                """Extract a durable timeboxing constraint from user text and upsert it into Notion.

                Args:
                    planned_date: The date being planned (YYYY-MM-DD format)
                    timezone: User's timezone (e.g. "Europe/Amsterdam")
                    stage_id: Current timeboxing stage ID (empty string if none)
                    user_utterance: The user's exact words
                    triggering_suggestion: The suggestion that triggered this (empty string if none)
                    impacted_event_types: List of event types affected
                    suggested_tags: List of suggested constraint tags
                    decision_scope: session|profile|datespan (empty string if unknown)
                """
                assert self._notion_extractor is not None

                payload = {
                    "planned_date": planned_date,
                    "timezone": timezone,
                    "stage_id": stage_id,
                    "user_utterance": user_utterance,
                    "triggering_suggestion": triggering_suggestion,
                    "impacted_event_types": impacted_event_types,
                    "suggested_tags": suggested_tags,
                    "decision_scope": decision_scope,
                }
                task_key = hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(
                        "utf-8"
                    )
                ).hexdigest()[: TIMEBOXING_LIMITS.durable_task_key_len]

                if task_key in self._durable_constraint_task_keys:
                    return {"queued": False, "deduped": True, "task_key": task_key}

                if (
                    len(self._durable_constraint_task_keys)
                    >= TIMEBOXING_LIMITS.durable_task_queue_limit
                ):
                    return {"queued": False, "rate_limited": True, "task_key": task_key}

                self._durable_constraint_task_keys.add(task_key)

                async def _background() -> None:
                    """Run the durable upsert in the background."""
                    acquired = False
                    try:
                        await self._durable_constraint_semaphore.acquire()
                        acquired = True
                        await with_timeout(
                            f"notion:constraint-upsert:{task_key}",
                            self._notion_extractor.extract_and_upsert_constraint(
                                planned_date=planned_date,
                                timezone=timezone,
                                stage_id=stage_id if stage_id else None,
                                user_utterance=user_utterance,
                                triggering_suggestion=(
                                    triggering_suggestion
                                    if triggering_suggestion
                                    else None
                                ),
                                impacted_event_types=impacted_event_types,
                                suggested_tags=suggested_tags,
                                decision_scope=(
                                    decision_scope if decision_scope else None
                                ),
                            ),
                            timeout_s=TIMEBOXING_TIMEOUTS.notion_upsert_s,
                        )
                    except Exception:
                        logger.error(
                            "Durable constraint upsert failed (task_key=%s)",
                            task_key,
                            exc_info=True,
                        )
                    finally:
                        if acquired:
                            self._durable_constraint_semaphore.release()
                        self._durable_constraint_task_keys.discard(task_key)

                asyncio.create_task(_background())
                return {"queued": True, "task_key": task_key}

            self._constraint_extractor_tool = FunctionTool(
                extract_and_upsert_constraint,
                name="extract_and_upsert_constraint",
                description=(
                    "Extract a durable timeboxing constraint from user text and upsert it into "
                    "Notion. Use empty string for optional fields (stage_id, triggering_suggestion, "
                    "decision_scope) if not applicable."
                ),
                strict=True,
            )
        except Exception:
            logger.exception(
                "Failed to initialize constraint MCP tools; skipping Notion upserts."
            )

    async def _extract_constraints(
        self,
        session: Session,
        text: str,
        *,
        scope_override: ConstraintScope | None = None,
    ) -> ConstraintBatch | None:
        """Extract session constraints and persist them in the local store."""
        if not text.strip():
            return None
        await self._ensure_constraint_store()
        await self._ensure_constraint_mcp_tools()
        message = TextMessage(content=text, source="user")
        response = await with_timeout(
            "timeboxing:constraint-extract",
            self._constraint_agent.on_messages([message], CancellationToken()),
            timeout_s=TIMEBOXING_TIMEOUTS.constraint_extract_s,
        )
        batch = _extract_constraint_batch(response)
        if not batch or not batch.constraints:
            return None
        if scope_override:
            for constraint in batch.constraints:
                constraint.scope = scope_override
        if self._constraint_store:
            await self._constraint_store.add_constraints(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                constraints=batch.constraints,
            )
            await self._collect_constraints(session)
        return batch

    async def _update_timebox_with_feedback(
        self, session: Session, text: str
    ) -> List[TimeboxAction]:
        """Apply user feedback to the current timebox draft."""
        if session.stage != TimeboxingStage.REFINE:
            logger.warning(
                "Ignoring patch request outside Stage 4 Refine: stage=%s",
                session.stage.value if session.stage else None,
            )
            return []
        if session.timebox is None and session.tb_plan is None:
            return []
        before = session.timebox or Timebox.model_construct(
            events=[],
            date=self._resolve_planning_date(session),
            timezone=session.tz_name or "UTC",
        )
        constraints = await self._collect_constraints(session)

        # Use TBPlan path if available, fall back to legacy Timebox path
        if session.tb_plan is not None:
            validated_timebox: Timebox | None = None

            def _materialize_timebox(plan: TBPlan) -> Timebox:
                nonlocal validated_timebox
                validated_timebox = tb_plan_to_timebox(plan)
                return validated_timebox

            patch_message = self._compose_patcher_message(
                base_message=text,
                session=session,
                stage=TimeboxingStage.REFINE.value,
                extra={"quality_snapshot": self._quality_snapshot_for_prompt(session)},
            )
            patched_plan, _patch = await self._timebox_patcher.apply_patch(
                stage=TimeboxingStage.REFINE.value,
                current=session.tb_plan,
                user_message=patch_message,
                constraints=constraints,
                actions=[],
                plan_validator=_materialize_timebox,
            )
            session.tb_plan = patched_plan
            if validated_timebox is None:
                raise ValueError(
                    "Patch validated without producing a materialized Timebox."
                )
            session.timebox = validated_timebox
        else:
            session.timebox = await self._timebox_patcher.apply_patch_legacy(
                stage=TimeboxingStage.REFINE.value,
                current=session.timebox,
                user_message=text,
                constraints=constraints,
                actions=[],
            )

        session.last_user_message = text
        actions = _build_actions(
            before, session.timebox, reason=text, constraints=constraints
        )
        return actions

    async def _collect_constraints(self, session: Session) -> list[Constraint]:
        """Return combined durable + session constraints and cache them on the session."""
        local_constraints: list[Constraint] = []
        if self._constraint_store:
            local_constraints = await self._constraint_store.list_constraints(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
            )
        durable_constraints = [
            c
            for stage_constraints in session.durable_constraints_by_stage.values()
            for c in (stage_constraints or [])
            if (uid := self._constraint_uid(c)) is None
            or uid not in session.suppressed_durable_uids
        ]
        combined = _dedupe_constraints(durable_constraints + list(local_constraints or []))
        session.active_constraints = [
            c for c in combined if c.status != ConstraintStatus.DECLINED
        ]
        return list(session.active_constraints or [])

    async def _sync_durable_constraints_to_store(
        self, session: Session, *, constraints: list[Constraint]
    ) -> None:
        """Mirror durable constraints into the local store for Slack display."""
        if not constraints:
            return
        await self._ensure_constraint_store()
        if not self._constraint_store:
            return
        existing = await self._constraint_store.list_constraints(
            user_id=session.user_id,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
        )
        existing_keys = {_constraint_identity_key(c) for c in existing}
        to_add: list[ConstraintBase] = []
        for constraint in constraints:
            key = _constraint_identity_key(constraint)
            if key in existing_keys:
                continue
            payload = constraint.model_dump(
                exclude={
                    "id",
                    "user_id",
                    "channel_id",
                    "thread_ts",
                    "created_at",
                    "updated_at",
                }
            )
            to_add.append(ConstraintBase.model_validate(payload))
        if to_add:
            await self._constraint_store.add_constraints(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                constraints=to_add,
            )

    async def _publish_update(
        self,
        *,
        session: Session,
        user_message: str,
        actions: List[TimeboxAction],
    ) -> None:
        """Publish a TimeboxingUpdate message to Slack subscribers."""
        await self.publish_message(
            TimeboxingUpdate(
                thread_ts=session.thread_ts,
                channel_id=session.channel_id,
                user_id=session.user_id,
                user_message=user_message,
                constraints=session.active_constraints,
                timebox=session.timebox,
                actions=actions,
                patch_history=[],
            ),
            DefaultTopicId(),
        )

    async def _maybe_wrap_constraint_review(
        self, *, reply: TextMessage, session: Session
    ) -> TextMessage | SlackBlockMessage:
        """Optionally wrap a reply with the constraint review UI when new proposals exist."""
        task = session.last_extraction_task
        if not task or not task.done():
            return reply
        try:
            extracted = task.result()
        except Exception:
            return reply
        if not extracted:
            return reply
        await self._ensure_constraint_store()
        constraints: list[Constraint] = []
        if self._constraint_store:
            constraints = await self._constraint_store.list_constraints(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
                status=ConstraintStatus.PROPOSED,
            )
        if not constraints:
            return reply
        return _wrap_with_constraint_review(
            reply, constraints=constraints, session=session
        )

    def _attach_presenter_blocks(
        self,
        *,
        reply: TextMessage | SlackBlockMessage,
        session: Session,
    ) -> TextMessage | SlackBlockMessage:
        """Attach pending presenter blocks to the outgoing Slack payload."""
        presenter_blocks = list(session.pending_presenter_blocks or [])
        session.pending_presenter_blocks = None
        stage_blocks = self._render_stage_action_blocks(session=session)
        combined_blocks = [*presenter_blocks, *stage_blocks]
        if not combined_blocks:
            return reply
        if isinstance(reply, SlackBlockMessage):
            return SlackBlockMessage(
                text=reply.text,
                blocks=list(reply.blocks) + combined_blocks,
            )
        return SlackBlockMessage(
            text=reply.content,
            blocks=[build_text_section_block(text=reply.content), *combined_blocks],
        )

    # endregion

    @message_handler
    async def on_start(
        self, message: StartTimeboxing, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
        """Handle the initial StartTimeboxing signal."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        logger.info("Starting timeboxing session on key=%s", key)
        timeboxing_activity.mark_active(
            user_id=message.user_id,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
        )
        tz_name = self._default_tz_name()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
            tz_name = "UTC"
        planned_date = await self._interpret_planned_date(
            message.user_input,
            now=datetime.now(timezone.utc),
            tz_name=tz_name,
        )
        session = Session(
            thread_ts=message.thread_ts,
            channel_id=message.channel_id,
            user_id=message.user_id,
            last_user_message=message.user_input,
            start_message=message.user_input,
            committed=False,
            planned_date=planned_date,
            tz_name=tz_name,
            session_key=key,
        )
        self._sessions[key] = session
        self._session_debug(
            session,
            "session_started",
            committed=False,
            user_input=(message.user_input or "")[:500],
        )
        asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
        self._queue_constraint_prefetch(session)
        return self._build_commit_prompt_blocks(session=session)

    @message_handler
    async def on_commit_date(
        self, message: TimeboxingCommitDate, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
        """Handle Stage 0 commit actions from Slack."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        timeboxing_activity.mark_active(
            user_id=message.user_id,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
        )
        session = self._sessions.get(key)
        if not session:
            session = Session(
                thread_ts=message.thread_ts,
                channel_id=message.channel_id,
                user_id=message.user_id,
                last_user_message="",
                session_key=key,
            )
            self._sessions[key] = session
        elif session.session_key is None:
            session.session_key = key

        session.committed = True
        session.planned_date = message.planned_date
        session.tz_name = message.timezone or session.tz_name or "UTC"
        session.frame_facts.setdefault("date", message.planned_date)
        session.frame_facts.setdefault("timezone", session.tz_name)
        self._session_debug(
            session,
            "commit_date",
            planned_date=message.planned_date,
            timezone=session.tz_name,
        )
        self._queue_constraint_prefetch(session)
        await self._await_pending_durable_constraint_prefetch(
            session,
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
        )

        await self._ensure_calendar_immovables(session)

        session.thread_state = None
        session.last_extraction_task = None
        user_message = ""
        response = await self._run_graph_turn(session=session, user_text=user_message)
        wrapped = await self._maybe_wrap_constraint_review(
            reply=response, session=session
        )
        outgoing = self._attach_presenter_blocks(reply=wrapped, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                outgoing.content
                if isinstance(outgoing, TextMessage)
                else getattr(outgoing, "text", "")
            ),
            actions=[],
        )
        return outgoing

    @message_handler
    async def on_user_reply(
        self, message: TimeboxingUserReply, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
        """Handle user replies within an active timeboxing session."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        timeboxing_activity.mark_active(
            user_id=message.user_id,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
        )
        session = self._sessions.get(key)
        if not session:
            tz_name = self._default_tz_name()
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
                tz_name = "UTC"
            planned_date = await self._interpret_planned_date(
                message.text,
                now=datetime.now(timezone.utc),
                tz_name=tz_name,
            )
            session = Session(
                thread_ts=message.thread_ts,
                channel_id=message.channel_id,
                user_id=message.user_id,
                last_user_message=message.text,
                committed=False,
                planned_date=planned_date,
                tz_name=tz_name,
                session_key=key,
            )
            self._sessions[key] = session
            self._session_debug(
                session,
                "session_started_from_reply",
                committed=False,
                user_input=(message.text or "")[:500],
            )
            asyncio.create_task(
                self._prefetch_calendar_immovables(session, planned_date)
            )
            self._queue_constraint_prefetch(session)
            return self._build_commit_prompt_blocks(session=session)
        if session.session_key is None:
            session.session_key = key
        self._session_debug(
            session,
            "user_reply",
            text=(message.text or "")[:800],
            committed=session.committed,
        )

        if not session.committed:
            # User is replying with text before clicking Confirm - treat this as implicit confirmation
            session.committed = True
            tz_name = session.tz_name or self._default_tz_name()
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
                tz_name = "UTC"

            # Update planned date if it seems like the user is specifying a different day
            planned_date = await self._interpret_planned_date(
                message.text,
                now=datetime.now(timezone.utc),
                tz_name=tz_name,
            )
            if planned_date != session.planned_date:
                session.planned_date = planned_date
                self._reset_durable_prefetch_state(session)
                await self._prefetch_calendar_immovables(session, planned_date)

            session.frame_facts.setdefault("date", session.planned_date)
            session.frame_facts.setdefault("timezone", tz_name)
            await self._ensure_calendar_immovables(session)
            self._queue_constraint_prefetch(session)
            await self._await_pending_durable_constraint_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            )

            # Now continue with normal constraint extraction and stage processing
            # Fall through to the committed session logic below

        # Session is committed - run the GraphFlow stage machine.
        if (
            session.stage == TimeboxingStage.COLLECT_CONSTRAINTS
            and not self._is_collect_stage_loaded(session)
        ):
            await self._await_pending_durable_constraint_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            )
        session.thread_state = None
        reply = await self._run_graph_turn(session=session, user_text=message.text)
        wrapped = await self._maybe_wrap_constraint_review(reply=reply, session=session)
        outgoing = self._attach_presenter_blocks(reply=wrapped, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                outgoing.content
                if isinstance(outgoing, TextMessage)
                else getattr(outgoing, "text", "")
            ),
            actions=[],
        )
        if session.thread_state:
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            return SlackThreadStateMessage(
                text=reply.content,
                thread_state=session.thread_state,
            )
        return outgoing

    @message_handler
    async def on_user_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
        """Handle generic text messages routed to the timeboxing agent."""
        key = self._session_key(ctx)
        session = self._sessions.get(key)
        if not session:
            return TextMessage(
                content="Let's start by telling me what window you want to plan.",
                source=self.id.type,
            )
        if session.session_key is None:
            session.session_key = key
        self._session_debug(
            session,
            "user_text",
            text=(message.content or "")[:800],
            committed=session.committed,
        )
        if not session.committed:
            tz_name = session.tz_name or self._default_tz_name()
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = ZoneInfo("UTC")
                tz_name = "UTC"
            planned_date = await self._interpret_planned_date(
                message.content,
                now=datetime.now(timezone.utc),
                tz_name=tz_name,
            )
            if planned_date != session.planned_date:
                self._reset_durable_prefetch_state(session)
            session.planned_date = planned_date
            session.tz_name = tz_name
            asyncio.create_task(
                self._prefetch_calendar_immovables(session, planned_date)
            )
            self._queue_constraint_prefetch(session)
            timeboxing_activity.mark_active(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
            )
            return self._build_commit_prompt_blocks(session=session)
        timeboxing_activity.mark_active(
            user_id=session.user_id,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
        )
        if (
            session.stage == TimeboxingStage.COLLECT_CONSTRAINTS
            and not self._is_collect_stage_loaded(session)
        ):
            await self._await_pending_durable_constraint_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            )
        session.thread_state = None
        reply = await self._run_graph_turn(session=session, user_text=message.content)
        wrapped = await self._maybe_wrap_constraint_review(reply=reply, session=session)
        outgoing = self._attach_presenter_blocks(reply=wrapped, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                outgoing.content
                if isinstance(outgoing, TextMessage)
                else getattr(outgoing, "text", "")
            ),
            actions=[],
        )
        if session.thread_state:
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            return SlackThreadStateMessage(
                text=reply.content,
                thread_state=session.thread_state,
            )
        return outgoing

    @message_handler
    async def on_stage_action(
        self, message: TimeboxingStageAction, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
        """Handle deterministic stage-control actions from Slack buttons."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        session = self._sessions.get(key)
        if not session:
            return TextMessage(
                content="That timeboxing session is no longer active.",
                source=self._agent_source(),
            )
        timeboxing_activity.mark_active(
            user_id=message.user_id,
            channel_id=message.channel_id,
            thread_ts=message.thread_ts,
        )
        self._session_debug(
            session,
            "stage_action",
            action=message.action,
            stage_ready=session.stage_ready,
            stage=session.stage.value if session.stage else None,
        )
        try:
            if message.action == "cancel":
                session.completed = True
                session.thread_state = "canceled"
                timeboxing_activity.mark_inactive(user_id=session.user_id)
                self._session_debug(session, "session_canceled")
                self._close_session_debug_logger(key)
                return TextMessage(
                    content="Okay—stopping this timeboxing session.",
                    source=self._agent_source(),
                )

            if message.action == "back":
                await self._advance_stage(
                    session, next_stage=self._previous_stage(session.stage)
                )
            elif message.action == "proceed":
                if not session.stage_ready:
                    missing_lines = (
                        "\n".join(f"- {item}" for item in (session.stage_missing or []))
                        if session.stage_missing
                        else "- (none listed)"
                    )
                    question = (
                        session.stage_question or "Share missing details, then retry."
                    )
                    blocked_reply = TextMessage(
                        content=(
                            "Cannot proceed yet.\n"
                            "Missing:\n"
                            f"{missing_lines}\n"
                            f"Question: {question}"
                        ),
                        source=self._agent_source(),
                    )
                    outgoing = self._attach_presenter_blocks(
                        reply=blocked_reply, session=session
                    )
                    await self._publish_update(
                        session=session,
                        user_message=(
                            outgoing.content
                            if isinstance(outgoing, TextMessage)
                            else getattr(outgoing, "text", "")
                        ),
                        actions=[],
                    )
                    return outgoing
                await self._proceed(session)
            elif message.action == "redo":
                pass

            session.thread_state = None
            reply = await self._run_graph_turn(session=session, user_text="")
            wrapped = await self._maybe_wrap_constraint_review(
                reply=reply, session=session
            )
            outgoing = self._attach_presenter_blocks(reply=wrapped, session=session)
            await self._publish_update(
                session=session,
                user_message=(
                    outgoing.content
                    if isinstance(outgoing, TextMessage)
                    else getattr(outgoing, "text", "")
                ),
                actions=[],
            )
            if session.thread_state:
                timeboxing_activity.mark_inactive(user_id=session.user_id)
                return SlackThreadStateMessage(
                    text=reply.content,
                    thread_state=session.thread_state,
                )
            return outgoing
        except Exception as exc:
            self._session_debug(
                session,
                "stage_action_error",
                action=message.action,
                error_type=type(exc).__name__,
                error=str(exc)[:2000],
            )
            raise

    @message_handler
    async def on_confirm_submit(
        self, message: TimeboxingConfirmSubmit, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
        """Handle explicit Stage 5 confirm-submit action."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        session = self._sessions.get(key)
        if not session:
            return TextMessage(
                content="That timeboxing session is no longer active.",
                source=self._agent_source(),
            )
        if session.completed or session.thread_state in {"done", "canceled"}:
            return TextMessage(
                content="This session has already ended; submission is no longer available.",
                source=self._agent_source(),
            )
        if not session.pending_submit:
            return TextMessage(
                content="There is no pending plan submission right now.",
                source=self._agent_source(),
            )
        if session.tb_plan is None or session.base_snapshot is None:
            session.pending_submit = False
            return TextMessage(
                content="Cannot submit yet because the plan is incomplete. Please refine first.",
                source=self._agent_source(),
            )

        previous_map = dict(session.event_id_map)
        try:
            tx = await self._calendar_submitter.submit_plan(
                desired=session.tb_plan,
                remote=session.base_snapshot,
                event_id_map=session.event_id_map,
                remote_event_ids_by_index=session.remote_event_ids_by_index,
            )
        except Exception:
            logger.exception("Calendar submission failed.")
            return TextMessage(
                content="Calendar submission failed. Please try again.",
                source=self._agent_source(),
            )

        session.pending_submit = False
        session.committed = True
        session.last_sync_transaction = tx
        session.last_sync_event_id_map = previous_map
        session.event_id_map = self._update_event_id_map_after_submit(
            session=session,
            transaction=tx,
        )
        session.remote_event_ids_by_index = []
        session.base_snapshot = session.tb_plan.model_copy(deep=True)

        if tx.status == "committed":
            text = "Submitted to Google Calendar. You can undo this submission."
        elif tx.status == "partial":
            text = "Submission completed with partial failures. You can undo attempted changes."
        else:
            text = f"Submission finished with status `{tx.status}`."
        return SlackBlockMessage(
            text=text,
            blocks=self._render_submit_result_blocks(
                session=session,
                text=text,
                include_undo=True,
            ),
        )

    @message_handler
    async def on_cancel_submit(
        self, message: TimeboxingCancelSubmit, ctx: MessageContext
    ) -> TextMessage:
        """Handle Stage 5 cancel-submit action and return to refine stage."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        session = self._sessions.get(key)
        if not session:
            return TextMessage(
                content="That timeboxing session is no longer active.",
                source=self._agent_source(),
            )
        session.pending_submit = False
        await self._advance_stage(session, next_stage=TimeboxingStage.REFINE)
        return TextMessage(
            content=(
                "Submission canceled. Returned to Stage 4/5 (Refine). "
                "Share what to adjust next."
            ),
            source=self._agent_source(),
        )

    @message_handler
    async def on_undo_submit(
        self, message: TimeboxingUndoSubmit, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
        """Handle Stage 5 undo-submit action using session-backed transaction state."""
        key = self._session_key(ctx, fallback=message.thread_ts)
        session = self._sessions.get(key)
        if not session:
            return TextMessage(
                content="That timeboxing session is no longer active.",
                source=self._agent_source(),
            )
        if session.completed or session.thread_state in {"done", "canceled"}:
            return TextMessage(
                content="Undo is unavailable because this session has already ended.",
                source=self._agent_source(),
            )
        transaction = session.last_sync_transaction
        if transaction is None:
            return TextMessage(
                content="There is no submission to undo.",
                source=self._agent_source(),
            )

        try:
            undo_tx = await self._calendar_submitter.undo_transaction(transaction)
        except Exception:
            logger.exception("Undo submission failed.")
            return TextMessage(
                content="Undo failed. Please try again.",
                source=self._agent_source(),
            )
        if undo_tx is None:
            return TextMessage(
                content="Undo is not available for the latest transaction.",
                source=self._agent_source(),
            )

        session.pending_submit = False
        session.last_sync_transaction = None
        if session.last_sync_event_id_map is not None:
            session.event_id_map = dict(session.last_sync_event_id_map)
        session.last_sync_event_id_map = None

        await self._advance_stage(session, next_stage=TimeboxingStage.REFINE)
        if session.base_snapshot is not None:
            from .timebox import tb_plan_to_timebox

            session.tb_plan = session.base_snapshot.model_copy(deep=True)
            try:
                session.timebox = tb_plan_to_timebox(session.tb_plan)
            except Exception:
                logger.debug(
                    "Failed to convert restored TBPlan to Timebox after undo.",
                    exc_info=True,
                )

        if undo_tx.status == "undone":
            text = "Undo successful. Restored your plan and returned to Refine."
        else:
            text = (
                f"Undo completed with status `{undo_tx.status}`. "
                "Please review the plan in Refine."
            )
        return SlackBlockMessage(
            text=text,
            blocks=self._render_submit_result_blocks(
                session=session,
                text=text,
                include_undo=False,
            ),
        )

    @message_handler
    async def on_finalise(
        self, message: TimeboxingFinalResult, ctx: MessageContext
    ) -> TextMessage:
        """Handle finalization callbacks and clean up session state."""
        key = self._session_key(ctx)
        session = self._sessions.pop(key, None)
        if session:
            self._session_debug(
                session,
                "session_finalized",
                status=message.status,
                summary=message.summary,
            )
            if session.session_key and session.session_key != key:
                self._close_session_debug_logger(session.session_key)
        self._close_session_debug_logger(key)
        return TextMessage(
            content=f"Session {message.thread_ts} marked {message.status}: {message.summary}",
            source=self.id.type,
        )

    async def cleanup(self) -> None:
        """Cleanup resources before shutdown."""
        for session_key in list(self._session_debug_loggers.keys()):
            self._close_session_debug_logger(session_key)
        if self._calendar_client:
            await self._calendar_client.close()
        # Add cleanup for other MCP clients if needed
        if self._constraint_memory_client:
            # ConstraintMemoryClient might also need cleanup
            pass


def _extract_constraint_batch(response: object) -> ConstraintBatch | None:
    """Extract a constraint batch from an agent response."""
    content = getattr(getattr(response, "chat_message", None), "content", None)
    if isinstance(content, ConstraintBatch):
        return content
    if content is not None:
        try:
            return ConstraintBatch.model_validate(content)
        except Exception:
            return None
    return None


# TODO: remove this hacky bullshit
def _capture_from_content(content) -> Timebox | None:
    """Parse a Timebox instance from arbitrary content payloads."""
    if isinstance(content, Timebox):
        return content
    if isinstance(content, dict):
        try:
            return Timebox.model_validate(content)
        except Exception:
            return None
    return None


# TODO: remove this hacky bullshit
def _capture_timebox(session: Session, content) -> None:
    """Capture a timebox into the session when present."""
    timebox = _capture_from_content(content)
    if timebox:
        session.timebox = timebox


def _build_actions(
    before: Timebox,
    after: Timebox,
    *,
    reason: str,
    constraints: List[Constraint],
) -> List[TimeboxAction]:
    """Compute timebox change actions for downstream logging."""
    actions: List[TimeboxAction] = []
    before_map = _event_map(before.events)
    after_map = _event_map(after.events)

    for key, event in after_map.items():
        if key not in before_map:
            actions.append(
                TimeboxAction(
                    kind="insert",
                    event_key=key,
                    summary=event.summary,
                    to_time=_format_time(event.start_time),
                    reason=_build_reason(reason, constraints),
                )
            )

    for key, event in before_map.items():
        if key not in after_map:
            actions.append(
                TimeboxAction(
                    kind="delete",
                    event_key=key,
                    summary=event.summary,
                    from_time=_format_time(event.start_time),
                    reason=_build_reason(reason, constraints),
                )
            )

    for key, event in after_map.items():
        if key not in before_map:
            continue
        before_event = before_map[key]
        if (
            before_event.start_time != event.start_time
            or before_event.end_time != event.end_time
        ):
            actions.append(
                TimeboxAction(
                    kind="move",
                    event_key=key,
                    summary=event.summary,
                    from_time=_format_time(before_event.start_time),
                    to_time=_format_time(event.start_time),
                    reason=_build_reason(reason, constraints),
                )
            )
        elif (
            before_event.summary != event.summary
            or before_event.description != event.description
            or before_event.location != event.location
        ):
            actions.append(
                TimeboxAction(
                    kind="update",
                    event_key=key,
                    summary=event.summary,
                    reason=_build_reason(reason, constraints),
                )
            )

    return actions


def _build_reason(user_message: str, constraints: List[Constraint]) -> str:
    """Build a human-readable reason string for action logs."""
    names = [c.name for c in constraints if c.name]
    if names:
        return f"user: {user_message} | constraints: {', '.join(names)}"
    return f"user: {user_message}"


def _event_map(events: List[object]) -> Dict[str, object]:
    """Return a stable mapping of events keyed by identifiers."""
    mapping: Dict[str, object] = {}
    for idx, event in enumerate(events):
        key = _event_key(event, idx)
        mapping[key] = event
    return mapping


def _event_key(event: object, idx: int) -> str:
    """Return a stable identifier for a timebox event."""
    event_id = getattr(event, "eventId", None)
    if event_id:
        return f"id:{event_id}"
    summary = getattr(event, "summary", None) or "event"
    start = getattr(event, "start_time", None)
    end = getattr(event, "end_time", None)
    if start or end:
        return f"{summary}:{start}:{end}"
    return f"{summary}:{idx}"


def _format_time(value) -> str | None:
    """Format a time value as HH:MM when present."""
    if value is None:
        return None
    return value.strftime("%H:%M")


def _wrap_with_constraint_review(
    message: TextMessage,
    *,
    constraints: list[Constraint],
    session: Session,
) -> SlackBlockMessage:
    """Attach single-row constraint review cards to a stage response."""
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": message.content}},
    ]
    if constraints:
        blocks.append({"type": "divider"})
        blocks.extend(
            build_constraint_row_blocks(
                constraints,
                thread_ts=session.thread_ts,
                user_id=session.user_id,
            )
        )
    return SlackBlockMessage(text=message.content, blocks=blocks)


# TODO(refactor): Replace manual enum coercion with Pydantic model validation.
def _parse_enum(enum_cls: Type[TEnum], value: object, default: TEnum) -> TEnum:
    """Coerce a value into the requested Enum, or return a default."""
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    try:
        return enum_cls(str(value).lower())
    except Exception:
        return default


# TODO(refactor): Parse enums/dates via Pydantic fields instead of try/except.
def _parse_dow(value: str | None) -> ConstraintDayOfWeek | None:
    """Parse a day-of-week enum from a string."""
    if not value:
        return None
    try:
        return ConstraintDayOfWeek(str(value).upper())
    except Exception:
        return None


# TODO(refactor): Parse ISO dates via Pydantic fields instead of try/except.
def _parse_date_value(value: str | None) -> date | None:
    """Parse an ISO date string into a date."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


# TODO: this should be part of a tool, not bolted onto an agent
def _constraints_from_memory(
    records: list[dict[str, Any]], *, user_id: str
) -> list[Constraint]:
    """Convert constraint-memory records to local Constraint instances."""
    constraints: list[Constraint] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        necessity = _parse_enum(
            ConstraintNecessity, record.get("necessity"), ConstraintNecessity.SHOULD
        )
        status = _parse_enum(
            ConstraintStatus, record.get("status"), ConstraintStatus.PROPOSED
        )
        source = _parse_enum(
            ConstraintSource, record.get("source"), ConstraintSource.SYSTEM
        )
        scope = _parse_enum(
            ConstraintScope, record.get("scope"), ConstraintScope.PROFILE
        )
        days_raw = record.get("days_of_week") or []
        days = [d for d in (_parse_dow(v) for v in days_raw) if d]
        hints = {}
        uid = record.get("uid")
        if uid:
            hints["uid"] = uid
        rule_kind = record.get("rule_kind") or record.get("type_id")
        if rule_kind:
            hints["rule_kind"] = rule_kind
        constraints.append(
            Constraint(
                user_id=user_id,
                channel_id=None,
                thread_ts=None,
                name=record.get("name") or "Constraint",
                description=record.get("description") or "",
                necessity=necessity,
                status=status,
                source=source,
                scope=scope,
                tags=list(record.get("topics") or []),
                hints=hints,
                start_date=_parse_date_value(record.get("start_date")),
                end_date=_parse_date_value(record.get("end_date")),
                days_of_week=days,
                timezone=record.get("timezone"),
            )
        )
    return constraints


def _constraint_identity_key(constraint: ConstraintBase) -> str:
    """Build a stable identity key for a constraint to support dedupe."""
    hints = constraint.hints if isinstance(constraint.hints, dict) else {}
    uid = hints.get("uid")
    if uid:
        return f"uid:{uid}"
    necessity = constraint.necessity.value if constraint.necessity else ""
    scope = constraint.scope.value if constraint.scope else ""
    return "|".join(
        [
            (constraint.name or "").strip().lower(),
            (constraint.description or "").strip().lower(),
            necessity,
            scope,
        ]
    )


def _dedupe_constraints(constraints: list[Constraint]) -> list[Constraint]:
    """Return a de-duplicated list of constraints."""
    seen: set[str] = set()
    deduped: list[Constraint] = []
    for constraint in constraints:
        key = _constraint_identity_key(constraint)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(constraint)
    return deduped


# TODO: this should not be neccesary at all
def _coerce_async_database_url(database_url: str) -> str:
    """Ensure a database URL uses an async driver when needed."""
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


__all__ = ["TimeboxingFlowAgent"]
