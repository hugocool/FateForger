"""The constraint-memory MCP client.

The calendar client and the Stage-4 day snapshot that used to sit beside it
went with the legacy timeboxing agent; this is the tasks defaults-memory
backend now, and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from fateforger.tools.constraint_mcp import (
    build_constraint_server_env,
    resolve_constraint_repo_root,
)


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
            raise RuntimeError(
                f"constraint-memory tool {tool_name} returned empty text"
            )
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
                    parsed = cls._parse_json_text(tool_name, content, allow_empty=True)
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

    async def _call_tool_json(
        self, tool_name: str, *, arguments: dict[str, Any]
    ) -> Any:
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
        data = await self._call_tool_json(
            "constraint_upsert_constraint", arguments=payload
        )
        if not isinstance(data, dict):
            raise RuntimeError(
                "constraint-memory tool constraint_upsert_constraint returned non-dict JSON"
            )
        if not data.get("uid"):
            raise RuntimeError(
                "constraint-memory tool constraint_upsert_constraint returned missing uid"
            )
        return data

    async def close(self) -> None:
        """Close the underlying MCP workbench when supported."""
        await self._workbench.stop()
