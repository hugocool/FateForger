"""Backend-agnostic durable constraint store adapter.

This module wraps the concrete durable-memory clients (Mem0, etc.)
behind one small interface so orchestration code can stay backend-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from deepdiff import DeepDiff
import jsonpatch
from pydantic import TypeAdapter, ValidationError


_DECLINED_STATUS = "declined"
_STATUS_RANK = {
    "locked": 0,
    "proposed": 1,
    _DECLINED_STATUS: 2,
}
_NECESSITY_RANK = {
    "must": 0,
    "should": 1,
    "prefer": 2,
}


def _to_text(value: Any) -> str:
    """Normalize arbitrary values into trimmed text."""
    return str(value or "").strip()


def _to_list(values: Any) -> list[Any]:
    """Normalize list-like values into a concrete list."""
    if values is None:
        return []
    if isinstance(values, list):
        return list(values)
    if isinstance(values, tuple):
        return list(values)
    if isinstance(values, set):
        return list(values)
    return [values]


def _constraint_record(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a normalized constraint_record view from payload."""
    if not isinstance(payload, dict):
        return {}
    record = payload.get("constraint_record")
    if isinstance(record, dict):
        return dict(record)
    return dict(payload)


def _normalize_windows(values: Any) -> list[tuple[str, str, str]]:
    """Normalize window definitions for semantic matching."""
    out: list[tuple[str, str, str]] = []
    for item in _to_list(values):
        if not isinstance(item, dict):
            continue
        out.append(
            (
                _to_text(item.get("kind")).lower(),
                _to_text(item.get("start_time_local")),
                _to_text(item.get("end_time_local")),
            )
        )
    return sorted(out)


def _normalize_time(text: str) -> str:
    """Normalize a local HH:MM-like text to strict HH:MM when possible."""
    candidate = _to_text(text)
    if not candidate:
        return ""
    parts = candidate.split(":")
    if len(parts) < 2:
        return candidate
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return candidate
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return candidate
    return f"{hour:02d}:{minute:02d}"


def _window_key(item: tuple[str, str, str]) -> tuple[str, str, str]:
    """Canonical window key with normalized times."""
    kind, start, end = item
    return kind, _normalize_time(start), _normalize_time(end)


def _window_overlap_score(
    lhs: list[tuple[str, str, str]],
    rhs: list[tuple[str, str, str]],
) -> int:
    """Score window overlap for near-duplicate fallback matching."""
    if not lhs or not rhs:
        return 0
    lhs_set = {_window_key(item) for item in lhs}
    rhs_set = {_window_key(item) for item in rhs}
    if lhs_set == rhs_set:
        return 4
    if lhs_set.intersection(rhs_set):
        return 2
    return 0


def _normalize_scalar_params(values: Any) -> dict[str, Any]:
    """Keep scalar payload fields in deterministic order."""
    if not isinstance(values, dict):
        return {}
    keys = ("duration_min", "duration_max", "contiguity")
    return {key: values[key] for key in keys if key in values}


def _semantic_identity(constraint: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build a stable semantic identity for duplicate detection."""
    applicability = constraint.get("applicability")
    if not isinstance(applicability, dict):
        applicability = {}
    payload = constraint.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    identity = {
        "name": _to_text(constraint.get("name")).lower(),
        "scope": _to_text(constraint.get("scope")).lower(),
        "rule_kind": _to_text(payload.get("rule_kind")).lower(),
        "windows": _normalize_windows(payload.get("windows")),
        "scalar_params": _normalize_scalar_params(payload.get("scalar_params")),
        "days_of_week": sorted(_to_text(value).upper() for value in _to_list(applicability.get("days_of_week")) if _to_text(value)),
        "start_date": _to_text(applicability.get("start_date")),
        "end_date": _to_text(applicability.get("end_date")),
        "timezone": _to_text(applicability.get("timezone")),
        "recurrence": _to_text(applicability.get("recurrence")),
        "topics": sorted(_to_text(value).lower() for value in _to_list(constraint.get("topics")) if _to_text(value)),
        "applies_stages": sorted(_to_text(value) for value in _to_list(constraint.get("applies_stages")) if _to_text(value)),
        "applies_event_types": sorted(_to_text(value) for value in _to_list(constraint.get("applies_event_types")) if _to_text(value)),
    }
    return str(identity), identity


def _semantic_similarity_score(
    lhs: dict[str, Any],
    rhs: dict[str, Any],
) -> int:
    """Score semantic similarity for near-duplicate fallback resolution."""
    score = 0
    if lhs.get("rule_kind") and lhs.get("rule_kind") == rhs.get("rule_kind"):
        score += 3
    if lhs.get("scope") and lhs.get("scope") == rhs.get("scope"):
        score += 2
    if lhs.get("name") and lhs.get("name") == rhs.get("name"):
        score += 2

    lhs_topics = set(lhs.get("topics") or [])
    rhs_topics = set(rhs.get("topics") or [])
    if lhs_topics and rhs_topics:
        if lhs_topics == rhs_topics:
            score += 3
        elif lhs_topics.intersection(rhs_topics):
            score += 1

    lhs_stages = set(lhs.get("applies_stages") or [])
    rhs_stages = set(rhs.get("applies_stages") or [])
    if lhs_stages and rhs_stages and lhs_stages.intersection(rhs_stages):
        score += 1

    lhs_events = set(lhs.get("applies_event_types") or [])
    rhs_events = set(rhs.get("applies_event_types") or [])
    if lhs_events and rhs_events and lhs_events.intersection(rhs_events):
        score += 1

    lhs_days = set(lhs.get("days_of_week") or [])
    rhs_days = set(rhs.get("days_of_week") or [])
    if lhs_days and rhs_days and lhs_days.intersection(rhs_days):
        score += 1

    if lhs.get("timezone") and lhs.get("timezone") == rhs.get("timezone"):
        score += 1
    if lhs.get("recurrence") and lhs.get("recurrence") == rhs.get("recurrence"):
        score += 1

    score += _window_overlap_score(lhs.get("windows") or [], rhs.get("windows") or [])
    return score


def _updated_at_epoch(metadata: dict[str, Any]) -> float:
    """Return an epoch score for ordering the latest canonical record."""
    text = _to_text(metadata.get("updated_at"))
    if not text:
        return 0.0
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _candidate_rank(entry: dict[str, Any]) -> tuple[int, int, float, str]:
    """Sort candidates by strongest active record then most recent."""
    constraint = entry.get("constraint_record") or {}
    metadata = entry.get("metadata") or {}
    status = _to_text(constraint.get("status")).lower()
    necessity = _to_text(constraint.get("necessity")).lower()
    return (
        _STATUS_RANK.get(status, 3),
        _NECESSITY_RANK.get(necessity, 3),
        -_updated_at_epoch(metadata),
        _to_text(entry.get("uid")),
    )


class DurableConstraintStore(Protocol):
    """Common read/write contract used by timeboxing orchestration."""

    async def get_store_info(self) -> dict[str, Any]:
        """Return backend diagnostics."""

    async def query_types(
        self, *, stage: str | None = None, event_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Query ranked constraint types."""

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query constraint rows."""

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update one durable constraint."""

    async def get_constraint(self, *, uid: str) -> dict[str, Any] | None:
        """Get one constraint record by uid."""

    async def update_constraint(
        self,
        *,
        uid: str,
        patch: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update one constraint by uid."""

    async def archive_constraint(
        self,
        *,
        uid: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Archive one constraint by uid."""

    async def supersede_constraint(
        self,
        *,
        uid: str,
        new_record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Supersede one existing constraint with a new record."""

    async def find_equivalent_constraint(
        self,
        *,
        record: dict[str, Any],
        limit: int = 200,
    ) -> dict[str, Any] | None:
        """Find the best semantic equivalent already stored."""

    async def dedupe_constraints(
        self,
        *,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Archive duplicate semantic constraints and keep one canonical copy."""

    async def add_reflection(
        self,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist a lightweight reflection memory entry."""


@dataclass
class ClientBackedDurableConstraintStore:
    """Thin adapter around existing MCP/Mem0 client implementations."""

    client: Any

    async def get_store_info(self) -> dict[str, Any]:
        getter = getattr(self.client, "get_store_info", None)
        if callable(getter):
            return await getter()
        return {"backend": "unknown", "reason": "missing_get_store_info"}

    async def query_types(
        self, *, stage: str | None = None, event_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        resolver = getattr(self.client, "query_types", None)
        if callable(resolver):
            return await resolver(stage=stage, event_types=event_types)
        return []

    async def query_constraints(
        self,
        *,
        filters: dict[str, Any],
        type_ids: list[str] | None = None,
        tags: list[str] | None = None,
        sort: list[list[str]] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        resolver = getattr(self.client, "query_constraints", None)
        if callable(resolver):
            return await resolver(
                filters=filters,
                type_ids=type_ids,
                tags=tags,
                sort=sort,
                limit=limit,
            )
        return []

    async def upsert_constraint(
        self,
        *,
        record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.client.upsert_constraint(record=record, event=event)

    async def get_constraint(self, *, uid: str) -> dict[str, Any] | None:
        getter = getattr(self.client, "get_constraint", None)
        if callable(getter):
            return await getter(uid=uid)
        rows = await self.query_constraints(
            filters={"require_active": False, "text_query": str(uid or "").strip()},
            type_ids=None,
            tags=None,
            sort=[["Status", "descending"]],
            limit=200,
        )
        for row in rows:
            if str(row.get("uid") or "").strip() == str(uid or "").strip():
                return {"uid": uid, "constraint_record": dict(row), "metadata": dict(row)}
        return None

    async def update_constraint(
        self,
        *,
        uid: str,
        patch: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updater = getattr(self.client, "update_constraint", None)
        if callable(updater):
            return await updater(uid=uid, patch=patch, event=event)
        return {"uid": uid, "updated": False, "reason": "unsupported_backend"}

    async def archive_constraint(
        self,
        *,
        uid: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        archiver = getattr(self.client, "archive_constraint", None)
        if callable(archiver):
            return await archiver(uid=uid, reason=reason)
        return await self.update_constraint(
            uid=uid,
            patch={"status": "declined"},
            event={"action": "archive", "reason": reason},
        )

    async def supersede_constraint(
        self,
        *,
        uid: str,
        new_record: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = await self.get_constraint(uid=uid)
        if not current:
            return {"uid": uid, "updated": False, "reason": "not_found"}
        payload = dict(new_record or {})
        constraint_record = dict(payload.get("constraint_record") or payload)
        lifecycle = dict(constraint_record.get("lifecycle") or {})
        supersedes = list(lifecycle.get("supersedes_uids") or [])
        if uid not in supersedes:
            supersedes.append(uid)
        lifecycle["supersedes_uids"] = supersedes
        constraint_record["lifecycle"] = lifecycle
        upsert_event = dict(event or {})
        upsert_event.setdefault("action", "supersede")
        created = await self.upsert_constraint(
            record={"constraint_record": constraint_record},
            event=upsert_event,
        )
        archived = await self.archive_constraint(uid=uid, reason="superseded")
        return {
            "uid": created.get("uid") or uid,
            "updated": bool(created.get("uid")) and bool(archived.get("updated")),
            "superseded_uid": uid,
            "new_uid": created.get("uid"),
        }

    async def find_equivalent_constraint(
        self,
        *,
        record: dict[str, Any],
        limit: int = 200,
    ) -> dict[str, Any] | None:
        """Return the strongest existing semantic match for a record."""
        constraint = _constraint_record(record)
        if not constraint:
            return None
        _, identity = _semantic_identity(constraint)
        search_name = _to_text(constraint.get("name"))
        rows = await self.query_constraints(
            filters={
                "require_active": False,
                "text_query": search_name or None,
            },
            type_ids=None,
            tags=_to_list(constraint.get("topics")) or None,
            sort=[["Status", "descending"]],
            limit=max(1, int(limit)),
        )
        if not rows and search_name:
            rows = await self.query_constraints(
                filters={"require_active": False},
                type_ids=None,
                tags=_to_list(constraint.get("topics")) or None,
                sort=[["Status", "descending"]],
                limit=max(1, int(limit)),
            )

        exact_matches: list[dict[str, Any]] = []
        near_matches: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            uid = _to_text(row.get("uid"))
            if not uid:
                continue
            full = await self.get_constraint(uid=uid)
            candidate_record = _constraint_record(full or row)
            _, candidate_identity = _semantic_identity(candidate_record)
            if candidate_identity != identity:
                similarity = _semantic_similarity_score(identity, candidate_identity)
                if similarity < 8:
                    continue
                near_matches.append(
                    (
                        similarity,
                        {
                            "uid": uid,
                            "constraint_record": candidate_record,
                            "metadata": dict((full or {}).get("metadata") or row),
                        },
                    )
                )
                continue
            exact_matches.append(
                {
                    "uid": uid,
                    "constraint_record": candidate_record,
                    "metadata": dict((full or {}).get("metadata") or row),
                }
            )
        if exact_matches:
            exact_matches.sort(key=_candidate_rank)
            return exact_matches[0]
        if not near_matches:
            return None
        near_matches.sort(
            key=lambda item: (
                -item[0],
                *_candidate_rank(item[1]),
            )
        )
        return near_matches[0][1]

    async def dedupe_constraints(
        self,
        *,
        limit: int = 500,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Detect and archive duplicate semantic constraints."""
        rows = await self.query_constraints(
            filters={"require_active": False},
            type_ids=None,
            tags=None,
            sort=[["Status", "descending"]],
            limit=max(1, int(limit)),
        )
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            uid = _to_text(row.get("uid"))
            if not uid:
                continue
            full = await self.get_constraint(uid=uid)
            constraint = _constraint_record(full or row)
            key, _ = _semantic_identity(constraint)
            groups.setdefault(key, []).append(
                {
                    "uid": uid,
                    "constraint_record": constraint,
                    "metadata": dict((full or {}).get("metadata") or row),
                }
            )

        duplicates_found = 0
        duplicates_archived = 0
        canonical_updates = 0
        failed_archives = 0
        merged_groups: list[dict[str, Any]] = []
        for entries in groups.values():
            if len(entries) <= 1:
                continue
            ranked = sorted(entries, key=_candidate_rank)
            canonical = ranked[0]
            duplicates = ranked[1:]
            duplicate_uids = [_to_text(item.get("uid")) for item in duplicates if _to_text(item.get("uid"))]
            if not duplicate_uids:
                continue
            duplicates_found += len(duplicate_uids)
            merged_groups.append(
                {
                    "canonical_uid": _to_text(canonical.get("uid")),
                    "duplicate_uids": duplicate_uids,
                }
            )
            if dry_run:
                continue

            canonical_record = dict(canonical.get("constraint_record") or {})
            lifecycle = dict(canonical_record.get("lifecycle") or {})
            supersedes = [_to_text(value) for value in _to_list(lifecycle.get("supersedes_uids")) if _to_text(value)]
            for duplicate_uid in duplicate_uids:
                if duplicate_uid not in supersedes:
                    supersedes.append(duplicate_uid)
            if supersedes:
                update_result = await self.update_constraint(
                    uid=_to_text(canonical.get("uid")),
                    patch={"supersedes_uids": supersedes},
                    event={
                        "action": "dedupe_merge",
                        "dedupe_archived_uids": duplicate_uids,
                    },
                )
                if update_result.get("updated"):
                    canonical_updates += 1

            for duplicate_uid in duplicate_uids:
                archive_result = await self.archive_constraint(
                    uid=duplicate_uid,
                    reason=f"dedupe:canonical:{_to_text(canonical.get('uid'))}",
                )
                if archive_result.get("updated"):
                    duplicates_archived += 1
                else:
                    failed_archives += 1

        return {
            "scanned": len(rows),
            "duplicate_groups": len(merged_groups),
            "duplicates_found": duplicates_found,
            "duplicates_archived": duplicates_archived,
            "canonical_updates": canonical_updates,
            "failed_archives": failed_archives,
            "dry_run": bool(dry_run),
            "groups": merged_groups,
        }

    @staticmethod
    def merge_constraint_records(
        *,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two semantically-equivalent records deterministically."""
        merged = dict(current or {})

        for key in ("name", "description", "scope", "source", "rationale"):
            value = _to_text(incoming.get(key))
            if value:
                merged[key] = value

        current_status = _to_text(current.get("status")).lower()
        incoming_status = _to_text(incoming.get("status")).lower()
        if _STATUS_RANK.get(incoming_status, 99) <= _STATUS_RANK.get(current_status, 99):
            merged["status"] = incoming_status or current_status

        current_necessity = _to_text(current.get("necessity")).lower()
        incoming_necessity = _to_text(incoming.get("necessity")).lower()
        if _NECESSITY_RANK.get(incoming_necessity, 99) <= _NECESSITY_RANK.get(
            current_necessity, 99
        ):
            merged["necessity"] = incoming_necessity or current_necessity

        confidence_candidates = [
            value
            for value in (current.get("confidence"), incoming.get("confidence"))
            if isinstance(value, (int, float))
        ]
        if confidence_candidates:
            merged["confidence"] = float(max(confidence_candidates))

        for list_key in ("topics", "applies_stages", "applies_event_types"):
            merged[list_key] = sorted(
                {
                    _to_text(item)
                    for item in [*(_to_list(current.get(list_key))), *(_to_list(incoming.get(list_key)))]
                    if _to_text(item)
                }
            )

        lifecycle = dict(current.get("lifecycle") or {})
        incoming_lifecycle = dict(incoming.get("lifecycle") or {})
        supersedes_uids = [
            _to_text(item)
            for item in [
                *(_to_list(lifecycle.get("supersedes_uids"))),
                *(_to_list(incoming_lifecycle.get("supersedes_uids"))),
            ]
            if _to_text(item)
        ]
        if supersedes_uids:
            lifecycle["supersedes_uids"] = sorted(set(supersedes_uids))
        if incoming_lifecycle.get("ttl_days") is not None:
            lifecycle["ttl_days"] = incoming_lifecycle.get("ttl_days")
        if lifecycle:
            merged["lifecycle"] = lifecycle

        current_applicability = dict(current.get("applicability") or {})
        incoming_applicability = dict(incoming.get("applicability") or {})
        applicability = dict(current_applicability)
        for key in ("start_date", "end_date", "timezone", "recurrence"):
            value = incoming_applicability.get(key)
            if _to_text(value):
                applicability[key] = value
        days = [
            _to_text(item).upper()
            for item in [
                *(_to_list(current_applicability.get("days_of_week"))),
                *(_to_list(incoming_applicability.get("days_of_week"))),
            ]
            if _to_text(item)
        ]
        if days:
            applicability["days_of_week"] = sorted(set(days))
        if applicability:
            merged["applicability"] = applicability

        current_payload = dict(current.get("payload") or {})
        incoming_payload = dict(incoming.get("payload") or {})
        payload = dict(current_payload)
        if _to_text(incoming_payload.get("rule_kind")):
            payload["rule_kind"] = incoming_payload.get("rule_kind")
        scalar = dict(current_payload.get("scalar_params") or {})
        scalar.update(
            {
                key: value
                for key, value in dict(incoming_payload.get("scalar_params") or {}).items()
                if value is not None
            }
        )
        if scalar:
            payload["scalar_params"] = scalar
        incoming_windows = _normalize_windows(incoming_payload.get("windows"))
        current_windows = _normalize_windows(current_payload.get("windows"))
        if incoming_windows:
            payload["windows"] = [
                {
                    "kind": kind,
                    "start_time_local": start,
                    "end_time_local": end,
                }
                for kind, start, end in sorted(set([*current_windows, *incoming_windows]))
            ]
        if payload:
            merged["payload"] = payload
        return merged

    @staticmethod
    def build_constraint_json_patch_ops(
        *,
        current: dict[str, Any],
        merged: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build RFC6902 patch ops for constraint updates."""
        diff = DeepDiff(current, merged, ignore_order=True)
        if not diff:
            return []
        try:
            raw_ops = jsonpatch.make_patch(
                {"constraint_record": dict(current or {})},
                {"constraint_record": dict(merged or {})},
            ).patch
            patch_ops = TypeAdapter(list[dict[str, Any]]).validate_python(raw_ops)
        except (ValidationError, TypeError, ValueError, jsonpatch.JsonPatchException):
            return [{"op": "replace", "path": "/constraint_record", "value": merged}]
        return patch_ops or [{"op": "replace", "path": "/constraint_record", "value": merged}]

    async def add_reflection(
        self,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        adder = getattr(self.client, "add_reflection", None)
        if callable(adder):
            return await adder(payload=payload)
        return {"saved": False, "reason": "unsupported_backend"}


def build_durable_constraint_store(client: Any | None) -> DurableConstraintStore | None:
    """Create an adapter around a concrete durable-memory client."""
    if client is None:
        return None
    return ClientBackedDurableConstraintStore(client=client)


__all__ = [
    "DurableConstraintStore",
    "ClientBackedDurableConstraintStore",
    "build_durable_constraint_store",
]
