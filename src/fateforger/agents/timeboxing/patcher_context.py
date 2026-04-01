"""Modular prompt context for the timebox patcher.

Three components:
- ErrorFeedback: structured error state for retry turns
- PatcherContext: Pydantic model that renders system_prompt() + user_message_text()
- PatchConversation: caller-owned multi-turn history
"""

from __future__ import annotations

import json

from autogen_agentchat.messages import TextMessage
from pydantic import BaseModel, ConfigDict

from .planning_policy import SHARED_PLANNING_POLICY_PROMPT
from .preferences import Constraint
from .tb_models import TBPlan
from .tb_ops import TBPatch
from .toon_views import constraints_rows
from fateforger.llm.toon import toon_encode


class ErrorFeedback(BaseModel):
    """Structured error state injected into the user turn on retry.

    Gives the LLM all three states so it can decide whether to patch the
    patch or rewrite from the original plan.
    """

    model_config = ConfigDict(extra="forbid")

    original_plan: TBPlan
    prior_patch: TBPatch
    partial_result: TBPlan | None = None
    error_message: str

    def render(self) -> str:
        """Render the error feedback block as a prompt string."""
        original_json = self.original_plan.model_dump_json(indent=2)
        patch_json = self.prior_patch.model_dump_json(indent=2)
        lines = [
            "Previous patch attempt failed.",
            f"Error: {self.error_message}",
            "",
            "Original TBPlan (before this call):",
            f"```json\n{original_json}\n```",
            "",
            "Prior patch attempt:",
            f"```json\n{patch_json}\n```",
        ]
        if self.partial_result is not None:
            partial_json = self.partial_result.model_dump_json(indent=2)
            lines += [
                "",
                "Partial result (state after ops applied up to the error):",
                f"```json\n{partial_json}\n```",
            ]
        lines += [
            "",
            "You may patch the prior attempt OR produce a fresh patch against the original plan.",
            "Return a corrected TBPatch that resolves the error while preserving user intent.",
        ]
        return "\n".join(lines)


_OP_REFERENCE = """\
Available patch operations (field ``op`` discriminator):
- ``ae`` (AddEvents): add one or more events. ``after`` = insert-after index (None → append).
- ``re`` (RemoveEvent): remove event at index ``i``.
- ``ue`` (UpdateEvent): merge partial changes onto event at index ``i``.
- ``me`` (MoveEvent): reorder event from index ``fr`` to ``to``.
- ``ra`` (ReplaceAll): replace entire event list (full rebuild only).

Time placement (field ``a`` on the event's ``p`` object):
- ``ap`` (AfterPrev): starts after previous event ends; needs ``dur`` (ISO 8601).
- ``bn`` (BeforeNext): ends when next event starts; needs ``dur``.
- ``fs`` (FixedStart): pinned start; needs ``st`` (HH:MM) and ``dur``.
- ``fw`` (FixedWindow): fixed start and end; needs ``st`` and ``et``.

Event types (``t``): M (meeting), C (commute), DW (deep work), SW (shallow work),
PR (plan & review), H (habit), R (regeneration), BU (buffer), BG (background).
"""

_ROLE_PREAMBLE = (
    "You are a timebox refinement assistant. You receive the current schedule "
    "as a TBPlan JSON, a user instruction, and optional constraints and memories.\n\n"
    "Your task: produce a single TBPatch JSON with the minimal set of typed domain "
    "operations that fulfils the user's request."
)

_OUTPUT_INSTRUCTION = (
    "Return ONLY the raw TBPatch JSON object — no markdown fences, no commentary."
)


class PatcherContext(BaseModel):
    """Prompt context for a single patcher invocation.

    Renders into two prompt roles:
    - ``system_prompt()``: cacheable per (stage_rules, schema version)
    - ``user_message_text()``: rebuilt per call with current plan + instruction + error state
    """

    model_config = ConfigDict(extra="forbid")

    plan: TBPlan
    user_message: str
    stage_rules: str
    constraints: list[Constraint] | None = None
    memories: list[str] | None = None
    error_feedback: ErrorFeedback | None = None

    def system_prompt(self) -> str:
        """Render the cacheable system prompt."""
        tbpatch_schema = json.dumps(TBPatch.model_json_schema(), indent=2)
        tbplan_schema = json.dumps(TBPlan.model_json_schema(), indent=2)
        sections = [
            _ROLE_PREAMBLE,
            "",
            "## TBPatch JSON Schema",
            f"```json\n{tbpatch_schema}\n```",
            "",
            "## TBPlan JSON Schema (input format)",
            f"```json\n{tbplan_schema}\n```",
            "",
            "## Operations Reference",
            _OP_REFERENCE,
            "",
            "## Stage Rules",
            self.stage_rules,
            "",
            "## Planning Policy",
            SHARED_PLANNING_POLICY_PROMPT,
            "",
            _OUTPUT_INSTRUCTION,
        ]
        return "\n".join(sections)

    def user_message_text(self) -> str:
        """Render the per-call user turn."""
        plan_json = self.plan.model_dump_json(indent=2)
        parts: list[str] = [
            f"Current TBPlan:\n```json\n{plan_json}\n```",
            "",
            f"User request: {self.user_message}",
        ]
        if self.constraints:
            toon = toon_encode(
                name="constraints",
                rows=constraints_rows(self.constraints),
                fields=["name", "necessity", "scope", "status", "source", "description"],
            )
            parts += ["", toon]
        if self.memories:
            parts += ["", "Memories / preferences:"]
            parts.extend(f"- {m}" for m in self.memories)
        if self.error_feedback is not None:
            parts += ["", self.error_feedback.render()]
        parts += ["", "Produce the TBPatch JSON with minimal ops to fulfil the request."]
        return "\n".join(parts)


class PatchConversation:
    """Caller-owned multi-turn conversation history for the patcher.

    Holds alternating user/assistant turns. The CLI holds one per session;
    TimeboxPatcher uses it so retries are follow-up turns, not fresh contexts.
    Call reset() after a ReplaceAll (ra) op — full rebuild = fresh context.
    """

    def __init__(self, *, max_turns: int = 20) -> None:
        self._max_turns = max_turns
        self.turns: list[dict[str, str]] = []

    def append_user(self, text: str) -> None:
        self.turns.append({"role": "user", "content": text})
        self._truncate()

    def append_assistant(self, text: str) -> None:
        self.turns.append({"role": "assistant", "content": text})
        self._truncate()

    def reset(self) -> None:
        """Clear history. Call after a ReplaceAll (ra) op."""
        self.turns = []

    def _truncate(self) -> None:
        max_messages = self._max_turns * 2
        if len(self.turns) > max_messages:
            self.turns = self.turns[-max_messages:]

    def to_autogen_messages(self) -> list[TextMessage]:
        """Convert turns to AutoGen TextMessage list for agent.on_messages()."""
        return [
            TextMessage(content=t["content"], source=t["role"])
            for t in self.turns
        ]


__all__ = ["ErrorFeedback", "PatcherContext", "PatchConversation"]
