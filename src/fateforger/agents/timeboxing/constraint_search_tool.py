"""Conversational constraint search tool for the timeboxing agent.

This module provides a FunctionTool-compatible search function that stage-gating
LLMs can invoke to find relevant durable constraints in the configured durable store.

The tool accepts a structured search plan (multiple query facets) and executes
them in parallel against the constraint-memory MCP server. Results are
deduplicated, formatted as human-scannable summaries, and returned to the
calling agent for review/selection.

Design decisions:
- Search is LLM-driven: the agent generates candidate queries based on session
  context (planned date, stage, topics, user utterances).
- The MCP server already supports ``text_query`` (Name/Description contains),
  ``event_types_any``, ``statuses_any``, ``tags``, and ``type_ids`` filters.
- Results are summarised as compact one-liners the LLM can reason about.
- The tool is idempotent: calling it multiple times refines the search.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.durable_constraint_store import DurableConstraintStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search plan model (the agent fills this in)
# ---------------------------------------------------------------------------


class ConstraintSearchQuery(BaseModel):
    """A single search facet within a search plan."""

    label: str = Field(
        description="Short human-readable label for this query (e.g. 'deep work rules').",
    )
    text_query: Optional[str] = Field(
        default=None,
        description="Free-text substring to match against constraint Name or Description.",
    )
    event_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Event-type codes to filter by. "
            "Options: M (meeting), C (commute), DW (deep work), SW (shallow work), "
            "H (habit), R (rest), BU (buffer), BG (break), PR (prep)."
        ),
    )
    tags: Optional[List[str]] = Field(
        default=None,
        description="Topic tag names to filter by (e.g. ['focus', 'meals']).",
    )
    statuses: Optional[List[str]] = Field(
        default=None,
        description="Constraint statuses to include. Options: 'locked', 'proposed'. Default: both.",
    )
    scopes: Optional[List[str]] = Field(
        default=None,
        description="Constraint scopes to include. Options: 'session', 'profile', 'datespan'.",
    )
    necessities: Optional[List[str]] = Field(
        default=None,
        description="Necessity levels to include. Options: 'must', 'should'.",
    )
    limit: int = Field(
        default=20,
        description="Maximum number of results for this query.",
    )


class ConstraintSearchPlan(BaseModel):
    """A set of parallel search queries the agent wants to execute."""

    queries: List[ConstraintSearchQuery] = Field(
        min_length=1,
        max_length=8,
        description="One or more search facets to execute in parallel.",
    )
    planned_date: Optional[str] = Field(
        default=None,
        description="ISO date (YYYY-MM-DD) for active-window filtering. Defaults to today.",
    )
    stage: Optional[str] = Field(
        default=None,
        description="Current timeboxing stage (e.g. 'Skeleton', 'Refine').",
    )


# ---------------------------------------------------------------------------
# Search result model
# ---------------------------------------------------------------------------


class ConstraintSearchResult(BaseModel):
    """A single constraint returned from a search."""

    uid: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    necessity: Optional[str] = None
    status: Optional[str] = None
    scope: Optional[str] = None
    rule_kind: Optional[str] = None
    type_id: Optional[str] = None
    days_of_week: List[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    page_id: Optional[str] = None


class ConstraintSearchResponse(BaseModel):
    """Aggregated search results returned to the agent."""

    total_found: int = 0
    constraints: List[ConstraintSearchResult] = Field(default_factory=list)
    queries_executed: int = 0
    errors: List[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_constraint_oneliner(c: ConstraintSearchResult) -> str:
    """Render a constraint as a compact one-liner for agent review.

    Format: [status|necessity] name — description (scope, rule_kind, days)
    """
    parts: list[str] = []

    # Status + necessity badge
    badge_parts: list[str] = []
    if c.status:
        badge_parts.append(c.status)
    if c.necessity:
        badge_parts.append(c.necessity)
    if badge_parts:
        parts.append(f"[{' | '.join(badge_parts)}]")

    # Name
    parts.append(c.name or "(unnamed)")

    # Description snippet (truncated)
    if c.description:
        desc = c.description[:80].rstrip()
        if len(c.description) > 80:
            desc += "…"
        parts.append(f"— {desc}")

    # Metadata tags
    meta: list[str] = []
    if c.scope:
        meta.append(f"scope={c.scope}")
    if c.rule_kind:
        meta.append(f"kind={c.rule_kind}")
    if c.days_of_week:
        meta.append(f"days={','.join(c.days_of_week)}")
    if c.topics:
        meta.append(f"topics={','.join(c.topics[:3])}")
    if c.start_date or c.end_date:
        date_range = f"{c.start_date or '…'}→{c.end_date or '…'}"
        meta.append(f"dates={date_range}")
    if meta:
        parts.append(f"({'; '.join(meta)})")

    return " ".join(parts)


def format_search_summary(results: list[ConstraintSearchResult]) -> str:
    """Render all search results as a numbered list of one-liners.

    Args:
        results: List of search results to format.

    Returns:
        A multi-line string with numbered constraint summaries.
    """
    if not results:
        return "No constraints found matching the search criteria."
    lines: list[str] = []
    for i, c in enumerate(results, 1):
        lines.append(f"{i}. {format_constraint_oneliner(c)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core search execution
# ---------------------------------------------------------------------------


def _raw_to_result(raw: Dict[str, Any]) -> ConstraintSearchResult:
    """Convert a raw MCP constraint dict to a typed search result."""
    return ConstraintSearchResult(
        uid=raw.get("uid"),
        name=raw.get("name"),
        description=raw.get("description"),
        necessity=raw.get("necessity"),
        status=raw.get("status"),
        scope=raw.get("scope"),
        rule_kind=raw.get("rule_kind"),
        type_id=raw.get("type_id"),
        days_of_week=raw.get("days_of_week") or [],
        start_date=raw.get("start_date"),
        end_date=raw.get("end_date"),
        topics=raw.get("topics") or [],
        page_id=raw.get("page_id"),
    )


def _dedupe_results(
    results: Sequence[ConstraintSearchResult],
) -> list[ConstraintSearchResult]:
    """Deduplicate results by UID, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[ConstraintSearchResult] = []
    for r in results:
        key = r.uid or r.page_id or r.name or ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(r)
    return unique


async def _execute_single_query(
    client: DurableConstraintStore,
    query: ConstraintSearchQuery,
    *,
    as_of: str,
    stage: str | None,
) -> list[ConstraintSearchResult]:
    """Execute a single search query against the MCP server.

    Args:
        client: The constraint-memory MCP client.
        query: A single search facet to execute.
        as_of: ISO date string for active-window filtering.
        stage: Optional current timeboxing stage.

    Returns:
        A list of typed search results.
    """
    filters: Dict[str, Any] = {
        "as_of": as_of,
        "require_active": True,
    }
    if stage:
        filters["stage"] = stage
    if query.text_query:
        filters["text_query"] = query.text_query
    if query.event_types:
        filters["event_types_any"] = query.event_types
    if query.statuses:
        filters["statuses_any"] = query.statuses
    if query.scopes:
        filters["scopes_any"] = query.scopes
    if query.necessities:
        filters["necessities_any"] = query.necessities

    raw_results = await client.query_constraints(
        filters=filters,
        tags=query.tags,
        sort=[["Status", "descending"]],
        limit=query.limit,
    )

    return [_raw_to_result(r) for r in raw_results]


async def execute_search_plan(
    client: DurableConstraintStore,
    plan: ConstraintSearchPlan,
) -> ConstraintSearchResponse:
    """Execute a full search plan (parallel queries), deduplicate, and summarise.

    Args:
        client: The constraint-memory MCP client.
        plan: The search plan with one or more query facets.

    Returns:
        A response containing deduplicated results and a formatted summary.
    """
    as_of = plan.planned_date or date.today().isoformat()

    async def _run(query: ConstraintSearchQuery):
        try:
            results = await _execute_single_query(client, query, as_of=as_of, stage=plan.stage)
            return query.label, results
        except Exception as exc:
            return query.label, exc

    tasks = [_run(query) for query in plan.queries]
    outputs = await asyncio.gather(*tasks)

    all_results: list[ConstraintSearchResult] = []
    errors: list[str] = []
    for label, payload in outputs:
        if isinstance(payload, BaseException):
            errors.append(f"{label}: {type(payload).__name__}: {payload}")
            continue
        all_results.extend(payload)

    unique = _dedupe_results(all_results)
    summary = format_search_summary(unique)

    return ConstraintSearchResponse(
        total_found=len(unique),
        constraints=unique,
        queries_executed=len(plan.queries),
        errors=errors,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# FunctionTool-compatible wrapper
# ---------------------------------------------------------------------------


async def search_constraints(
    queries: list[dict[str, Any]],
    planned_date: str | None = None,
    stage: str | None = None,
    _client: DurableConstraintStore | None = None,
) -> str:
    """Search the durable constraint store with one or more query facets.

    This tool lets the timeboxing agent find relevant constraints by combining
    text search, event-type filtering, tags, and status/scope filters.

    Args:
        queries: List of search facets. Each facet is a dict with optional keys:
            - label (str): Short description of this query.
            - text_query (str): Free-text search on Name/Description.
            - event_types (list[str]): Event-type codes (M, DW, SW, H, R, etc.).
            - tags (list[str]): Topic tag names.
            - statuses (list[str]): 'locked' and/or 'proposed'.
            - scopes (list[str]): 'session', 'profile', 'datespan'.
            - necessities (list[str]): 'must' and/or 'should'.
            - limit (int): Max results per facet (default 20).
        planned_date: ISO date string (YYYY-MM-DD) for active-window filtering.
            Null means today.
        stage: Current timeboxing stage (e.g. 'Skeleton'). Null means unfiltered.
        _client: Internal — injected by the agent. Do not set.

    Returns:
        A formatted summary of matching constraints (numbered list of one-liners),
        or an error message if no client is available.
    """
    if _client is None:
        return (
            "Error: Constraint memory client not available. Cannot search constraints."
        )

    parsed_queries = [
        ConstraintSearchQuery(
            label=q.get("label", f"query-{i}"),
            text_query=q.get("text_query"),
            event_types=q.get("event_types"),
            tags=q.get("tags"),
            statuses=q.get("statuses"),
            scopes=q.get("scopes"),
            necessities=q.get("necessities"),
            limit=q.get("limit", 20),
        )
        for i, q in enumerate(queries, 1)
    ]

    plan = ConstraintSearchPlan(
        queries=parsed_queries,
        planned_date=planned_date or None,
        stage=stage or None,
    )

    response = await execute_search_plan(_client, plan)

    header = f"Found {response.total_found} constraint(s) across {response.queries_executed} search(es):\n\n"
    if response.errors:
        lines = [f"{i}. {msg}" for i, msg in enumerate(response.errors, 1)]
        header += f"ERRORS ({len(response.errors)}):\n" + "\n".join(lines) + "\n\n"
    return header + response.summary


__all__ = [
    "ConstraintSearchPlan",
    "ConstraintSearchQuery",
    "ConstraintSearchResponse",
    "ConstraintSearchResult",
    "execute_search_plan",
    "format_constraint_oneliner",
    "format_search_summary",
    "search_constraints",
]
