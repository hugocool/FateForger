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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
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
from fateforger.tools.constraint_mcp import (
    build_constraint_server_env,
    get_constraint_mcp_tools,
    resolve_constraint_repo_root,
)
from fateforger.tools.ticktick_mcp import TickTickMcpClient, get_ticktick_mcp_url

from .actions import TimeboxAction
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
from .prompts import CONSTRAINT_INTENT_PROMPT, DRAFT_PROMPT, TIMEBOXING_SYSTEM_PROMPT
from .stage_gating import (
    CAPTURE_INPUTS_PROMPT,
    COLLECT_CONSTRAINTS_PROMPT,
    DECISION_PROMPT,
    REVIEW_COMMIT_PROMPT,
    TIMEBOX_SUMMARY_PROMPT,
    StageDecision,
    StageGateOutput,
    TimeboxingStage,
    format_stage_prompt_context,
)
from .timebox import Timebox

logger = logging.getLogger(__name__)


class ConstraintIntentDecision(BaseModel):
    """LLM output for deciding whether a constraint should be extracted and stored."""

    should_extract: bool
    decision_scope: Optional[str] = None
    reason: Optional[str] = None


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
    durable_constraints: List[Constraint] = field(default_factory=list)
    durable_constraints_loaded: bool = False
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


class TimeboxingFlowAgent(RoutedAgent):
    """Entry point for the GraphFlow-driven timeboxing workflow."""

    def __init__(self, name: str) -> None:
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
        self._calendar_client: _McpCalendarClient | None = None
        self._constraint_memory_client: _ConstraintMemoryClient | None = None
        self._ticktick_client: TickTickMcpClient | None = None
        self._constraint_store: ConstraintStore | None = None
        self._constraint_engine = None
        self._constraint_agent = self._build_constraint_agent()
        self._timebox_patcher = TimeboxPatcher()
        self._constraint_mcp_tools: list | None = None
        self._notion_extractor: NotionConstraintExtractor | None = None
        self._constraint_extractor_tool = None
        self._durable_constraint_task_keys: set[str] = set()
        self._durable_constraint_semaphore = asyncio.Semaphore(1)
        self._durable_constraint_prefetch_tasks: dict[str, asyncio.Task] = {}
        self._durable_constraint_prefetch_semaphore = asyncio.Semaphore(1)
        self._constraint_extraction_tasks: dict[str, asyncio.Task] = {}
        self._constraint_extraction_semaphore = asyncio.Semaphore(2)
        self._constraint_intent_agent: AssistantAgent | None = None
        self._stage_agents: Dict[TimeboxingStage, AssistantAgent] = {}
        self._decision_agent: AssistantAgent | None = None
        self._draft_agent: AssistantAgent | None = None
        self._summary_agent: AssistantAgent | None = None
        self._review_commit_agent: AssistantAgent | None = None

    # region helpers

    def _session_key(self, ctx: MessageContext, *, fallback: str | None = None) -> str:
        topic_key = ctx.topic_id.source if ctx.topic_id else None
        if topic_key:
            return topic_key
        if fallback:
            return fallback
        agent = ctx.sender if ctx.sender else None
        return agent.key if agent else "default"

    def _default_tz_name(self) -> str:
        return getattr(settings, "planning_timezone", "") or "Europe/Amsterdam"

    def _ensure_calendar_client(self) -> _McpCalendarClient | None:
        if self._calendar_client:
            return self._calendar_client
        server_url = os.getenv(
            "MCP_CALENDAR_SERVER_URL", "http://localhost:3000"
        ).strip()
        if not server_url:
            return None
        try:
            self._calendar_client = _McpCalendarClient(
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

    def _ensure_constraint_memory_client(self) -> "_ConstraintMemoryClient" | None:
        if self._constraint_memory_client:
            return self._constraint_memory_client
        if not settings.notion_timeboxing_parent_page_id:
            return None
        try:
            timeout = float(
                getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)
            )
            self._constraint_memory_client = _ConstraintMemoryClient(timeout=timeout)
        except Exception:
            logger.debug(
                "Failed to initialize constraint memory MCP client", exc_info=True
            )
            return None
        return self._constraint_memory_client

    async def _fetch_durable_constraints(self, session: Session) -> List[Constraint]:
        client = self._ensure_constraint_memory_client()
        if not client:
            return []
        planned_date = session.planned_date or datetime.utcnow().date().isoformat()
        filters = {
            "as_of": planned_date,
            "stage": TimeboxingStage.COLLECT_CONSTRAINTS.value,
            "statuses_any": ["locked", "proposed"],
            "require_active": True,
        }
        try:
            records = await client.query_constraints(filters=filters, limit=50)
        except Exception:
            logger.debug("Constraint memory query failed", exc_info=True)
            return []
        return _constraints_from_memory(records, user_id=session.user_id)

    @staticmethod
    def _infer_planned_date(text: str, *, now: datetime, tz: ZoneInfo) -> str:
        def next_workday(day: date) -> date:
            cursor = day
            while cursor.weekday() >= 5:
                cursor = cursor + timedelta(days=1)
            return cursor

        def tomorrow_workday(today: date) -> date:
            return next_workday(today + timedelta(days=1))

        cleaned = (text or "").strip().lower()
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
        if match:
            return match.group(1)
        if "tomorrow" in cleaned:
            base = now.astimezone(tz).date()
            return tomorrow_workday(base).isoformat()
        if "today" in cleaned:
            base = now.astimezone(tz).date()
            return next_workday(base).isoformat()

        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        for name, target in weekdays.items():
            if name not in cleaned:
                continue
            base = now.astimezone(tz).date()
            delta = (target - base.weekday()) % 7
            if delta == 0:
                delta = 7 if "next" in cleaned else 0
            return next_workday(base + timedelta(days=delta)).isoformat()

        local_now = now.astimezone(tz)
        base = next_workday(local_now.date())
        if base != local_now.date():
            return base.isoformat()
        if (local_now.hour, local_now.minute) >= (9, 0):
            return tomorrow_workday(base).isoformat()
        return base.isoformat()

    async def _ensure_constraint_intent_agent(self) -> None:
        if self._constraint_intent_agent:
            return
        self._constraint_intent_agent = AssistantAgent(
            name="ConstraintIntentClassifier",
            model_client=self._constraint_model_client,
            output_content_type=ConstraintIntentDecision,
            system_message=CONSTRAINT_INTENT_PROMPT,
            reflect_on_tool_use=False,
            max_tool_iterations=1,
        )

    async def _should_extract_constraints(
        self, text: str, *, is_initial: bool
    ) -> ConstraintIntentDecision:
        """Classify whether a message should be extracted and whether it is durable."""
        if not (text or "").strip():
            return ConstraintIntentDecision(should_extract=False, reason="empty")
        await self._ensure_constraint_intent_agent()
        payload = {"text": text, "is_initial": is_initial}
        response = await with_timeout(
            "timeboxing:constraint-intent",
            self._constraint_intent_agent.on_messages(
                [
                    TextMessage(
                        content=json.dumps(payload, ensure_ascii=False), source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=10,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, ConstraintIntentDecision):
            return content
        try:
            decision = ConstraintIntentDecision.model_validate(content)
            return decision
        except Exception:
            logger.debug("Constraint intent classifier returned invalid content")
            return ConstraintIntentDecision(
                should_extract=False, reason="invalid classifier response"
            )

    def _constraint_task_key(self, session: Session, text: str) -> str:
        payload = {
            "user_id": session.user_id,
            "channel_id": session.channel_id,
            "thread_ts": session.thread_ts,
            "text": text.strip(),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

    def _durable_prefetch_key(self, session: Session) -> str:
        planned_date = session.planned_date or "unknown"
        return f"{session.user_id}:{planned_date}"

    def _queue_constraint_prefetch(self, session: Session) -> None:
        self._queue_durable_constraint_prefetch(session=session, reason="prefetch")
        if session.constraints_prefetched:
            return
        if not settings.database_url:
            return

        async def _background() -> None:
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
        if not settings.notion_timeboxing_parent_page_id:
            return
        planned_date = session.planned_date or ""
        if (
            session.durable_constraints_loaded
            and session.durable_constraints_date == planned_date
        ):
            return
        if session.pending_durable_constraints:
            return

        task_key = self._durable_prefetch_key(session)
        if task_key in self._durable_constraint_prefetch_tasks:
            return

        async def _background() -> None:
            acquired = False
            session.pending_durable_constraints = True
            try:
                await self._durable_constraint_prefetch_semaphore.acquire()
                acquired = True
                constraints = await self._fetch_durable_constraints(session)
                session.durable_constraints = constraints
                session.durable_constraints_loaded = True
                session.durable_constraints_date = planned_date
                if constraints:
                    session.background_updates.append(
                        f"Loaded {len(constraints)} saved constraints."
                    )
                    await self._sync_durable_constraints_to_store(session)
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
            acquired = False
            try:
                await self._constraint_extraction_semaphore.acquire()
                acquired = True
                decision = await self._should_extract_constraints(
                    text, is_initial=is_initial
                )
                if not decision.should_extract:
                    return None
                explicit_scope = _infer_explicit_constraint_scope(text)
                decision_scope = _parse_enum(
                    ConstraintScope,
                    decision.decision_scope,
                    ConstraintScope.SESSION,
                )
                if explicit_scope:
                    decision_scope = explicit_scope
                elif decision_scope in (
                    ConstraintScope.PROFILE,
                    ConstraintScope.DATESPAN,
                ):
                    decision_scope = ConstraintScope.SESSION
                if decision_scope in (ConstraintScope.PROFILE, ConstraintScope.DATESPAN):
                    self._queue_durable_constraint_upsert(
                        session=session,
                        text=text,
                        reason=reason,
                        decision_scope=decision_scope.value,
                    )
                return await self._extract_constraints(
                    session, text, scope_override=decision_scope
                )
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
        ).hexdigest()[:16]
        if task_key in self._durable_constraint_task_keys:
            return
        if len(self._durable_constraint_task_keys) >= 10:
            return
        self._durable_constraint_task_keys.add(task_key)

        async def _background() -> None:
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
                    timeout_s=20,
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
        self, session: Session, timeout_s: float = 2.0
    ) -> None:
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
        if (
            self._stage_agents
            and self._decision_agent
            and self._draft_agent
            and self._summary_agent
            and self._review_commit_agent
        ):
            return
        tools = None

        def build(name: str, prompt: str, out_type):
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
        self._draft_agent = AssistantAgent(
            name="StageDraftSkeleton",
            model_client=self._draft_model_client,
            tools=tools,
            output_content_type=Timebox,
            system_message=f"{TIMEBOXING_SYSTEM_PROMPT}\n\n{DRAFT_PROMPT}",
            reflect_on_tool_use=False,
            max_tool_iterations=2,
        )
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
        facts: Dict[str, Any],
        user_message: str,
    ) -> StageGateOutput:
        await self._ensure_stage_agents()
        agent = self._stage_agents.get(stage)
        if not agent:
            raise ValueError(f"Unsupported stage: {stage}")

        task = format_stage_prompt_context(stage=stage, facts=facts) + (
            f"User message:\n{user_message}\n"
        )
        response = await with_timeout(
            f"timeboxing:stage:{stage.value}",
            agent.on_messages(
                [TextMessage(content=task, source="user")], CancellationToken()
            ),
            timeout_s=25,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, StageGateOutput):
            return content
        return StageGateOutput.model_validate(content)

    async def _run_timebox_summary(
        self, *, stage: TimeboxingStage, timebox: Timebox
    ) -> StageGateOutput:
        await self._ensure_stage_agents()
        assert self._summary_agent is not None
        payload = {"stage_id": stage.value, "timebox": timebox.model_dump(mode="json")}
        response = await with_timeout(
            f"timeboxing:summary:{stage.value}",
            self._summary_agent.on_messages(
                [
                    TextMessage(
                        content=json.dumps(payload, ensure_ascii=False), source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=20,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, StageGateOutput):
            return content
        return StageGateOutput.model_validate(content)

    async def _run_review_commit(self, *, timebox: Timebox) -> StageGateOutput:
        await self._ensure_stage_agents()
        assert self._review_commit_agent is not None
        payload = {"timebox": timebox.model_dump(mode="json")}
        response = await with_timeout(
            "timeboxing:review-commit",
            self._review_commit_agent.on_messages(
                [
                    TextMessage(
                        content=json.dumps(payload, ensure_ascii=False), source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=20,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, StageGateOutput):
            return content
        return StageGateOutput.model_validate(content)

    async def _run_skeleton_draft(self, session: Session) -> Timebox:
        await self._ensure_stage_agents()
        assert self._draft_agent is not None
        task = (
            "Draft a skeleton timebox using the known facts below.\n"
            + format_stage_prompt_context(
                stage=TimeboxingStage.SKELETON,
                facts={**session.frame_facts, **session.input_facts},
            )
        )
        response = await with_timeout(
            "timeboxing:skeleton-draft",
            self._draft_agent.on_messages(
                [TextMessage(content=task, source="user")], CancellationToken()
            ),
            timeout_s=45,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, Timebox):
            return content
        return Timebox.model_validate(content)

    async def _decide_next_action(
        self, session: Session, *, user_message: str
    ) -> StageDecision:
        await self._ensure_stage_agents()
        assert self._decision_agent is not None
        payload = {
            "current_stage": session.stage.value,
            "stage_ready": session.stage_ready,
            "stage_missing": session.stage_missing,
            "stage_question": session.stage_question,
            "user_message": user_message,
        }
        response = await with_timeout(
            "timeboxing:stage-decision",
            self._decision_agent.on_messages(
                [
                    TextMessage(
                        content=json.dumps(payload, ensure_ascii=False), source="user"
                    )
                ],
                CancellationToken(),
            ),
            timeout_s=20,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, StageDecision):
            return content
        return StageDecision.model_validate(content)

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
        prev_map = {
            TimeboxingStage.CAPTURE_INPUTS: TimeboxingStage.COLLECT_CONSTRAINTS,
            TimeboxingStage.SKELETON: TimeboxingStage.CAPTURE_INPUTS,
            TimeboxingStage.REFINE: TimeboxingStage.SKELETON,
            TimeboxingStage.REVIEW_COMMIT: TimeboxingStage.REFINE,
        }
        return prev_map.get(stage, TimeboxingStage.COLLECT_CONSTRAINTS)

    async def _run_stage(self, session: Session, *, user_message: str) -> TextMessage:
        background_notes = self._collect_background_notes(session)
        if session.stage == TimeboxingStage.COLLECT_CONSTRAINTS:
            gate = await self._run_stage_gate(
                stage=session.stage,
                facts=session.frame_facts,
                user_message=user_message,
            )
            session.stage_ready = gate.ready
            session.stage_missing = list(gate.missing or [])
            session.stage_question = gate.question
            session.frame_facts.update(gate.facts or {})
            return TextMessage(
                content=self._format_stage_message(
                    gate,
                    background_notes=background_notes,
                    constraints=session.active_constraints,
                    immovables=session.frame_facts.get("immovables"),
                ),
                source=self.id.type,
            )

        if session.stage == TimeboxingStage.CAPTURE_INPUTS:
            gate = await self._run_stage_gate(
                stage=session.stage,
                facts={**session.frame_facts, **session.input_facts},
                user_message=user_message,
            )
            session.stage_ready = gate.ready
            session.stage_missing = list(gate.missing or [])
            session.stage_question = gate.question
            session.input_facts.update(gate.facts or {})
            return TextMessage(
                content=self._format_stage_message(
                    gate,
                    background_notes=background_notes,
                    constraints=session.active_constraints,
                    immovables=session.frame_facts.get("immovables"),
                ),
                source=self.id.type,
            )

        if session.stage == TimeboxingStage.SKELETON:
            if not session.frame_facts and not session.input_facts:
                return TextMessage(
                    content="Stage 3/5 (Skeleton)\nMissing prior inputs. Please go back to earlier stages.",
                    source=self.id.type,
                )
            session.timebox = await self._run_skeleton_draft(session)
            gate = await self._run_timebox_summary(
                stage=TimeboxingStage.SKELETON, timebox=session.timebox
            )
            session.stage_ready = gate.ready
            session.stage_missing = list(gate.missing or [])
            session.stage_question = gate.question
            return TextMessage(
                content=self._format_stage_message(
                    gate,
                    background_notes=background_notes,
                    constraints=session.active_constraints,
                    immovables=session.frame_facts.get("immovables"),
                ),
                source=self.id.type,
            )

        if session.stage == TimeboxingStage.REFINE:
            if not session.timebox:
                return TextMessage(
                    content="Stage 4/5 (Refine)\nNo draft timebox yet. Proceed from Skeleton first.",
                    source=self.id.type,
                )
            if user_message.strip():
                await self._await_pending_constraint_extractions(session)
                constraints = await self._collect_constraints(session)
                session.timebox = await self._timebox_patcher.apply_patch(
                    current=session.timebox,
                    user_message=user_message,
                    constraints=constraints,
                    actions=[],
                )
            gate = await self._run_timebox_summary(
                stage=TimeboxingStage.REFINE, timebox=session.timebox
            )
            session.stage_ready = True
            session.stage_missing = []
            session.stage_question = gate.question
            return TextMessage(
                content=self._format_stage_message(
                    gate,
                    background_notes=background_notes,
                    constraints=session.active_constraints,
                    immovables=session.frame_facts.get("immovables"),
                ),
                source=self.id.type,
            )

        if session.stage == TimeboxingStage.REVIEW_COMMIT:
            if not session.timebox:
                return TextMessage(
                    content="Stage 5/5 (ReviewCommit)\nNo draft timebox yet. Go back to Skeleton.",
                    source=self.id.type,
                )
            gate = await self._run_review_commit(timebox=session.timebox)
            session.stage_ready = True
            session.stage_missing = []
            session.stage_question = gate.question
            return TextMessage(
                content=self._format_stage_message(
                    gate,
                    background_notes=background_notes,
                    constraints=session.active_constraints,
                    immovables=session.frame_facts.get("immovables"),
                ),
                source=self.id.type,
            )

        return TextMessage(
            content=f"Unknown stage: {session.stage.value}", source=self.id.type
        )

    async def _proceed(self, session: Session) -> None:
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
        return AssistantAgent(
            name="ConstraintExtractor",
            model_client=self._constraint_model_client,
            output_content_type=ConstraintBatch,
            system_message=(
                "Extract ONLY explicit scheduling preferences or constraints that the USER personally stated. "
                "Examples of valid constraints:\n"
                "- 'I have a meeting at 2pm' -> fixed appointment\n"
                "- 'I don't work before 9am' -> work window preference\n"
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
        if self._constraint_store or not settings.database_url:
            return
        async_url = _coerce_async_database_url(settings.database_url)
        engine = create_async_engine(async_url)
        await ensure_constraint_schema(engine)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        self._constraint_store = ConstraintStore(sessionmaker)
        self._constraint_engine = engine

    async def _ensure_constraint_mcp_tools(self) -> None:
        if self._constraint_mcp_tools:
            return
        if not settings.notion_timeboxing_parent_page_id:
            return
        try:
            self._constraint_mcp_tools = await get_constraint_mcp_tools()
            self._notion_extractor = NotionConstraintExtractor(
                model_client=self._constraint_model_client,
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
                decision_scope: str = "",
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
                ).hexdigest()[:16]

                if task_key in self._durable_constraint_task_keys:
                    return {"queued": False, "deduped": True, "task_key": task_key}

                if len(self._durable_constraint_task_keys) >= 10:
                    return {"queued": False, "rate_limited": True, "task_key": task_key}

                self._durable_constraint_task_keys.add(task_key)

                async def _background() -> None:
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
                            timeout_s=20,
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
            timeout_s=25,
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

    async def _collect_constraints(self, session: Session):
        local_constraints: list[Constraint] = []
        if self._constraint_store:
            local_constraints = await self._constraint_store.list_constraints(
                user_id=session.user_id,
                channel_id=session.channel_id,
                thread_ts=session.thread_ts,
            )
        combined = _dedupe_constraints(
            list(session.durable_constraints or []) + list(local_constraints or [])
        )
        session.active_constraints = [
            c for c in combined if c.status != ConstraintStatus.DECLINED
        ]
        return combined

    async def _sync_durable_constraints_to_store(self, session: Session) -> None:
        """Mirror durable constraints into the local store for Slack display."""
        if not session.durable_constraints:
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
        for constraint in session.durable_constraints:
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

    # endregion

    @message_handler
    async def on_start(
        self, message: StartTimeboxing, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
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
        planned_date = self._infer_planned_date(
            message.user_input,
            now=datetime.now(timezone.utc),
            tz=tz,
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

        user_message = session.last_user_message or ""
        response = await self._run_stage(session, user_message=user_message)
        await self._publish_update(
            session=session, user_message=response.content, actions=[]
        )
        return response

    @message_handler
    async def on_user_reply(
        self, message: TimeboxingUserReply, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
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
            planned_date = self._infer_planned_date(
                message.text,
                now=datetime.now(timezone.utc),
                tz=tz,
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
            planned_date = self._infer_planned_date(
                message.text,
                now=datetime.now(timezone.utc),
                tz=tz,
            )
            if planned_date != session.planned_date:
                session.planned_date = planned_date
                session.durable_constraints = []
                session.durable_constraints_loaded = False
                session.durable_constraints_date = None
                await self._prefetch_calendar_immovables(session, planned_date)

            session.frame_facts.setdefault("date", session.planned_date)
            session.frame_facts.setdefault("timezone", tz_name)
            await self._ensure_calendar_immovables(session)
            self._queue_constraint_prefetch(session)

            # Now continue with normal constraint extraction and stage processing
            # Fall through to the committed session logic below

        # Session is committed - continue with constraint collection and stage processing
        await self._ensure_calendar_immovables(session)

        extracted = None
        extraction_task = self._queue_constraint_extraction(
            session=session,
            text=message.text,
            reason="user_reply",
            is_initial=False,
        )
        decision = await self._decide_next_action(session, user_message=message.text)
        if decision.action == "cancel":
            session.completed = True
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            reply_text = "Okay—stopping this timeboxing session."
            await self._publish_update(
                session=session, user_message=reply_text, actions=[]
            )
            return SlackThreadStateMessage(
                text=reply_text,
                thread_state="canceled",
            )
        if decision.action == "back":
            target = decision.target_stage or self._previous_stage(session.stage)
            await self._advance_stage(session, next_stage=target)
            reply = await self._run_stage(session, user_message=message.text)
        elif decision.action == "proceed":
            if session.stage == TimeboxingStage.REVIEW_COMMIT:
                session.completed = True
                timeboxing_activity.mark_inactive(user_id=session.user_id)
                reply_text = "Finalized. If you want changes, say so and I can go back to Refine."
                reply = SlackThreadStateMessage(text=reply_text, thread_state="done")
            else:
                await self._proceed(session)
                reply = await self._run_stage(session, user_message="")
        else:
            # provide_info / redo default to re-running current stage
            reply = await self._run_stage(session, user_message=message.text)

        await self._publish_update(
            session=session,
            user_message=getattr(reply, "content", None)
            or getattr(reply, "text", "")
            or "",
            actions=[],
        )
        if extraction_task and extraction_task.done():
            extracted = extraction_task.result()
        if extracted:
            if isinstance(reply, TextMessage):
                await self._ensure_constraint_store()
                constraints: list[Constraint] = []
                if self._constraint_store:
                    constraints = await self._constraint_store.list_constraints(
                        user_id=session.user_id,
                        channel_id=session.channel_id,
                        thread_ts=session.thread_ts,
                        status=ConstraintStatus.PROPOSED,
                    )
                if constraints:
                    return _wrap_with_constraint_review(
                        reply,
                        constraints=constraints,
                        session=session,
                    )
        return reply

    @message_handler
    async def on_user_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
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
            planned_date = self._infer_planned_date(
                message.content,
                now=datetime.now(timezone.utc),
                tz=tz,
            )
            if planned_date != session.planned_date:
                session.durable_constraints = []
                session.durable_constraints_loaded = False
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
        extracted = None
        extraction_task = self._queue_constraint_extraction(
            session=session,
            text=message.content,
            reason="user_text",
            is_initial=False,
        )
        await self._ensure_calendar_immovables(session)
        decision = await self._decide_next_action(session, user_message=message.content)
        if decision.action == "cancel":
            session.completed = True
            timeboxing_activity.mark_inactive(user_id=session.user_id)
            reply_text = "Okay—stopping this timeboxing session."
            await self._publish_update(
                session=session, user_message=reply_text, actions=[]
            )
            return SlackThreadStateMessage(
                text=reply_text,
                thread_state="canceled",
            )
        if decision.action == "back":
            target = decision.target_stage or self._previous_stage(session.stage)
            await self._advance_stage(session, next_stage=target)
            reply = await self._run_stage(session, user_message=message.content)
        elif decision.action == "proceed":
            if session.stage == TimeboxingStage.REVIEW_COMMIT:
                session.completed = True
                timeboxing_activity.mark_inactive(user_id=session.user_id)
                reply_text = "Finalized. If you want changes, say so and I can go back to Refine."
                reply = SlackThreadStateMessage(text=reply_text, thread_state="done")
            else:
                await self._proceed(session)
                reply = await self._run_stage(session, user_message="")
        else:
            reply = await self._run_stage(session, user_message=message.content)
        await self._publish_update(
            session=session,
            user_message=getattr(reply, "content", None)
            or getattr(reply, "text", "")
            or "",
            actions=[],
        )
        if extraction_task and extraction_task.done():
            extracted = extraction_task.result()
        if extracted:
            if isinstance(reply, TextMessage):
                await self._ensure_constraint_store()
                constraints: list[Constraint] = []
                if self._constraint_store:
                    constraints = await self._constraint_store.list_constraints(
                        user_id=session.user_id,
                        channel_id=session.channel_id,
                        thread_ts=session.thread_ts,
                        status=ConstraintStatus.PROPOSED,
                    )
                if constraints:
                    return _wrap_with_constraint_review(
                        reply,
                        constraints=constraints,
                        session=session,
                    )
        return reply

    @message_handler
    async def on_finalise(
        self, message: TimeboxingFinalResult, ctx: MessageContext
    ) -> TextMessage:
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
    if isinstance(content, Timebox):
        return content
    if isinstance(content, dict):
        try:
            return Timebox.model_validate(content)
        except Exception:
            return None
    return None


def _capture_timebox(session: Session, content) -> None:
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
    names = [c.name for c in constraints if c.name]
    if names:
        return f"user: {user_message} | constraints: {', '.join(names)}"
    return f"user: {user_message}"


def _event_map(events: List[object]) -> Dict[str, object]:
    mapping: Dict[str, object] = {}
    for idx, event in enumerate(events):
        key = _event_key(event, idx)
        mapping[key] = event
    return mapping


def _event_key(event: object, idx: int) -> str:
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


def _parse_enum(enum_cls, value, default):
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return default
    try:
        return enum_cls(str(value).lower())
    except Exception:
        return default


def _infer_explicit_constraint_scope(text: str) -> ConstraintScope | None:
    """Infer an explicit constraint scope from user language when clearly stated."""
    cleaned = " ".join((text or "").lower().split())
    if not cleaned:
        return None
    session_phrases = [
        "only today",
        "today only",
        "just today",
        "for today",
        "this session",
        "this time",
        "today i",
    ]
    for phrase in session_phrases:
        if phrase in cleaned:
            return ConstraintScope.SESSION
    profile_phrases = [
        "always",
        "usually",
        "normally",
        "in general",
        "from now on",
        "every day",
        "most days",
        "generally",
    ]
    for phrase in profile_phrases:
        if phrase in cleaned:
            return ConstraintScope.PROFILE
    datespan_phrases = [
        "this week",
        "next week",
        "this month",
        "next month",
        "for the next",
        "over the next",
        "until",
        "through",
        "between",
    ]
    for phrase in datespan_phrases:
        if phrase in cleaned:
            return ConstraintScope.DATESPAN
    return None


def _parse_dow(value: str | None) -> ConstraintDayOfWeek | None:
    if not value:
        return None
    try:
        return ConstraintDayOfWeek(str(value).upper())
    except Exception:
        return None


def _parse_date_value(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def _constraints_from_memory(
    records: list[dict[str, Any]], *, user_id: str
) -> list[Constraint]:
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
    seen: set[str] = set()
    deduped: list[Constraint] = []
    for constraint in constraints:
        key = _constraint_identity_key(constraint)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(constraint)
    return deduped


class _ConstraintMemoryClient:
    def __init__(self, *, timeout: float = 10.0) -> None:
        try:
            from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for constraint memory access"
            ) from exc

        root = resolve_constraint_repo_root()
        server_path = root / "scripts" / "constraint_mcp_server.py"
        params = StdioServerParams(
            command=sys.executable,
            args=[str(server_path)],
            env=build_constraint_server_env(root),
            cwd=str(root),
            read_timeout_seconds=timeout,
        )
        self._workbench = McpWorkbench(params)

    async def query_constraints(
        self, *, filters: dict[str, Any], limit: int = 50
    ) -> list[dict[str, Any]]:
        payload = {
            "filters": filters,
            "limit": limit,
        }
        result = await self._workbench.call_tool(
            "constraint.query_constraints", arguments=payload
        )
        try:
            text = result.to_text()
            data = json.loads(text)
        except Exception:
            return []
        return data if isinstance(data, list) else []


class _McpCalendarClient:
    def __init__(self, *, server_url: str, timeout: float = 10.0) -> None:
        from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

        params = StreamableHttpServerParams(url=server_url, timeout=timeout)
        self._workbench = McpWorkbench(params)

    def get_tools(self) -> list:
        """Get calendar MCP tools for use by LLM agents."""
        try:
            return self._workbench.get_tools()
        except Exception:
            logger.debug("Failed to get calendar MCP tools", exc_info=True)
            return []

    @staticmethod
    def _extract_tool_payload(result: Any) -> Any:
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
    def _normalize_events(payload: Any) -> list[dict]:
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _parse_event_dt(raw: dict[str, Any] | None, *, tz: ZoneInfo) -> datetime | None:
        if not raw:
            return None
        if "dateTime" in raw and raw["dateTime"]:
            dt_val = date_parser.isoparse(raw["dateTime"])
            return dt_val.astimezone(tz)
        if "date" in raw and raw["date"]:
            day_val = date_parser.isoparse(raw["date"]).date()
            return datetime.combine(day_val, datetime.min.time(), tz)
        return None

    @staticmethod
    def _to_hhmm(dt_val: datetime | None, *, tz: ZoneInfo) -> str | None:
        if not dt_val:
            return None
        return dt_val.astimezone(tz).strftime("%H:%M")

    async def list_day_immovables(
        self, *, calendar_id: str, day: date, tz: ZoneInfo
    ) -> list[dict[str, str]]:
        start = (
            datetime.combine(day, datetime.min.time(), tz)
            .astimezone(timezone.utc)
            .isoformat()
        )
        end = (
            (datetime.combine(day, datetime.min.time(), tz) + timedelta(days=1))
            .astimezone(timezone.utc)
            .isoformat()
        )
        args = {
            "calendarId": calendar_id,
            "timeMin": start,
            "timeMax": end,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        result = await self._workbench.call_tool("list-events", arguments=args)
        payload = self._extract_tool_payload(result)
        events = self._normalize_events(payload)

        immovables: list[dict[str, str]] = []
        for event in events:
            if (event.get("status") or "").lower() == "cancelled":
                continue
            summary = (event.get("summary") or "").strip() or "Busy"
            start_dt = self._parse_event_dt(event.get("start"), tz=tz)
            end_dt = self._parse_event_dt(event.get("end"), tz=tz)
            if not start_dt or not end_dt or end_dt <= start_dt:
                continue
            start_str = self._to_hhmm(start_dt, tz=tz)
            end_str = self._to_hhmm(end_dt, tz=tz)
            if not start_str or not end_str:
                continue
            immovables.append({"title": summary, "start": start_str, "end": end_str})
        return immovables


def _coerce_async_database_url(database_url: str) -> str:
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


__all__ = ["TimeboxingFlowAgent"]
