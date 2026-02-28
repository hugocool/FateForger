"""Internal MCP clients used by the timeboxing coordinator.

These are intentionally kept out of `agent.py` to keep orchestration logic readable.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from fateforger.adapters.calendar.models import GCalEventsResponse
from fateforger.tools.constraint_mcp import (
    build_constraint_server_env,
    resolve_constraint_repo_root,
)


@dataclass(frozen=True)
class CalendarDaySnapshot:
    """Typed day snapshot used by Stage 4 sync preflight."""

    response: GCalEventsResponse
    immovables: list[dict[str, str]]


class ConstraintMemoryClient:
    """Client for the constraint-memory MCP server (stdio workbench)."""

    _MCP_TIMEOUT_MARKER = "Timed out while waiting for response to ClientRequest"

    @staticmethod
    def _result_text(tool_name: str, result: Any) -> str:
        to_text = getattr(result, "to_text", None)
        if callable(to_text):
            try:
                return (to_text() or "").strip()
            except Exception as exc:
                raise RuntimeError(
                    f"constraint-memory tool {tool_name} produced unreadable text"
                ) from exc
        return str(result).strip()

    @classmethod
    def _parse_json_text(
        cls, tool_name: str, text: str, *, allow_empty: bool = False
    ) -> Any:
        text = (text or "").strip()
        if not text:
            if allow_empty:
                return []
            raise RuntimeError(f"constraint-memory tool {tool_name} returned empty text")
        if text.startswith("Error executing tool "):
            raise RuntimeError(f"constraint-memory tool {tool_name} failed: {text}")
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except Exception:
            decoder = json.JSONDecoder()
            idx = 0
            chunks: list[Any] = []
            while idx < len(text):
                while idx < len(text) and text[idx].isspace():
                    idx += 1
                if idx >= len(text):
                    break
                try:
                    payload, next_idx = decoder.raw_decode(text, idx)
                except Exception as exc:
                    raise RuntimeError(
                        f"constraint-memory tool {tool_name} returned non-JSON text: {text}"
                    ) from exc
                chunks.append(payload)
                idx = next_idx
            if chunks:
                if len(chunks) == 1:
                    return chunks[0]
                merged: list[Any] = []
                for payload in chunks:
                    if isinstance(payload, list):
                        merged.extend(payload)
                    else:
                        merged.append(payload)
                return merged
            raise RuntimeError(
                f"constraint-memory tool {tool_name} returned non-JSON text: {text}"
            )

    @classmethod
    def _decode_tool_result(cls, tool_name: str, result: Any) -> Any:
        if isinstance(result, (dict, list)):
            return result

        is_error = bool(getattr(result, "is_error", False))
        raw_items = getattr(result, "result", None)
        if is_error:
            error_parts: list[str] = []
            if isinstance(raw_items, list):
                for item in raw_items:
                    content = getattr(item, "content", None)
                    if isinstance(content, str) and content.strip():
                        error_parts.append(content.strip())
                        continue
                    item_text = getattr(item, "text", None)
                    if isinstance(item_text, str) and item_text.strip():
                        error_parts.append(item_text.strip())
                        continue
                    if isinstance(item, str) and item.strip():
                        error_parts.append(item.strip())
            text = cls._result_text(tool_name, result)
            if text:
                error_parts.append(text)
            detail = " | ".join(part for part in error_parts if part).strip()
            if not detail:
                detail = "empty payload"
            raise RuntimeError(f"constraint-memory tool {tool_name} failed: {detail}")

        parsed_items: list[Any] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if isinstance(item, (dict, list)):
                    parsed_items.append(item)
                    continue
                if isinstance(item, str):
                    parsed = cls._parse_json_text(tool_name, item, allow_empty=True)
                    if isinstance(parsed, list):
                        parsed_items.extend(parsed)
                    elif parsed != []:
                        parsed_items.append(parsed)
                    continue
                content = getattr(item, "content", None)
                if isinstance(content, str):
                    parsed = cls._parse_json_text(
                        tool_name, content, allow_empty=True
                    )
                    if isinstance(parsed, list):
                        parsed_items.extend(parsed)
                    elif parsed != []:
                        parsed_items.append(parsed)
                    continue
                item_text = getattr(item, "text", None)
                if isinstance(item_text, str):
                    parsed = cls._parse_json_text(
                        tool_name, item_text, allow_empty=True
                    )
                    if isinstance(parsed, list):
                        parsed_items.extend(parsed)
                    elif parsed != []:
                        parsed_items.append(parsed)

        if parsed_items:
            if len(parsed_items) == 1:
                return parsed_items[0]
            merged: list[Any] = []
            for payload in parsed_items:
                if isinstance(payload, list):
                    merged.extend(payload)
                else:
                    merged.append(payload)
            return merged

        text = cls._result_text(tool_name, result)
        return cls._parse_json_text(tool_name, text, allow_empty=True)

    @classmethod
    def _is_mcp_timeout_error(cls, exc: Exception) -> bool:
        return cls._MCP_TIMEOUT_MARKER in str(exc)

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

    async def _call_tool_json(self, tool_name: str, *, arguments: dict[str, Any]) -> Any:
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                result = await self._workbench.call_tool(tool_name, arguments=arguments)
                return self._decode_tool_result(tool_name, result)
            except Exception as exc:
                if attempt >= attempts or not self._is_mcp_timeout_error(exc):
                    raise
                await asyncio.sleep(0.25 * attempt)
        raise RuntimeError(f"constraint-memory tool {tool_name} failed unexpectedly")

    async def get_store_info(self) -> dict[str, Any]:
        """Return store metadata from the MCP server."""
        data = await self._call_tool_json("constraint_get_store_info", arguments={})
        if not isinstance(data, dict):
            raise RuntimeError(
                "constraint-memory tool constraint_get_store_info returned non-dict JSON"
            )
        return data

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
        data = await self._call_tool_json("constraint_query_types", arguments=payload)
        if not isinstance(data, list):
            raise RuntimeError(
                "constraint-memory tool constraint_query_types returned non-list JSON"
            )
        return [item for item in data if isinstance(item, dict)]

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
        data = await self._call_tool_json(
            "constraint_query_constraints", arguments=payload
        )
        if not isinstance(data, list):
            raise RuntimeError(
                "constraint-memory tool constraint_query_constraints returned non-list JSON"
            )
        return [item for item in data if isinstance(item, dict)]

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upsert a durable constraint record in the MCP constraint store.

        Args:
            record: Notion-compatible constraint record payload.
            event: Optional extraction event metadata to log with the upsert.

        Returns:
            Tool payload as a dict when available; otherwise an empty dict.
        """
        payload = {"record": record, "event": event or None}
        data = await self._call_tool_json("constraint_upsert_constraint", arguments=payload)
        if not isinstance(data, dict):
            raise RuntimeError(
                "constraint-memory tool constraint_upsert_constraint returned non-dict JSON"
            )
        if not data.get("uid"):
            raise RuntimeError(
                "constraint-memory tool constraint_upsert_constraint returned missing uid"
            )
        return data


class McpCalendarClient:
    """Client for Google Calendar MCP server (streamable HTTP workbench)."""

    _RECOVERABLE_ERROR_MARKERS = (
        "mcp actor not running",
        "all connection attempts failed",
        "timed out while waiting for response to clientrequest",
        "connection refused",
        "server disconnected",
    )

    def __init__(self, *, server_url: str, timeout: float = 10.0) -> None:
        """Initialize the calendar MCP workbench.

        Args:
            server_url: MCP server base URL.
            timeout: HTTP timeout seconds.
        """
        self._server_url = server_url
        self._timeout = timeout
        self._params = self._build_params()
        self._workbench = self._build_workbench()

    def _build_params(self):
        """Build MCP server params from current server_url and timeout."""
        from autogen_ext.tools.mcp import StreamableHttpServerParams

        return StreamableHttpServerParams(url=self._server_url, timeout=self._timeout)

    def _build_workbench(self):
        """Build a fresh MCP workbench from current params."""
        from autogen_ext.tools.mcp import McpWorkbench

        return McpWorkbench(self._params)

    @classmethod
    def _is_recoverable_transport_error(cls, exc: Exception) -> bool:
        """Return True when the exception is a known transient MCP transport failure."""
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return any(marker in text for marker in cls._RECOVERABLE_ERROR_MARKERS)

    async def _reset_workbench(self) -> None:
        """Close the current workbench and create a fresh one for retry."""
        current = self._workbench
        close = getattr(current, "close", None)
        if callable(close):
            maybe = close()
            if hasattr(maybe, "__await__"):
                await maybe
        self._params = self._build_params()
        self._workbench = self._build_workbench()

    async def _call_tool_payload(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        diagnostics: dict[str, Any] | None = None,
    ) -> Any:
        """Call an MCP tool and extract its payload, retrying once on recoverable errors."""
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                result = await self._workbench.call_tool(tool_name, arguments=arguments)
                if diagnostics is not None:
                    diagnostics["result_type"] = type(result).__name__
                return self._extract_tool_payload(result)
            except Exception as exc:
                recoverable = self._is_recoverable_transport_error(exc)
                if diagnostics is not None:
                    attempt_errors = diagnostics.setdefault("attempt_errors", [])
                    attempt_errors.append(
                        {
                            "attempt": attempt,
                            "recoverable": recoverable,
                            "error": (str(exc) or type(exc).__name__)[:300],
                        }
                    )
                if attempt >= attempts or not recoverable:
                    raise
                await self._reset_workbench()

    async def get_tools(self) -> list:
        """Return MCP tool definitions for AutoGen tool wiring."""
        from autogen_ext.tools.mcp import mcp_server_tools

        tools = await mcp_server_tools(self._params)
        if not tools:
            raise RuntimeError("calendar MCP server returned no tools")
        return tools

    @staticmethod
    def _parse_json_text(raw: Any, *, source: str) -> Any:
        """Parse a JSON payload from text, raising on invalid content."""
        if not isinstance(raw, str):
            raise RuntimeError(
                f"calendar MCP payload from {source} is not text: {type(raw).__name__}"
            )
        text = raw.strip()
        if not text:
            raise RuntimeError(f"calendar MCP payload from {source} is empty")
        if text.startswith("Error executing tool "):
            raise RuntimeError(f"calendar MCP tool failed: {text}")
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        try:
            return json.loads(text)
        except Exception as exc:
            raise RuntimeError(
                f"calendar MCP payload from {source} is not valid JSON: {text}"
            ) from exc

    @classmethod
    def _extract_tool_payload(cls, result: Any) -> Any:
        """Normalize tool results into a raw payload.

        Raises:
            RuntimeError: if no supported payload shape can be decoded.
        """
        if isinstance(result, (dict, list)):
            return result
        to_text = getattr(result, "to_text", None)
        if callable(to_text):
            return cls._parse_json_text(to_text(), source="tool.to_text")
        payload = getattr(result, "content", None)
        if payload is not None:
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, (dict, list)):
                        return item
                    item_content = getattr(item, "content", None)
                    if item_content is not None:
                        return cls._parse_json_text(
                            item_content, source="tool.content[].content"
                        )
                    item_text = getattr(item, "text", None)
                    if item_text is not None:
                        return cls._parse_json_text(
                            item_text, source="tool.content[].text"
                        )
            else:
                return cls._parse_json_text(payload, source="tool.content")
        payload = getattr(result, "result", None)
        if payload is not None:
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, (dict, list)):
                        return item
                    item_content = getattr(item, "content", None)
                    if item_content is not None:
                        return cls._parse_json_text(
                            item_content, source="tool.result[].content"
                        )
                    item_text = getattr(item, "text", None)
                    if item_text is not None:
                        return cls._parse_json_text(
                            item_text, source="tool.result[].text"
                        )
            else:
                return cls._parse_json_text(payload, source="tool.result")
        raise RuntimeError(
            "calendar MCP tool returned unsupported payload; expected dict/list/JSON text"
        )

    @staticmethod
    def _normalize_events(payload: Any) -> list[dict[str, Any]]:
        """Coerce raw MCP payloads into a list of event dicts."""
        # TODO(refactor): Replace dict filtering with Pydantic CalendarEvent parsing.
        if isinstance(payload, dict):
            items = payload.get("events") or payload.get("items")
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
            event = payload.get("event")
            if isinstance(event, dict):
                return [event]
            return []
        if isinstance(payload, list):
            dict_items = [item for item in payload if isinstance(item, dict)]
            if not dict_items:
                return []
            direct_events = [
                item
                for item in dict_items
                if "start" in item and "end" in item
            ]
            if direct_events:
                return direct_events
            nested: list[dict[str, Any]] = []
            for item in dict_items:
                nested.extend(McpCalendarClient._normalize_events(item))
            if nested:
                return nested
            return dict_items
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

    @staticmethod
    def _list_events_args(
        *, calendar_id: str, day: date, tz: ZoneInfo
    ) -> dict[str, Any]:
        start = datetime.combine(day, datetime.min.time(), tz).replace(
            tzinfo=None,
            microsecond=0,
        )
        end = (
            datetime.combine(day, datetime.min.time(), tz) + timedelta(days=1)
        ).replace(
            tzinfo=None,
            microsecond=0,
        )
        return {
            "calendarId": calendar_id,
            "timeMin": start.isoformat(timespec="seconds"),
            "timeMax": end.isoformat(timespec="seconds"),
            "singleEvents": True,
            "orderBy": "startTime",
        }

    def _immovables_from_response(
        self,
        *,
        response: GCalEventsResponse,
        day: date,
        tz: ZoneInfo,
    ) -> list[dict[str, str]]:
        immovables: list[dict[str, str]] = []
        for event in response.events:
            if (event.status or "").lower() == "cancelled":
                continue
            event_dict = event.model_dump(mode="json", by_alias=True)
            summary = str(event.summary or "").strip() or "Busy"
            start_dt = self._parse_event_dt(event_dict.get("start"), tz=tz)
            end_dt = self._parse_event_dt(event_dict.get("end"), tz=tz)
            if not start_dt or not end_dt or end_dt <= start_dt:
                continue
            if start_dt.date() != day:
                continue
            start_str = self._to_hhmm(start_dt, tz=tz)
            end_str = self._to_hhmm(end_dt, tz=tz)
            if not start_str or not end_str:
                continue
            immovables.append({"title": summary, "start": start_str, "end": end_str})
        return immovables

    async def list_day_snapshot(
        self,
        *,
        calendar_id: str,
        day: date,
        tz: ZoneInfo,
        diagnostics: dict[str, Any] | None = None,
    ) -> CalendarDaySnapshot:
        """Fetch a typed day snapshot with both raw events and immovables."""
        args = self._list_events_args(calendar_id=calendar_id, day=day, tz=tz)
        if diagnostics is not None:
            diagnostics["request"] = args
        payload = await self._call_tool_payload(
            tool_name="list-events",
            arguments=args,
            diagnostics=diagnostics,
        )
        if diagnostics is not None:
            diagnostics["payload_type"] = type(payload).__name__
            if isinstance(payload, dict):
                diagnostics["payload_keys"] = sorted(payload.keys())
        events = self._normalize_events(payload)
        total_count = len(events)
        if isinstance(payload, dict):
            raw_total = payload.get("totalCount") or payload.get("total_count")
            if isinstance(raw_total, int):
                total_count = raw_total
        try:
            response = GCalEventsResponse.model_validate(
                {
                    "events": events,
                    "totalCount": total_count,
                }
            )
        except Exception:
            response = GCalEventsResponse(events=[], totalCount=0)
        immovables = self._immovables_from_response(response=response, day=day, tz=tz)
        if diagnostics is not None:
            diagnostics["raw_event_count"] = len(response.events)
            diagnostics["immovable_count"] = len(immovables)
        return CalendarDaySnapshot(response=response, immovables=immovables)

    async def list_day_immovables(
        self,
        *,
        calendar_id: str,
        day: date,
        tz: ZoneInfo,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Fetch a day's immovables from the MCP calendar server."""
        snapshot = await self.list_day_snapshot(
            calendar_id=calendar_id,
            day=day,
            tz=tz,
            diagnostics=diagnostics,
        )
        return snapshot.immovables

    async def close(self) -> None:
        """Close the underlying MCP workbench when supported."""
        await self._workbench.stop()
