"""Modular prompt context for the timebox patcher.

Three components:
- ErrorFeedback: structured error state for retry turns
- PatcherContext: Pydantic model that renders system_prompt() + user_message_text()
- PatchConversation: caller-owned multi-turn history
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .tb_models import TBPlan
from .tb_ops import TBPatch


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


__all__ = ["ErrorFeedback"]
