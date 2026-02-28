"""GraphFlow node agent implementations for the timeboxing stage machine.

These nodes are lightweight orchestration agents that:
- mutate the in-memory `Session`
- call existing stage helpers on `TimeboxingFlowAgent`
- emit small control messages (StructuredMessage) or the final user-facing TextMessage

The graph itself is built in `src/fateforger/agents/timeboxing/flow_graph.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Optional, Sequence

from autogen_agentchat.agents._base_chat_agent import BaseChatAgent
from autogen_agentchat.base._chat_agent import Response
from autogen_agentchat.messages import BaseChatMessage, StructuredMessage, TextMessage
from autogen_core import CancellationToken
from pydantic import BaseModel

from fateforger.agents.timeboxing.stage_gating import (
    StageDecision,
    StageGateOutput,
    TimeboxingStage,
)

if TYPE_CHECKING:  # pragma: no cover
    from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent

logger = logging.getLogger(__name__)


class FlowSignal(BaseModel):
    """Internal routing signal for GraphFlow nodes."""

    kind: str
    note: Optional[str] = None


def _latest_user_text(messages: Sequence[BaseChatMessage]) -> str:
    """Return the newest user text from a message batch."""
    for msg in reversed(messages):
        if isinstance(msg, TextMessage) and msg.source == "user":
            return msg.content
    # Fallback: some runtimes may pass task as a plain TextMessage with other source.
    for msg in reversed(messages):
        if isinstance(msg, TextMessage):
            return msg.content
    return ""


@dataclass(slots=True)
class TurnContext:
    """Per-turn state shared across nodes (in-memory only)."""

    user_text: str = ""
    decision: StageDecision | None = None
    extraction_task: Any | None = None


class TurnInitNode(BaseChatAgent):
    """Initialize per-turn context and kick off background work."""

    def __init__(
        self, *, orchestrator: "TimeboxingFlowAgent", session: "Session"
    ) -> None:
        super().__init__(name="TurnInitNode", description="Timeboxing turn initializer")
        self._orchestrator = orchestrator
        self._session = session
        self.turn = TurnContext()

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (StructuredMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        user_text = _latest_user_text(messages)
        self.turn = TurnContext(user_text=user_text)
        self._session.last_user_message = user_text

        await self._orchestrator._ensure_calendar_immovables(
            self._session
        )  # noqa: SLF001
        self.turn.extraction_task = (
            self._orchestrator._queue_constraint_extraction(  # noqa: SLF001
                session=self._session,
                text=user_text,
                reason="graphflow_turn",
                is_initial=False,
            )
        )
        self._session.last_extraction_task = self.turn.extraction_task
        return Response(
            chat_message=StructuredMessage(
                source=self.name,
                content=FlowSignal(kind="turn_init"),
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.turn = TurnContext()


class DecisionNode(BaseChatAgent):
    """Decide whether to proceed/back/cancel/redo based on the user reply."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        turn_init: TurnInitNode,
    ) -> None:
        super().__init__(name="DecisionNode", description="Timeboxing decision node")
        self._orchestrator = orchestrator
        self._session = session
        self._turn_init = turn_init

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (StructuredMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        user_text = self._turn_init.turn.user_text
        if not user_text.strip():
            decision = StageDecision(action="provide_info")
            self._turn_init.turn.decision = decision
            return Response(
                chat_message=StructuredMessage(source=self.name, content=decision)
            )
        decision = await self._orchestrator._decide_next_action(  # noqa: SLF001
            self._session, user_message=user_text
        )
        self._turn_init.turn.decision = decision
        return Response(
            chat_message=StructuredMessage(source=self.name, content=decision)
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None


class TransitionNode(BaseChatAgent):
    """Apply the decision to session.stage and derive the stage runner user_message."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        turn_init: TurnInitNode,
    ) -> None:
        super().__init__(
            name="TransitionNode", description="Timeboxing transition node"
        )
        self._orchestrator = orchestrator
        self._session = session
        self._turn_init = turn_init
        self.stage_user_message: str = ""

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (StructuredMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        decision = self._turn_init.turn.decision
        user_text = self._turn_init.turn.user_text
        self.stage_user_message = user_text

        if decision is None:
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="transition", note="no-decision"),
                )
            )

        if decision.action == "cancel":
            self._session.completed = True
            self._session.thread_state = "canceled"
            self._session.last_response = "Okay—stopping this timeboxing session."
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="transition", note="canceled"),
                )
            )

        if decision.action == "back":
            target = (
                decision.target_stage
                or self._orchestrator._previous_stage(  # noqa: SLF001
                    self._session.stage
                )
            )
            await self._orchestrator._advance_stage(
                self._session, next_stage=target
            )  # noqa: SLF001
            self.stage_user_message = user_text
            return Response(
                chat_message=StructuredMessage(
                    source=self.name, content=FlowSignal(kind="transition", note="back")
                )
            )

        if decision.action == "proceed":
            await self._orchestrator._proceed(self._session)  # noqa: SLF001
            self.stage_user_message = ""
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="transition", note="proceed"),
                )
            )

        if decision.action == "provide_info":
            # In Stage 5, user corrections must return to Stage 4 patching.
            if self._session.stage == TimeboxingStage.REVIEW_COMMIT:
                await self._orchestrator._advance_stage(  # noqa: SLF001
                    self._session,
                    next_stage=TimeboxingStage.REFINE,
                )
            self.stage_user_message = user_text
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="transition", note="rerun"),
                )
            )

        # redo / assist: rerun current stage with user text
        self.stage_user_message = user_text
        return Response(
            chat_message=StructuredMessage(
                source=self.name, content=FlowSignal(kind="transition", note="rerun")
            )
        )

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.stage_user_message = ""


class _StageNodeBase(BaseChatAgent):
    """Base class for stage nodes that update Session and cache the last gate output."""

    def __init__(
        self,
        *,
        name: str,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(name=name, description=f"Run stage {name}")
        self._orchestrator = orchestrator
        self._session = session
        self._transition = transition
        self.last_gate: Any | None = None

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (StructuredMessage,)

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        self.last_gate = None


class StageCollectConstraintsNode(_StageNodeBase):
    """Run CollectConstraints stage gate and update frame_facts."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(
            name="StageCollectConstraintsNode",
            orchestrator=orchestrator,
            session=session,
            transition=transition,
        )

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        user_message = self._transition.stage_user_message
        gate = await self._orchestrator._run_stage_gate(  # noqa: SLF001
            stage=TimeboxingStage.COLLECT_CONSTRAINTS,
            user_message=user_message,
            context=self._orchestrator._build_collect_constraints_context(  # noqa: SLF001
                self._session, user_message=user_message
            ),
        )
        self._session.frame_facts.update(gate.facts or {})
        if user_message.strip():
            await self._orchestrator._refresh_collect_constraints_durable(  # noqa: SLF001
                self._session,
                reason="collect_user_reply",
            )
        gate = self._orchestrator._normalize_collect_constraints_gate(  # noqa: SLF001
            session=self._session,
            gate=gate,
            user_message=user_message,
        )
        self._session.stage_ready = gate.ready
        self._session.stage_missing = list(gate.missing or [])
        self._session.stage_question = gate.question
        self._session.frame_facts.update(gate.facts or {})
        self.last_gate = gate
        return Response(chat_message=StructuredMessage(source=self.name, content=gate))


class StageCaptureInputsNode(_StageNodeBase):
    """Run CaptureInputs stage gate and update input_facts."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(
            name="StageCaptureInputsNode",
            orchestrator=orchestrator,
            session=session,
            transition=transition,
        )

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        user_message = self._transition.stage_user_message
        gate = await self._orchestrator._run_stage_gate(  # noqa: SLF001
            stage=TimeboxingStage.CAPTURE_INPUTS,
            user_message=user_message,
            context=self._orchestrator._build_capture_inputs_context(  # noqa: SLF001
                self._session, user_message=user_message
            ),
        )
        self._session.stage_ready = gate.ready
        self._session.stage_missing = list(gate.missing or [])
        self._session.stage_question = gate.question
        self._session.input_facts.update(gate.facts or {})
        self._orchestrator._queue_skeleton_pre_generation(self._session)  # noqa: SLF001
        self.last_gate = gate
        return Response(chat_message=StructuredMessage(source=self.name, content=gate))


class StageSkeletonNode(_StageNodeBase):
    """Draft a skeleton timebox and summarize it."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(
            name="StageSkeletonNode",
            orchestrator=orchestrator,
            session=session,
            transition=transition,
        )

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        if not self._session.frame_facts and not self._session.input_facts:
            self._session.last_response = "Stage 3/5 (Skeleton)\nMissing prior inputs. Please go back to earlier stages."
            self.last_gate = None
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="stage", note="missing-priors"),
                )
            )
        try:
            (
                self._session.timebox,
                self._session.skeleton_overview_markdown,
                self._session.tb_plan,
            ) = await self._orchestrator._consume_pre_generated_skeleton(
                self._session
            )  # noqa: SLF001
        except Exception as exc:
            logger.warning("Skeleton drafting failed: %s", exc, exc_info=True)
            gate = StageGateOutput(
                stage_id=TimeboxingStage.SKELETON,
                ready=False,
                summary=[
                    "I couldn't draft the Stage 3 overview yet.",
                    f"Drafting error: {str(exc) or type(exc).__name__}",
                ],
                missing=["A valid draft plan from current inputs/constraints"],
                question="Please click Redo to retry Stage 3 drafting.",
                facts={},
            )
            self._session.stage_ready = False
            self._session.stage_missing = list(gate.missing or [])
            self._session.stage_question = gate.question
            self.last_gate = gate
            return Response(chat_message=StructuredMessage(source=self.name, content=gate))
        # Stage 3 is presentation-only. Keep Timebox materialization for Stage 4.
        self._session.timebox = None
        # Stage 3 defers sync baseline preparation to Stage 4.
        self._session.base_snapshot = None
        markdown = (
            self._session.skeleton_overview_markdown
            or "## Day Overview\n- No skeleton overview was generated."
        )
        self._session.stage_ready = True
        self._session.stage_missing = []
        self._session.stage_question = "Reply with adjustments, or tell me to proceed."
        self._session.last_response = "Stage 3/5 (Skeleton)\nOverview ready below."
        self._session.pending_presenter_blocks = (
            self._orchestrator._render_markdown_summary_blocks(text=markdown)  # noqa: SLF001
        )
        self.last_gate = None
        return Response(
            chat_message=StructuredMessage(
                source=self.name,
                content=FlowSignal(kind="stage", note="skeleton-overview"),
            )
        )


class StageRefineNode(_StageNodeBase):
    """Apply patch-based refinement and summarize the updated timebox."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(
            name="StageRefineNode",
            orchestrator=orchestrator,
            session=session,
            transition=transition,
        )

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        if self._session.timebox is None and self._session.tb_plan is None:
            self._session.last_response = (
                "Stage 4/5 (Refine)\nNo draft timebox yet. Proceed from Skeleton first."
            )
            self.last_gate = None
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="stage", note="missing-timebox"),
                )
            )
        await self._orchestrator._ensure_calendar_immovables(self._session)  # noqa: SLF001
        try:
            preflight = self._orchestrator._ensure_refine_plan_state(  # noqa: SLF001
                self._session
            )
        except Exception as exc:
            logger.warning("Refine preflight failed: %s", exc, exc_info=True)
            gate = StageGateOutput(
                stage_id=TimeboxingStage.REFINE,
                ready=False,
                summary=[
                    "I couldn't prepare the editable Stage 4 plan yet.",
                    f"Preparation error: {str(exc) or type(exc).__name__}",
                ],
                missing=["A valid baseline plan and calendar snapshot"],
                question=(
                    "Please click Redo to retry Stage 4 preparation, or share a simpler "
                    "adjustment request."
                ),
                facts={},
            )
            self._session.stage_ready = False
            self._session.stage_missing = list(gate.missing or [])
            self._session.stage_question = gate.question
            self.last_gate = gate
            return Response(chat_message=StructuredMessage(source=self.name, content=gate))
        user_message = self._transition.stage_user_message.strip()
        if not user_message:
            user_message = (
                "Prepare the editable Stage 4 plan from the current draft. "
                "Preserve intent and ordering, and repair only what is needed "
                "for plan validity."
            )
        if preflight.has_plan_issues:
            issues = "\n".join(f"- {issue}" for issue in preflight.plan_issues)
            repair_instruction = (
                "Repair the current plan first so it passes validation, then apply the "
                "user refinement while preserving intent/order as much as possible.\n"
                f"Preflight validation issues:\n{issues}"
            )
            user_message = (
                f"{user_message}\n\n{repair_instruction}"
                if user_message
                else repair_instruction
            )
        user_message = self._orchestrator._compose_patcher_message(  # noqa: SLF001
            base_message=user_message,
            session=self._session,
            stage=TimeboxingStage.REFINE.value,
            extra={
                "preflight_plan_issues": list(preflight.plan_issues),
                "preflight_snapshot_issues": list(preflight.snapshot_issues),
                "quality_snapshot": self._orchestrator._quality_snapshot_for_prompt(  # noqa: SLF001
                    self._session
                ),
            },
        )
        if self._session.tb_plan is None:
            gate = StageGateOutput(
                stage_id=TimeboxingStage.REFINE,
                ready=False,
                summary=[
                    "I couldn't prepare the Stage 4 plan state.",
                    "Editable TBPlan is missing after preflight.",
                ],
                missing=["A TBPlan seed for Stage 4 patching"],
                question="Please click Redo to retry Stage 4 preparation.",
                facts={},
            )
            self._session.stage_ready = False
            self._session.stage_missing = list(gate.missing or [])
            self._session.stage_question = gate.question
            self.last_gate = gate
            return Response(chat_message=StructuredMessage(source=self.name, content=gate))

        try:
            execution = (
                await self._orchestrator._run_refine_tool_orchestration(  # noqa: SLF001
                    session=self._session,
                    patch_message=user_message,
                    user_message=self._transition.stage_user_message or user_message,
                )
            )
        except Exception as exc:
            logger.warning("Refine patch failed: %s", exc, exc_info=True)
            gate = StageGateOutput(
                stage_id=TimeboxingStage.REFINE,
                ready=False,
                summary=[
                    "I couldn't apply that refinement yet.",
                    f"Latest patch error: {str(exc) or type(exc).__name__}",
                ],
                missing=["A valid non-overlapping event sequence"],
                question=(
                    "Please adjust the request (or simplify it) and click Redo "
                    "to try again."
                ),
                facts={},
            )
            self._session.stage_ready = False
            self._session.stage_missing = list(gate.missing or [])
            self._session.stage_question = gate.question
            self.last_gate = gate
            return Response(chat_message=StructuredMessage(source=self.name, content=gate))
        gate = await self._orchestrator._run_timebox_summary(  # noqa: SLF001
            stage=TimeboxingStage.REFINE,
            timebox=self._session.timebox,
            session=self._session,
        )
        if execution.calendar.note:
            gate.summary.append(execution.calendar.note)
        if execution.fallback_patch_used:
            gate.summary.append(
                "Patch execution used fallback tool routing for this turn."
            )
        self._session.stage_ready = True
        self._session.stage_missing = []
        self._session.stage_question = gate.question
        self.last_gate = gate
        return Response(chat_message=StructuredMessage(source=self.name, content=gate))


class StageReviewCommitNode(_StageNodeBase):
    """Run the final review/commit stage."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        transition: TransitionNode,
    ) -> None:
        super().__init__(
            name="StageReviewCommitNode",
            orchestrator=orchestrator,
            session=session,
            transition=transition,
        )

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        if not self._session.timebox:
            self._session.last_response = (
                "Stage 5/5 (ReviewCommit)\nNo draft timebox yet. Go back to Skeleton."
            )
            self.last_gate = None
            return Response(
                chat_message=StructuredMessage(
                    source=self.name,
                    content=FlowSignal(kind="stage", note="missing-timebox"),
                )
            )
        gate = await self._orchestrator._run_review_commit(
            timebox=self._session.timebox
        )  # noqa: SLF001
        self._session.pending_submit = False
        self._session.stage_ready = True
        self._session.stage_missing = []
        self._session.stage_question = gate.question
        self.last_gate = gate
        return Response(chat_message=StructuredMessage(source=self.name, content=gate))


class PresenterNode(BaseChatAgent):
    """Build the Slack-facing message from the updated session state."""

    def __init__(
        self,
        *,
        orchestrator: "TimeboxingFlowAgent",
        session: "Session",
        stages: dict[TimeboxingStage, _StageNodeBase],
    ) -> None:
        super().__init__(name="PresenterNode", description="Timeboxing presenter")
        self._orchestrator = orchestrator
        self._session = session
        self._stages = stages

    @property
    def produced_message_types(self) -> Sequence[type[BaseChatMessage]]:
        return (TextMessage,)

    async def on_messages(
        self, messages: Sequence[BaseChatMessage], cancellation_token: CancellationToken
    ) -> Response:
        if self._session.last_response:
            content = self._session.last_response
            self._session.last_response = None
            return Response(chat_message=TextMessage(content=content, source=self.name))

        background_notes = self._orchestrator._collect_background_notes(
            self._session
        )  # noqa: SLF001
        stage_node = self._stages.get(self._session.stage)
        gate = getattr(stage_node, "last_gate", None) if stage_node else None
        if gate is None:
            fallback = (
                f"Stage {self._session.stage.value}: ready={self._session.stage_ready}"
            )
            return Response(
                chat_message=TextMessage(content=fallback, source=self.name)
            )

        text = self._orchestrator._format_stage_message(  # noqa: SLF001
            gate,
            background_notes=background_notes,
            constraints=self._session.active_constraints,
            immovables=self._session.frame_facts.get("immovables"),
        )
        if self._session.pending_submit:
            self._session.pending_presenter_blocks = (
                self._orchestrator._render_submit_prompt_blocks(
                    session=self._session,
                    text=text,
                )
            )
        return Response(chat_message=TextMessage(content=text, source=self.name))

    async def on_reset(self, cancellation_token: CancellationToken) -> None:
        return None
