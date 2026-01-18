"""Coordinator agent that runs a stage-gated timeboxing flow."""

from __future__ import annotations

import json
import logging
import os
import re
import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import (
    CancellationToken,
    DefaultTopicId,
    MessageContext,
    RoutedAgent,
    message_handler,
)
from dateutil import parser as date_parser
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fateforger.core.config import settings
from fateforger.debug.diag import with_timeout
from fateforger.agents.timeboxing.notion_constraint_extractor import (
    NotionConstraintExtractor,
)
from fateforger.llm import build_autogen_chat_client
from fateforger.tools.constraint_mcp import get_constraint_mcp_tools

from .messages import (
    StartTimeboxing,
    TimeboxingCommitDate,
    TimeboxingFinalResult,
    TimeboxingUserReply,
    TimeboxingUpdate,
)
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
from .preferences import (
    ConstraintBatch,
    Constraint,
    ConstraintStatus,
    ConstraintStore,
    ensure_constraint_schema,
)
from .timebox import Timebox
from .patching import TimeboxPatcher
from .actions import TimeboxAction
from .prompts import TIMEBOXING_SYSTEM_PROMPT, DRAFT_PROMPT
from fateforger.slack_bot.constraint_review import build_review_prompt_blocks, encode_metadata
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.timeboxing_commit import (
    FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID,
    FF_TIMEBOX_COMMIT_START_ACTION_ID,
)


logger = logging.getLogger(__name__)


@dataclass
class Session:
    """State container for an active timeboxing run."""

    thread_ts: str
    channel_id: str
    user_id: str
    last_user_message: str | None = None
    last_response: str | None = None
    completed: bool = False
    committed: bool = False
    planned_date: str | None = None
    tz_name: str = "UTC"
    prefetched_immovables_by_date: Dict[str, List[Dict[str, str]]] = field(
        default_factory=dict
    )
    active_constraints: List[Constraint] = field(default_factory=list)
    timebox: Timebox | None = None
    stage: TimeboxingStage = TimeboxingStage.COLLECT_CONSTRAINTS
    frame_facts: Dict[str, Any] = field(default_factory=dict)
    input_facts: Dict[str, Any] = field(default_factory=dict)
    stage_ready: bool = False
    stage_missing: List[str] = field(default_factory=list)
    stage_question: str | None = None


class TimeboxingFlowAgent(RoutedAgent):
    """Entry point for the GraphFlow-driven timeboxing workflow."""

    def __init__(self, name: str) -> None:
        super().__init__(description=name)
        self._sessions: Dict[str, Session] = {}
        self._model_client = build_autogen_chat_client(
            "timeboxing_agent", parallel_tool_calls=False
        )
        self._draft_model_client = build_autogen_chat_client(
            "timeboxing_draft", parallel_tool_calls=False
        )
        self._calendar_client: _McpCalendarClient | None = None
        self._constraint_store: ConstraintStore | None = None
        self._constraint_engine = None
        self._constraint_agent = self._build_constraint_agent()
        self._timebox_patcher = TimeboxPatcher()
        self._constraint_mcp_tools: list | None = None
        self._notion_extractor: NotionConstraintExtractor | None = None
        self._constraint_extractor_tool = None
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
        server_url = os.getenv("MCP_CALENDAR_SERVER_URL", "http://localhost:3000").strip()
        if not server_url:
            return None
        try:
            self._calendar_client = _McpCalendarClient(
                server_url=server_url,
                timeout=float(getattr(settings, "agent_mcp_discovery_timeout_seconds", 10)),
            )
        except Exception:
            logger.debug("Failed to initialize MCP calendar client", exc_info=True)
            return None
        return self._calendar_client

    @staticmethod
    def _infer_planned_date(text: str, *, now: datetime, tz: ZoneInfo) -> str:
        cleaned = (text or "").strip().lower()
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", cleaned)
        if match:
            return match.group(1)
        if "tomorrow" in cleaned:
            return (now.astimezone(tz).date() + timedelta(days=1)).isoformat()
        if "today" in cleaned:
            return now.astimezone(tz).date().isoformat()

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
            return (base + timedelta(days=delta)).isoformat()

        return now.astimezone(tz).date().isoformat()

    async def _prefetch_calendar_immovables(self, session: Session, planned_date: str) -> None:
        if planned_date in session.prefetched_immovables_by_date:
            return
        client = self._ensure_calendar_client()
        if not client:
            return
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
        except Exception:
            logger.debug("Calendar prefetch failed for %s", planned_date, exc_info=True)

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
        blocks: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Before we start:* confirm which day you want to timebox.\n"
                        f"Suggested: *{planned_date}* ({tz_name})"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": FF_TIMEBOX_COMMIT_START_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Start timeboxing"},
                        "style": "primary",
                        "value": meta,
                    },
                    {
                        "type": "button",
                        "action_id": FF_TIMEBOX_COMMIT_PICK_DAY_ACTION_ID,
                        "text": {"type": "plain_text", "text": "Pick another day"},
                        "value": meta,
                    },
                ],
            },
        ]
        return SlackBlockMessage(
            text=f"Confirm planned date: {planned_date} ({tz_name})",
            blocks=blocks,
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
        await self._ensure_constraint_mcp_tools()
        tools = (
            [self._constraint_extractor_tool] if self._constraint_extractor_tool else None
        )

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
        self._decision_agent = build(
            "StageDecision", DECISION_PROMPT, StageDecision
        )
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
            agent.on_messages([TextMessage(content=task, source="user")], CancellationToken()),
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
                [TextMessage(content=json.dumps(payload, ensure_ascii=False), source="user")],
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
                [TextMessage(content=json.dumps(payload, ensure_ascii=False), source="user")],
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
            self._draft_agent.on_messages([TextMessage(content=task, source="user")], CancellationToken()),
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
                [TextMessage(content=json.dumps(payload, ensure_ascii=False), source="user")],
                CancellationToken(),
            ),
            timeout_s=20,
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, StageDecision):
            return content
        return StageDecision.model_validate(content)

    def _format_stage_message(self, gate: StageGateOutput) -> str:
        stage_order = {
            TimeboxingStage.COLLECT_CONSTRAINTS: "Stage 1/5 (CollectConstraints)",
            TimeboxingStage.CAPTURE_INPUTS: "Stage 2/5 (CaptureInputs)",
            TimeboxingStage.SKELETON: "Stage 3/5 (Skeleton)",
            TimeboxingStage.REFINE: "Stage 4/5 (Refine)",
            TimeboxingStage.REVIEW_COMMIT: "Stage 5/5 (ReviewCommit)",
        }
        header = stage_order.get(gate.stage_id, f"Stage ({gate.stage_id.value})")
        bullets = "\n".join([f"- {b}" for b in gate.summary]) if gate.summary else "- (none)"
        missing = (
            "\n".join([f"- {m}" for m in gate.missing])
            if gate.missing
            else "- (none)"
        )
        parts = [header, "Summary:", bullets]
        if not gate.ready:
            parts.extend(["Missing:", missing])
        if gate.question:
            parts.append(f"Question: {gate.question}")
        if gate.ready:
            parts.append("Reply with what you'd like to adjust, or tell me to proceed.")
        return "\n".join(parts)

    async def _advance_stage(self, session: Session, *, next_stage: TimeboxingStage) -> None:
        session.stage = next_stage
        session.stage_ready = False
        session.stage_missing = []
        session.stage_question = None
        if next_stage in (TimeboxingStage.COLLECT_CONSTRAINTS, TimeboxingStage.CAPTURE_INPUTS):
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
            return TextMessage(content=self._format_stage_message(gate), source=self.id.type)

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
            return TextMessage(content=self._format_stage_message(gate), source=self.id.type)

        if session.stage == TimeboxingStage.SKELETON:
            if not session.frame_facts and not session.input_facts:
                return TextMessage(
                    content="Stage 3/5 (Skeleton)\nMissing prior inputs. Please go back to earlier stages.",
                    source=self.id.type,
                )
            session.timebox = await self._run_skeleton_draft(session)
            gate = await self._run_timebox_summary(stage=TimeboxingStage.SKELETON, timebox=session.timebox)
            session.stage_ready = gate.ready
            session.stage_missing = list(gate.missing or [])
            session.stage_question = gate.question
            return TextMessage(content=self._format_stage_message(gate), source=self.id.type)

        if session.stage == TimeboxingStage.REFINE:
            if not session.timebox:
                return TextMessage(
                    content="Stage 4/5 (Refine)\nNo draft timebox yet. Proceed from Skeleton first.",
                    source=self.id.type,
                )
            if user_message.strip():
                constraints = await self._collect_constraints(session)
                session.timebox = await self._timebox_patcher.apply_patch(
                    current=session.timebox,
                    user_message=user_message,
                    constraints=constraints,
                    actions=[],
                )
            gate = await self._run_timebox_summary(stage=TimeboxingStage.REFINE, timebox=session.timebox)
            session.stage_ready = True
            session.stage_missing = []
            session.stage_question = gate.question
            return TextMessage(content=self._format_stage_message(gate), source=self.id.type)

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
            return TextMessage(content=self._format_stage_message(gate), source=self.id.type)

        return TextMessage(content=f"Unknown stage: {session.stage.value}", source=self.id.type)

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
            model_client=self._model_client,
            output_content_type=ConstraintBatch,
            system_message=(
                "Extract scheduling preferences or constraints from the user's message. "
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
                model_client=self._model_client,
                tools=self._constraint_mcp_tools,
            )
            self._constraint_extractor_tool = (
                self._notion_extractor.extract_and_upsert_constraint
            )
        except Exception:
            logger.exception(
                "Failed to initialize constraint MCP tools; skipping Notion upserts."
            )

    async def _extract_constraints(
        self, session: Session, text: str
    ) -> ConstraintBatch | None:
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
        if not self._constraint_store:
            return []
        constraints = await self._constraint_store.list_constraints(
            user_id=session.user_id,
            channel_id=session.channel_id,
            thread_ts=session.thread_ts,
        )
        session.active_constraints = [
            c for c in constraints if c.status != ConstraintStatus.DECLINED
        ]
        return constraints

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
            committed=False,
            planned_date=planned_date,
            tz_name=tz_name,
        )
        self._sessions[key] = session
        asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
        return self._build_commit_prompt_blocks(session=session)

    @message_handler
    async def on_commit_date(
        self, message: TimeboxingCommitDate, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage:
        key = self._session_key(ctx, fallback=message.thread_ts)
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

        await self._prefetch_calendar_immovables(session, message.planned_date)
        self._apply_prefetched_calendar_immovables(session)

        user_message = session.last_user_message or ""
        extracted = await self._extract_constraints(session, user_message)
        response = await self._run_stage(session, user_message=user_message)
        await self._publish_update(session=session, user_message=response.content, actions=[])
        if extracted:
            return _wrap_with_constraint_review(
                response,
                count=len(extracted.constraints),
                session=session,
            )
        return response

    @message_handler
    async def on_user_reply(
        self, message: TimeboxingUserReply, ctx: MessageContext
    ) -> TextMessage | SlackBlockMessage | SlackThreadStateMessage:
        key = self._session_key(ctx, fallback=message.thread_ts)
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
            asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
            return self._build_commit_prompt_blocks(session=session)

        if not session.committed:
            tz_name = session.tz_name or self._default_tz_name()
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
            session.planned_date = planned_date
            session.tz_name = tz_name
            asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
            return self._build_commit_prompt_blocks(session=session)

        extracted = await self._extract_constraints(session, message.text)
        decision = await self._decide_next_action(session, user_message=message.text)
        if decision.action == "cancel":
            session.completed = True
            reply_text = "Okay—stopping this timeboxing session."
            await self._publish_update(session=session, user_message=reply_text, actions=[])
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
                reply_text = (
                    "Finalized. If you want changes, say so and I can go back to Refine."
                )
                reply = SlackThreadStateMessage(text=reply_text, thread_state="done")
            else:
                await self._proceed(session)
                reply = await self._run_stage(session, user_message="")
        else:
            # provide_info / redo default to re-running current stage
            reply = await self._run_stage(session, user_message=message.text)

        await self._publish_update(
            session=session,
            user_message=getattr(reply, "content", None) or getattr(reply, "text", "") or "",
            actions=[],
        )
        if extracted:
            if isinstance(reply, TextMessage):
                return _wrap_with_constraint_review(
                    reply,
                    count=len(extracted.constraints),
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
            session.planned_date = planned_date
            session.tz_name = tz_name
            asyncio.create_task(self._prefetch_calendar_immovables(session, planned_date))
            return self._build_commit_prompt_blocks(session=session)
        extracted = await self._extract_constraints(session, message.content)
        decision = await self._decide_next_action(session, user_message=message.content)
        if decision.action == "cancel":
            session.completed = True
            reply_text = "Okay—stopping this timeboxing session."
            await self._publish_update(session=session, user_message=reply_text, actions=[])
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
                reply_text = (
                    "Finalized. If you want changes, say so and I can go back to Refine."
                )
                reply = SlackThreadStateMessage(text=reply_text, thread_state="done")
            else:
                await self._proceed(session)
                reply = await self._run_stage(session, user_message="")
        else:
            reply = await self._run_stage(session, user_message=message.content)
        await self._publish_update(
            session=session,
            user_message=getattr(reply, "content", None) or getattr(reply, "text", "") or "",
            actions=[],
        )
        if extracted:
            if isinstance(reply, TextMessage):
                return _wrap_with_constraint_review(
                    reply,
                    count=len(extracted.constraints),
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
    count: int,
    session: Session,
) -> SlackBlockMessage:
    blocks = build_review_prompt_blocks(
        count=count,
        thread_ts=session.thread_ts,
        user_id=session.user_id,
    )
    return SlackBlockMessage(text=message.content, blocks=blocks)


class _McpCalendarClient:
    def __init__(self, *, server_url: str, timeout: float = 10.0) -> None:
        try:
            from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams
        except Exception as e:  # pragma: no cover
            raise RuntimeError("autogen_ext tools are required for MCP calendar access") from e
        params = StreamableHttpServerParams(url=server_url, timeout=timeout)
        self._workbench = McpWorkbench(params)

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
