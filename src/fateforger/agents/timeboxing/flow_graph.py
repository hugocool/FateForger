"""GraphFlow builder for the stage-gated TimeboxingFlowAgent.

This module encodes the stage machine declaratively using AutoGen GraphFlow + DiGraphBuilder.

Key properties:
- One Slack/user turn runs the graph until `PresenterNode` emits a single TextMessage, then stops.
- Stage routing is handled by conditional edges (no monolithic `if/elif` dispatch).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autogen_agentchat.conditions import TextMessageTermination
from autogen_agentchat.teams import DiGraphBuilder, GraphFlow

from fateforger.agents.timeboxing.stage_gating import TimeboxingStage
from fateforger.agents.timeboxing.nodes import (
    DecisionNode,
    PresenterNode,
    StageCaptureInputsNode,
    StageCollectConstraintsNode,
    StageRefineNode,
    StageReviewCommitNode,
    StageSkeletonNode,
    TransitionNode,
    TurnInitNode,
)

if TYPE_CHECKING:  # pragma: no cover
    from fateforger.agents.timeboxing.agent import Session, TimeboxingFlowAgent


def build_timeboxing_graphflow(
    *, orchestrator: "TimeboxingFlowAgent", session: "Session"
) -> GraphFlow:
    """Build a GraphFlow instance that runs exactly one stage per user turn."""
    builder = DiGraphBuilder()

    turn_init = TurnInitNode(orchestrator=orchestrator, session=session)
    decision = DecisionNode(orchestrator=orchestrator, session=session, turn_init=turn_init)
    transition = TransitionNode(orchestrator=orchestrator, session=session, turn_init=turn_init)

    stage_collect = StageCollectConstraintsNode(
        orchestrator=orchestrator, session=session, transition=transition
    )
    stage_capture = StageCaptureInputsNode(
        orchestrator=orchestrator, session=session, transition=transition
    )
    stage_skeleton = StageSkeletonNode(
        orchestrator=orchestrator, session=session, transition=transition
    )
    stage_refine = StageRefineNode(
        orchestrator=orchestrator, session=session, transition=transition
    )
    stage_review = StageReviewCommitNode(
        orchestrator=orchestrator, session=session, transition=transition
    )

    stages = {
        TimeboxingStage.COLLECT_CONSTRAINTS: stage_collect,
        TimeboxingStage.CAPTURE_INPUTS: stage_capture,
        TimeboxingStage.SKELETON: stage_skeleton,
        TimeboxingStage.REFINE: stage_refine,
        TimeboxingStage.REVIEW_COMMIT: stage_review,
    }
    presenter = PresenterNode(orchestrator=orchestrator, session=session, stages=stages)

    for agent in (
        turn_init,
        decision,
        transition,
        stage_collect,
        stage_capture,
        stage_skeleton,
        stage_refine,
        stage_review,
        presenter,
    ):
        builder.add_node(agent)

    builder.add_edge(turn_init, decision)
    builder.add_edge(decision, transition)

    # If the decision/transition completed the session, skip stage execution and present.
    builder.add_edge(
        transition,
        presenter,
        condition=lambda _m: bool(session.completed or session.skip_stage_execution),
        activation_condition="any",
    )

    builder.add_edge(
        transition,
        stage_collect,
        condition=lambda _m: (
            session.stage == TimeboxingStage.COLLECT_CONSTRAINTS
            and not session.completed
            and not session.skip_stage_execution
        ),
    )
    builder.add_edge(
        transition,
        stage_capture,
        condition=lambda _m: (
            session.stage == TimeboxingStage.CAPTURE_INPUTS
            and not session.completed
            and not session.skip_stage_execution
        ),
    )
    builder.add_edge(
        transition,
        stage_skeleton,
        condition=lambda _m: (
            session.stage == TimeboxingStage.SKELETON
            and not session.completed
            and not session.skip_stage_execution
        ),
    )
    builder.add_edge(
        transition,
        stage_refine,
        condition=lambda _m: (
            session.stage == TimeboxingStage.REFINE
            and not session.completed
            and not session.skip_stage_execution
        ),
    )
    builder.add_edge(
        transition,
        stage_review,
        condition=lambda _m: (
            session.stage == TimeboxingStage.REVIEW_COMMIT
            and not session.completed
            and not session.skip_stage_execution
        ),
    )

    builder.add_edge(stage_collect, presenter, activation_condition="any")
    builder.add_edge(stage_capture, presenter, activation_condition="any")
    builder.add_edge(stage_skeleton, presenter, activation_condition="any")
    builder.add_edge(stage_refine, presenter, activation_condition="any")
    builder.add_edge(stage_review, presenter, activation_condition="any")

    builder.set_entry_point(turn_init)
    graph = builder.build()

    return GraphFlow(
        participants=builder.get_participants(),
        graph=graph,
        termination_condition=TextMessageTermination(source=presenter.name),
    )


__all__ = ["build_timeboxing_graphflow"]
