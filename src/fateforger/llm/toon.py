"""TOON-style tabular prompt encoding helpers.

The upstream `toon-format` package in this repo is currently a stub (encoder not implemented).
This module provides a small, deterministic encoder that follows the core TOON conventions:

- Header: `<name>[N]{k1,k2,...}:`
- Records: one row per item, values in that exact key order

This is used to inject structured *lists* (constraints, tasks, events) into LLM prompts
without dumping large JSON blobs.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel


def toon_encode(
    *,
    name: str,
    rows: Sequence[Mapping[str, Any] | BaseModel],
    fields: Sequence[str],
    delimiter: str = ",",
) -> str:
    """Encode uniform rows into a TOON-style table string.

    Args:
        name: Logical name of the table (e.g. "constraints", "tasks").
        rows: Sequence of dicts or Pydantic models.
        fields: Ordered field names to emit as columns.
        delimiter: Column delimiter (default comma).

    Returns:
        A TOON-style string with a header and N record rows.
    """
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, BaseModel):
            normalized.append(row.model_dump(mode="json"))
        elif isinstance(row, Mapping):
            normalized.append(dict(row))
        else:
            normalized.append({})

    header = f"{name}[{len(normalized)}]" + "{" + ",".join(fields) + "}:"
    if not normalized:
        return header

    lines: list[str] = [header]
    for row in normalized:
        values = [_toon_scalar(row.get(field)) for field in fields]
        lines.append(delimiter.join(_toon_escape(v, delimiter=delimiter) for v in values))
    return "\n".join(lines)


def _toon_scalar(value: Any) -> str:
    """Convert a python value into a compact scalar string for TOON tables."""
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, timedelta):
        return str(value.total_seconds())
    if isinstance(value, (list, tuple, set)):
        return "|".join(_toon_scalar(item) for item in value)
    return str(value)


def _toon_escape(value: str, *, delimiter: str) -> str:
    """Escape a scalar for safe TOON/CSV-style parsing.

    We quote only when needed:
    - contains delimiter/newline/quote
    - has leading/trailing whitespace
    """
    if value == "":
        return ""
    needs_quote = (
        delimiter in value
        or "\n" in value
        or "\r" in value
        or '"' in value
        or value != value.strip()
    )
    if not needs_quote:
        return value
    escaped = value.replace('"', '""')
    return f"\"{escaped}\""


__all__ = ["toon_encode"]

