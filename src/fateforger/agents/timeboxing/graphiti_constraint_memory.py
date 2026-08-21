"""Graphiti-backed durable constraint memory adapter.

This adapter keeps the durable-constraint contract used by the timeboxing
orchestrator, but routes storage/query operations through a Graphiti MCP server
instead of the legacy local JSON fallback.
"""

from __future__ import annotations

import json
from typing import Any

from autogen_core.memory import MemoryContent, MemoryQueryResult

from fateforger.core.config import settings

from .constraint_record_memory import ConstraintRecordMemoryClient


def _tool_result_to_json(result: Any) -> Any:
    if isinstance(result, (dict, list)):
        return result

    to_text = getattr(result, "to_text", None)
    if callable(to_text):
        text = (to_text() or "").strip()
    else:
        text = str(result or "").strip()
    if not text:
        return []
    return json.loads(text)


class _GraphitiMcpMemoryBackend:
    """Minimal Graphiti MCP backend exposing the add/query contract we need."""

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        limit: int,
        timeout: float = 10.0,
        workbench: Any | None = None,
    ) -> None:
        self._server_url = str(server_url or "").strip()
        self._user_id = str(user_id or "").strip() or "timeboxing"
        self._limit = max(1, int(limit))
        self._timeout = float(timeout)
        self._workbench = workbench

    @property
    def server_url(self) -> str:
        return self._server_url

    def _ensure_workbench(self) -> Any:
        if self._workbench is not None:
            return self._workbench
        from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams

        self._workbench = McpWorkbench(
            StreamableHttpServerParams(
                url=self._server_url,
                timeout=self._timeout,
            )
        )
        return self._workbench

    async def add(self, content: MemoryContent) -> None:
        metadata = dict(content.metadata or {})
        payload = {
            "name": str(metadata.get("uid") or metadata.get("name") or "memory"),
            "episode_body": str(content.content or ""),
            "source": "text",
            "source_description": json.dumps(
                metadata,
                ensure_ascii=True,
                sort_keys=True,
            ),
            "group_id": self._user_id,
        }
        workbench = self._ensure_workbench()
        await workbench.call_tool("add_memory", arguments=payload)

    async def query(self, query: str, *, limit: int = 50) -> MemoryQueryResult:
        row_limit = max(1, int(limit or self._limit))
        payload = {"group_ids": [self._user_id], "max_episodes": row_limit}
        workbench = self._ensure_workbench()
        raw = await workbench.call_tool("get_episodes", arguments=payload)
        episodes = _tool_result_to_json(raw)
        if not isinstance(episodes, list):
            episodes = []

        query_terms = [term for term in str(query or "").lower().split() if term]
        scored: list[tuple[int, str, MemoryContent]] = []
        for episode in episodes:
            if not isinstance(episode, dict):
                continue
            source_description = episode.get("source_description")
            metadata: dict[str, Any] = {}
            if isinstance(source_description, str) and source_description.strip():
                try:
                    parsed = json.loads(source_description)
                except Exception:
                    parsed = {}
                if isinstance(parsed, dict):
                    metadata = parsed
            content_text = str(
                episode.get("episode_body")
                or episode.get("content")
                or episode.get("body")
                or ""
            )
            blob = (
                content_text
                + " "
                + json.dumps(episode, ensure_ascii=True, sort_keys=True)
            ).lower()
            score = sum(1 for term in query_terms if term in blob)
            updated_at = str(
                episode.get("created_at")
                or episode.get("valid_at")
                or episode.get("timestamp")
                or ""
            )
            scored.append(
                (
                    score,
                    updated_at,
                    MemoryContent(
                        content=content_text,
                        mime_type="text/plain",
                        metadata=metadata,
                    ),
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]), reverse=False)
        return MemoryQueryResult(results=[item[2] for item in scored[:row_limit]])


class GraphitiConstraintMemoryClient(ConstraintRecordMemoryClient):
    """Graphiti backend using the durable constraint contract."""

    def __init__(
        self,
        *,
        user_id: str,
        limit: int = 200,
        server_url: str,
        timeout: float = 10.0,
        memory_backend: Any | None = None,
    ) -> None:
        backend = memory_backend or _GraphitiMcpMemoryBackend(
            server_url=server_url,
            user_id=user_id,
            limit=limit,
            timeout=timeout,
        )
        super().__init__(
            user_id=user_id,
            limit=limit,
            memory_backend=backend,
        )
        self._graphiti_server_url = server_url

    async def get_store_info(self) -> dict[str, Any]:
        return {
            "backend": "graphiti",
            "user_id": self._user_id,
            "limit": self._limit,
            "server_url": self._graphiti_server_url,
        }

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
    return GraphitiConstraintMemoryClient(
        user_id=user_id,
        limit=int(getattr(settings, "graphiti_query_limit", 200) or 200),
        server_url=str(getattr(settings, "graphiti_mcp_server_url", "") or "").strip(),
    )


__all__ = [
    "GraphitiConstraintMemoryClient",
    "_GraphitiMcpMemoryBackend",
    "build_graphiti_client_from_settings",
]
