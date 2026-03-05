"""Durable user defaults for TaskMarshal task-source behavior."""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from fateforger.agents.timeboxing.durable_constraint_store import (
    DurableConstraintStore,
    build_durable_constraint_store,
)
from fateforger.agents.timeboxing.mcp_clients import ConstraintMemoryClient
from fateforger.agents.timeboxing.mem0_constraint_memory import (
    build_mem0_client_from_settings,
)
from fateforger.core.config import settings

logger = logging.getLogger(__name__)

_DEFAULTS_TOPIC = "taskmarshal-defaults"
_DEFAULTS_NAME_PREFIX = "taskmarshal-defaults:"
_DEFAULTS_DESC_PREFIX = "task_defaults_json:"
_TASK_DEFAULTS_BACKENDS = {
    "constraint_mcp",
    "mem0",
    "disabled",
    "inherit_timeboxing",
}


class TaskDueDefaults(BaseModel):
    """User-level due-task defaults persisted in durable memory."""

    user_id: str
    source: Literal["ticktick"] = "ticktick"
    ticktick_project_ids: list[str] = Field(default_factory=list)
    ticktick_project_names: list[str] = Field(default_factory=list)
    configured_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class TaskDefaultsMemoryStore:
    """Adapter that stores task defaults in the shared durable-memory backend."""

    timeout_s: float = 10.0
    _backend_mode_lock: ClassVar[threading.Lock] = threading.Lock()
    _reported_fallback_modes: ClassVar[set[tuple[str, str, str]]] = set()
    _reported_durable_modes: ClassVar[set[tuple[str, str]]] = set()

    def __post_init__(self) -> None:
        self._client: Any | None = None
        self._store: DurableConstraintStore | None = None
        self._backend = self._resolve_backend()
        self._unavailable_reason: str | None = None
        self._unavailable_reason_code: str | None = None
        self._fallback_path = Path(
            os.getenv("TASKS_DEFAULTS_CACHE_PATH", "logs/taskmarshal_defaults_cache.json")
        )
        self._cache_lock = threading.Lock()
        self._cache_mtime_ns: int | None = None
        self._local_defaults_by_user: dict[str, TaskDueDefaults] = {}
        self._refresh_local_cache_from_disk(force=True)

    def _ensure_store(self) -> DurableConstraintStore | None:
        if self._store is not None:
            return self._store
        if self._unavailable_reason:
            return None
        if self._backend == "disabled":
            self._unavailable_reason = "tasks defaults durable backend disabled by configuration"
            self._unavailable_reason_code = "backend_disabled"
            return None
        try:
            if self._backend == "constraint_mcp":
                self._client = ConstraintMemoryClient(timeout=self.timeout_s)
            elif self._backend == "mem0":
                user_id = (
                    str(getattr(settings, "mem0_user_id", "") or "").strip()
                    or "timeboxing"
                )
                self._client = build_mem0_client_from_settings(user_id=user_id)
            else:
                raise ValueError(f"Unsupported tasks defaults memory backend: {self._backend}")
            self._store = build_durable_constraint_store(self._client)
            self._unavailable_reason = None
            self._unavailable_reason_code = None
        except Exception as exc:
            self._mark_store_unavailable(exc)
            return None
        return self._store

    async def get_user_defaults(self, *, user_id: str) -> TaskDueDefaults | None:
        """Fetch the latest task defaults for one user."""
        cached = self._local_defaults_by_user.get(user_id)
        if cached is not None:
            return cached
        store = self._ensure_store()
        if not store:
            self._report_backend_mode(
                user_id=user_id,
                operation="get_user_defaults",
                mode="fallback_cache",
            )
            self._refresh_local_cache_from_disk()
            return self._local_defaults_by_user.get(user_id)
        self._report_backend_mode(
            user_id=user_id,
            operation="get_user_defaults",
            mode="durable",
        )
        uid = self._uid_for_user(user_id)
        try:
            exact = await store.get_constraint(uid=uid)
            parsed = self._parse_entry_to_defaults(entry=exact, user_id=user_id)
            if parsed is not None:
                self._local_defaults_by_user[user_id] = parsed
                return parsed
            rows = await store.query_constraints(
                filters={
                    "require_active": False,
                    "text_query": uid,
                    "scopes_any": ["profile"],
                },
                tags=[_DEFAULTS_TOPIC],
                sort=[["Status", "descending"]],
                limit=20,
            )
        except Exception as exc:
            self._mark_store_unavailable(exc)
            self._report_backend_mode(
                user_id=user_id,
                operation="get_user_defaults",
                mode="fallback_cache",
            )
            self._refresh_local_cache_from_disk()
            return self._local_defaults_by_user.get(user_id)
        for row in rows:
            parsed = self._parse_entry_to_defaults(entry=row, user_id=user_id)
            if parsed is not None:
                self._local_defaults_by_user[user_id] = parsed
                return parsed
        return None

    async def upsert_user_defaults(self, defaults: TaskDueDefaults) -> bool:
        """Persist task defaults for one user."""
        self._local_defaults_by_user[defaults.user_id] = defaults
        self._write_defaults_to_disk(defaults)
        store = self._ensure_store()
        if not store:
            self._report_backend_mode(
                user_id=defaults.user_id,
                operation="upsert_user_defaults",
                mode="fallback_cache",
            )
            return True
        self._report_backend_mode(
            user_id=defaults.user_id,
            operation="upsert_user_defaults",
            mode="durable",
        )
        payload = defaults.model_dump(mode="json")
        record = {
            "constraint_record": {
                "name": f"{_DEFAULTS_NAME_PREFIX}{defaults.user_id}",
                "description": _DEFAULTS_DESC_PREFIX + json.dumps(payload, sort_keys=True),
                "necessity": "should",
                "status": "locked",
                "source": "user",
                "confidence": 1.0,
                "scope": "profile",
                "applicability": {
                    "start_date": None,
                    "end_date": None,
                    "days_of_week": [],
                    "timezone": "",
                    "recurrence": "",
                },
                "lifecycle": {
                    "uid": self._uid_for_user(defaults.user_id),
                    "supersedes_uids": [],
                    "ttl_days": None,
                },
                "payload": {
                    "rule_kind": "task_defaults",
                    "scalar_params": {"source": defaults.source},
                    "windows": [],
                },
                "applies_stages": [],
                "applies_event_types": [],
                "topics": [_DEFAULTS_TOPIC, "tasks"],
            }
        }
        try:
            result = await store.upsert_constraint(
                record=record,
                event={
                    "action": "task_defaults_upsert",
                    "agent": "tasks_agent",
                    "user_id": defaults.user_id,
                },
            )
        except Exception as exc:
            self._mark_store_unavailable(exc)
            self._report_backend_mode(
                user_id=defaults.user_id,
                operation="upsert_user_defaults",
                mode="fallback_cache",
            )
            return True
        return bool((result or {}).get("uid"))

    @staticmethod
    def _uid_for_user(user_id: str) -> str:
        return f"taskmarshal-defaults:{user_id}"

    @staticmethod
    def _resolve_backend() -> str:
        configured = str(
            getattr(settings, "tasks_defaults_memory_backend", "constraint_mcp") or ""
        ).strip().lower()
        if not configured:
            configured = "constraint_mcp"
        if configured == "inherit_timeboxing":
            configured = str(
                getattr(settings, "timeboxing_memory_backend", "constraint_mcp") or ""
            ).strip().lower() or "constraint_mcp"
        if configured not in _TASK_DEFAULTS_BACKENDS:
            return "disabled"
        if configured == "inherit_timeboxing":
            return "constraint_mcp"
        return configured

    @staticmethod
    def _classify_unavailable_reason(exc: Exception) -> str:
        message = str(exc).lower()
        if "openai_api_key" in message:
            return "missing_openai_api_key"
        if "mem0_api_key" in message:
            return "missing_mem0_api_key"
        if "mem0 backend requires" in message:
            return "missing_mem0_runtime_config"
        if "notion_timeboxing_parent_page_id" in message:
            return "missing_notion_parent_page_id"
        if "notion_token" in message:
            return "missing_notion_token"
        if isinstance(exc, ValueError):
            return "invalid_backend_config"
        return "backend_unavailable"

    def _mark_store_unavailable(self, exc: Exception) -> None:
        self._store = None
        self._client = None
        self._unavailable_reason = f"{type(exc).__name__}: {exc}"
        self._unavailable_reason_code = self._classify_unavailable_reason(exc)

    def _report_backend_mode(
        self,
        *,
        user_id: str,
        operation: str,
        mode: Literal["durable", "fallback_cache"],
    ) -> None:
        clean_user_id = str(user_id or "").strip()
        if not clean_user_id:
            return
        if mode == "durable":
            key = (self._backend, clean_user_id)
            with self._backend_mode_lock:
                if key in self._reported_durable_modes:
                    return
                self._reported_durable_modes.add(key)
            logger.info(
                "Task defaults backend mode=durable backend=%s user_id=%s operation=%s",
                self._backend,
                clean_user_id,
                operation,
            )
            return

        reason_code = self._unavailable_reason_code or "backend_unavailable"
        key = (self._backend, reason_code, clean_user_id)
        with self._backend_mode_lock:
            if key in self._reported_fallback_modes:
                return
            self._reported_fallback_modes.add(key)
        logger.warning(
            "Task defaults backend mode=fallback_cache backend=%s reason_code=%s user_id=%s operation=%s reason=%s",
            self._backend,
            reason_code,
            clean_user_id,
            operation,
            self._unavailable_reason or "n/a",
        )

    @staticmethod
    def _parse_entry_to_defaults(
        *,
        entry: dict[str, Any] | None,
        user_id: str,
    ) -> TaskDueDefaults | None:
        if not isinstance(entry, dict):
            return None
        record = entry.get("constraint_record")
        source = record if isinstance(record, dict) else entry
        name = str(source.get("name") or "").strip()
        if name and name != f"{_DEFAULTS_NAME_PREFIX}{user_id}":
            return None
        description = str(source.get("description") or "").strip()
        if description.startswith(_DEFAULTS_DESC_PREFIX):
            description = description[len(_DEFAULTS_DESC_PREFIX) :].strip()
        if not description:
            return None
        try:
            payload = json.loads(description)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        payload.setdefault("user_id", user_id)
        try:
            return TaskDueDefaults.model_validate(payload)
        except Exception:
            return None

    def _refresh_local_cache_from_disk(self, *, force: bool = False) -> None:
        try:
            stat = self._fallback_path.stat()
        except FileNotFoundError:
            return
        except Exception:
            return
        if not force and self._cache_mtime_ns == stat.st_mtime_ns:
            return
        try:
            payload = json.loads(self._fallback_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        loaded: dict[str, TaskDueDefaults] = {}
        for user_id, raw in payload.items():
            if not isinstance(raw, dict):
                continue
            raw.setdefault("user_id", str(user_id))
            try:
                loaded[str(user_id)] = TaskDueDefaults.model_validate(raw)
            except Exception:
                continue
        if loaded:
            self._local_defaults_by_user.update(loaded)
        self._cache_mtime_ns = stat.st_mtime_ns

    def _write_defaults_to_disk(self, defaults: TaskDueDefaults) -> None:
        try:
            self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        with self._cache_lock:
            disk_payload: dict[str, Any] = {}
            if self._fallback_path.exists():
                try:
                    parsed = json.loads(self._fallback_path.read_text(encoding="utf-8"))
                    if isinstance(parsed, dict):
                        disk_payload = parsed
                except Exception:
                    disk_payload = {}
            disk_payload[defaults.user_id] = defaults.model_dump(mode="json")
            tmp_path = self._fallback_path.with_suffix(self._fallback_path.suffix + ".tmp")
            try:
                tmp_path.write_text(
                    json.dumps(disk_payload, ensure_ascii=True, sort_keys=True, indent=2),
                    encoding="utf-8",
                )
                tmp_path.replace(self._fallback_path)
                self._cache_mtime_ns = self._fallback_path.stat().st_mtime_ns
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass


__all__ = ["TaskDueDefaults", "TaskDefaultsMemoryStore"]
