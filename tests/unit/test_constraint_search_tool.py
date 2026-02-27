"""Tests for the constraint search tool module."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fateforger.agents.timeboxing.constraint_search_tool import (
    ConstraintSearchPlan,
    ConstraintSearchQuery,
    ConstraintSearchResponse,
    ConstraintSearchResult,
    _dedupe_results,
    _raw_to_result,
    execute_search_plan,
    format_constraint_oneliner,
    format_search_summary,
    search_constraints,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_raw_constraint(
    uid: str = "tb:test:abc123",
    name: str = "Deep Work Preference",
    description: str = "Prefer 2 deep work blocks in the morning",
    necessity: str = "should",
    status: str = "locked",
    scope: str = "profile",
    rule_kind: str = "prefer_window",
    days_of_week: list[str] | None = None,
    topics: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Create a raw constraint dict matching MCP server output format."""
    return {
        "page_id": kwargs.get("page_id", "page-001"),
        "uid": uid,
        "name": name,
        "description": description,
        "necessity": necessity,
        "status": status,
        "scope": scope,
        "rule_kind": rule_kind,
        "type_id": kwargs.get("type_id"),
        "days_of_week": days_of_week or [],
        "start_date": kwargs.get("start_date"),
        "end_date": kwargs.get("end_date"),
        "topics": topics or [],
        "url": kwargs.get("url"),
        "source": kwargs.get("source", "user"),
    }


def _make_mock_client(results: list[list[dict[str, Any]]]) -> MagicMock:
    """Create a mock ConstraintMemoryClient that returns sequential results."""
    client = MagicMock()
    call_count = 0

    async def query_constraints(**kwargs: Any) -> list[dict[str, Any]]:
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        return results[idx]

    client.query_constraints = AsyncMock(side_effect=query_constraints)
    return client


def _make_query(**overrides: Any) -> ConstraintSearchQuery:
    payload: dict[str, Any] = {
        "label": "q",
        "text_query": None,
        "event_types": None,
        "tags": None,
        "statuses": None,
        "scopes": None,
        "necessities": None,
        "limit": 20,
    }
    payload.update(overrides)
    return ConstraintSearchQuery(**payload)


# ---------------------------------------------------------------------------
# Unit tests: _raw_to_result
# ---------------------------------------------------------------------------


class TestRawToResult:
    """Tests for converting raw MCP dicts to typed results."""

    def test_basic_conversion(self) -> None:
        raw = _make_raw_constraint()
        result = _raw_to_result(raw)
        assert isinstance(result, ConstraintSearchResult)
        assert result.uid == "tb:test:abc123"
        assert result.name == "Deep Work Preference"
        assert result.necessity == "should"
        assert result.status == "locked"
        assert result.scope == "profile"

    def test_missing_fields_default_gracefully(self) -> None:
        result = _raw_to_result({"page_id": "p1"})
        assert result.uid is None
        assert result.name is None
        assert result.days_of_week == []
        assert result.topics == []


# ---------------------------------------------------------------------------
# Unit tests: deduplication
# ---------------------------------------------------------------------------


class TestDedupeResults:
    """Tests for result deduplication."""

    def test_dedupes_by_uid(self) -> None:
        r1 = ConstraintSearchResult(uid="a", name="Constraint A")
        r2 = ConstraintSearchResult(uid="a", name="Constraint A (dup)")
        r3 = ConstraintSearchResult(uid="b", name="Constraint B")
        result = _dedupe_results([r1, r2, r3])
        assert len(result) == 2
        assert result[0].name == "Constraint A"
        assert result[1].name == "Constraint B"

    def test_dedupes_by_page_id_fallback(self) -> None:
        r1 = ConstraintSearchResult(page_id="p1", name="X")
        r2 = ConstraintSearchResult(page_id="p1", name="Y")
        result = _dedupe_results([r1, r2])
        assert len(result) == 1

    def test_preserves_order(self) -> None:
        items = [
            ConstraintSearchResult(uid=f"u{i}", name=f"C{i}") for i in range(5)
        ]
        result = _dedupe_results(items)
        assert [r.uid for r in result] == ["u0", "u1", "u2", "u3", "u4"]


# ---------------------------------------------------------------------------
# Unit tests: formatting
# ---------------------------------------------------------------------------


class TestFormatting:
    """Tests for constraint summary formatting."""

    def test_oneliner_full(self) -> None:
        c = ConstraintSearchResult(
            uid="test",
            name="Morning Focus",
            description="Deep work sessions in the morning hours",
            necessity="must",
            status="locked",
            scope="profile",
            rule_kind="prefer_window",
            days_of_week=["MO", "TU", "WE"],
            topics=["focus", "productivity"],
        )
        line = format_constraint_oneliner(c)
        assert "[locked | must]" in line
        assert "Morning Focus" in line
        assert "Deep work sessions" in line
        assert "scope=profile" in line
        assert "kind=prefer_window" in line
        assert "days=MO,TU,WE" in line

    def test_oneliner_minimal(self) -> None:
        c = ConstraintSearchResult(name="Simple rule")
        line = format_constraint_oneliner(c)
        assert "Simple rule" in line

    def test_oneliner_unnamed(self) -> None:
        c = ConstraintSearchResult()
        line = format_constraint_oneliner(c)
        assert "(unnamed)" in line

    def test_summary_empty(self) -> None:
        assert "No constraints found" in format_search_summary([])

    def test_summary_numbered(self) -> None:
        items = [
            ConstraintSearchResult(name=f"Rule {i}") for i in range(3)
        ]
        summary = format_search_summary(items)
        assert "1. " in summary
        assert "2. " in summary
        assert "3. " in summary
        assert "Rule 0" in summary
        assert "Rule 2" in summary


# ---------------------------------------------------------------------------
# Unit tests: search plan execution
# ---------------------------------------------------------------------------


class TestExecuteSearchPlan:
    """Tests for parallel search execution."""

    @pytest.mark.asyncio
    async def test_single_query(self) -> None:
        raw = [_make_raw_constraint(uid="u1", name="Test Rule")]
        client = _make_mock_client([raw])
        plan = ConstraintSearchPlan(
            queries=[_make_query(label="test", text_query="deep work")],
            planned_date="2025-01-15",
        )
        response = await execute_search_plan(client, plan)
        assert response.total_found == 1
        assert response.queries_executed == 1
        assert "Test Rule" in response.summary

    @pytest.mark.asyncio
    async def test_multiple_queries_deduped(self) -> None:
        # Both queries return the same constraint — should be deduped.
        raw = [_make_raw_constraint(uid="u1", name="Shared")]
        client = _make_mock_client([raw, raw])
        plan = ConstraintSearchPlan(
            queries=[
                _make_query(label="q1", text_query="Shared"),
                _make_query(label="q2", event_types=["DW"]),
            ],
        )
        response = await execute_search_plan(client, plan)
        assert response.total_found == 1
        assert response.queries_executed == 2

    @pytest.mark.asyncio
    async def test_multiple_queries_distinct(self) -> None:
        raw_a = [_make_raw_constraint(uid="u1", name="A")]
        raw_b = [_make_raw_constraint(uid="u2", name="B")]
        client = _make_mock_client([raw_a, raw_b])
        plan = ConstraintSearchPlan(
            queries=[
                _make_query(label="q1", text_query="A"),
                _make_query(label="q2", text_query="B"),
            ],
        )
        response = await execute_search_plan(client, plan)
        assert response.total_found == 2

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        client = _make_mock_client([[]])
        plan = ConstraintSearchPlan(
            queries=[_make_query(label="empty", text_query="nonexistent")],
        )
        response = await execute_search_plan(client, plan)
        assert response.total_found == 0
        assert "No constraints found" in response.summary

    @pytest.mark.asyncio
    async def test_query_exception_handled(self) -> None:
        client = MagicMock()
        client.query_constraints = AsyncMock(side_effect=RuntimeError("MCP down"))
        plan = ConstraintSearchPlan(
            queries=[_make_query(label="fail", text_query="test")],
        )
        response = await execute_search_plan(client, plan)
        assert response.total_found == 0
        assert response.queries_executed == 1
        assert response.errors
        assert "fail:" in response.errors[0]


# ---------------------------------------------------------------------------
# Unit tests: FunctionTool wrapper
# ---------------------------------------------------------------------------


class TestSearchConstraintsWrapper:
    """Tests for the FunctionTool-compatible wrapper function."""

    @pytest.mark.asyncio
    async def test_no_client_returns_error(self) -> None:
        result = await search_constraints(
            queries=[{"label": "test", "text_query": "anything"}],
            _client=None,
        )
        assert "Error" in result
        assert "not available" in result

    @pytest.mark.asyncio
    async def test_with_client_returns_summary(self) -> None:
        raw = [_make_raw_constraint(uid="u1", name="Found It")]
        client = _make_mock_client([raw])
        result = await search_constraints(
            queries=[{"label": "test", "text_query": "deep work"}],
            planned_date="2025-01-15",
            stage="Skeleton",
            _client=client,
        )
        assert "Found It" in result
        assert "1 constraint(s)" in result

    @pytest.mark.asyncio
    async def test_wrapper_surfaces_errors_section(self) -> None:
        client = MagicMock()
        client.query_constraints = AsyncMock(side_effect=RuntimeError("MCP down"))
        result = await search_constraints(
            queries=[{"label": "fail", "text_query": "x"}],
            stage="Skeleton",
            _client=client,
        )
        assert "ERRORS (" in result
        assert "fail:" in result

    @pytest.mark.asyncio
    async def test_passes_filters_correctly(self) -> None:
        client = MagicMock()
        client.query_constraints = AsyncMock(return_value=[])
        await search_constraints(
            queries=[{
                "label": "scoped",
                "text_query": "focus",
                "event_types": ["DW"],
                "statuses": ["locked"],
                "scopes": ["profile"],
                "necessities": ["must"],
                "tags": ["work"],
            }],
            planned_date="2025-06-01",
            stage="Skeleton",
            _client=client,
        )
        call_kwargs = client.query_constraints.call_args
        filters = call_kwargs.kwargs["filters"]
        assert filters["text_query"] == "focus"
        assert filters["event_types_any"] == ["DW"]
        assert filters["statuses_any"] == ["locked"]
        assert filters["scopes_any"] == ["profile"]
        assert filters["necessities_any"] == ["must"]
        assert call_kwargs.kwargs["tags"] == ["work"]
        assert filters["as_of"] == "2025-06-01"
        assert filters["stage"] == "Skeleton"


# ---------------------------------------------------------------------------
# Unit tests: ConstraintSearchPlan validation
# ---------------------------------------------------------------------------


class TestSearchPlanValidation:
    """Tests for Pydantic model validation."""

    def test_min_one_query(self) -> None:
        with pytest.raises(Exception):
            ConstraintSearchPlan(queries=[])

    def test_max_eight_queries(self) -> None:
        queries = [_make_query(label=f"q{i}") for i in range(9)]
        with pytest.raises(Exception):
            ConstraintSearchPlan(queries=queries)

    def test_valid_plan(self) -> None:
        plan = ConstraintSearchPlan(
            queries=[_make_query(label="q1")],
            planned_date="2025-01-15",
            stage="Skeleton",
        )
        assert len(plan.queries) == 1
        assert plan.stage == "Skeleton"
