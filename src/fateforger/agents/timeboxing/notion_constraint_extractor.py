# TODO(deprecate): This entire module is dead code. The Notion-MCP extraction path
# (_ensure_constraint_mcp_tools / NotionConstraintExtractor) is never reached at
# runtime. The live write path goes through _upsert_constraints_to_durable_store →
# _build_durable_constraint_record → DurableConstraintStore. Do not import
# this module from new code. Remove once the agent.py dead-code block is cleaned up.
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.tools import AgentTool
from autogen_core import CancellationToken
from autogen_ext.models.openai import OpenAIChatCompletionClient
from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.constants import TIMEBOXING_TIMEOUTS
from fateforger.debug.diag import with_timeout


class ConstraintWindow(BaseModel):
    kind: str = Field(description="prefer|avoid")
    start_time_local: str = Field(description="HH:MM")
    end_time_local: str = Field(description="HH:MM")


class ScalarParams(BaseModel):
    duration_min: Optional[int] = None
    duration_max: Optional[int] = None
    contiguity: Optional[str] = Field(
        default=None, description="prefer|require|irrelevant"
    )


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


class AspectClassificationPayload(BaseModel):
    """Structured semantic metadata the LLM assigns to a constraint at extraction time.

    Stored as ``constraint_record.aspect_classification`` in the durable record and
    forwarded into ``Constraint.hints["aspect_classification"]`` when the record is
    loaded back from the constraint-memory store. Agent code that needs to know the
    scheduling domain of a constraint MUST read this field instead of scanning names
    or descriptions with keywords or regex.
    """

    aspect_id: str = Field(
        description="Stable lower_snake_case slug for this planning aspect, e.g. sleep_window, gym_training"
    )
    aspect_label: str = Field(
        description="Human-readable display name, e.g. Sleep window"
    )
    category: str = Field(
        description="Open category string. Well-known values: sleep|work|exercise|family|pet|social|transport|hobby|nutrition|health|learning"
    )
    frame_slot: Optional[str] = Field(
        default=None,
        description="Legacy slot: sleep_target or work_window. Null otherwise.",
    )
    is_startup_prefetch: bool = Field(
        default=False,
        description="True when this anchors the day and must be loaded before the user speaks (sleep schedule, work window)",
    )
    schedule_start: Optional[str] = Field(default=None, description="HH:MM if stated")
    schedule_end: Optional[str] = Field(default=None, description="HH:MM if stated")
    duration_min: Optional[int] = Field(
        default=None, description="Duration in minutes if stated"
    )
    is_conditional: bool = Field(
        default=False,
        description="True when only applies given another aspect being present/absent",
    )
    conditional_on_absent: List[str] = Field(
        default_factory=list,
        description="aspect_id values that must be absent for this to apply",
    )
    conditional_on_present: List[str] = Field(
        default_factory=list,
        description="aspect_id values that must be present for this to apply",
    )
    excludes_aspect_ids: List[str] = Field(
        default_factory=list,
        description="aspect_id values excluded when this aspect is confirmed",
    )


class ExtractedConstraintRecord(BaseModel):
    name: str
    description: str
    necessity: str = Field(description="must|should")
    status: str = Field(description="proposed|locked")
    source: str = Field(description="user|calendar|system|feedback")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scope: str = Field(description="session|profile|datespan")
    applicability: ConstraintApplicability = Field(
        default_factory=ConstraintApplicability
    )
    lifecycle: ConstraintLifecycle = Field(default_factory=ConstraintLifecycle)
    payload: ConstraintPayload
    aspect_classification: Optional[AspectClassificationPayload] = Field(
        default=None,
        description="Semantic aspect metadata required by agent code. Must be populated for every constraint.",
    )

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

Aspect classification (REQUIRED — populate aspect_classification for every record)
The `aspect_classification` field tells the agent which planning domain this constraint belongs to
without any keyword or regex scanning. You MUST always populate it. Fields:

- aspect_id (string): stable lower_snake_case slug for the planning aspect. Reuse the same slug
  for the same life-area across turns, e.g. "sleep_window", "work_window", "gym_training",
  "field_hockey", "morning_commute", "dog_walk", "school_run".
- aspect_label (string): human-readable display name.
- category (string): use one of the well-known values when applicable:
    sleep | work | exercise | family | pet | social | transport | hobby | nutrition | health | learning
  Any other lowercase slug is valid for domains not in this list.
- frame_slot (string or null): ONLY set when the constraint maps to a legacy planning slot.
  Valid values: "sleep_target" (sleep-window constraints) or "work_window" (work-hours constraints).
  Null for everything else.
- is_startup_prefetch (bool): true when this constraint anchors the whole day and must be fetched
  before the user speaks — sleep schedule, work window, or primary transport commitment.
  False for optional activities and session-specific overrides.
- schedule_start (string or null): HH:MM extracted from the utterance, if present.
- schedule_end (string or null): HH:MM extracted from the utterance, if present.
- duration_min (integer or null): duration in minutes if stated.
- is_conditional (bool): true when the constraint only applies given another aspect being present
  or absent.
- conditional_on_absent (list[string]): aspect_id values that must be absent for this to apply.
- conditional_on_present (list[string]): aspect_id values that must be present.
- excludes_aspect_ids (list[string]): aspect_id values that should not be scheduled when this
  aspect is confirmed (e.g. long commute excludes the gym).

Examples:
  Sleep:   aspect_id=sleep_window, category=sleep, frame_slot=sleep_target, is_startup_prefetch=true
  Work:    aspect_id=work_window,  category=work,  frame_slot=work_window,  is_startup_prefetch=true
  Gym:     aspect_id=gym_training, category=exercise, frame_slot=null, is_startup_prefetch=false
  Commute: aspect_id=morning_commute, category=transport, frame_slot=null, is_startup_prefetch=true
  Dog:     aspect_id=dog_walk, category=pet, frame_slot=null, is_startup_prefetch=false

Procedure:
1) If needed, call constraint_query_types to shortlist types for the stage/event_types.
2) Call constraint_query_constraints to check for duplicates/supersedes.
3) Call constraint_upsert_constraint with the constraint_record, including an event payload when possible.
4) If not using upsert event payload, call constraint_log_event separately.
""".strip()


def build_constraint_extractor_agent(
    *, model_client: OpenAIChatCompletionClient, tools: Optional[List[Any]] = None
) -> AssistantAgent:
    """Build the durable-constraint extractor agent.

    Structured `output_content_type` parsing is intentionally disabled here because
    MCP tool adapters are non-strict by default; OpenAI parse mode rejects non-strict
    tools before execution.
    """
    return AssistantAgent(
        name="ConstraintExtractorAgent",
        model_client=model_client,
        tools=tools,
        system_message=CONSTRAINT_EXTRACTOR_SYSTEM_PROMPT,
        reflect_on_tool_use=False,
        max_tool_iterations=3,
    )


def _strip_markdown_json_fence(payload: str) -> str:
    """Return JSON payload text without optional markdown code fences."""
    # TODO(refactor,typed-contracts): Remove markdown-fence normalization by
    # requiring strict structured output from extractor responses.
    text = payload.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_constraint_extraction_response(response: Any) -> ConstraintExtractionOutput:
    """Parse an AutoGen response into `ConstraintExtractionOutput`.

    The extractor prompt enforces JSON output, so this parser accepts either:
    - an already validated `ConstraintExtractionOutput`,
    - a dict payload,
    - raw JSON text (optionally in markdown fences).

    # TODO(refactor,typed-contracts): Remove raw string parsing path and depend
    # only on typed model payloads.
    """
    content = getattr(getattr(response, "chat_message", None), "content", None)
    if isinstance(content, ConstraintExtractionOutput):
        return content
    if isinstance(content, str):
        cleaned = _strip_markdown_json_fence(content)
        return ConstraintExtractionOutput.model_validate_json(cleaned)
    return ConstraintExtractionOutput.model_validate(content)


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
        self._agent_tool = AgentTool(
            agent=self._agent, return_value_as_last_message=True
        )

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
            return _parse_constraint_extraction_response(response)
        except Exception as exc:
            raise RuntimeError(
                "Constraint extractor returned invalid output payload"
            ) from exc

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
    "AspectClassificationPayload",
    "CONSTRAINT_EXTRACTOR_SYSTEM_PROMPT",
    "ConstraintExtractionOutput",
    "ConstraintHandoff",
    "NotionConstraintExtractor",
    "build_constraint_extractor_agent",
]
