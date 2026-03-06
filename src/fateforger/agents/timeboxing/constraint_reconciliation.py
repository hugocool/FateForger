"""Constraint reconciliation and applicability filtering utilities."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from typing import Any


_STATUS_RANK = {"locked": 0, "proposed": 1, "declined": 2}
_NECESSITY_RANK = {"must": 0, "should": 1, "prefer": 2}
_WEEKDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


@dataclass(frozen=True, slots=True)
class ReconciledConstraintRows:
    """Deterministic reconciliation result for durable constraint rows."""

    raw_count: int
    canonical_count: int
    applicable_count: int
    duplicate_groups: list[dict[str, Any]]
    canonical_rows: list[dict[str, Any]]
    applicable_rows: list[dict[str, Any]]


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _to_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _parse_iso_date(value: Any) -> date | None:
    text = _to_text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_iso_ts(value: Any) -> float:
    text = _to_text(value)
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _normalize_windows(values: Any) -> list[tuple[str, str, str]]:
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


def _record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("constraint_record")
    if isinstance(nested, dict):
        return dict(nested)
    applicability = {
        "start_date": row.get("start_date"),
        "end_date": row.get("end_date"),
        "days_of_week": list(_to_list(row.get("days_of_week"))),
        "timezone": row.get("timezone"),
        "recurrence": row.get("recurrence"),
    }
    payload = {
        "rule_kind": row.get("rule_kind") or row.get("type_id"),
        "windows": list(_to_list(row.get("windows"))),
        "scalar_params": dict(row.get("scalar_params") or {}),
    }
    lifecycle = {"uid": row.get("uid")}
    return {
        "name": row.get("name"),
        "description": row.get("description"),
        "necessity": row.get("necessity"),
        "status": row.get("status"),
        "source": row.get("source"),
        "scope": row.get("scope"),
        "topics": list(_to_list(row.get("topics"))),
        "confidence": row.get("confidence"),
        "applies_stages": list(_to_list(row.get("applies_stages"))),
        "applies_event_types": list(_to_list(row.get("applies_event_types"))),
        "aspect_classification": row.get("aspect_classification"),
        "applicability": applicability,
        "payload": payload,
        "lifecycle": lifecycle,
    }


def _semantic_key(record: dict[str, Any]) -> str:
    applicability = dict(record.get("applicability") or {})
    payload = dict(record.get("payload") or {})
    scalar_params = dict(payload.get("scalar_params") or {})
    key_payload = {
        "scope": _to_text(record.get("scope")).lower(),
        "rule_kind": _to_text(payload.get("rule_kind")).lower(),
        "name": _to_text(record.get("name")).lower(),
        "description": " ".join(_to_text(record.get("description")).lower().split()),
        "topics": sorted(
            _to_text(item).lower()
            for item in _to_list(record.get("topics"))
            if _to_text(item)
        ),
        "applies_stages": sorted(
            _to_text(item)
            for item in _to_list(record.get("applies_stages"))
            if _to_text(item)
        ),
        "applies_event_types": sorted(
            _to_text(item)
            for item in _to_list(record.get("applies_event_types"))
            if _to_text(item)
        ),
        "days_of_week": sorted(
            _to_text(item).upper()
            for item in _to_list(applicability.get("days_of_week"))
            if _to_text(item)
        ),
        "start_date": _to_text(applicability.get("start_date")),
        "end_date": _to_text(applicability.get("end_date")),
        "timezone": _to_text(applicability.get("timezone")),
        "recurrence": _to_text(applicability.get("recurrence")),
        "windows": _normalize_windows(payload.get("windows")),
        "duration_min": scalar_params.get("duration_min"),
        "duration_max": scalar_params.get("duration_max"),
        "contiguity": _to_text(scalar_params.get("contiguity")).lower(),
    }
    return json.dumps(key_payload, sort_keys=True, separators=(",", ":"))


def _rank_key(entry: dict[str, Any]) -> tuple[int, int, float, str]:
    record = entry["constraint_record"]
    status = _to_text(record.get("status")).lower()
    necessity = _to_text(record.get("necessity")).lower()
    updated_at = max(
        _parse_iso_ts(entry.get("updated_at")),
        _parse_iso_ts((entry.get("metadata") or {}).get("updated_at")),
    )
    return (
        _STATUS_RANK.get(status, 3),
        _NECESSITY_RANK.get(necessity, 3),
        -updated_at,
        _to_text(entry.get("uid")),
    )


def _row_from_record(entry: dict[str, Any]) -> dict[str, Any]:
    record = entry["constraint_record"]
    applicability = dict(record.get("applicability") or {})
    payload = dict(record.get("payload") or {})
    lifecycle = dict(record.get("lifecycle") or {})
    uid = _to_text(entry.get("uid")) or _to_text(lifecycle.get("uid"))
    out = {
        "uid": uid,
        "name": record.get("name"),
        "description": record.get("description"),
        "necessity": record.get("necessity"),
        "status": record.get("status"),
        "source": record.get("source"),
        "scope": record.get("scope"),
        "start_date": applicability.get("start_date"),
        "end_date": applicability.get("end_date"),
        "days_of_week": list(_to_list(applicability.get("days_of_week"))),
        "timezone": applicability.get("timezone"),
        "recurrence": applicability.get("recurrence"),
        "rule_kind": payload.get("rule_kind"),
        "type_id": record.get("type_id") or payload.get("rule_kind"),
        "topics": list(_to_list(record.get("topics"))),
        "confidence": record.get("confidence"),
        "applies_stages": list(_to_list(record.get("applies_stages"))),
        "applies_event_types": list(_to_list(record.get("applies_event_types"))),
        "aspect_classification": record.get("aspect_classification"),
        "updated_at": entry.get("updated_at"),
    }
    return {key: value for key, value in out.items() if value is not None}


def _is_applicable(
    *,
    row: dict[str, Any],
    planned_day: date,
    stage: str | None,
) -> bool:
    status = _to_text(row.get("status")).lower()
    if status and status not in {"locked", "proposed"}:
        return False
    start = _parse_iso_date(row.get("start_date"))
    end = _parse_iso_date(row.get("end_date"))
    if start and planned_day < start:
        return False
    if end and planned_day > end:
        return False
    allowed_days = {
        _to_text(item).upper()
        for item in _to_list(row.get("days_of_week"))
        if _to_text(item)
    }
    if allowed_days:
        weekday_code = _WEEKDAY_CODES[planned_day.weekday()]
        if weekday_code not in allowed_days:
            return False
    stage_value = _to_text(stage)
    applies_stages = {
        _to_text(item) for item in _to_list(row.get("applies_stages")) if _to_text(item)
    }
    if stage_value and applies_stages and stage_value not in applies_stages:
        return False
    return True


def reconcile_constraint_rows(
    *,
    rows: list[dict[str, Any]],
    planned_day: date,
    stage: str | None,
) -> ReconciledConstraintRows:
    """Canonicalize and applicability-filter raw durable rows."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    raw_count = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw_count += 1
        record = _record_from_row(row)
        lifecycle = dict(record.get("lifecycle") or {})
        uid = _to_text(row.get("uid")) or _to_text(lifecycle.get("uid"))
        entry = {
            "uid": uid,
            "constraint_record": record,
            "metadata": dict(row.get("metadata") or {}),
            "updated_at": row.get("updated_at"),
        }
        grouped.setdefault(_semantic_key(record), []).append(entry)

    duplicate_groups: list[dict[str, Any]] = []
    canonical_rows: list[dict[str, Any]] = []
    applicable_rows: list[dict[str, Any]] = []
    for entries in grouped.values():
        ranked = sorted(entries, key=_rank_key)
        canonical = ranked[0]
        canonical_row = _row_from_record(canonical)
        canonical_rows.append(canonical_row)
        duplicate_uids = [
            _to_text(item.get("uid"))
            for item in ranked[1:]
            if _to_text(item.get("uid"))
        ]
        if duplicate_uids:
            duplicate_groups.append(
                {
                    "canonical_uid": _to_text(canonical.get("uid")),
                    "duplicate_uids": duplicate_uids,
                }
            )
        if _is_applicable(row=canonical_row, planned_day=planned_day, stage=stage):
            applicable_rows.append(canonical_row)

    return ReconciledConstraintRows(
        raw_count=raw_count,
        canonical_count=len(canonical_rows),
        applicable_count=len(applicable_rows),
        duplicate_groups=duplicate_groups,
        canonical_rows=canonical_rows,
        applicable_rows=applicable_rows,
    )


__all__ = ["ReconciledConstraintRows", "reconcile_constraint_rows"]
