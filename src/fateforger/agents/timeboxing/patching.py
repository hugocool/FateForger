"""JSON patching helper for timeboxing updates."""

from __future__ import annotations

import asyncio
from typing import Iterable, List

from trustcall import create_extractor

from fateforger.llm import build_langchain_chat_openai

from .actions import TimeboxAction
from .preferences import Constraint
from .timebox import Timebox


class TimeboxPatcher:
    def __init__(
        self,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        agent_type: str = "timebox_patcher",
    ) -> None:
        llm = build_langchain_chat_openai(agent_type, model=model, temperature=temperature)
        self._extractor = create_extractor(
            llm,
            tools=[Timebox],
            tool_choice="Timebox",
            enable_updates=True,
            enable_inserts=True,
            enable_deletes=True,
        )

    async def apply_patch(
        self,
        *,
        current: Timebox,
        user_message: str,
        constraints: Iterable[Constraint],
        actions: Iterable[TimeboxAction],
    ) -> Timebox:
        payload = {
            "messages": _build_context(user_message, constraints, actions),
            "existing": {"Timebox": current.model_dump(mode="json")},
        }

        def _invoke():
            return self._extractor.invoke(payload)

        result = await asyncio.get_event_loop().run_in_executor(None, _invoke)
        patched = result.get("responses", [None])[0]
        if isinstance(patched, Timebox):
            return patched
        return Timebox.model_validate(patched)


def _build_context(
    user_message: str,
    constraints: Iterable[Constraint],
    actions: Iterable[TimeboxAction],
) -> str:
    constraints_text = _format_constraints(constraints)
    actions_text = _format_actions(actions)
    return (
        "Update the existing timebox using JSON patching.\n"
        f"User message: {user_message}\n"
        f"Active constraints:\n{constraints_text}\n"
        f"Recent actions:\n{actions_text}\n"
        "Return the updated Timebox only."
    )


def _format_constraints(constraints: Iterable[Constraint]) -> str:
    lines: List[str] = []
    for constraint in constraints:
        lines.append(
            f"- {constraint.name}: {constraint.description} ({constraint.necessity.value})"
        )
    return "\n".join(lines) if lines else "- (none)"


def _format_actions(actions: Iterable[TimeboxAction]) -> str:
    lines: List[str] = []
    for action in actions:
        details = []
        if action.from_time:
            details.append(f"from {action.from_time}")
        if action.to_time:
            details.append(f"to {action.to_time}")
        detail_text = " ".join(details)
        reason = f" | reason: {action.reason}" if action.reason else ""
        lines.append(
            f"- {action.kind} {action.summary} {detail_text}".strip() + reason
        )
    return "\n".join(lines) if lines else "- (none)"


__all__ = ["TimeboxPatcher"]
