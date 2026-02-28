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
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Literal, ParamSpec, Type, TypeVar
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
from pydantic import TypeAdapter, ValidationError, model_validator
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.agents.timeboxing.constraint_search_tool import (
    ConstraintSearchQuery,
    search_constraints,
)
from fateforger.core.config import settings
from fateforger.core.logging_config import observe_stage_duration
from fateforger.debug.diag import with_timeout
from fateforger.debug.log_index import append_index_entry
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.llm import (
    assert_strict_tools_for_structured_output,
    build_autogen_chat_client,
)
from fateforger.llm.toon import toon_encode
from fateforger.slack_bot.constraint_review import (
    CONSTRAINT_REVIEW_ALL_ACTION_ID,
    CONSTRAINT_ROW_REVIEW_ACTION_ID,
    build_constraint_review_all_action_block,
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
from fateforger.tools.ticktick_mcp import TickTickMcpClient, get_ticktick_mcp_url

from .actions import TimeboxAction
from .constants import TIMEBOXING_FALLBACK, TIMEBOXING_LIMITS, TIMEBOXING_TIMEOUTS
from .constraint_memory_component import ConstraintPlanningMemory
from .constraint_retriever import STARTUP_PREFETCH_TAG, ConstraintRetriever
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
from .durable_constraint_store import (
    DurableConstraintStore,
    build_durable_constraint_store,
)
from .flow_graph import build_timeboxing_graphflow
from .mcp_clients import McpCalendarClient
from .mem0_constraint_memory import build_mem0_client_from_settings
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
from .notion_constraint_extractor import NotionConstraintExtractor
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
from .scheduler_prefetch_capability import SchedulerPrefetchCapability
from .stage_gating import (
    CAPTURE_INPUTS_PROMPT,
    COLLECT_CONSTRAINTS_PROMPT,
    DECISION_PROMPT,
    REVIEW_COMMIT_PROMPT,
    TIMEBOX_SUMMARY_PROMPT,
    ConstraintsSection,
    FreeformSection,
    NextStepsSection,
    SessionMessage,
    StageDecision,
    StageGateOutput,
    TimeboxingStage,
)
from .submitter import CalendarSubmitter
from .sync_engine import (
    FFTB_PREFIX,
    SyncTransaction,
    gcal_response_to_tb_plan_with_identity,
)
from .task_marshalling_capability import TaskMarshallingCapability
from .tb_models import TBEvent, TBPlan
from .timebox import Timebox, tb_plan_to_timebox, timebox_to_tb_plan
from .tool_result_models import InteractionMode, MemoryConstraintItem, MemoryToolResult
from .tool_result_presenter import InteractionContext, present_memory_tool_result
from .toon_views import (
    constraints_rows,
    immovables_rows,
    tasks_rows,
    timebox_events_rows,
)

logger = logging.getLogger(__name__)
TEnum = TypeVar("TEnum", bound=Enum)
P = ParamSpec("P")
R = TypeVar("R")


def _fallback_on_parse_error(default: R) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Return a decorator that falls back when pydantic/date parsing fails."""

    def _decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def _wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return func(*args, **kwargs)
            except (ValidationError, TypeError, ValueError):
                return default

        return _wrapped

    return _decorator


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


class _CalendarSnapshotEvent(BaseModel):
    """Strict event shape used when normalizing remote calendar snapshots."""

    model_config = {"extra": "ignore", "populate_by_name": True}

    summary: str = PydanticField(min_length=1)
    event_type: EventType = PydanticField(default=EventType.MEETING, alias="type")
    start_time: time | None = PydanticField(default=None, alias="ST")
    end_time: time | None = PydanticField(default=None, alias="ET")
    duration: timedelta | None = PydanticField(default=None, alias="DT")
    start: datetime | date | None = None
    end: datetime | date | None = None
    calendarId: str = "primary"
    timeZone: str = "UTC"
    description: str | None = None
    eventId: str | None = None

    @model_validator(mode="after")
    def _require_timing(self) -> "_CalendarSnapshotEvent":
        if not any(
            (
                self.start is not None,
                self.end is not None,
                self.start_time is not None,
                self.end_time is not None,
                self.duration is not None,
            )
        ):
            raise ValueError("calendar snapshot event requires at least one timing field")
        return self

    def to_calendar_event(self) -> CalendarEvent:
        payload = self.model_dump(mode="python", by_alias=False, exclude_none=True)
        return CalendarEvent.model_validate(payload)


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
    prefetched_pending_tasks: List[TaskCandidate] = field(default_factory=list)
    pending_tasks_prefetch: bool = False
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
    reply_turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    graph_turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    skip_stage_execution: bool = False
    force_stage_rerun: bool = False
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


@dataclass
class CalendarSyncOutcome:
    """Structured result for calendar sync reporting in Stage 4/5 turns."""

    status: str
    changed: bool
    created: int = 0
    updated: int = 0
    deleted: int = 0
    failed: int = 0
    note: str = ""
    failed_details: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RefineToolExecutionOutcome:
    """Result of prompt-guided Stage 4 tool orchestration."""

    patch_selected: bool
    memory_queued: bool
    fallback_patch_used: bool
    calendar: CalendarSyncOutcome
    memory_selected: bool = False
    memory_operations: list[str] = field(default_factory=list)


async def get_constraint_mcp_tools() -> list:
    """Acquire constraint MCP tools from the Notion constraint MCP server.

    Returns the raw tool list from :func:`mcp_server_tools` for the configured
    Notion constraint endpoint.  Callers must handle connection errors.
    """
    from autogen_ext.tools.mcp import StreamableHttpServerParams, mcp_server_tools

    from fateforger.tools.notion_mcp import get_notion_mcp_url

    params = StreamableHttpServerParams(
        url=get_notion_mcp_url(),
        headers={},
        timeout=10.0,
    )
    return await mcp_server_tools(params)


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
        self._constraint_memory_client: Any | None = None
        self._constraint_memory_unavailable_reason: str | None = None
        self._durable_constraint_store: DurableConstraintStore | None = None
        self._ticktick_client: TickTickMcpClient | None = None
        self._constraint_store: ConstraintStore | None = None
        self._constraint_engine = None
        self._constraint_agent = self._build_constraint_agent()
        self._constraint_retriever = ConstraintRetriever()
        self._timebox_patcher = TimeboxPatcher()
        self._calendar_submitter = CalendarSubmitter()
        self._task_marshalling = TaskMarshallingCapability(
            send_message=self.send_message,
            timeout_s=TIMEBOXING_TIMEOUTS.tasks_snapshot_s,
            source_resolver=self._agent_source,
        )
        self._scheduler_prefetch = SchedulerPrefetchCapability(
            queue_constraint_prefetch=self._queue_constraint_prefetch,
            await_pending_durable_prefetch=self._await_pending_durable_constraint_prefetch,
            ensure_calendar_immovables=self._ensure_calendar_immovables,
            prefetch_calendar_immovables=self._prefetch_calendar_immovables,
            is_collect_stage_loaded=self._is_collect_stage_loaded,
        )
        self._constraint_search_tool: FunctionTool | None = None
        self._durable_constraint_task_keys: set[str] = set()
        self._durable_dedupe_task_keys: set[str] = set()
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
        self._constraint_mcp_tools: list | None = None
        self._notion_extractor: NotionConstraintExtractor | None = None
        self._constraint_extractor_tool: FunctionTool | None = None

    # region helpers

    def _session_key(self, ctx: MessageContext, *, fallback: str | None = None) -> str:
        """Return a stable session key for the current routing context."""
        if fallback:
            return fallback
        topic_key = ctx.topic_id.source if ctx.topic_id else None
        if topic_key:
            return topic_key
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

    def _resolve_tz_name(self, tz_name: str | None) -> str:
        """Normalize an IANA timezone name to a valid value."""
        candidate = (tz_name or "").strip() or "UTC"
        try:
            ZoneInfo(candidate)
            return candidate
        except Exception:
            return "UTC"

    def _ensure_uncommitted_session(
        self,
        *,
        key: str,
        thread_ts: str,
        channel_id: str,
        user_id: str,
        user_input: str,
        tz_name: str,
        default_planned_date: str,
        debug_event: str,
        start_message: str | None = None,
    ) -> tuple[Session, bool]:
        """Get existing session or create an uncommitted session deterministically."""
        session = self._sessions.get(key)
        if session:
            if session.session_key is None:
                session.session_key = key
            return session, False
        session = Session(
            thread_ts=thread_ts,
            channel_id=channel_id,
            user_id=user_id,
            last_user_message=user_input,
            start_message=start_message,
            committed=False,
            planned_date=default_planned_date,
            tz_name=tz_name,
            session_key=key,
        )
        self._sessions[key] = session
        self._session_debug(
            session,
            debug_event,
            committed=False,
            user_input=(user_input or "")[:500],
        )
        return session, True

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

    def _refresh_temporal_facts(self, session: Session) -> None:
        """Refresh timezone-local temporal anchors used by stage prompts."""
        tz_name = (session.tz_name or "UTC").strip() or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz_name = "UTC"
            tz = ZoneInfo("UTC")
            session.tz_name = tz_name
        local_now = datetime.now(timezone.utc).astimezone(tz)
        session.frame_facts["date"] = session.planned_date or local_now.date().isoformat()
        session.frame_facts["timezone"] = tz_name
        session.frame_facts["current_time"] = local_now.strftime("%H:%M")
        session.frame_facts["current_datetime"] = local_now.isoformat(timespec="minutes")

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
        append_index_entry(
            index_path=log_dir
            / os.getenv(
                "TIMEBOX_SESSION_INDEX_FILE", "timeboxing_session_index.jsonl"
            ),
            entry={
                "type": "timeboxing_session",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "session_key": session.session_key,
                "thread_ts": session.thread_ts,
                "channel_id": session.channel_id,
                "user_id": session.user_id,
                "planned_date": session.planned_date,
                "stage": session.stage.value if session.stage else None,
                "log_path": str(file_path),
                "pid": os.getpid(),
            },
        )
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
        async with session.graph_turn_lock:
            turn_started_at = perf_counter()
            turn_elapsed_s = lambda: round(perf_counter() - turn_started_at, 3)
            self._refresh_temporal_facts(session)
            self._session_debug(
                session,
                "graph_turn_start",
                user_text=(user_text or "")[:500],
            )
            flow = self._ensure_graphflow(session)
            presenter: TextMessage | None = None

            async def _run_stream() -> TextMessage | None:
                presenter_message: TextMessage | None = None
                async for item in flow.run_stream(
                    task=TextMessage(content=user_text, source="user")
                ):
                    if isinstance(item, TextMessage) and item.source == "PresenterNode":
                        presenter_message = item
                return presenter_message

            try:
                presenter = await with_timeout(
                    "timeboxing:graph-turn",
                    _run_stream(),
                    timeout_s=TIMEBOXING_TIMEOUTS.graph_turn_s,
                    dump_on_timeout=False,
                    dump_threads_on_timeout=False,
                )
            except TimeoutError as exc:
                timeout_message = (
                    "This turn hit a processing timeout. Reply `Redo` to retry this stage."
                )
                self._session_debug(
                    session,
                    "graph_turn_timeout",
                    error_type=type(exc).__name__,
                    timeout_s=TIMEBOXING_TIMEOUTS.graph_turn_s,
                    elapsed_s=turn_elapsed_s(),
                )
                self._session_debug(
                    session,
                    "graph_turn_end",
                    presenter_found=False,
                    output_preview=timeout_message[:500],
                    elapsed_s=turn_elapsed_s(),
                )
                observe_stage_duration(
                    stage=session.stage.value if session.stage else "unknown",
                    duration_s=turn_elapsed_s(),
                )
                return TextMessage(content=timeout_message, source=self.id.type)
            except Exception as exc:
                self._session_debug(
                    session,
                    "graph_turn_error",
                    error_type=type(exc).__name__,
                    error=str(exc)[:2000],
                    elapsed_s=turn_elapsed_s(),
                )
                raise
            content = (
                presenter.content
                if presenter
                else "No stage response was generated. Reply `Redo` to retry this stage."
            )
            elapsed_s = turn_elapsed_s()
            self._session_debug(
                session,
                "graph_turn_end",
                presenter_found=presenter is not None,
                output_preview=content[:500],
                elapsed_s=elapsed_s,
            )
            observe_stage_duration(
                stage=session.stage.value if session.stage else "unknown",
                duration_s=elapsed_s,
            )
            if elapsed_s >= TIMEBOXING_TIMEOUTS.slow_turn_warn_s:
                self._session_debug(
                    session,
                    "graph_turn_slow",
                    elapsed_s=elapsed_s,
                    threshold_s=TIMEBOXING_TIMEOUTS.slow_turn_warn_s,
                    user_text_preview=(user_text or "")[:200],
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
                dump_on_timeout=False,
                dump_threads_on_timeout=False,
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
    def _ensure_constraint_memory_client(self) -> Any | None:
        """Return the constraint-memory MCP client, initializing it if needed."""
        if self._constraint_memory_client:
            return self._constraint_memory_client
        if self._constraint_memory_unavailable_reason:
            return None
        try:
            user_id = str(getattr(settings, "mem0_user_id", "") or "").strip() or "timeboxing"
            self._constraint_memory_client = build_mem0_client_from_settings(
                user_id=user_id
            )
            self._constraint_memory_unavailable_reason = None
        except Exception as exc:
            self._constraint_memory_unavailable_reason = (
                f"{type(exc).__name__}: {exc}"
            )
            logger.warning(
                "Failed to initialize Mem0 constraint memory client; disabling retries "
                "for this runtime instance (%s)",
                self._constraint_memory_unavailable_reason,
            )
            return None
        return self._constraint_memory_client

    def _ensure_durable_constraint_store(self) -> DurableConstraintStore | None:
        """Return a backend-neutral durable-memory store adapter."""
        existing = getattr(self, "_durable_constraint_store", None)
        if existing is not None:
            return existing
        client = self._ensure_constraint_memory_client()
        store = build_durable_constraint_store(client)
        self._durable_constraint_store = store
        return store

    # TODO: thia should be a tool, not a bolted on method
    async def _fetch_durable_constraints(
        self, session: Session, *, stage: TimeboxingStage
    ) -> List[Constraint]:
        """Fetch durable constraints for a stage from the configured memory backend."""
        store = self._ensure_durable_constraint_store()
        if not store:
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
                client=store,
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
        self._task_marshalling.queue_prefetch(
            session=session,
            reason="prefetch",
            append_background_update=self._append_background_update_once,
        )
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

    async def _prime_collect_prefetch_non_blocking(
        self, *, session: Session, planned_date: str, blocking: bool = False
    ) -> None:
        """Prime collect-stage prefetch with optional blocking stage gate."""
        scheduler_prefetch = getattr(self, "_scheduler_prefetch", None)
        if scheduler_prefetch is not None:
            await scheduler_prefetch.prime_committed_collect_context(
                session=session,
                blocking=blocking,
            )
            return
        if blocking:
            await self._await_pending_durable_constraint_prefetch(
                session,
                stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            )
            if planned_date:
                await self._prefetch_calendar_immovables(session, planned_date)
            else:
                await self._ensure_calendar_immovables(
                    session, timeout_s=TIMEBOXING_TIMEOUTS.calendar_prefetch_wait_s
                )
            return
        if planned_date:
            asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
        self._queue_constraint_prefetch(session)

    def _queue_durable_constraint_prefetch(
        self,
        *,
        session: Session,
        reason: str,
        include_secondary: bool = True,
    ) -> None:
        """Start background durable constraint prefetch if needed."""
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
                        if self._constraint_needs_confirmation(constraint):
                            hints = (
                                dict(constraint.hints)
                                if isinstance(constraint.hints, dict)
                                else {}
                            )
                            hints["needs_confirmation"] = True
                            constraint.hints = hints
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
            except Exception as exc:
                logger.warning(
                    "Constraint extraction failed (reason=%s task_key=%s): %s",
                    reason,
                    task_key,
                    exc,
                    exc_info=True,
                )
                self._append_background_update_once(
                    session,
                    "Couldn't update remembered constraints from that message. "
                    "Calendar patching can still continue.",
                )
                self._session_debug(
                    session,
                    "constraint_extraction_error",
                    reason=reason,
                    task_key=task_key,
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
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
        """Queue durable constraint upserts into the configured durable-memory backend."""
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
            """Upsert durable constraints on a background task."""
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
                if persisted > 0:
                    self._append_background_update_once(
                        session,
                        f"Saved {persisted} durable constraint(s).",
                    )
                    self._queue_durable_constraint_dedupe(
                        session=session,
                        reason="post_upsert",
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
                    "Failed to save durable constraint(s); continuing with local session constraints.",
                )
            finally:
                if acquired:
                    self._durable_constraint_semaphore.release()
                self._durable_constraint_task_keys.discard(task_key)

        asyncio.create_task(_background())

    def _queue_durable_constraint_dedupe(
        self,
        *,
        session: Session,
        reason: str,
    ) -> None:
        """Queue non-blocking durable dedupe to clean legacy overlaps."""
        if not hasattr(self, "_durable_dedupe_task_keys"):
            self._durable_dedupe_task_keys = set()
        task_payload = {
            "user_id": session.user_id,
            "planned_date": session.planned_date,
            "stage": session.stage.value if session.stage else None,
            "reason": reason,
        }
        task_key = hashlib.sha256(
            json.dumps(task_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[: TIMEBOXING_LIMITS.durable_task_key_len]
        if task_key in self._durable_dedupe_task_keys:
            return
        self._durable_dedupe_task_keys.add(task_key)

        async def _background() -> None:
            acquired = False
            try:
                await self._durable_constraint_semaphore.acquire()
                acquired = True
                store = self._ensure_durable_constraint_store()
                if store is None:
                    return
                result = await store.dedupe_constraints(limit=2000, dry_run=False)
                archived = int(result.get("duplicates_archived") or 0)
                if archived > 0:
                    self._append_background_update_once(
                        session,
                        f"Merged and archived {archived} duplicate durable constraint(s).",
                    )
                if hasattr(self, "_session_debug_loggers"):
                    self._session_debug(
                        session,
                        "durable_dedupe",
                        reason=reason,
                        scanned=int(result.get("scanned") or 0),
                        duplicate_groups=int(result.get("duplicate_groups") or 0),
                        duplicates_archived=archived,
                        failed_archives=int(result.get("failed_archives") or 0),
                    )
            except Exception:
                logger.warning(
                    "Durable constraint dedupe failed (task_key=%s)",
                    task_key,
                    exc_info=True,
                )
            finally:
                if acquired:
                    self._durable_constraint_semaphore.release()
                self._durable_dedupe_task_keys.discard(task_key)

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
        store = self._ensure_durable_constraint_store()
        if store is None:
            return 0
        persisted = 0
        reused_existing = 0
        created_new = 0
        merge_conflicts = 0
        dedupe_matches = 0
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
                equivalent = await store.find_equivalent_constraint(record=record, limit=200)
                equivalent_uid = ""
                equivalent_record: dict[str, Any] = {}
                if isinstance(equivalent, dict):
                    equivalent_uid = str(equivalent.get("uid") or "").strip()
                    maybe_record = equivalent.get("constraint_record")
                    if isinstance(maybe_record, dict):
                        equivalent_record = dict(maybe_record)

                if equivalent_uid and equivalent_record:
                    dedupe_matches += 1
                    incoming_record = dict(record.get("constraint_record") or {})
                    merge_fn = getattr(store, "merge_constraint_records", None)
                    if callable(merge_fn):
                        merged_record = merge_fn(
                            current=equivalent_record,
                            incoming=incoming_record,
                        )
                    else:
                        merged_record = incoming_record
                    lifecycle = dict(merged_record.get("lifecycle") or {})
                    lifecycle["uid"] = equivalent_uid
                    merged_record["lifecycle"] = lifecycle
                    patch_ops_fn = getattr(store, "build_constraint_json_patch_ops", None)
                    patch_ops: list[dict[str, Any]] = []
                    if callable(patch_ops_fn):
                        patch_ops = patch_ops_fn(
                            current=equivalent_record,
                            merged=merged_record,
                        )
                    if not patch_ops:
                        reused_existing += 1
                        persisted += 1
                        continue

                    update_result = await store.update_constraint(
                        uid=equivalent_uid,
                        patch={
                            "constraint_record": merged_record,
                            "json_patch_ops": patch_ops,
                        },
                        event={
                            **event,
                            "action": "semantic_upsert",
                            "matched_uid": equivalent_uid,
                        },
                    )
                    if update_result.get("updated"):
                        reused_existing += 1
                        persisted += 1
                        continue
                    merge_conflicts += 1

                result = await store.upsert_constraint(record=record, event=event)
                if result.get("uid") or result.get("page_id"):
                    persisted += 1
                    created_new += 1
            except Exception:
                logger.debug(
                    "Deterministic durable upsert failed for constraint=%s",
                    constraint.name,
                    exc_info=True,
                )
        logger.info(
            "durable constraint upsert summary: persisted=%s created=%s reused=%s matches=%s merge_conflicts=%s",
            persisted,
            created_new,
            reused_existing,
            dedupe_matches,
            merge_conflicts,
        )
        return persisted

    def _build_durable_constraint_record(
        self,
        *,
        session: Session,
        constraint: ConstraintBase,
        decision_scope: str | None,
    ) -> dict[str, Any]:
        """Map a local extracted constraint to a durable-memory upsert payload."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        selector = constraint.selector if isinstance(constraint.selector, dict) else {}
        scope = constraint.scope.value if constraint.scope else (decision_scope or "profile")
        rule_kind = self._resolve_rule_kind(hints=hints, selector=selector)
        scalar_params = self._extract_scalar_params(hints=hints, selector=selector)
        windows = self._extract_windows(hints=hints, selector=selector)
        uid = self._build_durable_constraint_uid(
            session=session,
            constraint=constraint,
            scope=scope,
            rule_kind=rule_kind,
            scalar_params=scalar_params,
            windows=windows,
        )
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
        rule_kind: str | None,
        scalar_params: dict[str, Any],
        windows: list[dict[str, Any]],
    ) -> str:
        """Build a stable idempotency key for durable upserts."""
        normalized_tags = sorted(
            {
                str(tag).strip().lower()
                for tag in (constraint.tags or [])
                if isinstance(tag, str) and str(tag).strip()
            }
        )
        normalized_windows = sorted(
            {
                (
                    str(item.get("kind") or "").strip().lower(),
                    str(item.get("start_time_local") or "").strip(),
                    str(item.get("end_time_local") or "").strip(),
                )
                for item in (windows or [])
                if isinstance(item, dict)
            }
        )
        normalized_scalars = {
            key: scalar_params[key]
            for key in sorted(scalar_params.keys())
            if key in {"duration_min", "duration_max", "contiguity"}
        }
        material = {
            "user_id": session.user_id,
            "scope": scope,
            "name": (constraint.name or "").strip().lower(),
            # Keep UID stable for semantic updates even when prose wording changes.
            "rule_kind": (rule_kind or "").strip().lower(),
            "scalar_params": normalized_scalars,
            "windows": normalized_windows,
            "tags": normalized_tags,
            "start_date": (
                constraint.start_date.isoformat()
                if constraint.start_date is not None
                else None
            ),
            "end_date": (
                constraint.end_date.isoformat() if constraint.end_date is not None else None
            ),
            "days_of_week": sorted(day.value for day in (constraint.days_of_week or [])),
            "timezone": constraint.timezone or session.tz_name,
            "recurrence": constraint.recurrence,
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
        fail_on_timeout: bool = True,
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
            if not fail_on_timeout:
                self._session_debug(
                    session,
                    "durable_prefetch_soft_timeout",
                    stage=stage.value,
                    timeout_s=timeout_s,
                )
                return
            msg = (
                f"Saved constraints timed out after {int(timeout_s)}s for {stage.value}. "
                "You can continue now and use Redo to retry after a moment."
            )
            session.durable_constraints_failed_stages[stage.value] = msg
            self._append_background_update_once(session, msg)
            logger.error(msg)
        except Exception as exc:
            msg = (
                f"Saved constraints failed for {stage.value}: "
                f"{type(exc).__name__}: {str(exc)[:180]}"
            )
            session.durable_constraints_failed_stages[stage.value] = msg
            self._append_background_update_once(session, msg)
            logger.error(msg, exc_info=True)

    async def _prefetch_calendar_immovables(
        self,
        session: Session,
        planned_date: str,
        *,
        force_refresh: bool = False,
    ) -> None:
        """Fetch calendar immovables + remote identity for the planned date."""
        if (
            not force_refresh
            and planned_date in session.prefetched_immovables_by_date
            and planned_date in session.prefetched_remote_snapshots_by_date
        ):
            self._session_debug(
                session,
                "calendar_prefetch_skip_cached",
                planned_date=planned_date,
            )
            return
        if force_refresh and (
            planned_date in session.prefetched_immovables_by_date
            and planned_date in session.prefetched_remote_snapshots_by_date
        ):
            self._session_debug(
                session,
                "calendar_prefetch_force_refresh",
                planned_date=planned_date,
            )
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
        diagnostics: dict[str, Any] = {}
        try:
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
        except Exception as exc:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)
            self._session_debug(
                session,
                "calendar_prefetch_error",
                planned_date=planned_date,
                error="list_day_snapshot_failed",
                error_type=type(exc).__name__,
                error_detail=(str(exc) or type(exc).__name__)[:1200],
                diagnostics=diagnostics,
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
        if self._constraint_search_tool is None:
            self._constraint_search_tool = self._build_constraint_search_tool()

        optional_constraint_tools: list | None = (
            [self._constraint_search_tool] if self._constraint_search_tool else None
        )

        def _schema_prompt(prompt: str, model_type: Type) -> str:
            """Append a JSON schema for text-mode structured outputs."""
            schema = TypeAdapter(model_type).json_schema()
            schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2)
            return (
                f"{prompt}\n\n"
                "Return ONLY valid JSON matching this schema.\n"
                f"JSON Schema:\n```json\n{schema_json}\n```"
            )

        def build(
            name: str,
            prompt: str,
            out_type,
            *,
            tools: list[FunctionTool] | None = None,
            max_tool_iterations: int = 2,
            structured_output: bool = True,
        ) -> AssistantAgent:
            """Construct a stage helper agent with shared configuration."""
            output_type = out_type if structured_output else None
            assert_strict_tools_for_structured_output(
                tools=tools,
                output_content_type=output_type,
                agent_name=name,
            )
            return AssistantAgent(
                name=name,
                model_client=self._model_client,
                tools=tools,
                output_content_type=output_type,
                system_message=(
                    prompt if structured_output else _schema_prompt(prompt, out_type)
                ),
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
                structured_output=True,
            ),
            TimeboxingStage.CAPTURE_INPUTS: build(
                "StageCaptureInputs",
                CAPTURE_INPUTS_PROMPT,
                StageGateOutput,
                tools=optional_constraint_tools,
                max_tool_iterations=3,
                structured_output=True,
            ),
        }
        self._decision_agent = build("StageDecision", DECISION_PROMPT, StageDecision)
        self._summary_agent = build(
            "StageTimeboxSummary",
            TIMEBOX_SUMMARY_PROMPT,
            StageGateOutput,
            structured_output=False,
        )
        self._review_commit_agent = build(
            "StageReviewCommit",
            REVIEW_COMMIT_PROMPT,
            StageGateOutput,
            structured_output=False,
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
        try:
            response = await with_timeout(
                f"timeboxing:stage:{stage.value}",
                agent.on_messages(
                    [TextMessage(content=task, source="user")], CancellationToken()
                ),
                timeout_s=TIMEBOXING_TIMEOUTS.stage_gate_s,
            )
        except TimeoutError as exc:
            error = f"Stage gate timeout for {stage.value}: {type(exc).__name__}"
            logger.warning(error)
            return self._build_stage_gate_fallback(
                stage=stage,
                context=context,
                error=error,
                missing="stage gate timeout",
                question="This stage timed out. Reply in thread with `Redo` to retry, or share updates to continue.",
            )
        except Exception as exc:
            error = (
                f"Stage gate execution failed for {stage.value}: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.error(error, exc_info=True)
            return self._build_stage_gate_fallback(
                stage=stage,
                context=context,
                error=error,
                missing="stage retry required",
                question="I hit an internal stage error. Reply in thread with `Redo` to retry this stage.",
            )
        try:
            return parse_chat_content(StageGateOutput, response)
        except Exception as exc:
            error = (
                f"Stage gate parse failed for {stage.value}: "
                f"{type(exc).__name__}: {exc}"
            )
            logger.error(error, exc_info=True)
            return self._build_stage_gate_fallback(
                stage=stage,
                context=context,
                error=error,
                missing="stage response parse failure",
                question="Reply in thread with `Redo` to retry this stage, or provide any updates and continue.",
            )

    @staticmethod
    def _build_stage_gate_fallback(
        *,
        stage: TimeboxingStage,
        context: dict[str, Any],
        error: str,
        missing: str,
        question: str,
    ) -> StageGateOutput:
        """Build a safe gate result for recoverable stage failures."""
        fallback_facts = dict(context.get("facts") or {})
        fallback_facts["_stage_gate_error"] = error
        return StageGateOutput(
            stage_id=stage,
            ready=False,
            summary=[
                "I hit an internal stage-processing issue.",
                "I kept your known facts and can continue once you confirm or retry.",
            ],
            missing=[missing],
            question=question,
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
        necessity_map = _constraint_necessity_rank()
        necessity_value: ConstraintNecessity | str = constraint.necessity
        if necessity_value not in necessity_map and necessity_value is not None:
            necessity_value = str(necessity_value).lower()
        necessity_rank = necessity_map.get(necessity_value, 3)
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
        self._refresh_temporal_facts(session)
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
        input_facts = TaskMarshallingCapability.merge_prefetched_tasks(
            input_facts=dict(session.input_facts or {}),
            prefetched=list(session.prefetched_pending_tasks or []),
        )
        return CaptureInputsContext(
            user_message=user_message,
            frame_facts=dict(session.frame_facts or {}),
            input_facts=input_facts,
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
        except Exception:
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

    def _render_constraints_preview_blocks(
        self,
        *,
        session: Session,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Render a compact constraint preview with a modal entrypoint for the full list."""
        constraints = list(session.active_constraints or [])
        if not constraints:
            return []
        ranked = sorted(constraints, key=_constraint_priority)
        blocks: list[dict[str, Any]] = [
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Constraints*\n"
                        f"Showing the top {min(limit, len(ranked))} of {len(ranked)}."
                    ),
                },
            },
        ]
        blocks.extend(
            build_constraint_row_blocks(
                ranked,
                thread_ts=session.thread_ts,
                user_id=session.user_id,
                limit=limit,
                button_text="Deny / Edit",
            )
        )
        if len(ranked) > limit:
            blocks.append(
                build_constraint_review_all_action_block(
                    thread_ts=session.thread_ts,
                    user_id=session.user_id,
                    count=len(ranked),
                )
            )
        return blocks

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

    async def _refresh_remote_baseline_after_sync(self, session: Session) -> None:
        """Refresh baseline snapshot/event IDs from live calendar after sync."""
        fallback_plan = session.tb_plan.model_copy(deep=True) if session.tb_plan else None
        fallback_ids = list(session.remote_event_ids_by_index or [])
        planned_date = session.planned_date or self._resolve_planning_date(session).isoformat()
        try:
            await self._prefetch_calendar_immovables(
                session,
                planned_date,
                force_refresh=True,
            )
            session.base_snapshot = self._build_remote_snapshot_plan(session)
        except Exception as exc:
            logger.warning(
                "Unable to refresh remote baseline after sync; using local fallback (%s).",
                exc,
            )
            if fallback_plan is not None:
                session.base_snapshot = fallback_plan
            session.remote_event_ids_by_index = fallback_ids

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

    def _summarize_sync_transaction(self, tx: SyncTransaction) -> CalendarSyncOutcome:
        """Convert a sync transaction into a stable user-facing outcome payload."""
        created = 0
        updated = 0
        deleted = 0
        failed = 0
        succeeded = 0
        failed_details: list[dict[str, str]] = []
        for index, op in enumerate(tx.ops):
            result = tx.results[index] if index < len(tx.results) else {}
            ok = bool(result.get("ok", False))
            if ok:
                succeeded += 1
                if op.op_type.value == "create":
                    created += 1
                elif op.op_type.value == "update":
                    updated += 1
                elif op.op_type.value == "delete":
                    deleted += 1
            else:
                failed += 1
                failure_message = str(
                    result.get("error")
                    or result.get("content")
                    or "unknown sync failure"
                ).strip()
                if len(failure_message) > 300:
                    failure_message = f"{failure_message[:297]}..."
                failed_details.append(
                    {
                        "index": str(index),
                        "op": op.op_type.value,
                        "tool": op.tool_name,
                        "event_id": op.gcal_event_id,
                        "error": failure_message.replace("\n", " "),
                    }
                )

        changed = succeeded > 0
        if not tx.ops:
            note = "Calendar unchanged: no sync operations were needed."
        elif tx.status == "committed" and changed:
            note = (
                "Calendar changed: "
                f"{created} created, {updated} updated, {deleted} deleted."
            )
        elif tx.status == "partial":
            note = (
                "Calendar partially changed: "
                f"{created} created, {updated} updated, {deleted} deleted, {failed} failed."
            )
        else:
            note = (
                "Calendar sync finished with status "
                f"`{tx.status}` ({created} created, {updated} updated, "
                f"{deleted} deleted, {failed} failed)."
            )
        return CalendarSyncOutcome(
            status=tx.status,
            changed=changed,
            created=created,
            updated=updated,
            deleted=deleted,
            failed=failed,
            note=note,
            failed_details=failed_details,
        )

    async def _submit_current_plan(self, session: Session) -> CalendarSyncOutcome:
        """Sync the current TBPlan and return a structured calendar-change result."""
        if session.tb_plan is None:
            self._session_debug(
                session,
                "calendar_sync_skipped",
                reason="missing_tb_plan",
            )
            return CalendarSyncOutcome(
                status="skipped",
                changed=False,
                note="Calendar sync skipped: plan is not ready yet.",
            )
        if session.base_snapshot is None:
            self._session_debug(
                session,
                "calendar_sync_skipped",
                reason="missing_base_snapshot",
            )
            return CalendarSyncOutcome(
                status="skipped",
                changed=False,
                note=(
                    "Calendar sync skipped: baseline snapshot unavailable. "
                    "Click Redo to retry sync."
                ),
            )

        sync_started_at = perf_counter()
        self._session_debug(
            session,
            "calendar_sync_start",
            remote_events=len(session.base_snapshot.events),
            event_id_map_size=len(session.event_id_map),
        )
        previous_map = dict(session.event_id_map)
        try:
            tx = await self._calendar_submitter.submit_plan(
                desired=session.tb_plan,
                remote=session.base_snapshot,
                event_id_map=session.event_id_map,
                remote_event_ids_by_index=session.remote_event_ids_by_index,
            )
        except Exception as exc:
            logger.exception("Calendar sync failed during refine stage.")
            self._session_debug(
                session,
                "calendar_sync_error",
                error_type=type(exc).__name__,
                error=str(exc)[:2000],
                elapsed_s=round(perf_counter() - sync_started_at, 3),
            )
            return CalendarSyncOutcome(
                status="failed",
                changed=False,
                note="Calendar sync failed; keep refining and try again.",
            )
        session.committed = True
        session.last_sync_transaction = tx
        session.last_sync_event_id_map = previous_map
        session.event_id_map = self._update_event_id_map_after_submit(
            session=session,
            transaction=tx,
        )
        await self._refresh_remote_baseline_after_sync(session)
        outcome = self._summarize_sync_transaction(tx)
        self._session_debug(
            session,
            "calendar_sync_result",
            status=outcome.status,
            changed=outcome.changed,
            created=outcome.created,
            updated=outcome.updated,
            deleted=outcome.deleted,
            failed=outcome.failed,
            failed_ops=outcome.failed_details[:3],
            ops=len(tx.ops),
            elapsed_s=round(perf_counter() - sync_started_at, 3),
        )
        return outcome

    async def _execute_refine_patch_and_sync(
        self,
        *,
        session: Session,
        patch_message: str,
    ) -> CalendarSyncOutcome:
        """Run the patch-critical Stage 4 path: patch plan then sync calendar."""
        if session.tb_plan is None:
            raise ValueError("Refine patch requested without TBPlan state.")
        constraints = await self._collect_constraints(session)
        validated_timebox: Timebox | None = None

        def _materialize_timebox(plan: TBPlan) -> Timebox:
            nonlocal validated_timebox
            validated_timebox = tb_plan_to_timebox(plan)
            return validated_timebox

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
            raise ValueError("Patch completed without producing a validated Timebox.")
        session.timebox = validated_timebox
        return await self._submit_current_plan(session)

    @staticmethod
    def _select_refine_tool_intents(
        intents: list[tuple[int, str, str]],
    ) -> tuple[str | None, str | None]:
        """Select highest-priority patch intent and highest-priority memory intent.

        Intents are ``(priority, kind, text)`` tuples where lower priority numbers
        are preferred.  Returns ``(patch_text, memory_text)`` — either may be ``None``
        when no intent of that kind is present.
        """
        sorted_intents = sorted(intents, key=lambda x: x[0])
        patch = next((text for _, kind, text in sorted_intents if kind == "patch"), None)
        memory = next((text for _, kind, text in sorted_intents if kind == "memory"), None)
        return patch, memory

    @staticmethod
    def _build_refine_noop_execution(*, note: str) -> RefineToolExecutionOutcome:
        """Build a no-op refine outcome when no user edits were requested."""
        return RefineToolExecutionOutcome(
            patch_selected=False,
            memory_queued=False,
            fallback_patch_used=False,
            calendar=CalendarSyncOutcome(
                status="skipped",
                changed=False,
                note=note,
            ),
            memory_operations=[],
        )

    async def _ensure_constraint_mcp_tools(self) -> None:
        """Lazily initialise constraint MCP tools, extractor, and extraction tool.

        Idempotent — safe to call multiple times; initialisation only runs once.
        """
        if self._constraint_mcp_tools is not None:
            return
        tools = await get_constraint_mcp_tools()
        self._constraint_mcp_tools = tools
        self._notion_extractor = NotionConstraintExtractor(
            model_client=self._model_client,
            tools=tools,
        )
        extractor = self._notion_extractor

        async def _extract_and_queue(
            *,
            planned_date: str,
            timezone: str,
            stage_id: str,
            user_utterance: str,
            triggering_suggestion: str,
            impacted_event_types: list[str],
            suggested_tags: list[str],
            decision_scope: str,
        ) -> dict:
            """Queue a durable constraint extraction without blocking the stage gate."""
            asyncio.create_task(
                extractor.extract_and_upsert_constraint(
                    planned_date=planned_date,
                    timezone=timezone,
                    stage_id=stage_id,
                    user_utterance=user_utterance,
                    triggering_suggestion=triggering_suggestion,
                    impacted_event_types=list(impacted_event_types),
                    suggested_tags=list(suggested_tags),
                    decision_scope=decision_scope,
                )
            )
            return {"queued": True}

        self._constraint_extractor_tool = FunctionTool(
            _extract_and_queue,
            name="extract_and_upsert_constraint",
            description="Queue a durable constraint extraction from user utterance.",
            strict=True,
        )

    @staticmethod
    def _materialize_timebox_from_tb_plan(session: Session) -> None:
        """Ensure ``session.timebox`` is available from the current ``session.tb_plan``."""
        if session.timebox is None and session.tb_plan is not None:
            session.timebox = tb_plan_to_timebox(session.tb_plan)

    async def _run_refine_tool_orchestration(
        self,
        *,
        session: Session,
        patch_message: str,
        user_message: str,
    ) -> RefineToolExecutionOutcome:
        """Run prompt-guided patch tooling while always queueing memory in background."""
        requested_patch: list[str] = []
        memory_operations: list[str] = []
        memory_request_text = (user_message or "").strip() or (patch_message or "").strip()

        async def timebox_patch_and_sync(user_instruction: str) -> dict[str, Any]:
            instruction = (user_instruction or "").strip()
            if instruction:
                requested_patch.append(instruction)
            return {"queued": True, "priority": "critical"}

        async def memory_list_constraints(
            text_query: str | None,
            statuses: list[str] | None,
            scopes: list[str] | None,
            necessities: list[str] | None,
            tags: list[str] | None,
            limit: int,
        ) -> dict[str, Any]:
            return await self._run_memory_tool_action(
                action="list",
                session=session,
                memory_operations=memory_operations,
                memory_request_text=memory_request_text,
                text_query=text_query,
                statuses=statuses,
                scopes=scopes,
                necessities=necessities,
                tags=tags,
                limit=limit,
            )

        async def memory_get_constraint(uid: str) -> dict[str, Any]:
            return await self._run_memory_tool_action(
                action="get",
                session=session,
                memory_operations=memory_operations,
                memory_request_text=memory_request_text,
                uid=uid,
            )

        async def memory_update_constraint(
            uid: str,
            patch_json: str,
            note: str | None,
        ) -> dict[str, Any]:
            parsed_patch, parse_error = self._parse_memory_patch_json(patch_json)
            if parse_error:
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="update",
                        ok=False,
                        uid=str(uid or "").strip() or None,
                        error="invalid_patch_json",
                        message=parse_error,
                    ),
                )
            return await self._run_memory_tool_action(
                action="update",
                session=session,
                memory_operations=memory_operations,
                memory_request_text=memory_request_text,
                uid=uid,
                patch=parsed_patch,
                note=note,
            )

        async def memory_archive_constraint(
            uid: str,
            reason: str | None,
        ) -> dict[str, Any]:
            return await self._run_memory_tool_action(
                action="archive",
                session=session,
                memory_operations=memory_operations,
                memory_request_text=memory_request_text,
                uid=uid,
                reason=reason,
            )

        async def memory_supersede_constraint(
            uid: str,
            patch_json: str,
            reason: str | None,
        ) -> dict[str, Any]:
            parsed_patch, parse_error = self._parse_memory_patch_json(patch_json)
            if parse_error:
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="supersede",
                        ok=False,
                        uid=str(uid or "").strip() or None,
                        error="invalid_patch_json",
                        message=parse_error,
                    ),
                )
            return await self._run_memory_tool_action(
                action="supersede",
                session=session,
                memory_operations=memory_operations,
                memory_request_text=memory_request_text,
                uid=uid,
                patch=parsed_patch,
                reason=reason,
            )

        tool_agent = AssistantAgent(
            name="StageRefineExecutionPlanner",
            model_client=self._model_client,
            tools=[
                FunctionTool(
                    timebox_patch_and_sync,
                    name="timebox_patch_and_sync",
                    description=(
                        "Primary tool for Stage 4/5 schedule edits. "
                        "Use this to apply the requested patch and sync to calendar."
                    ),
                    strict=True,
                ),
                FunctionTool(
                    memory_list_constraints,
                    name="memory_list_constraints",
                    description=(
                        "Review durable memory constraints/preferences. Use when the user asks "
                        "what is remembered or wants to inspect active constraints."
                    ),
                    strict=True,
                ),
                FunctionTool(
                    memory_get_constraint,
                    name="memory_get_constraint",
                    description="Get one durable memory constraint by uid.",
                    strict=True,
                ),
                FunctionTool(
                    memory_update_constraint,
                    name="memory_update_constraint",
                    description=(
                        "Edit one durable memory constraint by uid. "
                        "Provide `patch_json` as a JSON object string "
                        '(for example "{\"status\":\"locked\"}" or "{\"json_patch_ops\":[...]}"). '
                        "Use for explicit user requests to revise remembered preferences."
                    ),
                    strict=True,
                ),
                FunctionTool(
                    memory_archive_constraint,
                    name="memory_archive_constraint",
                    description=(
                        "Archive one durable memory constraint by uid. Use when the user says "
                        "a remembered rule is no longer valid."
                    ),
                    strict=True,
                ),
                FunctionTool(
                    memory_supersede_constraint,
                    name="memory_supersede_constraint",
                    description=(
                        "Supersede an existing durable memory constraint by uid with a new record. "
                        "Provide `patch_json` as a JSON object string."
                    ),
                    strict=True,
                ),
            ],
            system_message=(
                "You are selecting tools for Stage 4/5 timeboxing execution.\n"
                "Primary objective: apply user-requested schedule changes now.\n"
                "Rules:\n"
                "1) If the user asks for any plan/calendar change, call `timebox_patch_and_sync` exactly once.\n"
                "2) If the user asks to review or edit remembered constraints/preferences, "
                "call the appropriate memory_* tool.\n"
                "3) If both schedule patching and memory edits are requested, call patch tool first, "
                "then memory tools.\n"
                "4) Memory extraction/upsert runs automatically in the background and is NOT a tool choice.\n"
                "5) Return a brief confirmation after tool calls."
            ),
            reflect_on_tool_use=False,
            max_tool_iterations=3,
            memory=[
                self._build_refine_memory_component(session=session),
            ],
        )
        await with_timeout(
            "timeboxing:refine-tool-orchestration",
            tool_agent.on_messages(
                [
                    TextMessage(
                        content=(
                            f"stage={session.stage.value}\n"
                            f"user_message={user_message.strip()}\n"
                            f"patch_message={patch_message}"
                        ),
                        source="user",
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.stage_decision_s,
        )

        patch_instruction = self._select_patch_instruction(requested_patch)

        fallback_patch_used = False
        if (
            not patch_instruction
            and not self._looks_like_memory_management_request(memory_request_text)
        ):
            patch_instruction = patch_message
            fallback_patch_used = True

        memory_instruction = (user_message or "").strip() or (patch_message or "").strip()
        memory_queued = False
        if memory_instruction:
            task = self._queue_constraint_extraction(
                session=session,
                text=memory_instruction,
                reason="refine_background_memory",
                is_initial=False,
            )
            memory_queued = task is not None
            if memory_queued:
                self._append_background_update_once(
                    session,
                    "Updating preference memory in the background.",
                )

        if patch_instruction:
            calendar = await self._execute_refine_patch_and_sync(
                session=session,
                patch_message=patch_instruction,
            )
        else:
            calendar = CalendarSyncOutcome(
                status="skipped",
                changed=False,
                note="No calendar patch requested in this turn.",
            )
        self._queue_reflection_memory_write(
            session=session,
            user_message=user_message or patch_message,
            calendar=calendar,
            memory_operations=list(memory_operations),
        )
        return RefineToolExecutionOutcome(
            patch_selected=bool(patch_instruction and not fallback_patch_used),
            memory_queued=memory_queued,
            fallback_patch_used=fallback_patch_used,
            calendar=calendar,
            memory_operations=memory_operations,
        )

    async def _run_memory_tool_action(
        self,
        *,
        action: Literal["list", "get", "update", "archive", "supersede"],
        session: Session,
        memory_operations: list[str],
        memory_request_text: str,
        uid: str | None = None,
        patch: dict[str, Any] | None = None,
        reason: str | None = None,
        note: str | None = None,
        text_query: str | None = None,
        statuses: list[str] | None = None,
        scopes: list[str] | None = None,
        necessities: list[str] | None = None,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Execute one durable-memory tool action with shared validation and logging."""
        store = self._ensure_durable_constraint_store()
        if store is None:
            return self._record_memory_tool_result(
                session=session,
                result=MemoryToolResult(
                    action=action,
                    ok=False,
                    error="durable memory store unavailable",
                    message="Memory backend is unavailable right now.",
                ),
            )

        match action:
            case "list":
                query_limit = max(1, min(int(limit or 20), 100))
                filters: dict[str, Any] = {
                    "as_of": (session.planned_date or datetime.utcnow().date().isoformat()),
                    "require_active": False,
                    "stage": session.stage.value if session.stage else None,
                }
                if (text_query or "").strip():
                    filters["text_query"] = str(text_query).strip()
                if statuses:
                    filters["statuses_any"] = statuses
                if scopes:
                    filters["scopes_any"] = scopes
                if necessities:
                    filters["necessities_any"] = necessities
                rows = await store.query_constraints(
                    filters=filters,
                    type_ids=None,
                    tags=tags or None,
                    sort=[["Status", "descending"], ["Name", "ascending"]],
                    limit=query_limit,
                )
                items = [
                    item
                    for item in (
                        MemoryConstraintItem.from_payload(row)
                        for row in rows
                        if isinstance(row, dict)
                    )
                    if item is not None
                ]
                memory_operations.append(f"list:{len(items)}")
                message = (
                    "Memory review: no matching constraints found."
                    if not items
                    else (
                        "Memory review: found "
                        f"{len(items)} constraint(s) "
                        f"({', '.join(item.name or item.uid for item in items[:3])})."
                    )
                )
                self._append_background_update_once(session, message)
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="list",
                        ok=True,
                        message=message,
                        count=len(items),
                        constraints=items,
                    ),
                )
            case "get" | "update" | "archive" | "supersede":
                cleaned_uid = str(uid or "").strip()
                if not cleaned_uid:
                    return self._record_memory_tool_result(
                        session=session,
                        result=MemoryToolResult(
                            action=action,
                            ok=False,
                            error="uid is required",
                            message="Memory action needs a constraint uid.",
                        ),
                    )
            case _:
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="list",
                        ok=False,
                        error=f"unsupported action: {action}",
                    ),
                )

        match action:
            case "get":
                item = await store.get_constraint(uid=cleaned_uid)
                if not item:
                    return self._record_memory_tool_result(
                        session=session,
                        result=MemoryToolResult(
                            action="get",
                            ok=False,
                            uid=cleaned_uid,
                            error="constraint not found",
                            message=f"Constraint `{cleaned_uid}` was not found in memory.",
                        ),
                    )
                memory_operations.append(f"get:{cleaned_uid}")
                parsed = MemoryConstraintItem.from_payload(item)
                result = MemoryToolResult(
                    action="get",
                    ok=True,
                    uid=cleaned_uid,
                    message=f"Loaded remembered constraint `{cleaned_uid}`.",
                    constraints=[parsed] if parsed else [],
                )
                payload = self._record_memory_tool_result(session=session, result=result)
                if parsed:
                    payload["constraint"] = item
                return payload
            case "update":
                result = await store.update_constraint(
                    uid=cleaned_uid,
                    patch=patch if isinstance(patch, dict) else {},
                    event={
                        "action": "update",
                        "stage": session.stage.value if session.stage else None,
                        "note": (note or "").strip() or None,
                        "user_utterance": memory_request_text,
                    },
                )
                parsed_item = None
                if result.get("updated"):
                    memory_operations.append(f"update:{cleaned_uid}")
                    self._append_background_update_once(
                        session,
                        f"Updated durable memory for constraint `{cleaned_uid}`.",
                    )
                    parsed_item = MemoryConstraintItem.from_payload(
                        await store.get_constraint(uid=cleaned_uid) or {}
                    )
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="update",
                        ok=bool(result.get("updated")),
                        uid=cleaned_uid,
                        error=None if result.get("updated") else str(result.get("reason") or ""),
                        message=(
                            f"Updated remembered constraint `{cleaned_uid}`."
                            if result.get("updated")
                            else f"Unable to update remembered constraint `{cleaned_uid}`."
                        ),
                        constraints=[parsed_item] if parsed_item else [],
                    ),
                )
            case "archive":
                result = await store.archive_constraint(uid=cleaned_uid, reason=reason)
                parsed_item = None
                if result.get("updated"):
                    memory_operations.append(f"archive:{cleaned_uid}")
                    self._append_background_update_once(
                        session,
                        f"Archived durable memory for constraint `{cleaned_uid}`.",
                    )
                    parsed_item = MemoryConstraintItem.from_payload(
                        await store.get_constraint(uid=cleaned_uid) or {}
                    )
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="archive",
                        ok=bool(result.get("updated")),
                        uid=cleaned_uid,
                        error=None if result.get("updated") else str(result.get("reason") or ""),
                        message=(
                            f"Archived remembered constraint `{cleaned_uid}`."
                            if result.get("updated")
                            else f"Unable to archive remembered constraint `{cleaned_uid}`."
                        ),
                        constraints=[parsed_item] if parsed_item else [],
                    ),
                )
            case "supersede":
                current = await store.get_constraint(uid=cleaned_uid)
                if not current:
                    return self._record_memory_tool_result(
                        session=session,
                        result=MemoryToolResult(
                            action="supersede",
                            ok=False,
                            uid=cleaned_uid,
                            error="constraint not found",
                            message=f"Constraint `{cleaned_uid}` was not found in memory.",
                        ),
                    )
                patch_payload = patch if isinstance(patch, dict) else {}
                if isinstance(patch_payload.get("constraint_record"), dict):
                    new_record = {
                        "constraint_record": dict(patch_payload["constraint_record"])
                    }
                else:
                    merged = dict(current.get("constraint_record") or {})
                    merged.update({k: v for k, v in patch_payload.items() if v is not None})
                    new_record = {"constraint_record": merged}
                result = await store.supersede_constraint(
                    uid=cleaned_uid,
                    new_record=new_record,
                    event={
                        "action": "supersede",
                        "reason": (reason or "").strip() or None,
                        "stage": session.stage.value if session.stage else None,
                        "user_utterance": memory_request_text,
                    },
                )
                if result.get("updated"):
                    memory_operations.append(f"supersede:{cleaned_uid}")
                    self._append_background_update_once(
                        session,
                        f"Superseded durable memory for constraint `{cleaned_uid}`.",
                    )
                new_uid = str(result.get("new_uid") or result.get("uid") or "").strip()
                parsed_item = None
                if new_uid:
                    parsed_item = MemoryConstraintItem.from_payload(
                        await store.get_constraint(uid=new_uid) or {}
                    )
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="supersede",
                        ok=bool(result.get("updated")),
                        uid=new_uid or cleaned_uid,
                        error=None if result.get("updated") else str(result.get("reason") or ""),
                        message=(
                            f"Superseded remembered constraint `{cleaned_uid}`."
                            if result.get("updated")
                            else f"Unable to supersede remembered constraint `{cleaned_uid}`."
                        ),
                        constraints=[parsed_item] if parsed_item else [],
                    ),
                )
            case _:
                return self._record_memory_tool_result(
                    session=session,
                    result=MemoryToolResult(
                        action="list",
                        ok=False,
                        error=f"unsupported action: {action}",
                    ),
                )

    def _queue_reflection_memory_write(
        self,
        *,
        session: Session,
        user_message: str,
        calendar: CalendarSyncOutcome,
        memory_operations: list[str],
    ) -> None:
        """Persist a lightweight per-turn reflection entry in durable memory."""
        text = (user_message or "").strip()
        if not text:
            return
        store = self._ensure_durable_constraint_store()
        if store is None:
            return
        payload = {
            "user_id": session.user_id,
            "stage": session.stage.value if session.stage else None,
            "planned_date": session.planned_date,
            "calendar_status": calendar.status,
            "calendar_changed": calendar.changed,
            "memory_operations": list(memory_operations or []),
            "summary": (
                f"Stage {session.stage.value if session.stage else 'unknown'} "
                f"calendar={calendar.status} changed={calendar.changed}"
            ),
            "user_utterance": text,
        }

        async def _background() -> None:
            try:
                await store.add_reflection(payload=payload)
            except Exception as exc:
                logger.warning(
                    "Reflection memory write failed: %s",
                    exc,
                    exc_info=True,
                )
                self._session_debug(
                    session,
                    "reflection_memory_error",
                    error_type=type(exc).__name__,
                    error=str(exc)[:500],
                )

        asyncio.create_task(_background())

    def _build_refine_memory_component(self, *, session: Session) -> ConstraintPlanningMemory:
        """Build a per-turn AutoGen memory component for stage-aware constraint injection."""
        component = ConstraintPlanningMemory(
            store_provider=self._ensure_durable_constraint_store,
            max_items=12,
        )
        component.set_planning_state(
            {
                "stage": session.stage.value if session.stage else None,
                "planned_date": session.planned_date,
                "event_types": [],
            }
        )
        return component

    @staticmethod
    def _select_patch_instruction(requested_patch: list[str]) -> str:
        """Return the first valid patch instruction from tool-selected operations."""
        for instruction in requested_patch:
            cleaned = (instruction or "").strip()
            if cleaned:
                return cleaned
        return ""

    @staticmethod
    def _looks_like_schedule_request(text: str) -> bool:
        """Heuristic to detect explicit schedule/calendar patch intent."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        schedule_markers = (
            "move",
            "reschedule",
            "shift",
            "patch",
            "calendar",
            "timebox",
            "schedule",
            "block",
            "add buffer",
            "remove buffer",
            "today plan",
            "tomorrow plan",
        )
        if any(marker in lowered for marker in schedule_markers):
            return True
        return bool(
            re.search(
                r"\b([01]?\d|2[0-3]):[0-5]\d\b|\b(today|tomorrow|tonight|morning|afternoon|evening)\b",
                lowered,
            )
        )

    @staticmethod
    def _looks_like_memory_management_request(text: str) -> bool:
        """Heuristic to detect explicit memory review/edit commands."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return False
        if TimeboxingFlowAgent._looks_like_schedule_request(lowered):
            return False
        memory_markers = (
            "memory",
            "remember",
            "constraint",
            "preference",
            "saved rule",
            "what do you know",
            "what do you remember",
            "show my",
            "list my",
            "update my",
            "edit my",
            "delete",
            "remove",
            "archive",
            "forget",
        )
        has_memory = any(marker in lowered for marker in memory_markers)
        if not has_memory:
            return False
        explicit_memory_only = (
            lowered.startswith("show my")
            or lowered.startswith("list my")
            or lowered.startswith("what do you remember")
            or lowered.startswith("what do you know")
            or lowered.startswith("forget ")
            or lowered.startswith("archive ")
            or lowered.startswith("delete ")
            or lowered.startswith("edit my preference")
        )
        return explicit_memory_only or has_memory

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
        raw_calendar_events = parse_model_list(CalendarEvent, immovables)
        normalized_events = parse_model_list(
            _CalendarSnapshotEvent,
            [
                {
                    "summary": getattr(event, "summary", None),
                    "event_type": getattr(event, "event_type", None),
                    "start_time": getattr(event, "start_time", None),
                    "end_time": getattr(event, "end_time", None),
                    "duration": getattr(event, "duration", None),
                    "start": getattr(event, "start", None),
                    "end": getattr(event, "end", None),
                    "calendarId": getattr(event, "calendarId", None),
                    "timeZone": getattr(event, "timeZone", None),
                    "description": getattr(event, "description", None),
                    "eventId": getattr(event, "eventId", None),
                }
                for event in raw_calendar_events
            ],
        )
        if normalized_events:
            return self._sort_calendar_events(
                [event.to_calendar_event() for event in normalized_events]
            )
        immovable_rows = parse_model_list(Immovable, immovables)
        if not immovable_rows:
            return []
        events = [
            event.to_calendar_event()
            for event in parse_model_list(
                _CalendarSnapshotEvent,
                [
                    {
                        "summary": row.title,
                        "event_type": EventType.MEETING,
                        "start_time": row.start,
                        "end_time": row.end,
                        "calendarId": "primary",
                        "timeZone": getattr(settings, "planning_timezone", "") or "UTC",
                    }
                    for row in immovable_rows
                ],
            )
        ]
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

    async def _run_assist_turn(
        self, *, session: Session, user_message: str, note: str | None
    ) -> str | None:
        """Handle adjacent assist requests without progressing the stage."""
        return await self._task_marshalling.assist_response(
            session=session,
            user_message=user_message,
            note=note,
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
        try:
            response = await with_timeout(
                "timeboxing:stage-decision",
                self._decision_agent.on_messages(
                    [TextMessage(content=payload, source="user")],
                    CancellationToken(),
                ),
                timeout_s=TIMEBOXING_TIMEOUTS.stage_decision_s,
            )
        except TimeoutError as exc:
            error = f"Stage decision timeout: {type(exc).__name__}"
            logger.warning(error)
            self._session_debug(
                session,
                "stage_decision_timeout",
                error=error,
            )
            return StageDecision(
                action="provide_info",
                note="stage_decision_timeout",
            )
        except Exception as exc:
            error = f"Stage decision failed: {type(exc).__name__}: {exc}"
            logger.error(error, exc_info=True)
            self._session_debug(
                session,
                "stage_decision_error",
                error=error[:2000],
            )
            return StageDecision(
                action="provide_info",
                note="stage_decision_error",
            )
        try:
            return parse_chat_content(StageDecision, response)
        except Exception as exc:
            error = f"Stage decision parse failed: {type(exc).__name__}: {exc}"
            logger.error(error, exc_info=True)
            self._session_debug(
                session,
                "stage_decision_parse_error",
                error=error[:2000],
            )
            return StageDecision(
                action="provide_info",
                note="stage_decision_parse_error",
            )

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

    @staticmethod
    def _constraint_needs_confirmation(constraint: ConstraintBase) -> bool:
        """Return whether a constraint should be explicitly confirmed by the user."""
        hints = constraint.hints if isinstance(constraint.hints, dict) else {}
        selector = constraint.selector if isinstance(constraint.selector, dict) else {}
        if bool(hints.get("needs_confirmation") or selector.get("needs_confirmation")):
            return True
        confidence = getattr(constraint, "confidence", None)
        if confidence is not None:
            try:
                return float(confidence) < 0.7
            except (TypeError, ValueError):
                return False
        if constraint.source == ConstraintSource.SYSTEM:
            return True
        if constraint.scope == ConstraintScope.DATESPAN and (
            constraint.start_date is None or constraint.end_date is None
        ):
            return True
        return False

    def _format_assumptions_section(
        self, constraints: list[Constraint], limit: int = 4
    ) -> list[str]:
        """Format inferred/proposed constraints as deny-able assumptions."""
        assumptions = [
            constraint
            for constraint in constraints
            if constraint.status == ConstraintStatus.PROPOSED
            and constraint.source == ConstraintSource.SYSTEM
        ]
        lines: list[str] = []
        for constraint in assumptions[:limit]:
            name = (constraint.name or "Assumption").strip()
            description = (constraint.description or "").strip()
            suffix = (
                " (needs confirmation; deny/edit if wrong)"
                if self._constraint_needs_confirmation(constraint)
                else " (reply with deny to remove)"
            )
            if description:
                lines.append(f"{name}: {description}{suffix}")
            else:
                lines.append(f"{name}{suffix}")
        remaining = len(assumptions) - len(lines)
        if remaining > 0:
            lines.append(f"...and {remaining} more assumption(s)")
        return lines

    @staticmethod
    def _sanitize_slack_markdown(text: str) -> str:
        """Strip unsupported HTML-like disclosure tags from Slack markdown text."""
        cleaned = str(text or "")
        cleaned = re.sub(r"</?details[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?summary[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _render_session_message(message: SessionMessage) -> str:
        """Render a structured session message into markdown text."""
        parts: list[str] = []
        for section in message.sections:
            match section:
                case NextStepsSection():
                    parts.append(f"### {section.heading}")
                    lines = [
                        TimeboxingFlowAgent._sanitize_slack_markdown(line)
                        for line in section.content
                    ]
                    lines = [line for line in lines if line]
                    if not lines:
                        continue
                    first, *rest = lines
                    parts.append(first)
                    if rest:
                        parts.append("\n".join([f"- {line}" for line in rest]))
                case ConstraintsSection():
                    parts.append(f"### {section.heading}")
                    top = [
                        TimeboxingFlowAgent._sanitize_slack_markdown(line)
                        for line in section.content
                    ]
                    top = [line for line in top if line]
                    parts.append(
                        "\n".join([f"- {line}" for line in top]) if top else "- (none)"
                    )
                    folded = [
                        TimeboxingFlowAgent._sanitize_slack_markdown(line)
                        for line in section.folded_content
                    ]
                    folded = [line for line in folded if line]
                    if folded:
                        parts.extend(
                            [
                                "### All active constraints",
                                "\n".join([f"- {line}" for line in folded]),
                            ]
                        )
                case FreeformSection():
                    content = TimeboxingFlowAgent._sanitize_slack_markdown(section.content)
                    parts.extend([f"### {section.heading}", content or "-"])
        return "\n".join(parts)

    @staticmethod
    def _ordered_session_sections(message: SessionMessage) -> SessionMessage:
        """Normalize section ordering so the final section is always next steps."""
        def _section_priority(section: MessageSection) -> int:
            kind = str(getattr(section, "kind", "freeform"))
            if kind == "constraints":
                return 0
            if kind == "next_steps":
                return 2
            heading = str(getattr(section, "heading", "")).strip().lower()
            if heading in {"what i need from you", "next steps"}:
                return 2
            return 1

        ordered = sorted(
            enumerate(message.sections),
            key=lambda pair: (_section_priority(pair[1]), pair[0]),
        )
        return SessionMessage(sections=[section for _, section in ordered])

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
        """Render a markdown stage update, preferring structured section payloads."""
        if gate.response_message and gate.response_message.sections:
            ordered = self._ordered_session_sections(gate.response_message)
            rendered = self._render_session_message(ordered)
            if rendered.strip():
                return rendered

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
        bullets = "\n".join([f"- {b}" for b in summary_lines]) if summary_lines else "- (none)"
        missing = "\n".join([f"- {m}" for m in gate.missing]) if gate.missing else "- (none)"
        question = (
            (gate.question or "").strip()
            or (
                "Share the missing inputs, then reply in thread with `Redo`."
                if not gate.ready
                else "Confirm this plan or share any changes."
            )
        )
        status_line = (
            "Stage criteria met. You can proceed or share adjustments."
            if gate.ready
            else "Stage criteria not met. Share missing inputs, then reply with `Redo`."
        )
        sections: list[NextStepsSection | ConstraintsSection | FreeformSection] = [
            NextStepsSection(heading="What I need from you", content=[question, status_line]),
            FreeformSection(heading="Current step", content=header),
        ]
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
            sections.extend(
                [
                    FreeformSection(heading="Confirmed defaults", content=defaults_block),
                    FreeformSection(heading="Still missing", content=missing),
                    FreeformSection(heading="What I have so far", content=bullets),
                ]
            )
            if constraints:
                top_constraints = self._format_constraints_section(constraints, limit=3)
                all_constraints = (
                    self._format_constraints_section(constraints, limit=100)
                    if len(constraints) > 3
                    else []
                )
                sections.append(
                    ConstraintsSection(
                        heading=f"Constraints (top {min(3, len(constraints))}/{len(constraints)})",
                        content=top_constraints,
                        folded_content=all_constraints,
                    )
                )
                assumption_lines = self._format_assumptions_section(constraints)
                if assumption_lines:
                    sections.append(
                        FreeformSection(
                            heading="Assumptions currently applied (yes-state; deny/edit if wrong)",
                            content="\n".join([f"- {line}" for line in assumption_lines]),
                        )
                    )
            if immovables:
                immovable_lines = self._format_immovables_section(immovables)
                sections.append(
                    FreeformSection(
                        heading="Calendar",
                        content="\n".join([f"- {line}" for line in immovable_lines]),
                    )
                )
            if background_notes:
                notes = "\n".join([f"- {note}" for note in background_notes])
                sections.append(FreeformSection(heading="Background", content=notes))
            return self._render_session_message(
                self._ordered_session_sections(SessionMessage(sections=sections))
            )

        if not gate.ready:
            sections.extend(
                [
                    FreeformSection(heading="Need Before Proceeding:", content=missing),
                    FreeformSection(heading="What I Have So Far:", content=bullets),
                ]
            )
        else:
            sections.append(FreeformSection(heading="Summary", content=bullets))
        if constraints:
            top_constraints = self._format_constraints_section(constraints, limit=3)
            all_constraints = (
                self._format_constraints_section(constraints, limit=100)
                if len(constraints) > 3
                else []
            )
            sections.append(
                ConstraintsSection(
                    heading=f"Constraints (top {min(3, len(constraints))}/{len(constraints)})",
                    content=top_constraints,
                    folded_content=all_constraints,
                )
            )
            assumption_lines = self._format_assumptions_section(constraints)
            if assumption_lines:
                sections.append(
                    FreeformSection(
                        heading="Assumptions currently applied (yes-state; deny/edit if wrong)",
                        content="\n".join([f"- {line}" for line in assumption_lines]),
                    )
                )
        if immovables:
            immovable_lines = self._format_immovables_section(immovables)
            sections.append(
                FreeformSection(
                    heading="Calendar",
                    content="\n".join([f"- {line}" for line in immovable_lines]),
                )
            )
        if background_notes:
            notes = "\n".join([f"- {note}" for note in background_notes])
            sections.append(FreeformSection(heading="Background", content=notes))
        return self._render_session_message(
            self._ordered_session_sections(SessionMessage(sections=sections))
        )

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
                "name, description, necessity (must/should/prefer), and any useful hints/selector "
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
            queries: list[ConstraintSearchQuery],
            planned_date: str | None,
            stage: str | None,
        ) -> str:
            """Search the durable constraint store with one or more query facets.

            Use this tool to find saved scheduling preferences and constraints
            from the durable preference store. You can search by:
            - text (free-text match on constraint name or description)
            - event type codes (M, DW, SW, H, R, C, BU, BG, PR)
            - topic tags
            - status (locked / proposed)
            - scope (session / profile / datespan)
            - necessity (must / should / prefer)

            Args:
                queries: List of search facets. Each facet is a dict with keys:
                    - label (str): Short description of this query.
                    - text_query (str): Free-text search on Name/Description.
                    - event_types (list[str]): Event-type codes.
                    - tags (list[str]): Topic tag names.
                    - statuses (list[str]): 'locked' and/or 'proposed'.
                    - scopes (list[str]): 'session', 'profile', 'datespan'.
                    - necessities (list[str]): 'must', 'should', and/or 'prefer'.
                    - limit (int): Max results per facet (default 20).
                planned_date: ISO date (YYYY-MM-DD), or null to use today.
                stage: Current timeboxing stage, or null for no stage filter.

            Returns:
                Formatted summary of matching constraints.
            """
            query_payloads = [
                query.model_dump(
                    mode="json",
                    exclude_none=True,
                    exclude_defaults=True,
                )
                for query in queries
            ]
            semantic_query_payloads = [
                {
                    key: value
                    for key, value in payload.items()
                    if key not in {"label", "limit"}
                }
                for payload in query_payloads
            ]
            if (
                stage == TimeboxingStage.COLLECT_CONSTRAINTS.value
                and (
                    not semantic_query_payloads
                    or all(not payload for payload in semantic_query_payloads)
                )
            ):
                return (
                    "Skipped search_constraints for Stage 1 because no concrete query "
                    "facet was provided. Using deterministic saved-default prefetch."
                )
            client = agent_ref._ensure_durable_constraint_store()
            return await search_constraints(
                queries=query_payloads,
                planned_date=planned_date,
                stage=stage,
                _client=client,
            )

        return FunctionTool(
            _search_constraints_wrapper,
            name="search_constraints",
            description=(
                "Search the durable constraint/preference store. "
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
        has_constraint_preview = False
        if isinstance(reply, SlackBlockMessage):
            for block in reply.blocks:
                accessory = block.get("accessory") if isinstance(block, dict) else None
                if (
                    isinstance(accessory, dict)
                    and accessory.get("action_id") == CONSTRAINT_ROW_REVIEW_ACTION_ID
                ):
                    has_constraint_preview = True
                    break
                if block.get("type") == "actions":
                    elements = block.get("elements") if isinstance(block, dict) else []
                    if any(
                        isinstance(element, dict)
                        and element.get("action_id") == CONSTRAINT_REVIEW_ALL_ACTION_ID
                        for element in (elements or [])
                    ):
                        has_constraint_preview = True
                        break
        constraint_blocks = (
            []
            if has_constraint_preview
            else self._render_constraints_preview_blocks(session=session)
        )
        submit_blocks: list[dict[str, Any]] = []
        match session.stage:
            case TimeboxingStage.REVIEW_COMMIT:
                if session.stage_ready and session.tb_plan is not None and session.base_snapshot is not None:
                    session.pending_submit = True
                    submit_blocks = self._render_submit_prompt_blocks(
                        session=session,
                        text=(
                            reply.content
                            if isinstance(reply, TextMessage)
                            else reply.text
                        ),
                    )
                else:
                    session.pending_submit = False
            case _:
                session.pending_submit = False
        stage_blocks = self._render_stage_action_blocks(session=session)
        combined_blocks = [
            *presenter_blocks,
            *constraint_blocks,
            *submit_blocks,
            *stage_blocks,
        ]
        if not combined_blocks:
            return reply
        if isinstance(reply, SlackBlockMessage):
            return SlackBlockMessage(
                text=reply.text,
                blocks=list(reply.blocks) + combined_blocks,
            )
        return SlackBlockMessage(
            text=reply.content,
            blocks=[build_markdown_block(text=reply.content), *combined_blocks],
        )

    @staticmethod
    def _interaction_mode(session: Session) -> InteractionMode:
        """Infer the interaction mode for response serialization."""
        if (session.channel_id or "").strip():
            return InteractionMode.SLACK
        return InteractionMode.TEXT

    @staticmethod
    def _append_presenter_blocks(session: Session, blocks: list[dict[str, Any]]) -> None:
        """Append blocks without overwriting previously queued presenter content."""
        if not blocks:
            return
        existing = list(session.pending_presenter_blocks or [])
        session.pending_presenter_blocks = [*existing, *blocks]

    def _record_memory_tool_result(
        self,
        *,
        session: Session,
        result: MemoryToolResult,
    ) -> dict[str, Any]:
        """Store/render one typed tool result and return tool-transport payload."""
        presentation = present_memory_tool_result(
            result=result,
            context=InteractionContext(
                mode=self._interaction_mode(session),
                user_id=session.user_id,
                thread_ts=session.thread_ts,
            ),
        )
        self._append_presenter_blocks(session, presentation.blocks)
        if presentation.text_update:
            self._append_background_update_once(session, presentation.text_update)
        return presentation.payload

    @staticmethod
    def _parse_memory_patch_json(patch_json: str) -> tuple[dict[str, Any], str | None]:
        """Parse a JSON object payload used by memory update/supersede tools."""
        cleaned = str(patch_json or "").strip()
        if not cleaned:
            return {}, "Memory patch payload cannot be empty."
        try:
            parsed = TypeAdapter(dict[str, Any]).validate_json(cleaned)
        except ValidationError:
            return {}, "Memory patch payload must be a valid JSON object string."
        except (TypeError, ValueError):
            return {}, "Memory patch payload must be a valid JSON object string."
        return parsed, None

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
        tz_name = self._resolve_tz_name(self._default_tz_name())
        now_utc = datetime.now(timezone.utc)
        default_planned_date = self._default_planned_date(
            now=now_utc,
            tz=ZoneInfo(tz_name),
        )
        session, created = self._ensure_uncommitted_session(
            key=key,
            thread_ts=message.thread_ts,
            channel_id=message.channel_id,
            user_id=message.user_id,
            user_input=message.user_input,
            tz_name=tz_name,
            default_planned_date=default_planned_date,
            debug_event="session_started",
            start_message=message.user_input,
        )

        planned_date = await self._interpret_planned_date(
            message.user_input,
            now=now_utc,
            tz_name=tz_name,
        )
        if not session.committed:
            session.planned_date = planned_date
            if created:
                asyncio.create_task(
                    self._prefetch_calendar_immovables(session, planned_date)
                )
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
        self._refresh_temporal_facts(session)
        self._session_debug(
            session,
            "commit_date",
            planned_date=message.planned_date,
            timezone=session.tz_name,
        )
        await self._prime_collect_prefetch_non_blocking(
            session=session,
            planned_date=message.planned_date,
            blocking=True,
        )

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
            tz_name = self._resolve_tz_name(self._default_tz_name())
            now_utc = datetime.now(timezone.utc)
            default_planned_date = self._default_planned_date(
                now=now_utc,
                tz=ZoneInfo(tz_name),
            )
            session, _ = self._ensure_uncommitted_session(
                key=key,
                thread_ts=message.thread_ts,
                channel_id=message.channel_id,
                user_id=message.user_id,
                user_input=message.text,
                tz_name=tz_name,
                default_planned_date=default_planned_date,
                debug_event="session_started_from_reply",
            )
        if session.session_key is None:
            session.session_key = key
        async with session.reply_turn_lock:
            self._session_debug(
                session,
                "user_reply",
                text=(message.text or "")[:800],
                committed=session.committed,
            )

            was_committed = session.committed
            if not session.committed:
                # Thread replies should progress naturally even without explicit button confirmation.
                tz_name = self._resolve_tz_name(session.tz_name or self._default_tz_name())
                session.tz_name = tz_name
                planned_date = await self._interpret_planned_date(
                    message.text,
                    now=datetime.now(timezone.utc),
                    tz_name=tz_name,
                )
                if planned_date != session.planned_date:
                    session.planned_date = planned_date
                    self._reset_durable_prefetch_state(session)
                resolved_planned_date = session.planned_date or planned_date
                session.committed = True
                self._session_debug(
                    session,
                    "implicit_commit_from_thread_reply",
                    planned_date=resolved_planned_date,
                )
                self._refresh_temporal_facts(session)
                await self._prime_collect_prefetch_non_blocking(
                    session=session,
                    planned_date=resolved_planned_date,
                    blocking=True,
                )
            if was_committed:
                await self._scheduler_prefetch.ensure_collect_stage_ready(session=session)
            if session.stage == TimeboxingStage.REVIEW_COMMIT and session.pending_submit:
                decision = await self._decide_next_action(
                    session,
                    user_message=message.text,
                )
                if decision.action == "proceed":
                    self._session_debug(
                        session,
                        "nl_submit_from_reply",
                        decision_action=decision.action,
                    )
                    submit_reply = await self._submit_pending_plan(session=session)
                    await self._publish_update(
                        session=session,
                        user_message=(
                            submit_reply.content
                            if isinstance(submit_reply, TextMessage)
                            else submit_reply.text
                        ),
                        actions=[],
                    )
                    return submit_reply
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
                ZoneInfo(tz_name)
            except Exception:
                ZoneInfo("UTC")
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
            self._scheduler_prefetch.queue_initial_prefetch(
                session=session, planned_date=planned_date
            )
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
        await self._scheduler_prefetch.ensure_collect_stage_ready(session=session)
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
            should_force_stage_rerun = False
            match message.action:
                case "cancel":
                    session.completed = True
                    session.thread_state = "canceled"
                    timeboxing_activity.mark_inactive(user_id=session.user_id)
                    self._session_debug(session, "session_canceled")
                    self._close_session_debug_logger(key)
                    return TextMessage(
                        content="Okay—stopping this timeboxing session.",
                        source=self._agent_source(),
                    )
                case "back":
                    await self._advance_stage(
                        session, next_stage=self._previous_stage(session.stage)
                    )
                    should_force_stage_rerun = True
                case "proceed":
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
                    should_force_stage_rerun = True
                case "redo":
                    should_force_stage_rerun = True
                case _:
                    return TextMessage(
                        content=f"Unknown stage action: {message.action}",
                        source=self._agent_source(),
                    )

            session.thread_state = None
            session.force_stage_rerun = should_force_stage_rerun
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
        return await self._submit_pending_plan(session=session)

    async def _submit_pending_plan(
        self,
        *,
        session: Session,
    ) -> TextMessage | SlackBlockMessage:
        """Submit the pending Stage 5 plan to calendar when ready."""
        if not session.pending_submit:
            self._session_debug(
                session,
                "submission_skipped",
                reason="not_pending_submit",
            )
            return TextMessage(
                content="There is no pending plan submission right now.",
                source=self._agent_source(),
            )
        if session.tb_plan is None:
            session.pending_submit = False
            self._session_debug(
                session,
                "submission_skipped",
                reason="missing_tb_plan",
            )
            return TextMessage(
                content="Cannot submit yet because the plan is incomplete. Please refine first.",
                source=self._agent_source(),
            )
        if session.base_snapshot is None:
            session.pending_submit = False
            self._session_debug(
                session,
                "submission_skipped",
                reason="missing_base_snapshot",
            )
            return TextMessage(
                content="Cannot submit yet because the plan is incomplete. Please refine first.",
                source=self._agent_source(),
            )

        submit_started_at = perf_counter()
        self._session_debug(
            session,
            "submission_start",
            remote_events=len(session.base_snapshot.events),
            event_id_map_size=len(session.event_id_map),
        )
        previous_map = dict(session.event_id_map)
        try:
            tx = await self._calendar_submitter.submit_plan(
                desired=session.tb_plan,
                remote=session.base_snapshot,
                event_id_map=session.event_id_map,
                remote_event_ids_by_index=session.remote_event_ids_by_index,
            )
        except Exception as exc:
            logger.exception("Calendar submission failed.")
            self._session_debug(
                session,
                "submission_error",
                error_type=type(exc).__name__,
                error=str(exc)[:2000],
                elapsed_s=round(perf_counter() - submit_started_at, 3),
            )
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
        await self._refresh_remote_baseline_after_sync(session)
        summary = self._summarize_sync_transaction(tx)
        self._session_debug(
            session,
            "submission_result",
            status=summary.status,
            changed=summary.changed,
            created=summary.created,
            updated=summary.updated,
            deleted=summary.deleted,
            failed=summary.failed,
            failed_ops=summary.failed_details[:3],
            ops=len(tx.ops),
            elapsed_s=round(perf_counter() - submit_started_at, 3),
        )

        if tx.status == "committed":
            text = "Submitted to Google Calendar. You can undo this submission."
        elif tx.status == "partial":
            first_error = (
                summary.failed_details[0].get("error", "").strip()
                if summary.failed_details
                else ""
            )
            if first_error:
                text = (
                    "Submission completed with partial failures. "
                    f"First error: {first_error}"
                )
            else:
                text = (
                    "Submission completed with partial failures. "
                    "You can undo attempted changes."
                )
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
            # Mem0 client currently does not expose async cleanup.
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


def _constraint_necessity_rank() -> dict[ConstraintNecessity | str, int]:
    """Return a stable necessity rank map tolerant to older enum definitions."""
    rank: dict[ConstraintNecessity | str, int] = {
        ConstraintNecessity.MUST: 0,
        ConstraintNecessity.SHOULD: 1,
    }
    prefer = getattr(ConstraintNecessity, "PREFER", None)
    if prefer is not None:
        rank[prefer] = 2
    rank["prefer"] = 2
    return rank


def _constraint_priority(constraint: Constraint) -> tuple[int, int, str]:
    """Rank constraints so the top rows are the most decision-critical."""
    necessity_rank = _constraint_necessity_rank()
    status_rank = {
        ConstraintStatus.LOCKED: 0,
        ConstraintStatus.PROPOSED: 1,
        ConstraintStatus.DECLINED: 2,
    }
    necessity_value: ConstraintNecessity | str = constraint.necessity
    if necessity_value not in necessity_rank and necessity_value is not None:
        necessity_value = str(necessity_value).lower()
    return (
        necessity_rank.get(necessity_value, 3),
        status_rank.get(constraint.status, 3),
        (constraint.name or "").lower(),
    )


def _wrap_with_constraint_review(
    message: TextMessage,
    *,
    constraints: list[Constraint],
    session: Session,
) -> SlackBlockMessage:
    """Attach a compact constraint-review section to a stage response."""
    blocks: list[dict[str, Any]] = [build_markdown_block(text=message.content)]
    if constraints:
        ranked = sorted(constraints, key=_constraint_priority)
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Constraints*\n"
                        f"Showing the top {min(3, len(ranked))} of {len(ranked)}. "
                        "Use Deny / Edit or open the full list."
                    ),
                },
            }
        )
        blocks.extend(
            build_constraint_row_blocks(
                ranked,
                thread_ts=session.thread_ts,
                user_id=session.user_id,
                limit=3,
                button_text="Deny / Edit",
            )
        )
        if len(ranked) > 3:
            blocks.append(
                build_constraint_review_all_action_block(
                    thread_ts=session.thread_ts,
                    user_id=session.user_id,
                    count=len(ranked),
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
    normalized = str(value).strip()
    if not normalized:
        return default
    adapter = TypeAdapter(enum_cls)
    candidates = [normalized, normalized.lower(), normalized.upper()]
    for candidate in candidates:
        try:
            return adapter.validate_python(candidate)
        except ValidationError:
            continue
    return default


# TODO(refactor): Parse enums/dates via Pydantic fields instead of try/except.
@_fallback_on_parse_error(default=None)
def _parse_dow(value: str | None) -> ConstraintDayOfWeek | None:
    """Parse a day-of-week enum from a string."""
    if not value:
        return None
    return TypeAdapter(ConstraintDayOfWeek).validate_python(str(value).upper())


# TODO(refactor): Parse ISO dates via Pydantic fields instead of try/except.
@_fallback_on_parse_error(default=None)
def _parse_date_value(value: str | None) -> date | None:
    """Parse an ISO date string into a date."""
    if not value:
        return None
    return TypeAdapter(date).validate_python(value)


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
        confidence = record.get("confidence")
        parsed_confidence: float | None = None
        if confidence is not None:
            try:
                parsed_confidence = float(confidence)
                if parsed_confidence < 0.7:
                    hints["needs_confirmation"] = True
            except (TypeError, ValueError):
                parsed_confidence = None
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
                confidence=parsed_confidence,
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
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


__all__ = ["TimeboxingFlowAgent"]
