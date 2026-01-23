"""Internal MCP clients used by the timeboxing coordinator.

These are intentionally kept out of `agent.py` to keep orchestration logic readable.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from fateforger.tools.constraint_mcp import (
    build_constraint_server_env,
    resolve_constraint_repo_root,
)


class ConstraintMemoryClient:
    """Client for the constraint-memory MCP server (stdio workbench)."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        """Initialize the constraint-memory MCP workbench client.

        Args:
            timeout: MCP read timeout seconds for stdio transport.
        """
        try:
            from autogen_ext.tools.mcp import McpWorkbench, StdioServerParams
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "autogen_ext tools are required for constraint memory access"
            ) from exc

        root = resolve_constraint_repo_root()
        server_path = root / "scripts" / "constraint_mcp_server.py"
        params = StdioServerParams(
            command=sys.executable,
            args=[str(server_path)],
            env=build_constraint_server_env(root),
            cwd=str(root),
            read_timeout_seconds=timeout,
        )
        self._workbench = McpWorkbench(params)

    async def get_store_info(self) -> dict[str, Any]:
        """Return store metadata from the MCP server."""
        result = await self._workbench.call_tool(
            "constraint_get_store_info", arguments={}
        )
        try:
            text = result.to_text()
            data = json.loads(text)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    async def query_types(
        self, *, stage: str | None = None, event_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Query ranked constraint types from the MCP server.

        Args:
            stage: Optional stage filter (e.g. "Skeleton").
            event_types: Optional list of Notion event type codes (e.g. ["DW","M"]).

        Returns:
            A list of type dicts (raw MCP payload).
        """
        payload = {"stage": stage, "event_types": event_types or []}
        result = await self._workbench.call_tool(
            "constraint_query_types", arguments=payload
        )
        try:
            text = result.to_text()
            data = json.loads(text)
        except Exception:
            return []
        return data if isinstance(data, list) else []

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query the MCP constraint store.

        Args:
            filters: Query filters passed to the MCP server.
            type_ids: Optional list of constraint type_ids to include.
            tags: Optional list of topic tags to include.
            sort: Optional sort spec (e.g. [["Status","descending"]]).
            limit: Maximum number of results.

        Returns:
            A list of constraint dicts (raw MCP payload).
        """
        payload = {
            "filters": filters,
            "type_ids": type_ids or None,
            "tags": tags or None,
            "sort": sort or None,
            "limit": limit,
        }
        result = await self._workbench.call_tool(
            "constraint_query_constraints", arguments=payload
        )
        # TODO(refactor): Validate MCP responses with a Pydantic schema.
        try:
            text = result.to_text()
            data = json.loads(text)
        except Exception:
            return []
        return data if isinstance(data, list) else []


class McpCalendarClient:
    """Client for Google Calendar MCP server (streamable HTTP workbench)."""

    def __init__(self, *, server_url: str, timeout: float = 10.0) -> None:
        """Initialize the calendar MCP workbench.

        Args:
            server_url: MCP server base URL.
            timeout: HTTP timeout seconds.
        """
        from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

        params = StreamableHttpServerParams(url=server_url, timeout=timeout)
        self._workbench = McpWorkbench(params)

    def get_tools(self) -> list:
        """Return MCP tool definitions for AutoGen tool wiring."""
        return self._workbench.get_tools()

    @staticmethod
    def _extract_tool_payload(result: Any) -> Any:
        """Normalize tool results into a raw payload."""
        # TODO(refactor): Replace dict probing with Pydantic parsing of tool results.
        if isinstance(result, dict):
            return result
        payload = getattr(result, "content", None)
        if payload is not None:
            return payload
        payload = getattr(result, "result", None)
        if payload is not None:
            return payload
        return {}

    @staticmethod
    def _normalize_events(payload: Any) -> list[dict[str, Any]]:
        """Coerce raw MCP payloads into a list of event dicts."""
        # TODO(refactor): Replace dict filtering with Pydantic CalendarEvent parsing.
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        return []

    @staticmethod
    def _parse_event_dt(raw: dict[str, Any] | None, *, tz: ZoneInfo) -> datetime | None:
        """Parse a calendar event datetime payload into a timezone-aware datetime."""
        # TODO(refactor): Use a Pydantic date-time parser instead of raw dict reads.
        if not raw:
            return None
        if raw.get("dateTime"):
            dt_val = date_parser.isoparse(raw["dateTime"])
            return dt_val.astimezone(tz)
        if raw.get("date"):
            day_val = date_parser.isoparse(raw["date"]).date()
            return datetime.combine(day_val, datetime.min.time(), tz)
        return None

    @staticmethod
    def _to_hhmm(dt_val: datetime | None, *, tz: ZoneInfo) -> str | None:
        """Format an event datetime as HH:MM in the requested timezone."""
        if not dt_val:
            return None
        return dt_val.astimezone(tz).strftime("%H:%M")

    async def list_day_immovables(
        self, *, calendar_id: str, day: date, tz: ZoneInfo
    ) -> list[dict[str, str]]:
        """Fetch a day's immovables from the MCP calendar server."""
        start = (
            datetime.combine(day, datetime.min.time(), tz)
            .astimezone(timezone.utc)
            .isoformat()
        )
        end = (
            (datetime.combine(day, datetime.min.time(), tz) + timedelta(days=1))
            .astimezone(timezone.utc)
            .isoformat()
        )
        args = {
            "calendarId": calendar_id,
            "timeMin": start,
            "timeMax": end,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        result = await self._workbench.call_tool("list-events", arguments=args)
        payload = self._extract_tool_payload(result)
        events = self._normalize_events(payload)

        immovables: list[dict[str, str]] = []
        for event in events:
            if (event.get("status") or "").lower() == "cancelled":
                continue
            summary = (event.get("summary") or "").strip() or "Busy"
            start_dt = self._parse_event_dt(event.get("start"), tz=tz)
            end_dt = self._parse_event_dt(event.get("end"), tz=tz)
            if not start_dt or not end_dt or end_dt <= start_dt:
                continue
            start_str = self._to_hhmm(start_dt, tz=tz)
            end_str = self._to_hhmm(end_dt, tz=tz)
            if not start_str or not end_str:
                continue
            immovables.append({"title": summary, "start": start_str, "end": end_str})
        return immovables
