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
from collections.abc import Iterator
from typing import Any, Callable, Iterable, List, Literal

from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

from fateforger.debug.diag import with_timeout
from fateforger.llm import build_autogen_chat_client

from .actions import TimeboxAction
from .constants import TIMEBOXING_TIMEOUTS
from .patcher_context import ErrorFeedback, PatchConversation, PatcherContext
from .planning_policy import (
    STAGE4_REFINEMENT_PROMPT as _DEFAULT_STAGE_RULES,
)
from .preferences import Constraint
from .tb_models import TBPlan
from .tb_ops import TBPatch, apply_tb_ops
from .timebox import Timebox

logger = logging.getLogger(__name__)


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
        stage: Literal["Refine"],
        current: TBPlan,
        user_message: str,
        constraints: Iterable[Constraint] | None = None,
        actions: Iterable[TimeboxAction] | None = None,
        plan_validator: Callable[[TBPlan], Any] | None = None,
        stage_rules: str | None = None,
        conversation: PatchConversation | None = None,
        memories: list[str] | None = None,
    ) -> tuple[TBPlan, TBPatch]:
        """Generate and apply a ``TBPatch`` to the current plan.

        Args:
            current: The current ``TBPlan``.
            user_message: The user's refinement instruction.
            constraints: Active constraints (optional).
            actions: Recent actions log (optional, kept for backward compat).
            plan_validator: Optional callback to validate the patched plan.
                Any raised exception is fed back into retry guidance.
            stage_rules: Stage-specific rules for the system prompt.
                Defaults to ``STAGE4_REFINEMENT_PROMPT``.
            conversation: Caller-owned multi-turn history for multi-turn retries.
            memories: Optional list of memory/preference strings.

        Returns:
            Tuple of ``(patched_plan, patch)`` so callers can inspect
            what changed.

        Raises:
            ValueError: If the LLM output cannot be parsed as ``TBPatch``.
        """
        if stage != "Refine":
            raise ValueError(
                f"TimeboxPatcher.apply_patch only supports stage='Refine'. Got {stage!r}."
            )
        constraints_list = list(constraints or [])
        _actions_list = list(actions or [])  # kept for future use
        _stage_rules = stage_rules or _DEFAULT_STAGE_RULES
        request_id = f"patch-{int(time.time() * 1000)}"

        original_plan = current
        error_feedback: ErrorFeedback | None = None
        last_assistant_text: str | None = None
        last_error: Exception | None = None
        last_retryable = True
        patch: TBPatch | None = None

        for attempt in range(1, self._max_attempts + 1):
            ctx = PatcherContext(
                plan=current,
                user_message=user_message,
                stage_rules=_stage_rules,
                constraints=constraints_list or None,
                memories=memories,
                error_feedback=error_feedback,
            )
            agent = AssistantAgent(
                name="TimeboxPatcherAgent",
                model_client=self._model_client,
                system_message=ctx.system_prompt(),
                reflect_on_tool_use=False,
            )
            user_text = ctx.user_message_text()
            prior_messages = conversation.to_autogen_messages() if conversation else []
            new_user_msg = TextMessage(content=user_text, source="user")
            messages_for_agent = prior_messages + [new_user_msg]

            logger.debug(
                "timebox_patcher request_id=%s attempt=%s/%s events=%s",
                request_id,
                attempt,
                self._max_attempts,
                len(current.events),
            )

            try:
                response = await with_timeout(
                    "timeboxing:patcher",
                    agent.on_messages(messages_for_agent, CancellationToken()),
                    timeout_s=TIMEBOXING_TIMEOUTS.skeleton_draft_s,
                )
                raw_content = getattr(
                    getattr(response, "chat_message", None), "content", None
                )
                last_assistant_text = (
                    raw_content if isinstance(raw_content, str) else str(raw_content or "")
                )
                logger.debug(
                    "timebox_patcher request_id=%s attempt=%s raw_len=%s",
                    request_id,
                    attempt,
                    len(last_assistant_text),
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

                # Success — record turns and reset on full rebuild
                if conversation is not None:
                    conversation.append_user(user_text)
                    conversation.append_assistant(last_assistant_text)
                    if any(op.op == "ra" for op in patch.ops):
                        conversation.reset()

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
                retryable = _is_retryable_patch_error(exc)
                last_retryable = retryable
                error_msg = _build_retry_feedback(error=exc)
                # Use model_construct to bypass min_length validation when no patch
                # was produced (e.g. provider error before _extract_patch ran).
                prior_patch = patch if patch is not None else TBPatch.model_construct(ops=[])
                error_feedback = ErrorFeedback(
                    original_plan=original_plan,
                    prior_patch=prior_patch,
                    partial_result=None,
                    error_message=error_msg,
                )
                if conversation is not None and last_assistant_text:
                    conversation.append_user(user_text)
                    conversation.append_assistant(last_assistant_text)
                logger.warning(
                    "timebox_patcher request_id=%s failed attempt=%s/%s retryable=%s error=%s",
                    request_id,
                    attempt,
                    self._max_attempts,
                    retryable,
                    error_msg,
                )
                if not retryable:
                    break
                continue

        assert last_error is not None
        qualifier = "non-retryable " if not last_retryable else ""
        raise ValueError(
            f"Timebox patch failed after {attempt} attempts due to {qualifier}error: {last_error}"
        ) from last_error

    async def apply_patch_legacy(
        self,
        *,
        stage: Literal["Refine"],
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
            stage=stage,
            current=tb_plan,
            user_message=user_message,
            constraints=constraints,
            actions=actions,
            stage_rules=_DEFAULT_STAGE_RULES,
        )
        return tb_plan_to_timebox(patched_plan)


# ── Internal helpers ─────────────────────────────────────────────────────


def _patcher_system_prompt_with_schema(stage_rules: str = "") -> str:
    """Delegate to PatcherContext.system_prompt() — kept for backward compat."""
    import datetime as _dt
    dummy = TBPlan(date=_dt.date.today(), events=[])
    return PatcherContext(
        plan=dummy,
        user_message="",
        stage_rules=stage_rules or _DEFAULT_STAGE_RULES,
    ).system_prompt()


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
        except Exception as exc:
            raise ValueError(f"TBPatch parse/validation failed: {exc}") from exc

    # Try from dict
    if isinstance(content, dict):
        try:
            return TBPatch.model_validate(content)
        except Exception as exc:
            raise ValueError(f"TBPatch validation failed from dict payload: {exc}") from exc

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


def _iter_exception_chain(error: Exception) -> Iterator[Exception]:
    seen: set[int] = set()
    current: Exception | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        if current.__cause__ is not None:
            current = current.__cause__
            continue
        current = current.__context__


def _status_code_from_exception(error: Exception) -> int | None:
    for attr in ("status_code", "http_status", "status"):
        value = getattr(error, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    return None


def _is_retryable_patch_error(error: Exception) -> bool:
    non_retryable_types = {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
    }
    for item in _iter_exception_chain(error):
        if type(item).__name__ in non_retryable_types:
            return False
        status = _status_code_from_exception(item)
        if status is None:
            continue
        if status in {400, 401, 403, 404, 422}:
            return False
        if status in {408, 409, 425, 429}:
            return True
        if status >= 500:
            return True
    return True


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
