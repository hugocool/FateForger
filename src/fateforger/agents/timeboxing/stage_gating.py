"""Stage-gated timeboxing models and prompts (no string matching required)."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TimeboxingStage(str, Enum):
    COLLECT_CONSTRAINTS = "CollectConstraints"
    CAPTURE_INPUTS = "CaptureInputs"
    SKELETON = "Skeleton"
    REFINE = "Refine"
    REVIEW_COMMIT = "ReviewCommit"


StageAction = Literal["provide_info", "proceed", "back", "redo", "cancel", "assist"]


class StageGateOutput(BaseModel):
    stage_id: TimeboxingStage
    ready: bool
    summary: List[str] = Field(default_factory=list, description="1-4 short bullets")
    missing: List[str] = Field(
        default_factory=list, description="missing items blocking readiness"
    )
    question: Optional[str] = Field(default=None, description="single concise question")
    facts: Dict[str, Any] = Field(
        default_factory=dict, description="canonical structured facts for this stage"
    )


class StageDecision(BaseModel):
    action: StageAction
    target_stage: Optional[TimeboxingStage] = None
    note: Optional[str] = None


def format_stage_prompt_context(
    *, stage: TimeboxingStage, facts: Dict[str, Any]
) -> str:
    payload = json.dumps(facts or {}, ensure_ascii=False, indent=2, sort_keys=True)
    return f"Current stage: {stage.value}\nKnown facts (JSON):\n{payload}\n"


COLLECT_CONSTRAINTS_PROMPT = """
Stage: CollectConstraints

Goal
- Build the day frame: work window, timezone, sleep target, immovable events, commutes, and any hard commitments.
- Update/merge the provided Known facts JSON with any new details in the user message.
- External data fetches are handled by the coordinator in the background (you should not request tools).

Output
- Return STRICT JSON matching StageGateOutput.

Rules
- If immovables are missing from Known facts and a date/timezone is set, call it out in missing/question so the coordinator can fetch it.
- If the user asks about their calendar, tasks, or other related info, note the request in summary/question and keep going.
- Be conservative: if a fact is uncertain, omit it from facts and add it to missing/question.
- Always keep the user oriented: summary should include what you assumed/locked so far.
- ready=true only when the frame is sufficient to draft a skeleton timebox.
- Keep the conversation flowing naturally; don't be overly rigid about stage structure.

facts keys (preferred)
- timezone: string
- date: YYYY-MM-DD (if known)
- work_window: {start: "HH:MM", end: "HH:MM"}
- sleep_target: {start: "HH:MM"|null, end: "HH:MM"|null, hours: number|null}
- immovables: [{title: string, start: "HH:MM", end: "HH:MM"}]
- commutes: [{label: string, duration_min: int}]
- habits: [{name: string, duration_min: int, preferred_window: string|null}]

missing (typical)
- timezone, work window, key immovable events, sleep target
""".strip()


CAPTURE_INPUTS_PROMPT = """
Stage: CaptureInputs

Goal
- Capture tasks + durations, DailyOneThing, and any must-do items for the day.
- Update/merge the provided Known facts JSON with any new details in the user message.

Output
- Return STRICT JSON matching StageGateOutput.

facts keys (preferred)
- daily_one_thing: {title: string, duration_min: int|null}
- tasks: [{title: string, duration_min: int|null, due: "YYYY-MM-DD"|null, importance: "high|med|low"|null}]
- goals: [string]

Rules
- ready=true only when you have enough to draft a skeleton (DailyOneThing or a task list with rough durations).
- If the user mentions wanting to check tasks, calendar, or other sources, note it in summary/question (the coordinator will fetch in background).
- Keep the conversation natural; guide them towards providing what's needed but don't be rigid.
""".strip()


DECISION_PROMPT = """
You are a stage-gating controller for a timeboxing flow.

Input
- You will receive:
  - current_stage (string)
  - stage_ready (boolean)
  - stage_missing (list)
  - stage_question (string|null)
  - user_message (string)

Task
- Decide what the user wants next without relying on fixed phrases.
- Output STRICT JSON matching StageDecision.

Decision rules
- If the user supplies new details for the current stage, use action="provide_info".
- If the user wants to move forward, use action="proceed".
- If the user asks to revisit earlier stages, use action="back" and set target_stage.
- If the user asks to redo the current stage, use action="redo".
- If the user wants to stop, use action="cancel".
- If the user asks an adjacent question (e.g. "what's on my calendar?", "show my tasks", "what did I plan yesterday?"), use action="assist" with a note describing what they need. This lets you help them with info that feeds back into timeboxing.

Constraints
- Never output prose.
- Prefer keeping the user on track; use assist sparingly for genuinely helpful adjacent queries.
""".strip()


TIMEBOX_SUMMARY_PROMPT = """
You are summarizing a timebox draft for the user.

Input
- You will receive JSON with:
  - stage_id (string)
  - timebox (a JSON object)

Task
- Output STRICT JSON matching StageGateOutput.
- stage_id must match the input stage_id.
- ready should be true (the draft exists); use missing/question only if the timebox is invalid or incomplete.
- summary should be 2-4 short bullets describing the main blocks and the intent.
- question should ask what the user wants to change, or whether to proceed to the next stage.
""".strip()


REVIEW_COMMIT_PROMPT = """
Stage: ReviewCommit

Goal
- Provide a concise final review of the timebox and ask the user to approve finalization.

Input
- You will receive JSON with:
  - timebox (a JSON object)

Output
- Return STRICT JSON matching StageGateOutput with stage_id="ReviewCommit" and ready=true.
- summary should be 2-4 bullets plus (optionally) a single risk/edge case.
- question should ask whether to finalize or go back to refine.
""".strip()


__all__ = [
    "CAPTURE_INPUTS_PROMPT",
    "COLLECT_CONSTRAINTS_PROMPT",
    "DECISION_PROMPT",
    "StageDecision",
    "StageGateOutput",
    "TimeboxingStage",
    "REVIEW_COMMIT_PROMPT",
    "TIMEBOX_SUMMARY_PROMPT",
    "format_stage_prompt_context",
]
