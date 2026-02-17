"""GraphFlow builder for the timeboxing workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.conditions import MaxMessageTermination
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.teams import DiGraphBuilder, GraphFlow
from autogen_ext.models.openai import OpenAIChatCompletionClient
from pydantic import BaseModel

from fateforger.llm import assert_strict_tools_for_structured_output

from .timebox import Timebox
from .prompts import (
    ASSESS_PROMPT,
    APPROVAL_PROMPT,
    DONE_PROMPT,
    DRAFT_PROMPT,
    HYDRATE_PROMPT,
    REVIEW_PROMPT,
    SUBMIT_PROMPT,
    TIMEBOXING_SYSTEM_PROMPT,
)


@dataclass
class PlanningState:
    """In-memory state shared across the graph flow."""

    thread_ts: str
    channel_id: str
    user_id: str
    user_input: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timebox_json: Dict[str, Any] | None = None
    approval: bool | None = None


class ApprovalDecision(BaseModel):
    approved: bool
    message: str


class SubmitDecision(BaseModel):
    submitted: bool
    failed: bool
    message: str


def _approved(message) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, ApprovalDecision):
        return bool(content.approved)
    if isinstance(content, dict):
        return bool(content.get("approved") is True)
    return False


def _declined(message) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, ApprovalDecision):
        return not content.approved
    if isinstance(content, dict):
        approved = content.get("approved")
        return approved is False
    return False


def _submitted(message) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, SubmitDecision):
        return bool(content.submitted) and not bool(content.failed)
    if isinstance(content, dict):
        return bool(content.get("submitted") is True) and not bool(content.get("failed") is True)
    return False


def _submit_failed(message) -> bool:
    content = getattr(message, "content", None)
    if isinstance(content, SubmitDecision):
        return bool(content.failed)
    if isinstance(content, dict):
        return bool(content.get("failed") is True)
    return False


def _build_node(
    name: str,
    prompt: str,
    model_client: OpenAIChatCompletionClient,
    *,
    output_content_type: Optional[Type] = None,
    tools: Optional[List] = None,
) -> AssistantAgent:
    """Create an AssistantAgent configured for a specific phase."""
    assert_strict_tools_for_structured_output(
        tools=tools,
        output_content_type=output_content_type,
        agent_name=name,
    )

    return AssistantAgent(
        name=name,
        system_message=f"{TIMEBOXING_SYSTEM_PROMPT}\n\n{prompt}",
        model_client=model_client,
        tools=tools,
        output_content_type=output_content_type,
        reflect_on_tool_use=False,
        max_tool_iterations=1,
    )


def build_timeboxing_flow(
    model_client: OpenAIChatCompletionClient, *, tools: Optional[List] = None
) -> GraphFlow:
    """Construct the directed graph coordinating the timeboxing workflow."""

    builder = DiGraphBuilder()

    hydrate = _build_node("HydrateContext", HYDRATE_PROMPT, model_client, tools=tools)
    assess = _build_node("AssessReadiness", ASSESS_PROMPT, model_client, tools=tools)
    draft = _build_node(
        "DraftTimebox",
        DRAFT_PROMPT,
        model_client,
        output_content_type=Timebox,
        tools=tools,
    )
    review = _build_node("ReviewWithUser", REVIEW_PROMPT, model_client, tools=tools)
    approve = _build_node(
        "ApprovalGate",
        APPROVAL_PROMPT,
        model_client,
        output_content_type=ApprovalDecision,
        tools=tools,
    )
    submit = _build_node(
        "SubmitToCalendar",
        SUBMIT_PROMPT,
        model_client,
        output_content_type=SubmitDecision,
        tools=tools,
    )
    done = _build_node("Done", DONE_PROMPT, model_client, tools=tools)

    for agent in (hydrate, assess, draft, review, approve, submit, done):
        builder.add_node(agent)

    builder.add_edge(hydrate, assess)
    builder.add_edge(assess, draft)
    builder.add_edge(draft, review)
    builder.add_edge(review, approve)
    builder.add_edge(approve, submit, condition=_approved)
    builder.add_edge(approve, done, condition=_declined)
    builder.add_edge(submit, done, condition=_submitted)
    builder.add_edge(submit, review, condition=_submit_failed)

    builder.set_entry_point(hydrate)
    graph = builder.build()

    return GraphFlow(
        participants=builder.get_participants(),
        graph=graph,
        termination_condition=MaxMessageTermination(20),
    )


__all__ = ["PlanningState", "build_timeboxing_flow"]
