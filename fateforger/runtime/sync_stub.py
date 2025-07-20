"""
Sync Stub - Hand-off mechanism for AutoGen Sequential Workflow (Ticket #1).

Provides the sync_plan_to_calendar() function that publishes PlanDiff messages
into AutoGen's runtime topic system, enabling deterministic multi-agent processing.
"""

from typing import Awaitable

from autogen_core import SingleThreadedAgentRuntime, TopicId

from ..contracts import CalendarOp, PlanDiff


class DiffMessage:
    """Message wrapper for PlanDiff to publish through AutoGen runtime topics."""

    def __init__(self, diff: PlanDiff):
        self.diff = diff


class OpMessage:
    """Message wrapper for CalendarOp to publish through AutoGen runtime topics."""

    def __init__(self, op: CalendarOp):
        self.op = op


def create_workflow_runtime() -> SingleThreadedAgentRuntime:
    """
    Create a configured AutoGen runtime for Sequential Workflow.

    Returns:
        Configured SingleThreadedAgentRuntime ready for agent registration
    """
    runtime = SingleThreadedAgentRuntime()
    return runtime


async def sync_plan_to_calendar(
    runtime: SingleThreadedAgentRuntime,
    plan: PlanDiff,
    *,
    planner_topic: str = "planner",
    queue_topic: str = "queue",
) -> None:
    """
    Publishes a PlanDiff into the workflow and blocks until runtime is idle.

    This is the main hand-off function for Ticket #1. It takes a PlanDiff
    (containing validated calendar operations) and publishes it as a DiffMessage
    to the AutoGen runtime topic system.

    Args:
        runtime: Configured SingleThreadedAgentRuntime with registered agents
        plan: Validated PlanDiff containing calendar operations
        planner_topic: Topic name for PlannerAgent subscription
        queue_topic: Topic name for TaskQueueAgent subscription

    Note:
        Assumes PlannerAgent is subscribed to `planner_topic`.
        No calendar side-effects yet—those belong to Tickets 3-4.
    """
    # Validate the plan before publishing
    plan.validate_all_operations()

    # Start the runtime
    runtime.start()

    # Publish the plan diff to the workflow
    await runtime.publish_message(
        DiffMessage(plan), TopicId(planner_topic, source="user")
    )

    # Block until all agents are idle (workflow complete)
    await runtime.stop_when_idle()
