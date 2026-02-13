"""Typed domain-op patching for timebox plans.

Uses an AutoGen ``AssistantAgent`` with the ``TBPatch`` JSON schema
injected into the system prompt (schema-in-prompt approach).  The LLM
produces a raw JSON ``TBPatch`` response, which is parsed and applied
deterministically via ``apply_tb_ops()``.

Note: ``output_content_type=TBPatch`` is intentionally NOT used because
OpenAI's ``response_format`` rejects ``oneOf`` from Pydantic discriminated
unions and OpenRouter/Gemini hangs with structured output on complex schemas.

The legacy interface (``Timebox`` in / out) is preserved via conversion
helpers so existing nodes can transition incrementally.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.llm.toon import toon_encode

from .actions import TimeboxAction
from .constants import TIMEBOXING_TIMEOUTS
from .preferences import Constraint
from .tb_models import TBPlan
from .tb_ops import TBPatch, apply_tb_ops
from .timebox import Timebox
from .toon_views import constraints_rows

logger = logging.getLogger(__name__)

# ── System prompt for the patcher agent ──────────────────────────────────

_PATCHER_SYSTEM_PROMPT = """\
You are a timebox refinement assistant.  You receive the current schedule
as a TBPlan JSON, plus a user instruction and optional constraints.

**Your task**: produce a single ``TBPatch`` JSON with the minimal set of
typed domain operations that fulfills the user's request.

Available operations (field ``op`` discriminator):
- ``ae`` (AddEvents): add one or more events.  Set ``after`` to insert position.
- ``re`` (RemoveEvent): remove by index ``i``.
- ``ue`` (UpdateEvent): merge partial changes onto event at index ``i``.
- ``me`` (MoveEvent): reorder event from ``fr`` to ``to``.
- ``ra`` (ReplaceAll): replace the entire event list (only for full rebuilds).

Time placement (field ``a`` discriminator on ``p``):
- ``ap`` (AfterPrev): starts after previous event ends; needs ``dur`` (ISO 8601).
- ``bn`` (BeforeNext): ends when next event starts; needs ``dur``.
- ``fs`` (FixedStart): pinned start time; needs ``st`` (HH:MM) and ``dur``.
- ``fw`` (FixedWindow): fixed start and end; needs ``st`` and ``et``.

Event types (``t``): M (meeting), C (commute), DW (deep work), SW (shallow work),
PR (plan & review), H (habit), R (regeneration), BU (buffer), BG (background).

Rules:
- Prefer fine-grained ops (ue, re, ae) over ra.
- Keep immovable events (meetings, fixed windows) unchanged unless explicitly asked.
- Maintain time chain validity: at least one fixed anchor must exist.
- BG events must use fs or fw timing.

Return ONLY the TBPatch JSON.
"""


class TimeboxPatcher:
    """Apply user-requested refinements to a ``TBPlan`` via typed domain ops.

    Uses an AutoGen ``AssistantAgent`` with the TBPatch JSON schema injected
    into the system prompt.  The LLM returns raw JSON which is parsed by
    ``_extract_patch()``.  This avoids OpenAI's ``response_format`` rejection
    of ``oneOf`` (from Pydantic discriminated unions) and OpenRouter timeouts
    with ``output_content_type``.
    """

    def __init__(
        self,
        *,
        model_client: Any | None = None,
        agent_type: str = "timebox_patcher",
    ) -> None:
        """Initialize the patcher.

        Args:
            model_client: An AutoGen chat model client.  If ``None``, one is
                built from the ``agent_type`` config key.
            agent_type: Config key for ``build_autogen_chat_client``.
        """
        self._model_client = model_client or build_autogen_chat_client(
            agent_type,
            parallel_tool_calls=False,
        )

    async def apply_patch(
        self,
        *,
        current: TBPlan,
        user_message: str,
        constraints: Iterable[Constraint] | None = None,
        actions: Iterable[TimeboxAction] | None = None,
    ) -> tuple[TBPlan, TBPatch]:
        """Generate and apply a ``TBPatch`` to the current plan.

        Args:
            current: The current ``TBPlan``.
            user_message: The user's refinement instruction.
            constraints: Active constraints (optional).
            actions: Recent actions log (optional).

        Returns:
            Tuple of ``(patched_plan, patch)`` so callers can inspect
            what changed.

        Raises:
            ValueError: If the LLM output cannot be parsed as ``TBPatch``.
        """
        context = _build_context(
            current,
            user_message,
            constraints or [],
            actions or [],
        )
        agent = AssistantAgent(
            name="TimeboxPatcherAgent",
            model_client=self._model_client,
            system_message=_patcher_system_prompt_with_schema(),
            reflect_on_tool_use=False,
        )
        response = await with_timeout(
            "timeboxing:patcher",
            agent.on_messages(
                [TextMessage(content=context, source="user")],
                CancellationToken(),
            ),
            timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
        )

        # Parse the structured output
        patch = _extract_patch(response)
        patched = apply_tb_ops(current, patch)
        return patched, patch

    async def apply_patch_legacy(
        self,
        *,
        current: Timebox,
        user_message: str,
        constraints: Iterable[Constraint] | None = None,
        actions: Iterable[TimeboxAction] | None = None,
    ) -> Timebox:
        """Legacy interface: ``Timebox`` in → ``Timebox`` out.

        Converts to ``TBPlan``, patches, and converts back.
        Preserves backward compat with existing ``StageRefineNode``.

        Args:
            current: The current ``Timebox``.
            user_message: User refinement instruction.
            constraints: Active constraints.
            actions: Recent actions log.

        Returns:
            A new ``Timebox`` with the patch applied.
        """
        from .timebox import tb_plan_to_timebox, timebox_to_tb_plan

        tb_plan = timebox_to_tb_plan(current)
        patched_plan, _ = await self.apply_patch(
            current=tb_plan,
            user_message=user_message,
            constraints=constraints,
            actions=actions,
        )
        return tb_plan_to_timebox(patched_plan)


# ── Internal helpers ─────────────────────────────────────────────────────


def _patcher_system_prompt_with_schema() -> str:
    """Build patcher system prompt with the TBPatch JSON schema appended.

    The schema is included so the LLM produces valid JSON matching the
    ``TBPatch`` structure without relying on ``response_format`` (which
    rejects ``oneOf`` from discriminated unions).

    Returns:
        Full system prompt string.
    """
    schema_json = json.dumps(TBPatch.model_json_schema(), indent=2)
    return (
        _PATCHER_SYSTEM_PROMPT
        + f"\n\nTBPatch JSON Schema:\n```json\n{schema_json}\n```"
        + "\n\nReturn ONLY the raw TBPatch JSON object — no markdown fences, no commentary."
    )


def _build_context(
    plan: TBPlan,
    user_message: str,
    constraints: Iterable[Constraint],
    actions: Iterable[TimeboxAction],
) -> str:
    """Build the prompt context for the patcher agent.

    Args:
        plan: Current TBPlan.
        user_message: User's refinement instruction.
        constraints: Active constraints.
        actions: Recent actions.

    Returns:
        Formatted prompt string.
    """
    plan_json = plan.model_dump_json(indent=2)
    constraints_list = list(constraints)
    constraints_toon = ""
    if constraints_list:
        constraints_toon = toon_encode(
            name="constraints",
            rows=constraints_rows(constraints_list),
            fields=["name", "necessity", "scope", "status", "source", "description"],
        )
    actions_text = _format_actions(actions)

    return (
        f"Current TBPlan:\n```json\n{plan_json}\n```\n\n"
        f"User request: {user_message}\n\n"
        f"{constraints_toon}\n"
        f"Recent actions:\n{actions_text}\n\n"
        "Produce the TBPatch JSON with minimal ops to fulfill the request."
    )


def _format_actions(actions: Iterable[TimeboxAction]) -> str:
    """Format action log for prompt context.

    Args:
        actions: Iterable of TimeboxAction.

    Returns:
        Formatted string.
    """
    lines: List[str] = []
    for action in actions:
        details = []
        if action.from_time:
            details.append(f"from {action.from_time}")
        if action.to_time:
            details.append(f"to {action.to_time}")
        detail_text = " ".join(details)
        reason = f" | reason: {action.reason}" if action.reason else ""
        lines.append(f"- {action.kind} {action.summary} {detail_text}".strip() + reason)
    return "\n".join(lines) if lines else "- (none)"


def _extract_patch(response: Any) -> TBPatch:
    """Extract ``TBPatch`` from an AutoGen agent response.

    Args:
        response: The ``Response`` object from ``agent.on_messages()``.

    Returns:
        Parsed ``TBPatch``.

    Raises:
        ValueError: If the response cannot be parsed as ``TBPatch``.
    """
    msg = response.chat_message
    content = getattr(msg, "content", None)

    # If AutoGen returned a structured TBPatch directly
    if isinstance(content, TBPatch):
        return content

    # Try parsing from string (strip markdown fences if present)
    if isinstance(content, str):
        text = content.strip()
        if text.startswith("```"):
            # Remove opening ```json or ``` and closing ```
            lines = text.split("\n")
            lines = lines[1:]  # drop opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]  # drop closing fence
            text = "\n".join(lines)
        try:
            return TBPatch.model_validate_json(text)
        except Exception:
            pass

    # Try from dict
    if isinstance(content, dict):
        return TBPatch.model_validate(content)

    raise ValueError(
        f"Could not extract TBPatch from response: {type(content).__name__}"
    )


__all__ = ["TimeboxPatcher"]
