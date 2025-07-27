"""
Sync Stub - Hand-off function for AutoGen Sequential Workflow (Ticket #1)

Provides sync_plan_to_calendar() function that publishes PlanDiff into AutoGen runtime
and manages the Sequential Workflow orchestration between agents.

This is the main entry point for triggering the multi-agent calendar pipeline.
"""

from __future__ import annotations

from autogen_core import SingleThreadedAgentRuntime, TopicId

from .schedular.models import CalendarOp, PlanDiff


class DiffMessage:
    """
    Message wrapper for PlanDiff in AutoGen runtime topics.

    Sequential Workflow pattern requires explicit message types for topic routing.
    """

    def __init__(self, diff: PlanDiff):
        self.diff = diff

    def __repr__(self) -> str:
        return f"DiffMessage({self.diff})"


class OpMessage:
    """
    Message wrapper for individual CalendarOp in AutoGen runtime topics.

    Used by TaskQueueAgent to process operations one-by-one through the pipeline.
    """

    def __init__(self, op: CalendarOp):
        self.op = op

    def __repr__(self) -> str:
        return f"OpMessage({self.op.op.value}: {self.op.event_id or 'new'})"


class StatusMessage:
    """
    Message wrapper for operation status in AutoGen runtime topics.

    Used for verification loop and result collection.
    """

    def __init__(self, status: str, operation_id: str, details: str = ""):
        self.status = status  # "success" | "error" | "pending"
        self.operation_id = operation_id
        self.details = details

    def __repr__(self) -> str:
        return f"StatusMessage({self.status}: {self.operation_id})"


async def sync_plan_to_calendar(
    runtime: SingleThreadedAgentRuntime,
    plan: PlanDiff,
    *,
    planner_topic: str = "planner",
    queue_topic: str = "queue",
    cal_topic: str = "calendar",
    verify_topic: str = "verify",
) -> None:
    """
    Publishes a PlanDiff into the Sequential Workflow and blocks until runtime is idle.

    This is the main orchestration function for the multi-agent calendar pipeline.
    Assumes the following agents are subscribed to their respective topics:
    - PlannerAgent subscribed to `planner_topic`
    - TaskQueueAgent subscribed to `queue_topic`
    - CalAgent subscribed to `cal_topic`
    - ResultCollector subscribed to `verify_topic`

    Args:
        runtime: AutoGen SingleThreadedAgentRuntime instance
        plan: PlanDiff containing calendar operations to execute
        planner_topic: Topic name for PlannerAgent (default: "planner")
        queue_topic: Topic name for TaskQueueAgent (default: "queue")
        cal_topic: Topic name for CalAgent (default: "calendar")
        verify_topic: Topic name for ResultCollector (default: "verify")

    Returns:
        None - Function completes when all operations are processed

    Raises:
        RuntimeError: If runtime fails to start or agents are not properly configured
        ValueError: If plan validation fails

    Example:
        ```python
        from autogen_core import SingleThreadedAgentRuntime
        from fateforger.agents.calendar_contract import PlanDiff, CalendarOp, OpType
        from fateforger.agents.sync_stub import sync_plan_to_calendar

        # Create a simple plan
        plan = PlanDiff(operations=[
            CalendarOp(op=OpType.CREATE, event=some_calendar_event)
        ])

        # Execute through Sequential Workflow
        runtime = SingleThreadedAgentRuntime()
        await sync_plan_to_calendar(runtime, plan)
        ```
    """
    # Validate the plan before processing
    try:
        plan.validate_all_operations()
    except ValueError as e:
        raise ValueError(f"Plan validation failed: {e}") from e

    if not plan.operations:
        # Empty plan - nothing to do
        return

    try:
        # Start the runtime for message processing
        runtime.start()

        # Publish the PlanDiff to trigger the Sequential Workflow
        # PlannerAgent should be subscribed to planner_topic to receive this
        await runtime.publish_message(
            DiffMessage(plan), TopicId(planner_topic, source="user")
        )

        # Block until all agents complete their processing
        # Sequential Workflow will route messages through:
        # Planner → Queue → CalAgent → Verification → Complete
        await runtime.stop_when_idle()

    except Exception as e:
        # Ensure runtime cleanup on errors
        try:
            await runtime.stop()
        except Exception:
            pass  # Ignore cleanup errors
        raise RuntimeError(f"Sequential Workflow execution failed: {e}") from e


async def create_workflow_runtime() -> SingleThreadedAgentRuntime:
    """
    Factory function to create a configured AutoGen runtime for calendar workflow.

    This is a convenience function for setting up the runtime with proper configuration.
    Agents still need to be registered separately via runtime.register().

    Returns:
        Configured SingleThreadedAgentRuntime ready for agent registration

    Example:
        ```python
        runtime = await create_workflow_runtime()

        # Register your agents
        planner_agent = PlannerAgent("planner")  # Ticket #2
        queue_agent = TaskQueueAgent("queue")     # Ticket #3
        cal_agent = CalAgent("calendar")          # Ticket #4

        runtime.register("planner", planner_agent)
        runtime.register("queue", queue_agent)
        runtime.register("calendar", cal_agent)

        # Execute workflow
        await sync_plan_to_calendar(runtime, plan)
        ```
    """
    return SingleThreadedAgentRuntime()


# Utility functions for message creation
def create_diff_message(plan: PlanDiff) -> DiffMessage:
    """Create a DiffMessage for runtime publishing."""
    return DiffMessage(plan)


def create_op_message(operation: CalendarOp) -> OpMessage:
    """Create an OpMessage for individual operation processing."""
    return OpMessage(operation)


def create_status_message(status: str, op_id: str, details: str = "") -> StatusMessage:
    """Create a StatusMessage for result tracking."""
    return StatusMessage(status, op_id, details)
