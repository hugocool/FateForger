from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime
import json
import os
import re
from typing import Any

import ultimate_notion as uno
from sqlalchemy import create_engine, text

from fateforger.adapters.notion.timeboxing_preferences import (
    ConstraintQueryFilters,
    NotionConstraintStore,
    NotionPreferenceDBs,
    get_notion_session,
)
from fateforger.agents.timeboxing.mem0_constraint_memory import (
    build_mem0_client_from_settings,
)
from fateforger.core.config import settings

_DEFAULT_APPLIES_STAGES = [
    "CollectConstraints",
    "CaptureInputs",
    "Skeleton",
    "Refine",
    "ReviewCommit",
]
_DEFAULT_APPLIES_EVENT_TYPES = ["M", "C", "DW", "SW", "H", "R", "BU", "BG", "PR"]


def _option_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


def _option_list(values: Any) -> list[str]:
    if not values:
        return []
    return [str(getattr(value, "name", None) or value) for value in values]


def _date_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _normalize_enum_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "." in text:
        text = text.split(".")[-1]
    return text.lower()


def _normalize_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "." in text:
        text = text.split(".")[-1]
    day = text.upper()
    return day if day in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"} else None


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        text_value = value.strip()
        if text_value.startswith("{") or text_value.startswith("["):
            try:
                return json.loads(text_value)
            except Exception:
                return value
    return value


def _to_dict(value: Any) -> dict[str, Any]:
    parsed = _jsonish(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _to_list(value: Any) -> list[Any]:
    parsed = _jsonish(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, str):
        text_value = parsed.strip()
        return [text_value] if text_value else []
    return [parsed]


def _raw_relation_ids(page: Any, prop_name: str) -> list[str]:
    raw_candidates = [
        getattr(page, "obj_ref", None),
        getattr(page, "_obj_ref", None),
        getattr(page, "raw", None),
        getattr(page, "_raw", None),
    ]
    payload: dict[str, Any] | None = None
    for candidate in raw_candidates:
        if candidate is None:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
        if hasattr(candidate, "model_dump"):
            try:
                maybe = candidate.model_dump(mode="json")
            except Exception:
                maybe = None
            if isinstance(maybe, dict):
                payload = maybe
                break
    if not isinstance(payload, dict):
        return []
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        return []
    prop = properties.get(prop_name)
    if not isinstance(prop, dict):
        return []
    relation = prop.get("relation")
    if not isinstance(relation, list):
        return []
    out: list[str] = []
    for item in relation:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        if isinstance(rid, str) and rid.strip():
            out.append(rid.strip())
    return out


def _discover_existing_store(session: uno.Session) -> NotionConstraintStore | None:
    titles = {
        "topics_db_id": "TB Topics",
        "types_db_id": "TB Constraint Types",
        "constraints_db_id": "TB Constraints",
        "windows_db_id": "TB Constraint Windows",
        "events_db_id": "TB Constraint Events",
    }
    ids: dict[str, str] = {}
    for key, title in titles.items():
        matches = list(session.search_db(title))
        if not matches:
            return None
        ids[key] = str(matches[0].id)
    dbs = NotionPreferenceDBs(
        topics_db_id=ids["topics_db_id"],
        types_db_id=ids["types_db_id"],
        constraints_db_id=ids["constraints_db_id"],
        windows_db_id=ids["windows_db_id"],
        events_db_id=ids["events_db_id"],
    )
    return NotionConstraintStore(session, dbs)


def _resolve_notion_store(
    *,
    parent_page_id: str | None,
    notion_token: str | None,
    dbs: NotionPreferenceDBs | None,
) -> NotionConstraintStore:
    session = get_notion_session(notion_token=notion_token)
    if dbs is not None:
        return NotionConstraintStore(session, dbs)
    page_id = (parent_page_id or "").strip()
    if page_id:
        try:
            return NotionConstraintStore.from_parent_page(
                parent_page_id=page_id,
                notion=session,
                write_registry_block=False,
            )
        except Exception as exc:
            fallback = _discover_existing_store(session)
            if fallback is None:
                raise RuntimeError(
                    "Failed to initialize Notion constraint store from parent page "
                    f"{page_id}: {exc}"
                ) from exc
            return fallback
    fallback = _discover_existing_store(session)
    if fallback is None:
        raise RuntimeError(
            "Could not find TB memory databases in Notion workspace. "
            "Set NOTION_TIMEBOXING_PARENT_PAGE_ID or ensure TB databases exist."
        )
    return fallback


@dataclass(frozen=True)
class SourceSnapshot:
    constraints: list[Any]
    windows_by_constraint_id: dict[str, list[dict[str, str]]]
    topic_name_by_id: dict[str, str]
    uid_by_constraint_id: dict[str, str]
    events: list[Any]


def _load_source_snapshot(store: NotionConstraintStore) -> SourceSnapshot:
    constraints = store.query_constraints(
        ConstraintQueryFilters(as_of=date.today(), require_active=False),
        type_ids=None,
        tags=None,
        sort=None,
        limit=100_000,
    )
    uid_by_constraint_id: dict[str, str] = {}
    for page in constraints:
        uid = str(getattr(page.props, "uid", "") or "").strip()
        if uid:
            uid_by_constraint_id[str(page.id)] = uid

    topic_name_by_id: dict[str, str] = {}
    for topic_page in store.topics_db.query.execute():
        topic_name_by_id[str(topic_page.id)] = str(
            getattr(topic_page.props, "name", "") or ""
        ).strip()

    windows_by_constraint_id: dict[str, list[dict[str, str]]] = {}
    for window_page in store.windows_db.query.execute():
        constraint_ids = _raw_relation_ids(window_page, "Constraint")
        kind = _option_name(getattr(window_page.props, "kind", None)) or "prefer"
        start = str(getattr(window_page.props, "start_time_local", "") or "").strip()
        end = str(getattr(window_page.props, "end_time_local", "") or "").strip()
        if not (start and end):
            continue
        item = {"kind": kind, "start_time_local": start, "end_time_local": end}
        for constraint_id in constraint_ids:
            windows_by_constraint_id.setdefault(constraint_id, []).append(item)

    events = list(store.events_db.query.execute())
    return SourceSnapshot(
        constraints=list(constraints),
        windows_by_constraint_id=windows_by_constraint_id,
        topic_name_by_id=topic_name_by_id,
        uid_by_constraint_id=uid_by_constraint_id,
        events=events,
    )


def _constraint_to_record(page: Any, snapshot: SourceSnapshot) -> dict[str, Any]:
    props = page.props
    page_id = str(page.id)
    uid = str(getattr(props, "uid", "") or "").strip()

    scalar_params: dict[str, Any] = {}
    for key in ("duration_min", "duration_min_min"):
        if hasattr(props, key):
            value = getattr(props, key)
            if value is not None:
                scalar_params["duration_min"] = int(value)
            break
    for key in ("duration_max", "duration_max_min"):
        if hasattr(props, key):
            value = getattr(props, key)
            if value is not None:
                scalar_params["duration_max"] = int(value)
            break
    contiguity = _option_name(getattr(props, "contiguity", None))
    if contiguity:
        scalar_params["contiguity"] = contiguity

    topic_ids = _raw_relation_ids(page, "Topics")
    topics = [
        snapshot.topic_name_by_id.get(topic_id, topic_id)
        for topic_id in topic_ids
        if topic_id
    ]

    supersedes_ids = _raw_relation_ids(page, "Supersedes")
    supersedes_uids = [
        snapshot.uid_by_constraint_id.get(sid, sid) for sid in supersedes_ids if sid
    ]

    record = {
        "constraint_record": {
            "name": str(getattr(props, "name", "") or "").strip(),
            "description": str(getattr(props, "description", "") or "").strip(),
            "necessity": _option_name(getattr(props, "necessity", None)),
            "status": _option_name(getattr(props, "status", None)),
            "source": _option_name(getattr(props, "source", None)),
            "confidence": getattr(props, "confidence", None),
            "scope": _option_name(getattr(props, "scope", None)),
            "applies_stages": _option_list(getattr(props, "applies_stages", None)),
            "applies_event_types": _option_list(
                getattr(props, "applies_event_types", None)
            ),
            "topics": topics,
            "applicability": {
                "start_date": _date_to_iso(getattr(props, "start_date", None)),
                "end_date": _date_to_iso(getattr(props, "end_date", None)),
                "days_of_week": _option_list(getattr(props, "days_of_week", None)),
                "timezone": str(getattr(props, "timezone", "") or "").strip() or None,
                "recurrence": str(getattr(props, "recurrence", "") or "").strip() or None,
            },
            "lifecycle": {
                "uid": uid or None,
                "supersedes_uids": supersedes_uids,
                "ttl_days": getattr(props, "ttl_days", None),
            },
            "payload": {
                "rule_kind": _option_name(getattr(props, "rule_kind", None)),
                "scalar_params": scalar_params,
                "windows": snapshot.windows_by_constraint_id.get(page_id, []),
            },
        }
    }
    return record


def _event_to_reflection_payload(event_page: Any) -> dict[str, Any]:
    props = event_page.props
    stage = _option_name(getattr(props, "stage", None))
    action = _option_name(getattr(props, "action", None))
    event_types = _option_list(getattr(props, "event_types", None))
    user_utterance = str(getattr(props, "user_utterance", "") or "").strip()
    extracted_uid = str(getattr(props, "extracted_uid", "") or "").strip()
    occurred_at = _date_to_iso(getattr(props, "occurred_at", None))
    summary_parts = [part for part in [action, extracted_uid, stage] if part]
    summary = "notion_event: " + " | ".join(summary_parts or ["unknown"])
    return {
        "summary": summary,
        "kind": "timeboxing_reflection",
        "source": "notion_migration",
        "stage": stage,
        "action": action,
        "event_types": event_types,
        "user_utterance": user_utterance,
        "constraint_uid": extracted_uid or None,
        "triggering_suggestion": str(
            getattr(props, "triggering_suggestion", "") or ""
        ).strip()
        or None,
        "decision_scope": _option_name(getattr(props, "decision_scope", None)),
        "overrode_planner": bool(getattr(props, "overrode_planner", False)),
        "extraction_confidence": getattr(props, "extraction_confidence", None),
        "occurred_at": occurred_at,
        "name": str(getattr(props, "name", "") or "").strip() or None,
    }


def _coerce_sync_sqlite_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("sqlite+aiosqlite://"):
        return value.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return value


def _sqlite_uid(row: dict[str, Any], hints: dict[str, Any], selector: dict[str, Any]) -> str:
    for source in (hints, selector):
        uid = str(source.get("uid") or "").strip()
        if uid:
            return uid
    row_id = int(row.get("id") or 0)
    user_raw = str(row.get("user_id") or "").strip() or "user"
    user_token = re.sub(r"[^a-zA-Z0-9]+", "_", user_raw).strip("_") or "user"
    return f"tb_sqlite_{user_token}_{row_id}"


def _sqlite_rule_kind(hints: dict[str, Any], selector: dict[str, Any]) -> str | None:
    for source in (hints, selector):
        value = source.get("rule_kind")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _sqlite_scalar_params(
    hints: dict[str, Any],
    selector: dict[str, Any],
) -> dict[str, Any]:
    scalar: dict[str, Any] = {}
    for key in ("duration_min", "duration_max", "contiguity"):
        for source in (hints, selector):
            if key in source and source.get(key) is not None:
                scalar[key] = source.get(key)
                break
        for source in (hints, selector):
            nested = source.get("scalar_params")
            if isinstance(nested, dict) and key in nested and nested.get(key) is not None:
                scalar[key] = nested.get(key)
                break
    return scalar


def _sqlite_windows(hints: dict[str, Any], selector: dict[str, Any]) -> list[dict[str, str]]:
    for source in (hints, selector):
        windows = _to_list(source.get("windows"))
        out: list[dict[str, str]] = []
        for item in windows:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip() or "prefer"
            start = str(item.get("start_time_local") or "").strip()
            end = str(item.get("end_time_local") or "").strip()
            if not (start and end):
                continue
            out.append(
                {
                    "kind": kind,
                    "start_time_local": start,
                    "end_time_local": end,
                }
            )
        if out:
            return out
    return []


def _sqlite_constraint_to_record(row: dict[str, Any]) -> dict[str, Any]:
    hints = _to_dict(row.get("hints"))
    selector = _to_dict(row.get("selector"))
    tags = [
        str(tag).strip()
        for tag in _to_list(row.get("tags"))
        if str(tag).strip()
    ]
    supersedes = [
        str(value).strip()
        for value in _to_list(row.get("supersedes"))
        if str(value).strip()
    ]
    days = [
        day
        for day in (_normalize_day(value) for value in _to_list(row.get("days_of_week")))
        if day
    ]
    applies_stages = [
        str(value).strip()
        for value in _to_list(hints.get("applies_stages") or selector.get("applies_stages"))
        if str(value).strip()
    ] or list(_DEFAULT_APPLIES_STAGES)
    applies_event_types = [
        str(value).strip()
        for value in _to_list(
            hints.get("applies_event_types") or selector.get("applies_event_types")
        )
        if str(value).strip()
    ] or list(_DEFAULT_APPLIES_EVENT_TYPES)

    uid = _sqlite_uid(row, hints, selector)
    rule_kind = _sqlite_rule_kind(hints, selector)
    scalar_params = _sqlite_scalar_params(hints, selector)
    windows = _sqlite_windows(hints, selector)

    return {
        "constraint_record": {
            "name": str(row.get("name") or "").strip() or f"Constraint {row.get('id')}",
            "description": str(row.get("description") or "").strip(),
            "necessity": _normalize_enum_text(row.get("necessity")) or "should",
            "status": _normalize_enum_text(row.get("status")) or "proposed",
            "source": _normalize_enum_text(row.get("source")) or "user",
            "confidence": row.get("confidence"),
            "scope": _normalize_enum_text(row.get("scope")) or "session",
            "applies_stages": applies_stages,
            "applies_event_types": applies_event_types,
            "topics": tags,
            "applicability": {
                "start_date": _date_to_iso(row.get("start_date")),
                "end_date": _date_to_iso(row.get("end_date")),
                "days_of_week": days,
                "timezone": str(row.get("timezone") or "").strip() or None,
                "recurrence": str(row.get("recurrence") or "").strip() or None,
            },
            "lifecycle": {
                "uid": uid,
                "supersedes_uids": supersedes,
                "ttl_days": row.get("ttl_days"),
            },
            "payload": {
                "rule_kind": rule_kind,
                "scalar_params": scalar_params,
                "windows": windows,
            },
        }
    }


def _load_sqlite_constraint_records(
    *,
    sqlite_url: str,
    limit: int,
) -> tuple[int, list[dict[str, Any]]]:
    sync_url = _coerce_sync_sqlite_url(sqlite_url)
    if not sync_url.startswith("sqlite://"):
        raise RuntimeError(f"sqlite source requires sqlite URL, got: {sqlite_url}")
    engine = create_engine(sync_url)
    with engine.connect() as conn:
        total = int(
            conn.execute(text("SELECT COUNT(*) FROM timeboxing_constraints")).scalar_one()
        )
        sql = "SELECT * FROM timeboxing_constraints ORDER BY id ASC"
        params: dict[str, Any] = {}
        if limit > 0:
            sql += " LIMIT :limit"
            params["limit"] = int(limit)
        rows = [dict(item) for item in conn.execute(text(sql), params).mappings().all()]
    records = [_sqlite_constraint_to_record(row) for row in rows]
    return total, records


async def _run(args: argparse.Namespace) -> None:
    source = str(args.source or "notion").strip().lower()
    if not os.getenv("OPENAI_API_KEY"):
        configured_openai_key = str(settings.openai_api_key or "").strip()
        if configured_openai_key:
            os.environ["OPENAI_API_KEY"] = configured_openai_key
    parent_page_id = (args.parent_page_id or "").strip() or (
        os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "").strip()
        or (settings.notion_timeboxing_parent_page_id or "").strip()
    )
    notion_token = (args.notion_token or "").strip() or (
        os.getenv("NOTION_TOKEN", "").strip()
        or os.getenv("WORK_NOTION_TOKEN", "").strip()
        or (settings.work_notion_token or "").strip()
    )
    mem0_user_id = (
        args.mem0_user_id.strip()
        if args.mem0_user_id
        else str(settings.mem0_user_id or "timeboxing").strip()
    )

    direct_dbs = None
    direct_ids = {
        "topics_db_id": (args.topics_db_id or "").strip()
        or os.getenv("NOTION_TB_TOPICS_DB_ID", "").strip(),
        "types_db_id": (args.types_db_id or "").strip()
        or os.getenv("NOTION_TB_TYPES_DB_ID", "").strip(),
        "constraints_db_id": (args.constraints_db_id or "").strip()
        or os.getenv("NOTION_TB_CONSTRAINTS_DB_ID", "").strip(),
        "windows_db_id": (args.windows_db_id or "").strip()
        or os.getenv("NOTION_TB_WINDOWS_DB_ID", "").strip(),
        "events_db_id": (args.events_db_id or "").strip()
        or os.getenv("NOTION_TB_EVENTS_DB_ID", "").strip(),
    }
    if all(direct_ids.values()):
        direct_dbs = NotionPreferenceDBs(**direct_ids)

    target = build_mem0_client_from_settings(user_id=mem0_user_id)
    events_total = 0
    reflections_payloads: list[dict[str, Any]] = []
    direct_ids_used: dict[str, str] = {}
    if source == "sqlite":
        sqlite_db_url = (args.sqlite_db_url or "").strip() or str(settings.database_url)
        constraints_total, records = _load_sqlite_constraint_records(
            sqlite_url=sqlite_db_url,
            limit=int(args.limit or 0),
        )
        constraints = records
        constraints_processed = len(constraints)
    else:
        source_store = _resolve_notion_store(
            parent_page_id=parent_page_id or None,
            notion_token=notion_token or None,
            dbs=direct_dbs,
        )
        snapshot = _load_source_snapshot(source_store)
        constraints_total = len(snapshot.constraints)
        constraints = [
            _constraint_to_record(page, snapshot)
            for page in (
                snapshot.constraints[: args.limit]
                if args.limit > 0
                else snapshot.constraints
            )
        ]
        constraints_processed = len(constraints)
        if args.include_events:
            reflections_payloads = [
                _event_to_reflection_payload(event_page) for event_page in snapshot.events
            ]
            events_total = len(snapshot.events)
        direct_ids_used = direct_ids

    migrated = 0
    skipped = 0
    failed: list[str] = []

    for record in constraints:
        uid = str(
            (record.get("constraint_record") or {})
            .get("lifecycle", {})
            .get("uid", "")
            or ""
        ).strip()
        if not uid:
            failed.append("missing_uid")
            continue
        if args.skip_existing:
            existing = await target.get_constraint(uid=uid)
            if existing is not None:
                skipped += 1
                continue
        if not args.apply:
            migrated += 1
            continue
        try:
            migration_event = {
                "action": "migrate_from_sqlite"
                if source == "sqlite"
                else "migrate_from_notion",
                "decision_scope": "other",
                "user_utterance": (
                    "Migrated from sqlite timeboxing_constraints mirror"
                    if source == "sqlite"
                    else "Migrated from Notion durable memory"
                ),
            }
            await target.upsert_constraint(
                record=record,
                event=migration_event if args.include_events else None,
            )
            migrated += 1
        except Exception as exc:
            failed.append(f"{uid}:{type(exc).__name__}:{exc}")

    reflections_migrated = 0
    reflection_failed = 0
    if args.include_events:
        for payload in reflections_payloads:
            if not args.apply:
                reflections_migrated += 1
                continue
            try:
                await target.add_reflection(payload=payload)
                reflections_migrated += 1
            except Exception:
                reflection_failed += 1

    report = {
        "apply": bool(args.apply),
        "source": source,
        "mem0_user_id": mem0_user_id,
        "notion_parent_page_id": parent_page_id or None,
        "notion_db_ids": direct_ids_used,
        "sqlite_db_url": (
            _coerce_sync_sqlite_url((args.sqlite_db_url or "").strip() or str(settings.database_url))
            if source == "sqlite"
            else None
        ),
        "constraints_total": constraints_total,
        "constraints_processed": constraints_processed,
        "constraints_migrated": migrated,
        "constraints_skipped_existing": skipped,
        "constraints_failed": len(failed),
        "failed_examples": failed[:20],
        "events_total": events_total,
        "events_migrated_to_reflections": reflections_migrated,
        "events_failed": reflection_failed,
    }
    print(json.dumps(report, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate TB durable constraints from Notion or sqlite mirror to Mem0."
    )
    parser.add_argument(
        "--source",
        choices=["notion", "sqlite"],
        default="notion",
        help="Source backend (default: notion).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to Mem0. Without this flag, runs a dry-run.",
    )
    parser.add_argument(
        "--include-events",
        action="store_true",
        help="Also migrate Notion TB Constraint Events into Mem0 reflections.",
    )
    parser.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip constraints whose UID already exists in Mem0 (default: true).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max constraints to process (0 = all).",
    )
    parser.add_argument(
        "--parent-page-id",
        default="",
        help="Override Notion parent page id for TB databases.",
    )
    parser.add_argument(
        "--notion-token",
        default="",
        help="Override Notion token for source reads.",
    )
    parser.add_argument(
        "--sqlite-db-url",
        default="",
        help="Override sqlite database URL when --source sqlite (defaults to settings.database_url).",
    )
    parser.add_argument(
        "--mem0-user-id",
        default="",
        help="Target Mem0 user_id scope (defaults to MEM0_USER_ID).",
    )
    parser.add_argument("--topics-db-id", default="", help="Notion TB Topics database id.")
    parser.add_argument(
        "--types-db-id", default="", help="Notion TB Constraint Types database id."
    )
    parser.add_argument(
        "--constraints-db-id", default="", help="Notion TB Constraints database id."
    )
    parser.add_argument(
        "--windows-db-id", default="", help="Notion TB Constraint Windows database id."
    )
    parser.add_argument(
        "--events-db-id", default="", help="Notion TB Constraint Events database id."
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        asyncio.run(_run(args))
    except Exception as exc:
        error = {
            "apply": bool(getattr(args, "apply", False)),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "hint": (
                "For --source notion, ensure the Notion integration has access to TB databases "
                "(or pass --topics-db-id/--types-db-id/--constraints-db-id/"
                "--windows-db-id/--events-db-id for direct lookup). "
                "For --source sqlite, ensure settings.database_url points to a sqlite DB "
                "with table `timeboxing_constraints`."
            ),
        }
        print(json.dumps(error, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
