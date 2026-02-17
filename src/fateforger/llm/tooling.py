"""Tooling helpers for structured-output agent safety contracts."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _tool_name(tool: Any) -> str:
    """Return a human-readable tool name for diagnostics."""
    try:
        return str(getattr(tool, "name"))
    except Exception:
        return repr(tool)


def assert_strict_tools_for_structured_output(
    *,
    tools: Sequence[Any] | None,
    output_content_type: type | None,
    agent_name: str,
) -> None:
    """Fail fast when structured-output agents are wired with non-strict tools.

    OpenAI parse-mode requires strict function tools whenever
    ``output_content_type`` is enabled. This helper enforces that contract at
    agent construction time so failures are deterministic and explicit.
    """
    if output_content_type is None or not tools:
        return
    non_strict_names: list[str] = []
    for tool in tools:
        schema = getattr(tool, "schema", None)
        if not isinstance(schema, dict) or schema.get("strict") is not True:
            non_strict_names.append(_tool_name(tool))
    if non_strict_names:
        joined = ", ".join(non_strict_names)
        raise RuntimeError(
            f"{agent_name} uses output_content_type={output_content_type.__name__} "
            f"with non-strict tools: {joined}. "
            "All attached tools must be strict."
        )


__all__ = ["assert_strict_tools_for_structured_output"]
