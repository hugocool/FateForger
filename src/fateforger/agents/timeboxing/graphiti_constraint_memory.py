"""Graphiti-backed durable constraint memory adapter.

This adapter uses Graphiti MCP as the persistence/query backend and keeps the
existing durable-memory contract used by the timeboxing agent.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from autogen_core.memory import MemoryContent, MemoryQueryResult
from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

from fateforger.core.config import settings

from .mem0_constraint_memory import Mem0ConstraintMemoryClient


class _GraphitiMcpMemoryBackend:
    """Minimal memory backend adapter over Graphiti MCP tools."""

    _RECOVERABLE_ERROR_MARKERS = (
        "timed out while waiting for response",
        "all connection attempts failed",
        "connection refused",
        "server disconnected",
    )

    def __init__(
        self,
        *,
        server_url: str,
        group_id: str,
        user_id: str,
        timeout_s: float,
        ingest_wait_s: float = 12.0,
    ) -> None:
        self._server_url = server_url
        self._group_id = group_id
        self._user_id = user_id
        self._timeout_s = timeout_s
        self._ingest_wait_s = max(0.0, float(ingest_wait_s))
        params = StreamableHttpServerParams(url=self._server_url, timeout=self._timeout_s)
        self._workbench = McpWorkbench(params)
        self._lock = asyncio.Lock()

    @staticmethod
    def _decode_tool_json(tool_name: str, result: Any) -> Any:
        if isinstance(result, (dict, list)):
            return result
        to_text = getattr(result, "to_text", None)
        text = to_text() if callable(to_text) else str(result)
        text = (text or "").strip()
        if not text:
            return {}
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
                f"graphiti tool {tool_name} returned non-JSON text: {text[:400]}"
            ) from exc

    @classmethod
    def _is_recoverable_transport_error(cls, exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return any(marker in text for marker in cls._RECOVERABLE_ERROR_MARKERS)

    async def _call_tool_json(self, tool_name: str, *, arguments: dict[str, Any]) -> Any:
        attempts = 2
        for attempt in range(1, attempts + 1):
            try:
                async with self._lock:
                    result = await self._workbench.call_tool(tool_name, arguments=arguments)
                return self._decode_tool_json(tool_name, result)
            except Exception as exc:
                if attempt >= attempts or not self._is_recoverable_transport_error(exc):
                    raise
                await asyncio.sleep(0.25 * attempt)
        raise RuntimeError(f"graphiti tool {tool_name} failed unexpectedly")

    async def _list_episodes(self, *, max_episodes: int) -> list[dict[str, Any]]:
        payload = await self._call_tool_json(
            "get_episodes",
            arguments={
                "group_ids": [self._group_id],
                "max_episodes": max(1, int(max_episodes)),
            },
        )
        episodes = payload.get("episodes") if isinstance(payload, dict) else None
        if not isinstance(episodes, list):
            return []
        return [item for item in episodes if isinstance(item, dict)]

    async def _list_memory_rows(self, *, max_records: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for episode in await self._list_episodes(max_episodes=max_records):
            content_raw = str(episode.get("content") or "").strip()
            if not content_raw:
                continue
            try:
                payload = json.loads(content_raw)
            except Exception:
                payload = {
                    "content": content_raw,
                    "mime_type": "text/plain",
                    "metadata": {},
                }
            if not isinstance(payload, dict):
                continue
            metadata = dict(payload.get("metadata") or {})
            metadata.setdefault("memory_id", str(episode.get("uuid") or "").strip())
            metadata.setdefault(
                "updated_at",
                str(episode.get("created_at") or datetime.now(timezone.utc).isoformat()),
            )
            metadata.setdefault("user_id", self._user_id)
            rows.append(
                {
                    "content": str(payload.get("content") or ""),
                    "mime_type": str(payload.get("mime_type") or "text/plain"),
                    "metadata": metadata,
                    "created_at": str(episode.get("created_at") or ""),
                    "name": str(episode.get("name") or ""),
                }
            )
        return rows

    async def status(self) -> dict[str, Any]:
        payload = await self._call_tool_json("get_status", arguments={})
        return payload if isinstance(payload, dict) else {"status": "unknown"}

    async def add(self, content: MemoryContent) -> None:
        metadata = dict(content.metadata or {})
        if not metadata.get("memory_id"):
            metadata["memory_id"] = f"graphiti:{uuid4().hex}"
        metadata.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        metadata.setdefault("user_id", self._user_id)
        row = {
            "content": str(content.content or ""),
            "mime_type": str(content.mime_type or "text/plain"),
            "metadata": metadata,
        }
        name = (
            str(metadata.get("uid") or "").strip()
            or str(metadata.get("name") or "").strip()
            or str(metadata.get("kind") or "").strip()
            or "timeboxing-memory"
        )
        await self._call_tool_json(
            "add_memory",
            arguments={
                "name": name,
                "episode_body": json.dumps(row, ensure_ascii=True, sort_keys=True),
                "group_id": self._group_id,
                "source": "json",
                "source_description": "fateforger_graphiti_memory",
            },
        )
        if self._ingest_wait_s <= 0:
            return
        wanted_memory_id = str(metadata.get("memory_id") or "").strip()
        deadline = asyncio.get_event_loop().time() + self._ingest_wait_s
        while asyncio.get_event_loop().time() < deadline:
            rows = await self._list_memory_rows(max_records=200)
            if any(
                str((row.get("metadata") or {}).get("memory_id") or "").strip()
                == wanted_memory_id
                for row in rows
            ):
                return
            await asyncio.sleep(0.4)

    async def query(self, query: str, *, limit: int = 50) -> MemoryQueryResult:
        query_terms = [term for term in str(query or "").lower().split() if term]
        row_limit = max(1, int(limit or 50))
        rows = await self._list_memory_rows(max_records=max(200, row_limit * 10))
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for row in rows:
            metadata = dict(row.get("metadata") or {})
            row_user = str(metadata.get("user_id") or "").strip()
            if row_user and row_user != self._user_id:
                continue
            blob = (
                str(row.get("content") or "")
                + " "
                + json.dumps(metadata, ensure_ascii=True, sort_keys=True)
            ).lower()
            score = 0
            for term in query_terms:
                if term in blob:
                    score += 1
            scored.append((score, str(metadata.get("updated_at") or ""), row))
        scored.sort(key=lambda item: (-item[0], item[1]), reverse=False)
        results: list[MemoryContent] = []
        for _, _, row in scored[:row_limit]:
            results.append(
                MemoryContent(
                    content=str(row.get("content") or ""),
                    mime_type=str(row.get("mime_type") or "text/plain"),
                    metadata=dict(row.get("metadata") or {}),
                )
            )
        return MemoryQueryResult(results=results)

    async def close(self) -> None:
        await self._workbench.stop()


class GraphitiConstraintMemoryClient(Mem0ConstraintMemoryClient):
    """Graphiti backend using Graphiti MCP with durable constraint contract."""

    def __init__(
        self,
        *,
        user_id: str,
        limit: int = 200,
        server_url: str,
        group_id: str,
        timeout_s: float,
    ) -> None:
        backend = _GraphitiMcpMemoryBackend(
            server_url=server_url,
            group_id=group_id,
            user_id=user_id,
            timeout_s=timeout_s,
        )
        super().__init__(
            user_id=user_id,
            limit=limit,
            is_cloud=False,
            local_config=None,
            memory_backend=backend,
        )
        self._graphiti_server_url = server_url
        self._graphiti_group_id = group_id
        self._graphiti_timeout_s = timeout_s

    async def get_store_info(self) -> dict[str, Any]:
        info = {
            "backend": "graphiti",
            "user_id": self._user_id,
            "limit": self._limit,
            "mcp_server_url": self._graphiti_server_url,
            "group_id": self._graphiti_group_id,
            "timeout_s": self._graphiti_timeout_s,
        }
        try:
            status_fn = getattr(self._memory, "status", None)
            if callable(status_fn):
                status = await status_fn()
                if isinstance(status, dict):
                    info["status"] = status
        except Exception as exc:
            info["status_error"] = f"{type(exc).__name__}: {exc}"
        return info

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        out = await super().upsert_constraint(record=record, event=event)
        out["backend"] = "graphiti"
        return out


def build_graphiti_client_from_settings(*, user_id: str) -> GraphitiConstraintMemoryClient:
    """Create a Graphiti constraint client from application settings."""
    server_url = str(getattr(settings, "graphiti_mcp_server_url", "") or "").strip()
    if not server_url:
        raise ValueError("GRAPHITI_MCP_SERVER_URL is required for graphiti backend")
    group_id = (
        str(getattr(settings, "graphiti_mcp_group_id", "") or "").strip()
        or "timeboxing"
    )
    timeout_s = float(getattr(settings, "graphiti_mcp_timeout_seconds", 15.0) or 15.0)
    return GraphitiConstraintMemoryClient(
        user_id=user_id,
        limit=int(getattr(settings, "graphiti_query_limit", 200) or 200),
        server_url=server_url,
        group_id=group_id,
        timeout_s=timeout_s,
    )


__all__ = ["GraphitiConstraintMemoryClient", "build_graphiti_client_from_settings"]
