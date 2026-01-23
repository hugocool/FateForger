"""Coordinator agent that runs a stage-gated timeboxing flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar
from zoneinfo import ZoneInfo

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.agents.timeboxing.notion_constraint_extractor import (
    NotionConstraintExtractor,
)
from fateforger.agents.schedular.models.calendar import CalendarEvent, EventType
from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.haunt.timeboxing_activity import timeboxing_activity
from fateforger.llm import build_autogen_chat_client
from fateforger.slack_bot.constraint_review import (
    build_constraint_row_blocks,
    encode_metadata,
)
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.timeboxing_commit import build_timebox_commit_prompt_message
from fateforger.tools.constraint_mcp import get_constraint_mcp_tools
from fateforger.tools.ticktick_mcp import TickTickMcpClient, get_ticktick_mcp_url

from .actions import TimeboxAction
from .constants import TIMEBOXING_FALLBACK, TIMEBOXING_LIMITS, TIMEBOXING_TIMEOUTS
from .constraint_retriever import ConstraintRetriever
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
from .messages import (
    StartTimeboxing,
    TimeboxingCommitDate,
    TimeboxingFinalResult,
    TimeboxingUpdate,
    TimeboxingUserReply,
)
from .patching import TimeboxPatcher
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
from .timebox import Timebox
from .mcp_clients import ConstraintMemoryClient, McpCalendarClient
from fateforger.llm.toon import toon_encode
from .toon_views import constraints_rows, immovables_rows, tasks_rows, timebox_events_rows
from .nlu import (
    ConstraintInterpretation,
    PlannedDateResult,
    build_constraint_interpreter,
    build_planned_date_interpreter,
)
from .flow_graph import build_timeboxing_graphflow
from autogen_agentchat.teams import GraphFlow

logger = logging.getLogger(__name__)
TEnum = TypeVar("TEnum", bound=Enum)

class _ConstraintInterpretationPayload(BaseModel):
    """Input payload for constraint interpretation (multilingual, structured output)."""

    text: str
    is_initial: bool
    planned_date: str | None = None
    timezone: str | None = None
    stage_id: str | None = None


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
    active_constraints: List[Constraint] = field(default_factory=list)
    durable_constraints_by_stage: Dict[str, List[Constraint]] = field(default_factory=dict)
    durable_constraints_loaded_stages: set[str] = field(default_factory=set)
    durable_constraints_date: str | None = None
    pending_durable_constraints: bool = False
    pending_calendar_prefetch: bool = False
    background_updates: List[str] = field(default_factory=list)
    timebox: Timebox | None = None
    stage: TimeboxingStage = TimeboxingStage.COLLECT_CONSTRAINTS
    frame_facts: Dict[str, Any] = field(default_factory=dict)
    input_facts: Dict[str, Any] = field(default_factory=dict)
    stage_ready: bool = False
    stage_missing: List[str] = field(default_factory=list)
    stage_question: str | None = None
    constraints_prefetched: bool = False
    pending_constraint_extractions: set[str] = field(default_factory=set)
    last_extraction_task: asyncio.Task | None = None
    graphflow: GraphFlow | None = None
    thread_state: str | None = None


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
        self._constraint_mcp_tools: list | None = None
        self._notion_extractor: NotionConstraintExtractor | None = None
        self._constraint_extractor_tool = None
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

    def _default_tz_name(self) -> str:
        """Return the default timezone name for planning."""
        return getattr(settings, "planning_timezone", "") or "Europe/Amsterdam"

    def _default_planned_date(self, *, now: datetime, tz: ZoneInfo) -> str:
        """Return a deterministic default planned date without parsing user language."""
        local_now = now.astimezone(tz)
        base = next_workday(local_now.date())
        if base != local_now.date():
            return base.isoformat()
        if (local_now.hour, local_now.minute) >= (9, 0):
            return tomorrow_workday(base).isoformat()
        return base.isoformat()

    def _ensure_graphflow(self, session: Session) -> GraphFlow:
        """Return the per-session GraphFlow instance, building it if needed."""
        if session.graphflow is not None:
            return session.graphflow
        session.graphflow = build_timeboxing_graphflow(orchestrator=self, session=session)
        return session.graphflow

    async def _run_graph_turn(self, *, session: Session, user_text: str) -> TextMessage:
        """Run one GraphFlow turn and return the presenter text message."""
        flow = self._ensure_graphflow(session)
        presenter: TextMessage | None = None
        async for item in flow.run_stream(task=TextMessage(content=user_text, source="user")):
            if isinstance(item, TextMessage) and item.source == "PresenterNode":
                presenter = item
        content = presenter.content if presenter else "Timed out waiting for tools/LLM. Please try again in a moment."
        return TextMessage(content=content, source=self.id.type)

    async def _ensure_planning_date_interpreter_agent(self) -> None:
        """Initialize the multilingual planned-date interpreter agent if needed."""
        if self._planning_date_interpreter_agent:
            return
        self._planning_date_interpreter_agent = build_planned_date_interpreter(
            model_client=self._model_client
        )

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
                    [TextMessage(content=json.dumps(payload, ensure_ascii=False), source="user")],
                    CancellationToken(),
                ),
                timeout_s=TIMEBOXING_TIMEOUTS.planning_date_interpret_s,
            )
            result = parse_chat_content(PlannedDateResult, response)
            if result.planned_date:
                return result.planned_date
        except Exception:
            logger.debug("Planned date interpretation failed; using default.", exc_info=True)
        return self._default_planned_date(now=now, tz=tz)

    def _ensure_calendar_client(self) -> McpCalendarClient | None:
        """Return the calendar MCP client, initializing it if needed."""
        if self._calendar_client:
            return self._calendar_client
        server_url = os.getenv(
            "MCP_CALENDAR_SERVER_URL", "http://localhost:3000"
        ).strip()
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
            logger.debug("Failed to initialize MCP calendar client", exc_info=True)
            return None
        return self._calendar_client

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
            logger.debug("Failed to initialize TickTick MCP client", exc_info=True)
            return None
        return self._ticktick_client

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
            self._constraint_memory_client = ConstraintMemoryClient(timeout=timeout)
        except Exception:
            logger.debug(
                "Failed to initialize constraint memory MCP client", exc_info=True
            )
            return None
        return self._constraint_memory_client

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

        work_window = parse_model_optional(WorkWindow, session.frame_facts.get("work_window"))
        sleep_target = parse_model_optional(SleepTarget, session.frame_facts.get("sleep_target"))
        immovables = parse_model_list(Immovable, session.frame_facts.get("immovables"))
        block_plan = parse_model_optional(BlockPlan, session.input_facts.get("block_plan"))

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
        except Exception:
            logger.debug("Constraint memory query failed", exc_info=True)
            return []
        return _constraints_from_memory(records, user_id=session.user_id)

    async def _ensure_constraint_interpreter_agent(self) -> None:
        """Initialize the structured constraint interpreter agent if needed."""
        if self._constraint_interpreter_agent:
            return
        self._constraint_interpreter_agent = build_constraint_interpreter(
            model_client=self._constraint_model_client
        )

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
        ).hexdigest()[:TIMEBOXING_LIMITS.durable_task_key_len]

    def _durable_prefetch_key(self, session: Session) -> str:
        """Return a stable key for deduping durable constraint prefetch tasks."""
        planned_date = session.planned_date or "unknown"
        return f"{session.user_id}:{planned_date}"

    def _queue_constraint_prefetch(self, session: Session) -> None:
        """Prefetch session-scoped constraints and durable constraints in background."""
        self._queue_durable_constraint_prefetch(session=session, reason="prefetch")
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
        self, *, session: Session, reason: str
    ) -> None:
        """Start background durable constraint prefetch if needed."""
        if not settings.notion_timeboxing_parent_page_id:
            return
        planned_date = session.planned_date or ""
        prefetch_stages = (
            TimeboxingStage.COLLECT_CONSTRAINTS,
            TimeboxingStage.SKELETON,
            TimeboxingStage.REFINE,
        )
        if session.durable_constraints_date == planned_date and all(
            stage.value in session.durable_constraints_loaded_stages
            for stage in prefetch_stages
        ):
            return
        if session.pending_durable_constraints:
            return

        task_key = self._durable_prefetch_key(session)
        if task_key in self._durable_constraint_prefetch_tasks:
            return

        async def _background() -> None:
            """Fetch durable constraints on a background task."""
            acquired = False
            session.pending_durable_constraints = True
            try:
                await self._durable_constraint_prefetch_semaphore.acquire()
                acquired = True
                for stage in prefetch_stages:
                    if stage.value in session.durable_constraints_loaded_stages:
                        continue
                    constraints = await self._fetch_durable_constraints(session, stage=stage)
                    session.durable_constraints_by_stage[stage.value] = constraints
                    session.durable_constraints_loaded_stages.add(stage.value)
                session.durable_constraints_date = planned_date

                union = _dedupe_constraints(
                    [
                        c
                        for stage_constraints in session.durable_constraints_by_stage.values()
                        for c in (stage_constraints or [])
                    ]
                )
                if union:
                    session.background_updates.append(
                        f"Loaded {len(union)} saved constraint(s)."
                    )
                    await self._sync_durable_constraints_to_store(
                        session, constraints=union
                    )
                await self._collect_constraints(session)
            except Exception:
                logger.debug(
                    "Durable constraint prefetch failed (reason=%s)",
                    reason,
                    exc_info=True,
                )
            finally:
                if acquired:
                    self._durable_constraint_prefetch_semaphore.release()
                session.pending_durable_constraints = False
                self._durable_constraint_prefetch_tasks.pop(task_key, None)

        task = asyncio.create_task(_background())
        self._durable_constraint_prefetch_tasks[task_key] = task

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
                if scope in (ConstraintScope.PROFILE, ConstraintScope.DATESPAN):
                    self._queue_durable_constraint_upsert(
                        session=session,
                        text=text,
                        reason=reason,
                        decision_scope=scope.value,
                    )

                # Persist extracted constraints to the session store (non-blocking UX).
                await self._ensure_constraint_store()
                constraints = list(interpretation.constraints or [])
                if constraints:
                    for constraint in constraints:
                        if constraint.scope is None:
                            constraint.scope = scope
                        if scope == ConstraintScope.DATESPAN:
                            if interpretation.start_date and constraint.start_date is None:
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
    ) -> None:
        """Queue a durable constraint upsert into Notion when explicitly requested."""
        if not settings.notion_timeboxing_parent_page_id:
            return
        if not (text or "").strip():
            return

        payload = {
            "planned_date": session.planned_date or "",
            "timezone": session.tz_name or "UTC",
            "stage_id": session.stage.value,
            "user_utterance": text,
            "triggering_suggestion": reason,
            "impacted_event_types": [],
            "suggested_tags": [],
            "decision_scope": decision_scope,
        }
        task_key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:TIMEBOXING_LIMITS.durable_task_key_len]
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
                await self._ensure_constraint_mcp_tools()
                if not self._notion_extractor:
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
            except Exception:
                logger.debug(
                    "Durable constraint upsert failed (task_key=%s)",
                    task_key,
                    exc_info=True,
                )
            finally:
                if acquired:
                    self._durable_constraint_semaphore.release()
                self._durable_constraint_task_keys.discard(task_key)

        asyncio.create_task(_background())

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

    async def _prefetch_calendar_immovables(
        self, session: Session, planned_date: str
    ) -> None:
        """Fetch calendar immovables for the planned date in the background."""
        if planned_date in session.prefetched_immovables_by_date:
            return
        client = self._ensure_calendar_client()
        if not client:
            return
        session.pending_calendar_prefetch = True
        try:
            tz = ZoneInfo(session.tz_name or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        # TODO(refactor): Validate planned_date with Pydantic before calendar prefetch.
        try:
            immovables = await client.list_day_immovables(
                calendar_id="primary",
                day=date.fromisoformat(planned_date),
                tz=tz,
            )
            session.prefetched_immovables_by_date[planned_date] = immovables
            if immovables:
                session.background_updates.append(
                    f"Loaded {len(immovables)} calendar immovable(s)."
                )
        except Exception:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)
        finally:
            session.pending_calendar_prefetch = False

    async def _ensure_calendar_immovables(
        self, session: Session, *, timeout_s: float = 4.0
    ) -> None:
        """Ensure calendar immovables are fetched and applied to frame facts."""
        planned_date = session.planned_date
        if not planned_date:
            return
        if session.prefetched_immovables_by_date.get(planned_date):
            self._apply_prefetched_calendar_immovables(session)
            return
        try:
            await asyncio.wait_for(
                self._prefetch_calendar_immovables(session, planned_date),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.debug("Calendar prefetch timed out for %s", planned_date)
        except Exception:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)
        self._apply_prefetched_calendar_immovables(session)

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
        tools = None

        def build(name: str, prompt: str, out_type) -> AssistantAgent:
            """Construct a stage helper agent with shared configuration."""
            return AssistantAgent(
                name=name,
                model_client=self._model_client,
                tools=tools,
                output_content_type=out_type,
                system_message=prompt,
                reflect_on_tool_use=False,
                max_tool_iterations=2,
            )

        self._stage_agents = {
            TimeboxingStage.COLLECT_CONSTRAINTS: build(
                "StageCollectConstraints", COLLECT_CONSTRAINTS_PROMPT, StageGateOutput
            ),
            TimeboxingStage.CAPTURE_INPUTS: build(
                "StageCaptureInputs", CAPTURE_INPUTS_PROMPT, StageGateOutput
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
        return parse_chat_content(StageGateOutput, response)

    def _format_stage_gate_input(self, *, stage: TimeboxingStage, context: dict[str, Any]) -> str:
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
                fields=["name", "necessity", "scope", "status", "source", "description"],
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
            daily_one_thing = parse_model_optional(DailyOneThing, input_facts.get("daily_one_thing"))
            frame_facts_json = json.dumps(frame_facts, ensure_ascii=False, sort_keys=True)
            scrubbed_input = dict(input_facts)
            scrubbed_input.pop("tasks", None)
            scrubbed_input.pop("daily_one_thing", None)
            input_facts_json = json.dumps(scrubbed_input, ensure_ascii=False, sort_keys=True)
            tasks_toon = toon_encode(
                name="tasks",
                rows=tasks_rows(tasks),
                fields=["title", "block_count", "duration_min", "due", "importance"],
            )
            daily_toon = toon_encode(
                name="daily_one_thing",
                rows=[{"title": daily_one_thing.title, "block_count": daily_one_thing.block_count or "", "duration_min": daily_one_thing.duration_min or ""}]
                if daily_one_thing
                else [],
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
        durable = session.durable_constraints_by_stage.get(
            TimeboxingStage.COLLECT_CONSTRAINTS.value, []
        )
        return CollectConstraintsContext(
            user_message=user_message,
            facts=dict(session.frame_facts or {}),
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

    async def _run_timebox_summary(
        self, *, stage: TimeboxingStage, timebox: Timebox
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
                [
                    TextMessage(
                        content=payload, source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.summary_s,
        )
        return parse_chat_content(StageGateOutput, response)

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
                [
                    TextMessage(
                        content=payload, source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.review_commit_s,
        )
        return parse_chat_content(StageGateOutput, response)

    async def _run_skeleton_draft(self, session: Session) -> Timebox:
        """Draft a skeleton timebox using known facts."""
        await self._ensure_stage_agents()
        context = await self._build_skeleton_context(session)
        system_prompt = render_skeleton_draft_system_prompt(context=context)
        draft_agent = AssistantAgent(
            name="StageDraftSkeleton",
            model_client=self._draft_model_client,
            tools=None,
            output_content_type=Timebox,
            system_message=system_prompt,
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )
        task = "Produce the Timebox JSON now."
        try:
            response = await with_timeout(
                "timeboxing:skeleton-draft",
                draft_agent.on_messages(
                    [TextMessage(content=task, source="user")], CancellationToken()
                ),
                timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
            )
            return parse_chat_content(Timebox, response)
        except asyncio.TimeoutError:
            session.background_updates.append(
                "Drafting took longer than expected; using a minimal skeleton to keep moving."
            )
            return self._build_fallback_skeleton_timebox(session)
        except Exception:
            logger.debug("Skeleton draft failed; using fallback.", exc_info=True)
            session.background_updates.append(
                "Drafting failed; using a minimal skeleton to keep moving."
            )
            return self._build_fallback_skeleton_timebox(session)

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

    def _normalize_calendar_events(
        self, immovables: Any | None
    ) -> list[CalendarEvent]:
        """Normalize immovable payloads into CalendarEvent instances."""
        events = parse_model_list(CalendarEvent, immovables)
        return self._sort_calendar_events(events)

    def _sort_calendar_events(
        self, events: list[CalendarEvent]
    ) -> list[CalendarEvent]:
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
                [
                    TextMessage(
                        content=payload, source="user"
                    )
                ],
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
        bullets = (
            "\n".join([f"- {b}" for b in gate.summary]) if gate.summary else "- (none)"
        )
        missing = (
            "\n".join([f"- {m}" for m in gate.missing]) if gate.missing else "- (none)"
        )
        parts = [header, "Summary:", bullets]
        if not gate.ready:
            parts.extend(["Missing:", missing])
        if gate.question:
            parts.append(f"Question: {gate.question}")
        if gate.ready:
            parts.append("Reply with what you'd like to adjust, or tell me to proceed.")
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
        if session.pending_durable_constraints:
            notes.append("Fetching your saved constraints in the background.")
        if session.pending_calendar_prefetch:
            notes.append("Loading calendar immovables for the day.")
        if session.pending_constraint_extractions:
            notes.append(
                "Syncing your preferences in the background so we can keep moving."
            )
        if session.background_updates:
            notes.extend(session.background_updates)
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
        if next_stage in (
            TimeboxingStage.COLLECT_CONSTRAINTS,
            TimeboxingStage.CAPTURE_INPUTS,
        ):
            session.timebox = None

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
            self._constraint_mcp_tools = await get_constraint_mcp_tools()
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
                ).hexdigest()[:TIMEBOXING_LIMITS.durable_task_key_len]

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
                                decision_scope=decision_scope if decision_scope else None,
                            ),
                            timeout_s=TIMEBOXING_TIMEOUTS.notion_upsert_s,
                        )
                    except Exception:
                        logger.debug(
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
        if not session.timebox:
            return []
        before = session.timebox
        await self._await_pending_constraint_extractions(session)
        constraints = await self._collect_constraints(session)
        patched = await self._timebox_patcher.apply_patch(
            current=session.timebox,
            user_message=text,
            constraints=constraints,
            actions=[],
        )
        session.timebox = patched
        session.last_user_message = text
        actions = _build_actions(before, patched, reason=text, constraints=constraints)
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
        combined = _dedupe_constraints(
            [
                c
                for stage_constraints in session.durable_constraints_by_stage.values()
                for c in (stage_constraints or [])
            ]
            + list(local_constraints or [])
        )
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
        return _wrap_with_constraint_review(reply, constraints=constraints, session=session)

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
        )
        self._sessions[key] = session
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
            )
            self._sessions[key] = session

        session.committed = True
        session.planned_date = message.planned_date
        session.tz_name = message.timezone or session.tz_name or "UTC"
        session.frame_facts.setdefault("date", message.planned_date)
        session.frame_facts.setdefault("timezone", session.tz_name)
        self._queue_constraint_prefetch(session)

        await self._ensure_calendar_immovables(session)

        session.thread_state = None
        session.last_extraction_task = None
        user_message = ""
        response = await self._run_graph_turn(session=session, user_text=user_message)
        wrapped = await self._maybe_wrap_constraint_review(reply=response, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                wrapped.content
                if isinstance(wrapped, TextMessage)
                else getattr(wrapped, "text", "")
            ),
            actions=[],
        )
        return wrapped

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
            )
            self._sessions[key] = session
            asyncio.create_task(
                self._prefetch_calendar_immovables(session, planned_date)
            )
            self._queue_constraint_prefetch(session)
            return self._build_commit_prompt_blocks(session=session)

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
                session.durable_constraints_by_stage = {}
                session.durable_constraints_loaded_stages = set()
                session.durable_constraints_date = None
                await self._prefetch_calendar_immovables(session, planned_date)

            session.frame_facts.setdefault("date", session.planned_date)
            session.frame_facts.setdefault("timezone", tz_name)
            await self._ensure_calendar_immovables(session)
            self._queue_constraint_prefetch(session)

            # Now continue with normal constraint extraction and stage processing
            # Fall through to the committed session logic below

        # Session is committed - run the GraphFlow stage machine.
        session.thread_state = None
        reply = await self._run_graph_turn(session=session, user_text=message.text)
        wrapped = await self._maybe_wrap_constraint_review(reply=reply, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                wrapped.content
                if isinstance(wrapped, TextMessage)
                else getattr(wrapped, "text", "")
            ),
            actions=[],
        )
        if session.thread_state:
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            return SlackThreadStateMessage(
                text=reply.content,
                thread_state=session.thread_state,
            )
        return wrapped

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
                session.durable_constraints_by_stage = {}
                session.durable_constraints_loaded_stages = set()
                session.durable_constraints_date = None
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
        session.thread_state = None
        reply = await self._run_graph_turn(session=session, user_text=message.content)
        wrapped = await self._maybe_wrap_constraint_review(reply=reply, session=session)
        await self._publish_update(
            session=session,
            user_message=(
                wrapped.content
                if isinstance(wrapped, TextMessage)
                else getattr(wrapped, "text", "")
            ),
            actions=[],
        )
        if session.thread_state:
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            return SlackThreadStateMessage(
                text=reply.content,
                thread_state=session.thread_state,
            )
        return wrapped

    @message_handler
    async def on_finalise(
        self, message: TimeboxingFinalResult, ctx: MessageContext
    ) -> TextMessage:
        """Handle finalization callbacks and clean up session state."""
        key = self._session_key(ctx)
        self._sessions.pop(key, None)
        return TextMessage(
            content=f"Session {message.thread_ts} marked {message.status}: {message.summary}",
            source=self.id.type,
        )

    async def cleanup(self) -> None:
        """Cleanup resources before shutdown."""
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


def _coerce_async_database_url(database_url: str) -> str:
    """Ensure a database URL uses an async driver when needed."""
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


__all__ = ["TimeboxingFlowAgent"]
