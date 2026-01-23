from __future__ import annotations

import json
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.tools import AgentTool
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from pydantic import BaseModel, Field
from pydantic import ValidationError

from fateforger.debug.diag import with_timeout
from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS
from fateforger.agents.timeboxing.pydantic_parsing import parse_chat_content


class ConstraintWindow(BaseModel):
    kind: str = Field(description="prefer|avoid")
    start_time_local: str = Field(description="HH:MM")
    end_time_local: str = Field(description="HH:MM")


class ScalarParams(BaseModel):
    duration_min: Optional[int] = None
    duration_max: Optional[int] = None
    contiguity: Optional[str] = Field(default=None, description="prefer|require|irrelevant")


class ConstraintPayload(BaseModel):
    rule_kind: str
    scalar_params: ScalarParams = Field(default_factory=ScalarParams)
    windows: List[ConstraintWindow] = Field(default_factory=list)


class ConstraintApplicability(BaseModel):
    start_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    days_of_week: Optional[List[str]] = None  # [MO,TU,...]
    timezone: Optional[str] = None
    recurrence: Optional[str] = None


class ConstraintLifecycle(BaseModel):
    uid: Optional[str] = None
    supersedes_uids: List[str] = Field(default_factory=list)
    ttl_days: Optional[int] = None


class ExtractedConstraintRecord(BaseModel):
    name: str
    description: str
    necessity: str = Field(description="must|should")
    status: str = Field(description="proposed|locked")
    source: str = Field(description="user|calendar|system|feedback")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scope: str = Field(description="session|profile|datespan")
    applicability: ConstraintApplicability = Field(default_factory=ConstraintApplicability)
    lifecycle: ConstraintLifecycle = Field(default_factory=ConstraintLifecycle)
    payload: ConstraintPayload

    applies_stages: List[str] = Field(default_factory=list)
    applies_event_types: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)


class ConstraintExtractionOutput(BaseModel):
    constraint_record: ExtractedConstraintRecord
    clarifying_question: Optional[str] = None
    notes_for_page_body: Optional[str] = None


class ConstraintHandoff(BaseModel):
    planned_date: date
    timezone: str
    stage_id: Optional[str] = None
    user_utterance: str
    triggering_suggestion: Optional[str] = None
    impacted_event_types: List[str] = Field(default_factory=list)
    suggested_tags: List[str] = Field(default_factory=list)
    session_id: Optional[str] = None
    decision_scope: Optional[str] = None


CONSTRAINT_EXTRACTOR_SYSTEM_PROMPT = """
Role: Preference Constraint Librarian

Goal:
Convert a user's natural-language preference/correction into ONE Notion-compatible constraint record
that a timeboxing agent can apply in future sessions without chat history.

Input:
You receive a single JSON payload (ConstraintHandoff) that includes:
- planned_date, timezone, stage_id
- user_utterance (verbatim)
- triggering_suggestion (optional)
- impacted_event_types, suggested_tags, decision_scope (optional)

Output:
Return ONLY a JSON object matching the provided schema (ConstraintExtractionOutput).
Do not include extra keys or prose.

Operational rules:
- Prefer structured properties (fields) over page-body prose.
- Keep records MECE: governance vs applicability vs routing vs rule payload vs lifecycle.
- Default `status=proposed` unless the user explicitly confirms/locks it.
- Default `scope=profile` for "in general / usually / I prefer" statements; otherwise `session` or `datespan`.
- If ambiguity remains, choose conservative defaults and add a single `clarifying_question`.

Tools:
- constraint_query_types(stage, event_types)
- constraint_query_constraints(filters, type_ids, tags, sort, limit)
- constraint_upsert_constraint(record, event)
- constraint_log_event(event)

Allowed enums:
- necessity: must|should
- status: proposed|locked
- source: user|calendar|system|feedback
- scope: session|profile|datespan
- payload.rule_kind: prefer_window|avoid_window|fixed_bedtime|min_sleep|buffer|sequencing|capacity
- payload.scalar_params.contiguity: prefer|require|irrelevant
- applicability.days_of_week: MO|TU|WE|TH|FR|SA|SU
- applies_stages: CollectConstraints|CaptureInputs|Skeleton|Refine|ReviewCommit
- applies_event_types: M|C|DW|SW|H|R|BU|BG|PR
- windows[].kind: prefer|avoid

Record guidelines:
- `name`: short label, human scannable.
- `description`: one sentence operational meaning.
- `topics`: small list of stable routing tags (create if new); prefer concise nouns.
- `uid`: leave null if unsure; the caller will derive an idempotency key.

Procedure:
1) If needed, call constraint_query_types to shortlist types for the stage/event_types.
2) Call constraint_query_constraints to check for duplicates/supersedes.
3) Call constraint_upsert_constraint with the constraint_record, including an event payload when possible.
4) If not using upsert event payload, call constraint_log_event separately.
""".strip()


def build_constraint_extractor_agent(
    *, model_client: OpenAIChatCompletionClient, tools: Optional[List[Any]] = None
) -> AssistantAgent:
    return AssistantAgent(
        name="ConstraintExtractorAgent",
        model_client=model_client,
        tools=tools,
        output_content_type=ConstraintExtractionOutput,
        system_message=CONSTRAINT_EXTRACTOR_SYSTEM_PROMPT,
        reflect_on_tool_use=False,
        max_tool_iterations=3,
    )


class NotionConstraintExtractor:
    """LLM-powered extractor that calls MCP tools to persist durable constraints."""

    def __init__(
        self,
        *,
        model_client: OpenAIChatCompletionClient,
        tools: List[Any],
    ) -> None:
        self._agent = build_constraint_extractor_agent(
            model_client=model_client, tools=tools
        )
        self._agent_tool = AgentTool(agent=self._agent, return_value_as_last_message=True)

    async def extract_and_upsert(
        self,
        handoff: ConstraintHandoff,
    ) -> ConstraintExtractionOutput | None:
        """Extract a durable constraint record from a user utterance and persist it via MCP tools."""
        if not handoff.user_utterance.strip():
            return None

        payload = handoff.model_dump(mode="json")
        task = json.dumps(payload, ensure_ascii=False)
        response = await with_timeout(
            "notion:constraint-extract",
            self._agent_tool.run_json({"task": task}, CancellationToken()),
            timeout_s=TIMEBOXING_TIMEOUTS.notion_extract_s,
        )
        try:
            return parse_chat_content(ConstraintExtractionOutput, response)
        except ValidationError:
            return None

    async def extract_and_upsert_constraint(
        self,
        *,
        planned_date: str,
        timezone: str,
        stage_id: Optional[str],
        user_utterance: str,
        triggering_suggestion: Optional[str] = None,
        impacted_event_types: Optional[List[str]] = None,
        suggested_tags: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        decision_scope: Optional[str] = None,
    ) -> Dict[str, Any] | None:
        """Tool-facing wrapper for timeboxing agent handoffs."""

        if not user_utterance.strip():
            return None
        # TODO(refactor): Validate planned_date via a Pydantic schema.
        try:
            parsed_date = date.fromisoformat(planned_date)
        except Exception:
            parsed_date = datetime.utcnow().date()
        handoff = ConstraintHandoff(
            planned_date=parsed_date,
            timezone=timezone,
            stage_id=stage_id,
            user_utterance=user_utterance,
            triggering_suggestion=triggering_suggestion,
            impacted_event_types=impacted_event_types or [],
            suggested_tags=suggested_tags or [],
            session_id=session_id,
            decision_scope=decision_scope,
        )
        extracted = await self.extract_and_upsert(handoff)
        return extracted.model_dump() if extracted else None


__all__ = [
    "CONSTRAINT_EXTRACTOR_SYSTEM_PROMPT",
    "ConstraintExtractionOutput",
    "ConstraintHandoff",
    "NotionConstraintExtractor",
    "build_constraint_extractor_agent",
]
