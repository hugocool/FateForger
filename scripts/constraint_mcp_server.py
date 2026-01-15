from __future__ import annotations

import os
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from fateforger.adapters.notion.timeboxing_preferences import (
    ConstraintQueryFilters,
    NotionConstraintStore,
    get_notion_session,
    seed_default_constraint_types,
)


mcp = FastMCP(
    name="constraint-memory",
    instructions="Tools for querying and updating the timeboxing constraint memory store.",
)

_STORE: Optional[NotionConstraintStore] = None


def _get_store() -> NotionConstraintStore:
    global _STORE
    if _STORE:
        return _STORE
    parent_page_id = os.getenv("NOTION_TIMEBOXING_PARENT_PAGE_ID", "").strip()
    if not parent_page_id:
        raise RuntimeError("NOTION_TIMEBOXING_PARENT_PAGE_ID is required.")
    session = get_notion_session(notion_token=os.getenv("NOTION_TOKEN"))
    _STORE = NotionConstraintStore.from_parent_page(
        parent_page_id=parent_page_id,
        notion=session,
        write_registry_block=False,
    )
    return _STORE


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


def _serialize_constraint(page) -> Dict[str, Any]:
    props = page.props
    type_id = None
    constraint_type_pages = None
    try:
        constraint_type_pages = getattr(props, "constraint_type", None)
    except Exception:
        constraint_type_pages = None
    if constraint_type_pages:
        type_id = getattr(constraint_type_pages[0].props, "type_id", None)
    else:
        try:
            rel_pages = page.get_property("Constraint Type") or []
            if rel_pages:
                type_id = getattr(rel_pages[0].props, "type_id", None)
        except Exception:
            type_id = None
    topics = getattr(props, "topics", None) or []
    return {
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
        "topics": [str(topic) for topic in topics],
    }


@mcp.tool(name="constraint.query_types")
def query_types(
    stage: Optional[str] = None, event_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    store = _get_store()
    return store.query_types(stage=stage, event_types=event_types)


@mcp.tool(name="constraint.query_constraints")
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


@mcp.tool(name="constraint.upsert_constraint")
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


@mcp.tool(name="constraint.log_event")
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


@mcp.tool(name="constraint.seed_types")
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
