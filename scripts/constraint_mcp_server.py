from __future__ import annotations

import os
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from fateforger.adapters.notion.timeboxing_preferences import (
    NotionPreferenceDBs,
    ConstraintQueryFilters,
    NotionConstraintStore,
    get_notion_session,
    seed_default_constraint_types,
)
from fateforger.core.config import settings


mcp = FastMCP(
    name="constraint-memory",
    instructions="Tools for querying and updating the timeboxing constraint memory store.",
)

_STORE: Optional[NotionConstraintStore] = None


def _get_store() -> NotionConstraintStore:
    global _STORE
    if _STORE:
        return _STORE
    parent_page_id = (
        os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "").strip()
        or os.getenv("WORK_NOTION_PARENT_PAGE_ID", "").strip()
        or (settings.notion_timeboxing_parent_page_id or "").strip()
    )
    if not parent_page_id:
        raise RuntimeError("NOTION_TIMEBOXING_PARENT_PAGE_ID is required.")
    notion_token = (
        os.getenv("NOTION_TOKEN", "").strip()
        or os.getenv("WORK_NOTION_TOKEN", "").strip()
        or (settings.work_notion_token or "").strip()
    )
    session = get_notion_session(notion_token=notion_token or None)
    try:
        _STORE = NotionConstraintStore.from_parent_page(
            parent_page_id=parent_page_id,
            notion=session,
            write_registry_block=False,
        )
    except Exception as exc:
        # Fallback: if the parent page is inaccessible, try discovering the known
        # databases by title in the current workspace scope.
        fallback = _discover_existing_store(session=session)
        if fallback is None:
            raise RuntimeError(
                f"Failed to initialize constraint memory store via parent page "
                f"{parent_page_id}: {exc}"
            ) from exc
        _STORE = fallback
    return _STORE


def _discover_existing_store(session) -> NotionConstraintStore | None:
    """Try constructing a store by discovering pre-existing DBs by title."""
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


def _option_name(value) -> Optional[str]:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


def _option_list(values) -> List[str]:
    if not values:
        return []
    return [getattr(value, "name", None) or str(value) for value in values]


def _date_to_iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return getattr(value, "isoformat", lambda: str(value))()


def _safe_str(value) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(value)
    except Exception:
        return None


def _raw_relation_ids(page, prop_name: str) -> List[str]:
    """Extract relation target IDs from raw page payload without page hydration."""
    raw_candidates = [
        getattr(page, "obj_ref", None),
        getattr(page, "_obj_ref", None),
        getattr(page, "raw", None),
        getattr(page, "_raw", None),
    ]
    payload: Dict[str, Any] | None = None
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
    out: List[str] = []
    for item in relation:
        if not isinstance(item, dict):
            continue
        rid = item.get("id")
        if isinstance(rid, str) and rid.strip():
            out.append(rid.strip())
    return out


def _serialize_constraint(page) -> Dict[str, Any]:
    props = page.props
    type_id = _option_name(getattr(props, "rule_kind", None))
    topics = _raw_relation_ids(page, "Topics")
    return {
        "page_id": str(page.id),
        "url": getattr(page, "url", None),
        "uid": getattr(props, "uid", None),
        "name": getattr(props, "name", None),
        "description": getattr(props, "description", None),
        "necessity": _option_name(getattr(props, "necessity", None)),
        "status": _option_name(getattr(props, "status", None)),
        "source": _option_name(getattr(props, "source", None)),
        "scope": _option_name(getattr(props, "scope", None)),
        "start_date": _date_to_iso(getattr(props, "start_date", None)),
        "end_date": _date_to_iso(getattr(props, "end_date", None)),
        "days_of_week": _option_list(getattr(props, "days_of_week", None)),
        "timezone": getattr(props, "timezone", None),
        "rule_kind": _option_name(getattr(props, "rule_kind", None)),
        "type_id": type_id,
        "topics": topics,
    }


@mcp.tool(name="constraint_get_store_info")
def get_store_info() -> Dict[str, Any]:
    """Return parent page + DB ids/URLs for the constraint memory store."""

    store = _get_store()
    parent_page_id = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "").strip()
    info: Dict[str, Any] = {
        "parent_page_id": parent_page_id,
        "parent_page_title": None,
        "parent_page_url": None,
        "dbs": {
            "topics": {
                "db_id": str(store.topics_db.id),
                "db_url": _safe_str(getattr(store.topics_db, "url", None)),
            },
            "types": {
                "db_id": str(store.types_db.id),
                "db_url": _safe_str(getattr(store.types_db, "url", None)),
            },
            "constraints": {
                "db_id": str(store.constraints_db.id),
                "db_url": _safe_str(getattr(store.constraints_db, "url", None)),
            },
            "windows": {
                "db_id": str(store.windows_db.id),
                "db_url": _safe_str(getattr(store.windows_db, "url", None)),
            },
            "events": {
                "db_id": str(store.events_db.id),
                "db_url": _safe_str(getattr(store.events_db, "url", None)),
            },
        },
    }
    if parent_page_id:
        try:
            page = store.notion.get_page(parent_page_id)
            info["parent_page_title"] = _safe_str(getattr(page, "title", None)) or _safe_str(page)
            info["parent_page_url"] = _safe_str(getattr(page, "url", None))
        except Exception:
            pass
    return info


@mcp.tool(name="constraint_get_constraint")
def get_constraint(uid: str) -> Dict[str, Any] | None:
    """Get a single constraint by UID (includes page_id + url)."""

    store = _get_store()
    page = store._get_constraint_by_uid(uid)
    if not page:
        return None
    return _serialize_constraint(page)


@mcp.tool(name="constraint_query_types")
def query_types(
    stage: Optional[str] = None, event_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    store = _get_store()
    return store.query_types(stage=stage, event_types=event_types)


@mcp.tool(name="constraint_query_constraints")
def query_constraints(
    filters: Dict[str, Any],
    type_ids: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    sort: Optional[List[List[str]]] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    store = _get_store()
    as_of = filters.get("as_of")
    if not as_of:
        as_of = datetime.utcnow().date().isoformat()
    try:
        as_of_date = datetime.fromisoformat(as_of).date()
    except Exception:
        as_of_date = datetime.utcnow().date()
    query_filters = ConstraintQueryFilters(
        as_of=as_of_date,
        stage=filters.get("stage"),
        event_types_any=filters.get("event_types_any"),
        scopes_any=filters.get("scopes_any"),
        statuses_any=filters.get("statuses_any"),
        necessities_any=filters.get("necessities_any"),
        text_query=filters.get("text_query"),
        require_active=filters.get("require_active", True),
    )
    sort_spec = [(item[0], item[1]) for item in (sort or [])]
    pages = store.query_constraints(
        query_filters,
        type_ids=type_ids,
        tags=tags,
        sort=sort_spec or None,
        limit=limit,
    )
    return [_serialize_constraint(page) for page in pages]


@mcp.tool(name="constraint_upsert_constraint")
def upsert_constraint(
    record: Dict[str, Any], event: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    store = _get_store()
    _ensure_uid(record)
    page = store.upsert_constraint(record)
    if event:
        if not event.get("extracted_uid"):
            event["extracted_uid"] = _extract_uid(record)
        store.log_extraction_event(
            occurred_at=datetime.utcnow(),
            user_utterance=event.get("user_utterance", ""),
            triggering_suggestion=event.get("triggering_suggestion"),
            extracted_uid=event.get("extracted_uid", ""),
            extraction_confidence=event.get("extraction_confidence"),
            constraint_page=page,
            stage=event.get("stage"),
            event_types=event.get("event_types"),
            decision_scope=event.get("decision_scope"),
            action=event.get("action"),
            overrode_planner=event.get("overrode_planner"),
            extracted_type_id=event.get("extracted_type_id"),
        )
    return {"uid": getattr(page.props, "uid", None), "page_id": str(page.id)}


@mcp.tool(name="constraint_log_event")
def log_event(event: Dict[str, Any]) -> Dict[str, Any]:
    store = _get_store()
    uid = event.get("constraint_uid")
    if not uid:
        raise ValueError("constraint_uid is required")
    constraint_page = store._get_constraint_by_uid(uid)
    if not constraint_page:
        raise ValueError(f"constraint not found for uid={uid}")
    page = store.log_extraction_event(
        occurred_at=datetime.utcnow(),
        user_utterance=event.get("user_utterance", ""),
        triggering_suggestion=event.get("triggering_suggestion"),
        extracted_uid=event.get("extracted_uid", uid),
        extraction_confidence=event.get("extraction_confidence"),
        constraint_page=constraint_page,
        stage=event.get("stage"),
        event_types=event.get("event_types"),
        decision_scope=event.get("decision_scope"),
        action=event.get("action"),
        overrode_planner=event.get("overrode_planner"),
        extracted_type_id=event.get("extracted_type_id"),
    )
    return {"page_id": str(page.id)}


@mcp.tool(name="constraint_seed_types")
def seed_types() -> Dict[str, Any]:
    store = _get_store()
    pages = seed_default_constraint_types(store)
    return {"count": len(pages)}


def _extract_uid(record: Dict[str, Any]) -> Optional[str]:
    constraint = record.get("constraint_record", record)
    lifecycle = constraint.get("lifecycle", {}) or {}
    return lifecycle.get("uid") or constraint.get("uid")


def _ensure_uid(record: Dict[str, Any]) -> None:
    constraint = record.get("constraint_record", record)
    lifecycle = constraint.get("lifecycle", {}) or {}
    uid = lifecycle.get("uid") or constraint.get("uid")
    if uid:
        return
    stable_bits = {
        "scope": constraint.get("scope"),
        "rule_kind": (constraint.get("payload") or {}).get("rule_kind"),
        "topics": sorted(constraint.get("topics") or []),
        "description": (constraint.get("description") or "").strip(),
        "days_of_week": (constraint.get("applicability") or {}).get("days_of_week"),
        "start_date": (constraint.get("applicability") or {}).get("start_date"),
        "end_date": (constraint.get("applicability") or {}).get("end_date"),
    }
    digest = hashlib.sha1(json.dumps(stable_bits, sort_keys=True).encode("utf-8")).hexdigest()
    uid = f"tb:{stable_bits.get('rule_kind') or 'rule'}:{digest[:12]}"
    lifecycle["uid"] = uid
    constraint["lifecycle"] = lifecycle


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
