"""Graphiti-backed durable constraint memory adapter.

Current implementation provides a local JSON-backed temporal memory substrate
with the same contract as the legacy mem0 adapter so orchestration can switch
backend paths without changing the durable store interface.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from autogen_core.memory import MemoryContent, MemoryQueryResult

from fateforger.core.config import settings

from .mem0_constraint_memory import Mem0ConstraintMemoryClient


class _GraphitiLocalMemoryBackend:
    """Minimal local backend that mimics memory add/query behavior."""

    def __init__(self, *, path: str, user_id: str, limit: int) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._user_id = str(user_id or "").strip() or "timeboxing"
        self._limit = max(1, int(limit))
        self._lock = Lock()
        self._rows: list[dict[str, Any]] = []
        self._load()

    @property
    def path(self) -> str:
        return str(self._path)

    def _load(self) -> None:
        if not self._path.exists():
            self._rows = []
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
        self._rows = payload if isinstance(payload, list) else []

    def _persist(self) -> None:
        self._path.write_text(
            json.dumps(self._rows, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )

    async def add(self, content: MemoryContent) -> None:
        """Persist one memory row."""
        metadata = dict(content.metadata or {})
        if not metadata.get("memory_id"):
            metadata["memory_id"] = f"graphiti:{uuid4().hex}"
        now_iso = datetime.now(timezone.utc).isoformat()
        metadata.setdefault("updated_at", now_iso)
        metadata.setdefault("user_id", self._user_id)

        row = {
            "content": str(content.content or ""),
            "mime_type": str(content.mime_type or "text/plain"),
            "metadata": metadata,
            "created_at": now_iso,
        }
        with self._lock:
            self._rows.append(row)
            self._persist()

    async def query(self, query: str, *, limit: int = 50) -> MemoryQueryResult:
        """Query memories with deterministic lexical ranking."""
        query_terms = [term for term in str(query or "").lower().split() if term]
        row_limit = max(1, int(limit or self._limit))
        with self._lock:
            rows = list(self._rows)
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


class GraphitiConstraintMemoryClient(Mem0ConstraintMemoryClient):
    """Graphiti backend using the durable constraint contract."""

    def __init__(
        self,
        *,
        user_id: str,
        limit: int = 200,
        local_config: dict[str, Any] | None = None,
        memory_backend: Any | None = None,
    ) -> None:
        local = dict(local_config or {})
        path = str(local.get("path") or "./data/graphiti_memory.json")
        backend = memory_backend or _GraphitiLocalMemoryBackend(
            path=path,
            user_id=user_id,
            limit=limit,
        )
        super().__init__(
            user_id=user_id,
            limit=limit,
            is_cloud=False,
            local_config=None,
            memory_backend=backend,
        )
        self._graphiti_path = path

    async def get_store_info(self) -> dict[str, Any]:
        return {
            "backend": "graphiti",
            "user_id": self._user_id,
            "limit": self._limit,
            "path": self._graphiti_path,
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
    local_config_raw = (
        getattr(settings, "graphiti_local_config_json", "") or ""
    ).strip()
    local_config = json.loads(local_config_raw) if local_config_raw else None
    return GraphitiConstraintMemoryClient(
        user_id=user_id,
        limit=int(getattr(settings, "graphiti_query_limit", 200) or 200),
        local_config=local_config,
    )


__all__ = ["GraphitiConstraintMemoryClient", "build_graphiti_client_from_settings"]
