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
import os
import time
from typing import Any, Callable, Iterable, List

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client
from fateforger.llm.toon import toon_encode

from .actions import TimeboxAction
from .constants import TIMEBOXING_TIMEOUTS
from .planning_policy import (
    PLANNING_POLICY_VERSION,
    QUALITY_RUBRIC_PROMPT,
    SHARED_PLANNING_POLICY_PROMPT,
    STAGE3_OUTLINE_PROMPT,
    STAGE4_REFINEMENT_PROMPT,
)
from .preferences import Constraint
from .tb_models import TBPlan
from .tb_ops import TBPatch, apply_tb_ops
from .timebox import Timebox
from .toon_views import constraints_rows

logger = logging.getLogger(__name__)

# ── System prompt for the patcher agent ──────────────────────────────────

_PATCHER_SYSTEM_PROMPT = f"""\
You are a timebox refinement assistant. You receive the current schedule
as a TBPlan JSON, plus a user instruction and optional constraints.

Planning policy version: {PLANNING_POLICY_VERSION}

**Your task**: produce a single ``TBPatch`` JSON with the minimal set of
typed domain operations that fulfills the user's request.

Available operations (field ``op`` discriminator):
- ``ae`` (AddEvents): add one or more events. Set ``after`` to insert position.
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

Shared planning policy:
{SHARED_PLANNING_POLICY_PROMPT}

Stage-aware behavior (read the "Planning context" JSON included in user_message):
- If context stage is ``Skeleton`` (or ``extra.mode`` is ``outline``), follow:
{STAGE3_OUTLINE_PROMPT}
- If context stage is ``Refine``, follow:
{STAGE4_REFINEMENT_PROMPT}

Quality rubric guidance:
{QUALITY_RUBRIC_PROMPT}

Rules:
- Prefer fine-grained ops (ue, re, ae) over ra.
- Keep immovable events (meetings, fixed windows) unchanged unless explicitly asked.
- Maintain time chain validity: at least one fixed anchor must exist.
- BG events must use fs or fw timing.
- If validation feedback lists rule violations, satisfy those first with minimal edits
  and then apply the requested refinement while preserving intent.

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
        max_attempts: int | None = None,
    ) -> None:
        """Initialize the patcher.

        Args:
            model_client: An AutoGen chat model client.  If ``None``, one is
                built from the ``agent_type`` config key.
            agent_type: Config key for ``build_autogen_chat_client``.
            max_attempts: Maximum patch attempts before failing hard.
        """
        self._model_client = model_client or build_autogen_chat_client(
            agent_type,
            parallel_tool_calls=False,
        )
        env_attempts = _coerce_positive_int(
            os.getenv("TIMEBOX_PATCHER_MAX_ATTEMPTS"), default=5
        )
        self._max_attempts = max_attempts if max_attempts is not None else env_attempts
        self._max_attempts = max(1, int(self._max_attempts))

    async def apply_patch(
        self,
        *,
        current: TBPlan,
        user_message: str,
        constraints: Iterable[Constraint] | None = None,
        actions: Iterable[TimeboxAction] | None = None,
        plan_validator: Callable[[TBPlan], Any] | None = None,
    ) -> tuple[TBPlan, TBPatch]:
        """Generate and apply a ``TBPatch`` to the current plan.

        Args:
            current: The current ``TBPlan``.
            user_message: The user's refinement instruction.
            constraints: Active constraints (optional).
            actions: Recent actions log (optional).
            plan_validator: Optional callback to validate the patched plan.
                Any raised exception is fed back into retry guidance.

        Returns:
            Tuple of ``(patched_plan, patch)`` so callers can inspect
            what changed.

        Raises:
            ValueError: If the LLM output cannot be parsed as ``TBPatch``.
        """
        constraints_list = list(constraints or [])
        actions_list = list(actions or [])
        request_id = f"patch-{int(time.time() * 1000)}"
        agent = AssistantAgent(
            name="TimeboxPatcherAgent",
            model_client=self._model_client,
            system_message=_patcher_system_prompt_with_schema(),
            reflect_on_tool_use=False,
        )
        retry_feedback: str | None = None
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            context = _build_context(
                current,
                user_message,
                constraints_list,
                actions_list,
                retry_feedback=retry_feedback,
            )
            logger.debug(
                "timebox_patcher request_id=%s attempt=%s/%s events=%s constraints=%s actions=%s",
                request_id,
                attempt,
                self._max_attempts,
                len(current.events),
                len(constraints_list),
                len(actions_list),
            )
            if retry_feedback:
                logger.debug(
                    "timebox_patcher request_id=%s retry_feedback=%s",
                    request_id,
                    retry_feedback,
                )
            try:
                response = await with_timeout(
                    "timeboxing:patcher",
                    agent.on_messages(
                        [TextMessage(content=context, source="user")],
                        CancellationToken(),
                    ),
                    timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
                )
                raw_content = getattr(getattr(response, "chat_message", None), "content", None)
                logger.debug(
                    "timebox_patcher request_id=%s attempt=%s raw_content=%s",
                    request_id,
                    attempt,
                    _to_log_string(raw_content),
                )
                patch = _extract_patch(response)
                logger.debug(
                    "timebox_patcher request_id=%s attempt=%s patch=%s",
                    request_id,
                    attempt,
                    patch.model_dump_json(),
                )
                patched = apply_tb_ops(current, patch)
                if plan_validator is not None:
                    plan_validator(patched)
                logger.info(
                    "timebox_patcher request_id=%s success attempt=%s/%s ops=%s",
                    request_id,
                    attempt,
                    self._max_attempts,
                    len(patch.ops),
                )
                return patched, patch
            except Exception as exc:
                last_error = exc
                retry_feedback = _build_retry_feedback(error=exc)
                logger.warning(
                    "timebox_patcher request_id=%s failed attempt=%s/%s error=%s",
                    request_id,
                    attempt,
                    self._max_attempts,
                    retry_feedback,
                )
                continue

        assert last_error is not None
        raise ValueError(
            f"Timebox patch failed after {self._max_attempts} attempts: {last_error}"
        ) from last_error

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
    retry_feedback: str | None = None,
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

    context = (
        f"Current TBPlan:\n```json\n{plan_json}\n```\n\n"
        f"User request: {user_message}\n\n"
        f"{constraints_toon}\n"
        f"Recent actions:\n{actions_text}\n\n"
        "Produce the TBPatch JSON with minimal ops to fulfill the request."
    )
    if retry_feedback:
        context += (
            "\n\nPrevious patch attempt failed.\n"
            f"Validation/apply error: {retry_feedback}\n"
            "Return a corrected TBPatch that resolves this error while preserving user intent."
        )
    return context


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
        # TODO(refactor,typed-contracts): Remove markdown-fence stripping fallback.
        # Enforce strict typed tool/message output so TBPatch is parsed without
        # free-text normalization.
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


def _coerce_positive_int(value: str | None, *, default: int) -> int:
    """Parse a positive integer from env-like input, with safe fallback."""
    try:
        if value is None:
            return default
        parsed = int(str(value).strip())
        if parsed < 1:
            return default
        return parsed
    except Exception:
        return default


def _to_log_string(value: Any) -> str:
    """Render patcher values as compact log-safe strings."""
    try:
        if hasattr(value, "model_dump_json"):
            text = value.model_dump_json()  # type: ignore[call-arg]
        elif isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
    except Exception:
        text = repr(value)
    return _truncate(text, max_chars=6000)


def _truncate(value: str, *, max_chars: int) -> str:
    """Truncate long log lines while preserving length metadata."""
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 16)] + f" …(truncated,{len(value)})"


def _build_retry_feedback(*, error: Exception) -> str:
    """Build concise, structured retry guidance from apply/validation exceptions."""
    head = str(error).strip() or type(error).__name__
    lines: list[str] = [head]
    details = _extract_error_details(error)
    if details:
        lines.append("Violations:")
        lines.extend(f"- {item}" for item in details)
    return _truncate("\n".join(lines), max_chars=1200)


def _extract_error_details(error: Exception) -> list[str]:
    """Extract normalized validation/apply details from nested exceptions."""
    details: list[str] = []
    seen: set[int] = set()
    current: Exception | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        extractor = getattr(current, "errors", None)
        if callable(extractor):
            try:
                raw = extractor()
            except Exception:
                raw = None
            if isinstance(raw, list):
                for item in raw[:6]:
                    if not isinstance(item, dict):
                        continue
                    loc = item.get("loc")
                    loc_text = (
                        ".".join(str(part) for part in loc)
                        if isinstance(loc, (list, tuple)) and loc
                        else "root"
                    )
                    typ = str(item.get("type") or "validation_error")
                    msg = str(item.get("msg") or "").strip()
                    details.append(
                        f"{typ} at {loc_text}: {msg}".strip(": ")
                    )
        current = current.__cause__
    return details


__all__ = ["TimeboxPatcher"]
