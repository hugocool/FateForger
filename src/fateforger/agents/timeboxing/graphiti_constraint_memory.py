"""Graphiti-backed durable constraint memory adapter.

This adapter reuses the durable-memory contract from ``Mem0ConstraintMemoryClient``
but does not provide a local JSON fallback. Runtime configuration must point to a
real Graphiti-compatible backend (cloud or local runtime config).
"""

from __future__ import annotations

import json
from typing import Any

from fateforger.core.config import settings

from .mem0_constraint_memory import Mem0ConstraintMemoryClient

class GraphitiConstraintMemoryClient(Mem0ConstraintMemoryClient):
    """Graphiti backend using the durable constraint contract."""

    def __init__(
        self,
        *,
        user_id: str,
        limit: int = 200,
        is_cloud: bool = False,
        api_key: str | None = None,
        local_config: dict[str, Any] | None = None,
        cloud_url: str = "",
    ) -> None:
        super().__init__(
            user_id=user_id,
            limit=limit,
            is_cloud=is_cloud,
            api_key=api_key,
            local_config=local_config,
        )
        self._graphiti_is_cloud = bool(is_cloud)
        self._graphiti_cloud_url = str(cloud_url or "").strip()
        self._graphiti_local_config = dict(local_config or {})

    async def get_store_info(self) -> dict[str, Any]:
        info = {
            "backend": "graphiti",
            "user_id": self._user_id,
            "limit": self._limit,
            "is_cloud": self._graphiti_is_cloud,
        }
        if self._graphiti_is_cloud:
            if self._graphiti_cloud_url:
                info["cloud_url"] = self._graphiti_cloud_url
        elif self._graphiti_local_config:
            info["local_config_keys"] = sorted(self._graphiti_local_config.keys())
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
    local_config_raw = (
        getattr(settings, "graphiti_local_config_json", "") or ""
    ).strip()
    local_config = json.loads(local_config_raw) if local_config_raw else None
    is_cloud = bool(getattr(settings, "graphiti_is_cloud", False))
    api_key = (getattr(settings, "graphiti_api_key", "") or "").strip() or None
    cloud_url = (getattr(settings, "graphiti_cloud_url", "") or "").strip()
    return GraphitiConstraintMemoryClient(
        user_id=user_id,
        limit=int(getattr(settings, "graphiti_query_limit", 200) or 200),
        is_cloud=is_cloud,
        api_key=api_key,
        local_config=local_config,
        cloud_url=cloud_url,
    )


__all__ = ["GraphitiConstraintMemoryClient", "build_graphiti_client_from_settings"]
