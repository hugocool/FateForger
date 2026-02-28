"""Revisor agent with guided weekly review session flow."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.base import Handoff as HandoffBase
from autogen_agentchat.messages import HandoffMessage, TextMessage
from autogen_core import MessageContext, RoutedAgent, message_handler
from pydantic import TypeAdapter

from fateforger.core.config import settings
from fateforger.core.logging_config import (
    emit_llm_audit_event,
    observe_stage_duration,
    record_error,
    record_llm_call,
)
from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client

from .messages import (
    ReviewIntentDecision,
    WeeklyReviewPhase,
    WeeklyReviewRecap,
    WeeklyReviewRecapRequest,
    WeeklyReviewRecapResponse,
    WeeklyReviewSessionState,
    WeeklyReviewTurn,
)

logger = logging.getLogger(__name__)

REVISOR_PROMPT = """
You are the FateForger Revisor. Your role is long-term optimization and review.

Core Responsibilities:
1. **Weekly Reviews**: Facilitate the transition from one week to the next. Ask:
   - What went well?
   - What didn't go as planned?
   - What concretely needs to change?
   - What are the risks for the coming week?
2. **Agent Optimization**: Analyze interactions with other agents (Receptionist, Timeboxer, Planner) and suggest or implement configuration changes to improve their performance.
3. **Long-term Management**: Connect with Notion Projects and manage the "Life Management" project. Look beyond daily timeboxing to monthly and quarterly goals.
4. **Strategic Alignment**: Ensure daily actions (Timeboxing) align with long-term projects and life goals.

Tone: Professional, analytical, but encouraging. You are the high-level strategist of the user's life.

Routing rule:
- If the user asks for operational sprint/backlog execution (finding/filtering tickets,
  linking parent/subtasks, or patching Notion sprint page content), hand off to
  `tasks_agent`.
""".strip()

REVIEW_INTENT_PROMPT = """
Classify whether the user is trying to start/continue a guided weekly review session.

Return only ReviewIntentDecision:
- start_session=true when the user asks to start, continue, or proceed with weekly review.
- start_session=false when the user asks for general strategy chat, unrelated questions,
  or explicit sprint/task execution requests.
""".strip()

GUIDED_WEEKLY_REVIEW_PROMPT = """
You run a gated weekly review session. Keep it short, direct, and conversational.

Rules:
- Ask -> wait -> reflect -> gate.
- Never advance unless gate is met.
- This flow is review, not scheduling.
- If user drifts into scheduling, park it and continue review.
- If data is sparse, ask one concise clarifying prompt.

Output contract:
- Return only WeeklyReviewTurn.
- Respect current phase from context.
- `gate_met=true` only when phase requirements are satisfied.
- On close phase, provide recap and set `session_complete=true`.

Phase requirements:
1) reflect:
   - 2-3 wins, 1-2 misses, and progress update(s) vs last week goals.
2) scan_board:
   - at least 3 active items reviewed OR explicit confirmation no active items.
3) outcomes:
   - one must outcome with definition of done; optional support outcomes also have done-defs.
4) systems_risks:
   - start/stop/continue captured + at least one risk and mitigation.
5) close:
   - one-sentence intention and compact recap (include weekly constraints/preferences if provided).
""".strip()

_START_COMMANDS = {
    "start a weekly review.",
    "start a weekly review",
    "start weekly review",
    "/weekly-review",
    "/review-weekly",
}
_CANCEL_COMMANDS = {
    "cancel weekly review",
    "stop weekly review",
    "exit weekly review",
}
_PHASE_ORDER = (
    WeeklyReviewPhase.REFLECT,
    WeeklyReviewPhase.SCAN_BOARD,
    WeeklyReviewPhase.OUTCOMES,
    WeeklyReviewPhase.SYSTEMS_RISKS,
    WeeklyReviewPhase.CLOSE,
)
_PHASE_LABELS = {
    WeeklyReviewPhase.REFLECT: "Reflect",
    WeeklyReviewPhase.SCAN_BOARD: "Scan Board",
    WeeklyReviewPhase.OUTCOMES: "Outcomes",
    WeeklyReviewPhase.SYSTEMS_RISKS: "Systems & Risks",
    WeeklyReviewPhase.CLOSE: "Close",
}


class RevisorAgent(RoutedAgent):
    """Agent that handles long-term planning and guided weekly review sessions."""

    def __init__(
        self, name: str, *, allowed_handoffs: List[HandoffBase] | None = None
    ) -> None:
        super().__init__(name)
        self._name = name
        self._assistant = AssistantAgent(
            name=f"{name}_assistant",
            system_message=REVISOR_PROMPT,
            model_client=build_autogen_chat_client("revisor_agent"),
            handoffs=allowed_handoffs or [],
        )
        self._intent_assistant = AssistantAgent(
            name=f"{name}_intent_classifier",
            system_message=REVIEW_INTENT_PROMPT,
            model_client=build_autogen_chat_client("revisor_agent"),
            output_content_type=ReviewIntentDecision,
            reflect_on_tool_use=False,
            max_tool_iterations=1,
        )
        self._guided_assistant = AssistantAgent(
            name=f"{name}_guided_weekly_review",
            system_message=GUIDED_WEEKLY_REVIEW_PROMPT,
            model_client=build_autogen_chat_client("revisor_agent"),
            output_content_type=WeeklyReviewTurn,
            reflect_on_tool_use=False,
            max_tool_iterations=1,
        )
        self._session: WeeklyReviewSessionState | None = None
        self._latest_recap_by_user: dict[str, WeeklyReviewRecap] = {}

    @message_handler
    async def handle_recap_request(
        self, message: WeeklyReviewRecapRequest, ctx: MessageContext
    ) -> WeeklyReviewRecapResponse:
        _ = ctx
        recap = self._latest_recap_by_user.get(message.user_id)
        if recap is None:
            return WeeklyReviewRecapResponse(found=False, recap=None)
        return WeeklyReviewRecapResponse(found=True, recap=recap)

    @message_handler
    async def handle_text(
        self, message: TextMessage, ctx: MessageContext
    ) -> TextMessage | HandoffMessage:
        content = (message.content or "").strip()
        normalized = content.lower()
        logger.debug("Revisor received message: %s", content)

        if self._session is not None:
            if normalized in _CANCEL_COMMANDS:
                self._session = None
                return TextMessage(
                    content="Weekly review session canceled.",
                    source="revisor_agent",
                )
            return await self._handle_guided_turn(message=message, ctx=ctx)

        should_start = normalized in _START_COMMANDS
        if not should_start:
            decision = await self._classify_review_intent(message=message, ctx=ctx)
            should_start = decision.start_session
        if should_start:
            self._session = WeeklyReviewSessionState(user_id=message.source)
            return self._render_phase_intro(self._session)

        response = await self._run_assistant(
            assistant=self._assistant,
            message=message,
            ctx=ctx,
            stage="weekly_review_general",
            call_label="weekly_review_general",
        )
        chat_message = getattr(response, "chat_message", None)
        if isinstance(chat_message, (TextMessage, HandoffMessage)):
            return chat_message
        fallback = getattr(chat_message, "content", None) if chat_message else None
        return TextMessage(content=str(fallback or "(no response)"), source=self._name)

    async def _classify_review_intent(
        self, *, message: TextMessage, ctx: MessageContext
    ) -> ReviewIntentDecision:
        response = await self._run_assistant(
            assistant=self._intent_assistant,
            message=TextMessage(
                content=f"User message:\n{message.content}\n", source=message.source
            ),
            ctx=ctx,
            stage="weekly_review_intent",
            call_label="weekly_review_intent",
        )
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, ReviewIntentDecision):
            return content
        return TypeAdapter(ReviewIntentDecision).validate_python(content)

    async def _handle_guided_turn(
        self, *, message: TextMessage, ctx: MessageContext
    ) -> TextMessage:
        assert self._session is not None
        session = self._session
        phase = session.phase
        response = await self._run_assistant(
            assistant=self._guided_assistant,
            message=TextMessage(
                content=self._build_guided_context(
                    session=session, user_message=message.content
                ),
                source=message.source,
            ),
            ctx=ctx,
            stage=f"weekly_review_{phase.value}",
            call_label=f"weekly_review_{phase.value}",
        )
        turn = self._extract_review_turn(response)
        session.turns += 1

        if turn.phase_summary:
            existing = list(session.phase_summaries.get(phase, []))
            existing.extend(turn.phase_summary)
            session.phase_summaries[phase] = existing[-8:]

        if turn.gate_met:
            if phase == WeeklyReviewPhase.CLOSE or turn.session_complete:
                if turn.recap is not None and session.user_id:
                    self._latest_recap_by_user[session.user_id] = turn.recap
                self._session = None
            else:
                session.phase = self._next_phase(phase)
        return self._render_turn(phase=phase, turn=turn)

    async def _run_assistant(
        self,
        *,
        assistant: AssistantAgent,
        message: TextMessage,
        ctx: MessageContext,
        stage: str,
        call_label: str,
    ) -> Any:
        started = perf_counter()
        try:
            response = await with_timeout(
                f"revisor:{call_label}",
                assistant.on_messages([message], ctx.cancellation_token),
                timeout_s=float(
                    getattr(settings, "agent_on_messages_timeout_seconds", 20)
                ),
            )
        except Exception as exc:
            observe_stage_duration(stage=stage, duration_s=perf_counter() - started)
            record_error(component="revisor_agent", error_type=type(exc).__name__)
            record_llm_call(
                agent="revisor_agent",
                model=self._configured_model_name(),
                status="error",
                call_label=call_label,
            )
            emit_llm_audit_event(
                {
                    "event_type": "review_llm_call",
                    "agent": "revisor_agent",
                    "stage": stage,
                    "call_label": call_label,
                    "status": "error",
                    "session_key": self._runtime_key(),
                    "request_excerpt": message.content,
                    "error": str(exc),
                }
            )
            raise

        observe_stage_duration(stage=stage, duration_s=perf_counter() - started)
        usage = getattr(getattr(response, "chat_message", None), "models_usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        record_llm_call(
            agent="revisor_agent",
            model=self._configured_model_name(),
            status="ok",
            call_label=call_label,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        emit_llm_audit_event(
            {
                "event_type": "review_llm_call",
                "agent": "revisor_agent",
                "stage": stage,
                "call_label": call_label,
                "status": "ok",
                "session_key": self._runtime_key(),
                "request_excerpt": message.content,
                "response_excerpt": getattr(
                    getattr(response, "chat_message", None), "content", None
                ),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        )
        return response

    @staticmethod
    def _extract_review_turn(response: Any) -> WeeklyReviewTurn:
        content = getattr(getattr(response, "chat_message", None), "content", None)
        if isinstance(content, WeeklyReviewTurn):
            return content
        return TypeAdapter(WeeklyReviewTurn).validate_python(content)

    def _build_guided_context(
        self, *, session: WeeklyReviewSessionState, user_message: str
    ) -> str:
        prior = []
        for phase in _PHASE_ORDER:
            lines = session.phase_summaries.get(phase, [])
            if not lines:
                continue
            prior.append(f"{self._phase_label(phase)}: " + " | ".join(lines[-3:]))
        prior_text = "\n".join(f"- {line}" for line in prior) if prior else "- none yet"
        return (
            "Weekly review context\n"
            f"Current phase: {session.phase.value}\n"
            f"Session turns so far: {session.turns}\n"
            "Prior phase summaries:\n"
            f"{prior_text}\n\n"
            f"User message:\n{user_message}\n"
        )

    def _render_phase_intro(self, session: WeeklyReviewSessionState) -> TextMessage:
        phase = session.phase
        lines = [
            f"*Weekly Review — Phase {self._phase_position(phase)}/{len(_PHASE_ORDER)} ({self._phase_label(phase)})*",
            "Gate: ⏳ pending",
        ]
        previous = self._latest_recap_by_user.get(session.user_id)
        if previous and previous.summary:
            lines.extend(
                [
                    "*Last review snapshot:*",
                    f"- {previous.summary}",
                    (
                        f"- Last intention: {previous.weekly_intention}"
                        if previous.weekly_intention
                        else "- Last intention: (not recorded)"
                    ),
                ]
            )
        lines.append("Kickoff: share 2-3 wins, 1-2 misses, and progress vs last week goals.")
        return TextMessage(content="\n".join(lines), source="revisor_agent")

    def _render_turn(
        self, *, phase: WeeklyReviewPhase, turn: WeeklyReviewTurn
    ) -> TextMessage:
        gate_line = "✅ met" if turn.gate_met else "❌ not met"
        lines = [
            f"*Weekly Review — Phase {self._phase_position(phase)}/{len(_PHASE_ORDER)} ({self._phase_label(phase)})*",
            f"Gate: {gate_line}",
            turn.assistant_message.strip(),
        ]
        if turn.phase_summary:
            lines.append("*Captured so far:*")
            lines.extend(f"- {item}" for item in turn.phase_summary[:5])
        if turn.missing_fields and not turn.gate_met:
            lines.append("*Still needed:*")
            lines.extend(f"- {item}" for item in turn.missing_fields[:5])
        if turn.recap:
            lines.append("*Weekly recap:*")
            lines.append(f"- {turn.recap.summary or 'No summary provided.'}")
            if turn.recap.weekly_intention:
                lines.append(f"- Intention: {turn.recap.weekly_intention}")
            if turn.recap.weekly_constraints:
                lines.extend(
                    f"- Weekly constraint: {item}"
                    for item in turn.recap.weekly_constraints[:4]
                )
        return TextMessage(content="\n".join(lines), source="revisor_agent")

    @staticmethod
    def _phase_label(phase: WeeklyReviewPhase) -> str:
        return _PHASE_LABELS.get(phase, phase.value.replace("_", " ").title())

    @staticmethod
    def _phase_position(phase: WeeklyReviewPhase) -> int:
        return _PHASE_ORDER.index(phase) + 1

    @staticmethod
    def _next_phase(phase: WeeklyReviewPhase) -> WeeklyReviewPhase:
        idx = _PHASE_ORDER.index(phase)
        if idx + 1 >= len(_PHASE_ORDER):
            return WeeklyReviewPhase.CLOSE
        return _PHASE_ORDER[idx + 1]

    @staticmethod
    def _configured_model_name() -> str:
        explicit = (getattr(settings, "llm_model_revisor", "") or "").strip()
        if explicit:
            return explicit
        provider = (getattr(settings, "llm_provider", "openai") or "openai").strip()
        if provider == "openrouter":
            return (
                getattr(settings, "openrouter_default_model_pro", "")
                or "openrouter-default"
            )
        return (getattr(settings, "openai_model", "") or "openai-default").strip()

    def _runtime_key(self) -> str:
        agent_id = getattr(self, "_id", None)
        return str(getattr(agent_id, "key", ""))
