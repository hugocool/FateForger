"""Mem0-backed durable constraint memory adapter.

This module preserves the existing durable constraint datamodel used by the
timeboxing agent while replacing the backing store/retrieval path with Mem0.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any, Sequence

from autogen_core.memory import MemoryContent, MemoryQueryResult
import jsonpatch
from pydantic import TypeAdapter, ValidationError

from fateforger.core.config import settings


def _derive_uid(constraint: dict[str, Any]) -> str:
    """Build a deterministic uid when one is not provided."""
    payload = constraint.get("payload") or {}
    applicability = constraint.get("applicability") or {}
    stable_bits = {
        "scope": constraint.get("scope"),
        "rule_kind": payload.get("rule_kind"),
        "topics": sorted(constraint.get("topics") or []),
        "description": (constraint.get("description") or "").strip(),
        "days_of_week": applicability.get("days_of_week"),
        "start_date": applicability.get("start_date"),
        "end_date": applicability.get("end_date"),
    }
    digest = hashlib.sha1(
        json.dumps(stable_bits, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"tb:{stable_bits.get('rule_kind') or 'rule'}:{digest[:12]}"


def _normalize_constraint_record(record: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Normalize nested constraint payload and return `(constraint_record, uid)`."""
    constraint = dict(record.get("constraint_record", record) or {})
    lifecycle = dict(constraint.get("lifecycle") or {})
    applicability = dict(constraint.get("applicability") or {})
    payload = dict(constraint.get("payload") or {})
    scalar_params = dict(payload.get("scalar_params") or {})
    windows = list(payload.get("windows") or [])
    uid = (
        lifecycle.get("uid")
        or constraint.get("uid")
        or _derive_uid({"payload": payload, **constraint})
    )
    lifecycle["uid"] = uid
    payload["scalar_params"] = scalar_params
    payload["windows"] = windows
    constraint["lifecycle"] = lifecycle
    constraint["applicability"] = applicability
    constraint["payload"] = payload
    return constraint, uid


def _as_iso_date(value: Any) -> str | None:
    """Normalize value into `YYYY-MM-DD` when possible."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = TypeAdapter(date).validate_python(text)
    except ValidationError:
        return None
    return parsed.isoformat()


def _parse_iso_date(value: Any) -> date | None:
    """Parse an ISO date string safely."""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        return TypeAdapter(date).validate_python(text)
    except ValidationError:
        return None


def _to_text_list(values: Any) -> list[str]:
    """Normalize optional list-like values into a list of non-empty strings."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _match_any(values: Sequence[str], expected: Sequence[str]) -> bool:
    """Return true when at least one expected value is present."""
    if not expected:
        return True
    actual = {str(value).strip().lower() for value in values if str(value).strip()}
    return any(str(value).strip().lower() in actual for value in expected)


def _apply_json_patch_document(
    *,
    document: dict[str, Any],
    operations: Any,
) -> dict[str, Any]:
    """Apply RFC6902 JSON Patch operations via jsonpatch library."""
    ops = TypeAdapter(list[dict[str, Any]]).validate_python(operations)
    try:
        patched = jsonpatch.apply_patch(document, ops, in_place=False)
    except jsonpatch.JsonPatchException as exc:
        raise ValueError(f"invalid json patch operations: {exc}") from exc
    if not isinstance(patched, dict):
        raise TypeError("json patch must produce a dict document")
    return patched


class Mem0ConstraintMemoryClient:
    """Mem0-backed constraint memory with the same API as ConstraintMemoryClient."""

    def __init__(
        self,
        *,
        user_id: str,
        limit: int = 200,
        is_cloud: bool = True,
        api_key: str | None = None,
        local_config: dict[str, Any] | None = None,
        memory_backend: Any | None = None,
    ) -> None:
        self._user_id = user_id
        self._limit = max(1, int(limit))
        self._is_cloud = bool(is_cloud)

        if memory_backend is not None:
            self._memory = memory_backend
            return

        try:
            from autogen_ext.memory.mem0 import Mem0Memory
        except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "Mem0 backend selected but Mem0 dependencies are missing. "
                "Install autogen-ext[mem0] and configure MEM0_API_KEY."
            ) from exc

        if self._is_cloud:
            self._memory = Mem0Memory(
                user_id=self._user_id,
                limit=self._limit,
                is_cloud=True,
                api_key=api_key or None,
            )
        else:
            if local_config is None:
                local_config = {"path": "./data/mem0"}
            self._memory = Mem0Memory(
                user_id=self._user_id,
                limit=self._limit,
                is_cloud=False,
                config=local_config,
            )

    @staticmethod
    def _build_content_text(constraint: dict[str, Any], uid: str) -> str:
        """Build searchable content text for Mem0 semantic retrieval."""
        applicability = constraint.get("applicability") or {}
        payload = constraint.get("payload") or {}
        scalar = payload.get("scalar_params") or {}
        topics = ", ".join(_to_text_list(constraint.get("topics")))
        stages = ", ".join(_to_text_list(constraint.get("applies_stages")))
        event_types = ", ".join(_to_text_list(constraint.get("applies_event_types")))
        return (
            f"uid:{uid}\n"
            f"name:{constraint.get('name') or ''}\n"
            f"description:{constraint.get('description') or ''}\n"
            f"rule_kind:{payload.get('rule_kind') or ''}\n"
            f"scope:{constraint.get('scope') or ''}\n"
            f"status:{constraint.get('status') or ''}\n"
            f"necessity:{constraint.get('necessity') or ''}\n"
            f"topics:{topics}\n"
            f"stages:{stages}\n"
            f"event_types:{event_types}\n"
            f"start_date:{applicability.get('start_date') or ''}\n"
            f"end_date:{applicability.get('end_date') or ''}\n"
            f"days_of_week:{', '.join(_to_text_list(applicability.get('days_of_week')))}\n"
            f"timezone:{applicability.get('timezone') or ''}\n"
            f"duration_min:{scalar.get('duration_min')}\n"
            f"duration_max:{scalar.get('duration_max')}\n"
            f"contiguity:{scalar.get('contiguity') or ''}"
        )

    @staticmethod
    def _build_metadata(constraint: dict[str, Any], uid: str) -> dict[str, Any]:
        """Build normalized metadata for deterministic filtering."""
        applicability = constraint.get("applicability") or {}
        payload = constraint.get("payload") or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        type_id = constraint.get("type_id") or payload.get("type_id") or payload.get(
            "rule_kind"
        )
        return {
            "kind": "timeboxing_constraint",
            "uid": uid,
            "name": constraint.get("name"),
            "description": constraint.get("description"),
            "necessity": constraint.get("necessity"),
            "status": constraint.get("status"),
            "source": constraint.get("source"),
            "scope": constraint.get("scope"),
            "start_date": _as_iso_date(applicability.get("start_date")),
            "end_date": _as_iso_date(applicability.get("end_date")),
            "days_of_week": _to_text_list(applicability.get("days_of_week")),
            "timezone": applicability.get("timezone"),
            "rule_kind": payload.get("rule_kind"),
            "type_id": type_id,
            "topics": _to_text_list(constraint.get("topics")),
            "applies_stages": _to_text_list(constraint.get("applies_stages")),
            "applies_event_types": _to_text_list(constraint.get("applies_event_types")),
            "confidence": constraint.get("confidence"),
            "constraint_record": constraint,
            "updated_at": now_iso,
        }

    @staticmethod
    def _event_metadata(event: dict[str, Any], uid: str) -> dict[str, Any]:
        """Normalize extraction event metadata (optional audit payload)."""
        return {
            "kind": "timeboxing_constraint_event",
            "constraint_uid": uid,
            "event": dict(event),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_store_info(self) -> dict[str, Any]:
        """Return backend metadata for diagnostics."""
        return {
            "backend": "mem0",
            "user_id": self._user_id,
            "limit": self._limit,
            "is_cloud": self._is_cloud,
        }

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add or update a constraint memory entry while preserving schema."""
        constraint, uid = _normalize_constraint_record(record)
        metadata = self._build_metadata(constraint, uid)
        content = self._build_content_text(constraint, uid)
        await self._add_memory_text(content=content, metadata=metadata)
        if event:
            event_metadata = self._event_metadata(event, uid)
            await self._add_memory_text(
                content=f"constraint_event uid:{uid}",
                metadata=event_metadata,
            )
        return {"uid": uid, "backend": "mem0"}

    async def _add_memory_text(self, *, content: str, metadata: dict[str, Any]) -> None:
        """Store one memory entry with deterministic direct-import semantics.

        Prefer direct vector-store insertion on local Mem0 clients to avoid
        threaded infer-path behavior (`infer=True`) and preserve exact payloads.
        """
        client = getattr(self._memory, "_client", None)
        user_id = str(metadata.get("user_id") or self._user_id).strip() or self._user_id
        payload_metadata = dict(metadata)
        payload_metadata.pop("user_id", None)
        if client is not None and callable(getattr(client, "_add_to_vector_store", None)):
            await asyncio.to_thread(
                client._add_to_vector_store,
                [{"role": "user", "content": content}],
                payload_metadata,
                {"user_id": user_id},
                False,
            )
            return
        await self._memory.add(
            MemoryContent(
                content=content,
                mime_type="text/plain",
                metadata={**payload_metadata, "user_id": user_id},
            )
        )

    @staticmethod
    def _serialize_constraint(metadata: dict[str, Any]) -> dict[str, Any]:
        """Convert stored metadata to the legacy constraint query payload shape."""
        return {
            "page_id": metadata.get("memory_id"),
            "url": None,
            "uid": metadata.get("uid"),
            "name": metadata.get("name"),
            "description": metadata.get("description"),
            "necessity": metadata.get("necessity"),
            "status": metadata.get("status"),
            "source": metadata.get("source"),
            "scope": metadata.get("scope"),
            "start_date": metadata.get("start_date"),
            "end_date": metadata.get("end_date"),
            "days_of_week": _to_text_list(metadata.get("days_of_week")),
            "timezone": metadata.get("timezone"),
            "rule_kind": metadata.get("rule_kind"),
            "type_id": metadata.get("type_id") or metadata.get("rule_kind"),
            "topics": _to_text_list(metadata.get("topics")),
            "confidence": metadata.get("confidence"),
            "applies_stages": _to_text_list(metadata.get("applies_stages")),
            "applies_event_types": _to_text_list(metadata.get("applies_event_types")),
        }

    @staticmethod
    def _query_text(
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None,
        tags: list[str] | None,
    ) -> str:
        """Build a broad retrieval query string for Mem0 semantic search."""
        parts: list[str] = ["timeboxing constraint preference"]
        if filters.get("text_query"):
            parts.append(str(filters["text_query"]))
        if filters.get("stage"):
            parts.append(f"stage {filters['stage']}")
        if filters.get("event_types_any"):
            parts.append("event types " + " ".join(_to_text_list(filters["event_types_any"])))
        if type_ids:
            parts.append("types " + " ".join(_to_text_list(type_ids)))
        if tags:
            parts.append("topics " + " ".join(_to_text_list(tags)))
        return " | ".join(parts)

    @staticmethod
    def _matches_filters(
        *,
        row: dict[str, Any],
        filters: dict[str, Any],
        type_ids: list[str] | None,
        tags: list[str] | None,
    ) -> bool:
        """Apply deterministic filtering on serialized rows."""
        if not isinstance(row, dict):
            return False
        if type_ids:
            allowed = {value.lower() for value in _to_text_list(type_ids)}
            row_type = str(row.get("type_id") or row.get("rule_kind") or "").lower()
            if row_type not in allowed:
                return False
        if tags and not _match_any(_to_text_list(row.get("topics")), _to_text_list(tags)):
            return False

        as_of = _parse_iso_date(filters.get("as_of")) or datetime.utcnow().date()
        require_active = bool(filters.get("require_active", True))
        if require_active:
            start = _parse_iso_date(row.get("start_date"))
            end = _parse_iso_date(row.get("end_date"))
            if start and start > as_of:
                return False
            if end and end < as_of:
                return False

        stage = str(filters.get("stage") or "").strip()
        if stage and stage not in _to_text_list(row.get("applies_stages")):
            return False

        event_types = _to_text_list(filters.get("event_types_any"))
        if event_types and not _match_any(
            _to_text_list(row.get("applies_event_types")), event_types
        ):
            return False

        scopes = _to_text_list(filters.get("scopes_any"))
        if scopes and str(row.get("scope") or "").lower() not in {
            value.lower() for value in scopes
        }:
            return False

        statuses = _to_text_list(filters.get("statuses_any"))
        if statuses and str(row.get("status") or "").lower() not in {
            value.lower() for value in statuses
        }:
            return False

        necessities = _to_text_list(filters.get("necessities_any"))
        if necessities and str(row.get("necessity") or "").lower() not in {
            value.lower() for value in necessities
        }:
            return False

        text_query = str(filters.get("text_query") or "").strip().lower()
        if text_query:
            haystack = " ".join(
                [
                    str(row.get("name") or ""),
                    str(row.get("description") or ""),
                    " ".join(_to_text_list(row.get("topics"))),
                ]
            ).lower()
            if text_query not in haystack:
                return False
        return True

    async def _search_memories(
        self,
        *,
        query_text: str,
        limit: int,
    ) -> MemoryQueryResult:
        """Run a Mem0 query with graceful fallback."""
        query_limit = max(limit, self._limit)
        result = await self._memory.query(query_text, limit=query_limit)
        return result if isinstance(result, MemoryQueryResult) else MemoryQueryResult(results=[])

    async def _latest_constraint_metadata(self, uid: str) -> dict[str, Any] | None:
        """Return the latest stored metadata payload for a constraint uid."""
        cleaned_uid = str(uid or "").strip()
        if not cleaned_uid:
            return None
        candidates: list[dict[str, Any]] = []
        for query_text in (f"uid:{cleaned_uid}", cleaned_uid, "timeboxing constraint"):
            result = await self._search_memories(
                query_text=query_text,
                limit=max(self._limit, 500),
            )
            for memory in result.results:
                metadata = dict(memory.metadata or {})
                if metadata.get("kind") != "timeboxing_constraint":
                    continue
                if str(metadata.get("uid") or "").strip() != cleaned_uid:
                    continue
                candidates.append(metadata)
            if candidates:
                break
        if not candidates:
            return None
        candidates.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return candidates[0]

    async def get_constraint(self, *, uid: str) -> dict[str, Any] | None:
        """Fetch one durable constraint record by uid."""
        metadata = await self._latest_constraint_metadata(uid)
        if not metadata:
            return None
        record = metadata.get("constraint_record")
        if not isinstance(record, dict):
            return None
        return {
            "uid": str(metadata.get("uid") or "").strip(),
            "constraint_record": dict(record),
            "metadata": metadata,
        }

    async def update_constraint(
        self,
        *,
        uid: str,
        patch: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a partial update to one durable constraint record."""
        current = await self.get_constraint(uid=uid)
        if not current:
            return {"uid": uid, "updated": False, "reason": "not_found"}
        constraint = dict(current.get("constraint_record") or {})
        applicability = dict(constraint.get("applicability") or {})
        payload = dict(constraint.get("payload") or {})
        lifecycle = dict(constraint.get("lifecycle") or {})
        updates = dict(patch or {})

        full_record = updates.get("constraint_record")
        if isinstance(full_record, dict):
            constraint = dict(full_record)
            applicability = dict(constraint.get("applicability") or {})
            payload = dict(constraint.get("payload") or {})
            lifecycle = dict(constraint.get("lifecycle") or {})
        else:
            patch_ops = updates.get("json_patch_ops")
            if patch_ops is not None:
                patched = _apply_json_patch_document(
                    document={"constraint_record": dict(constraint)},
                    operations=patch_ops,
                )
                maybe_constraint = patched.get("constraint_record")
                if isinstance(maybe_constraint, dict):
                    constraint = dict(maybe_constraint)
                    applicability = dict(constraint.get("applicability") or {})
                    payload = dict(constraint.get("payload") or {})
                    lifecycle = dict(constraint.get("lifecycle") or {})

        for key in (
            "name",
            "description",
            "necessity",
            "status",
            "source",
            "scope",
            "confidence",
            "topics",
            "applies_stages",
            "applies_event_types",
        ):
            if key in updates and updates[key] is not None:
                constraint[key] = updates[key]

        for key in ("start_date", "end_date", "days_of_week", "timezone", "recurrence"):
            if key in updates:
                applicability[key] = updates[key]

        if "rule_kind" in updates:
            payload["rule_kind"] = updates["rule_kind"]
        if "scalar_params" in updates and isinstance(updates["scalar_params"], dict):
            payload["scalar_params"] = dict(updates["scalar_params"])
        if "windows" in updates and isinstance(updates["windows"], list):
            payload["windows"] = list(updates["windows"])

        if "supersedes_uids" in updates and isinstance(updates["supersedes_uids"], list):
            lifecycle["supersedes_uids"] = list(updates["supersedes_uids"])
        if "ttl_days" in updates:
            lifecycle["ttl_days"] = updates["ttl_days"]

        lifecycle["uid"] = str(lifecycle.get("uid") or uid).strip()
        constraint["applicability"] = applicability
        constraint["payload"] = payload
        constraint["lifecycle"] = lifecycle

        upsert_event = dict(event or {})
        upsert_event.setdefault("action", "update")
        upsert_result = await self.upsert_constraint(
            record={"constraint_record": constraint},
            event=upsert_event,
        )
        return {
            "uid": str(upsert_result.get("uid") or lifecycle["uid"]),
            "updated": True,
        }

    async def archive_constraint(
        self,
        *,
        uid: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Archive (decline) one durable constraint."""
        return await self.update_constraint(
            uid=uid,
            patch={"status": "declined"},
            event={
                "action": "archive",
                "reason": (reason or "").strip() or None,
            },
        )

    async def supersede_constraint(
        self,
        *,
        uid: str,
        new_record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Supersede one constraint and archive the previous uid."""
        payload = dict(new_record or {})
        constraint = dict(payload.get("constraint_record") or payload)
        lifecycle = dict(constraint.get("lifecycle") or {})
        supersedes = list(lifecycle.get("supersedes_uids") or [])
        if uid not in supersedes:
            supersedes.append(uid)
        lifecycle["supersedes_uids"] = supersedes
        constraint["lifecycle"] = lifecycle
        upsert_event = dict(event or {})
        upsert_event.setdefault("action", "supersede")
        created = await self.upsert_constraint(
            record={"constraint_record": constraint},
            event=upsert_event,
        )
        archived = await self.archive_constraint(uid=uid, reason="superseded")
        return {
            "uid": created.get("uid"),
            "updated": bool(created.get("uid")) and bool(archived.get("updated")),
            "superseded_uid": uid,
        }

    async def add_reflection(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        """Store a lightweight reflection memory entry."""
        metadata = {
            "kind": "timeboxing_reflection",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **dict(payload or {}),
        }
        content_parts = [
            str(metadata.get("summary") or "").strip(),
            str(metadata.get("user_utterance") or "").strip(),
            str(metadata.get("stage") or "").strip(),
        ]
        content = " | ".join([part for part in content_parts if part])
        await self._add_memory_text(
            content=content or "timeboxing reflection",
            metadata=metadata,
        )
        return {"saved": True, "kind": "timeboxing_reflection"}

    @staticmethod
    def _sort_rows(
        rows: list[dict[str, Any]],
        *,
        sort: list[list[str]] | None,
    ) -> list[dict[str, Any]]:
        """Apply Notion-like sorting semantics for known fields."""
        if not sort:
            return rows
        mapping = {
            "name": "name",
            "status": "status",
            "confidence": "confidence",
            "scope": "scope",
            "rule kind": "rule_kind",
        }
        output = list(rows)
        for prop_name, direction in reversed(sort):
            key = mapping.get(str(prop_name).strip().lower())
            if not key:
                continue
            reverse = str(direction).strip().lower() in {"desc", "descending"}
            output.sort(key=lambda row: row.get(key) or "", reverse=reverse)
        return output

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query constraint rows with deterministic post-filtering."""
        query_text = self._query_text(filters=filters, type_ids=type_ids, tags=tags)
        search = await self._search_memories(query_text=query_text, limit=max(limit * 5, 100))

        deduped: dict[str, tuple[str, dict[str, Any]]] = {}
        for memory in search.results:
            metadata = dict(memory.metadata or {})
            if metadata.get("kind") != "timeboxing_constraint":
                continue
            row = self._serialize_constraint(metadata)
            if not self._matches_filters(
                row=row,
                filters=filters,
                type_ids=type_ids,
                tags=tags,
            ):
                continue
            uid = str(row.get("uid") or "").strip()
            if not uid:
                continue
            updated_at = str(metadata.get("updated_at") or "")
            current = deduped.get(uid)
            if current is None or updated_at >= current[0]:
                deduped[uid] = (updated_at, row)

        rows = [entry[1] for entry in deduped.values()]
        rows = self._sort_rows(rows, sort=sort)
        return rows[: max(0, limit)]

    async def query_types(
        self, *, stage: str | None = None, event_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Aggregate type counts from active Mem0 constraint rows."""
        filters = {
            "as_of": datetime.utcnow().date().isoformat(),
            "stage": stage,
            "event_types_any": event_types or [],
            "require_active": True,
        }
        rows = await self.query_constraints(
            filters=filters,
            type_ids=None,
            tags=None,
            sort=None,
            limit=max(self._limit, 100),
        )
        aggregates: dict[str, dict[str, Any]] = {}
        for row in rows:
            type_id = str(row.get("type_id") or row.get("rule_kind") or "").strip()
            if not type_id:
                continue
            item = aggregates.get(type_id)
            if item is None:
                item = {
                    "type_id": type_id,
                    "name": type_id,
                    "rule_shape": row.get("rule_kind"),
                    "count": 0,
                    "requires_windows": False,
                    "requires_scalars": [],
                }
                aggregates[type_id] = item
            item["count"] += 1
        ranked = sorted(
            aggregates.values(),
            key=lambda entry: int(entry.get("count") or 0),
            reverse=True,
        )
        return ranked


def build_mem0_client_from_settings(*, user_id: str) -> Mem0ConstraintMemoryClient:
    """Create a Mem0 constraint client from application settings."""
    local_config_raw = (getattr(settings, "mem0_local_config_json", "") or "").strip()
    local_config = json.loads(local_config_raw) if local_config_raw else None
    return Mem0ConstraintMemoryClient(
        user_id=user_id,
        limit=int(getattr(settings, "mem0_query_limit", 200) or 200),
        is_cloud=bool(getattr(settings, "mem0_is_cloud", True)),
        api_key=(getattr(settings, "mem0_api_key", "") or "").strip() or None,
        local_config=local_config,
    )


__all__ = ["Mem0ConstraintMemoryClient", "build_mem0_client_from_settings"]
