from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import ultimate_notion as uno
from ultimate_notion.schema import AggFunc

# ----------------------------
# Option namespaces (enums)
# ----------------------------


class Necessity(uno.OptionNS):
    MUST = uno.Option("must", color=uno.Color.RED)
    SHOULD = uno.Option("should", color=uno.Color.YELLOW)


class CStatus(uno.OptionNS):
    PROPOSED = uno.Option("proposed", color=uno.Color.GRAY)
    LOCKED = uno.Option("locked", color=uno.Color.GREEN)


class CSource(uno.OptionNS):
    USER = uno.Option("user", color=uno.Color.BLUE)
    CALENDAR = uno.Option("calendar", color=uno.Color.PURPLE)
    SYSTEM = uno.Option("system", color=uno.Color.ORANGE)
    FEEDBACK = uno.Option("feedback", color=uno.Color.PINK)


class Scope(uno.OptionNS):
    SESSION = uno.Option("session", color=uno.Color.GRAY)
    PROFILE = uno.Option("profile", color=uno.Color.GREEN)
    DATESPAN = uno.Option("datespan", color=uno.Color.YELLOW)


class RuleKind(uno.OptionNS):
    PREFER_WINDOW = uno.Option("prefer_window", color=uno.Color.GREEN)
    AVOID_WINDOW = uno.Option("avoid_window", color=uno.Color.RED)
    FIXED_BEDTIME = uno.Option("fixed_bedtime", color=uno.Color.PURPLE)
    MIN_SLEEP = uno.Option("min_sleep", color=uno.Color.BLUE)
    BUFFER = uno.Option("buffer", color=uno.Color.YELLOW)
    SEQUENCING = uno.Option("sequencing", color=uno.Color.ORANGE)
    CAPACITY = uno.Option(
        "capacity", color=uno.Color.GRAY
    )  # e.g., #DW blocks, shallow cap


class Contiguity(uno.OptionNS):
    PREFER = uno.Option("prefer", color=uno.Color.GREEN)
    REQUIRE = uno.Option("require", color=uno.Color.RED)
    IRRELEVANT = uno.Option("irrelevant", color=uno.Color.GRAY)


class Stage(uno.OptionNS):
    COLLECT = uno.Option("CollectConstraints", color=uno.Color.BLUE)
    INPUTS = uno.Option("CaptureInputs", color=uno.Color.PURPLE)
    SKELETON = uno.Option("Skeleton", color=uno.Color.GREEN)
    REFINE = uno.Option("Refine", color=uno.Color.YELLOW)
    REVIEW = uno.Option("ReviewCommit", color=uno.Color.ORANGE)


class EventType(uno.OptionNS):
    M = uno.Option("M", color=uno.Color.RED)
    C = uno.Option("C", color=uno.Color.GRAY)
    DW = uno.Option("DW", color=uno.Color.GREEN)
    SW = uno.Option("SW", color=uno.Color.YELLOW)
    H = uno.Option("H", color=uno.Color.BLUE)
    R = uno.Option("R", color=uno.Color.PURPLE)
    BU = uno.Option("BU", color=uno.Color.ORANGE)
    BG = uno.Option("BG", color=uno.Color.GRAY)
    PR = uno.Option("PR", color=uno.Color.PINK)


class DOW(uno.OptionNS):
    MO = uno.Option("MO", color=uno.Color.GRAY)
    TU = uno.Option("TU", color=uno.Color.GRAY)
    WE = uno.Option("WE", color=uno.Color.GRAY)
    TH = uno.Option("TH", color=uno.Color.GRAY)
    FR = uno.Option("FR", color=uno.Color.GRAY)
    SA = uno.Option("SA", color=uno.Color.GRAY)
    SU = uno.Option("SU", color=uno.Color.GRAY)


class WindowKind(uno.OptionNS):
    PREFER = uno.Option("prefer", color=uno.Color.GREEN)
    AVOID = uno.Option("avoid", color=uno.Color.RED)


class RuleShape(uno.OptionNS):
    PREFER_WINDOW = uno.Option("prefer_window", color=uno.Color.GREEN)
    AVOID_WINDOW = uno.Option("avoid_window", color=uno.Color.RED)
    MUST_WINDOW = uno.Option("must_window", color=uno.Color.RED)
    FIXED_ANCHOR_TIME = uno.Option("fixed_anchor_time", color=uno.Color.PURPLE)
    FIXED_ANCHOR_INTERVAL = uno.Option("fixed_anchor_interval", color=uno.Color.PURPLE)
    COUNT_TARGET_PER_DAY = uno.Option("count_target_per_day", color=uno.Color.YELLOW)
    COUNT_CAP_PER_DAY = uno.Option("count_cap_per_day", color=uno.Color.YELLOW)
    MINUTES_TARGET_PER_DAY = uno.Option("minutes_target_per_day", color=uno.Color.YELLOW)
    MINUTES_CAP_PER_DAY = uno.Option("minutes_cap_per_day", color=uno.Color.YELLOW)
    FRACTION_CAP = uno.Option("fraction_cap", color=uno.Color.BLUE)
    DURATION_RANGE = uno.Option("duration_range", color=uno.Color.GREEN)
    MIN_BLOCK_DURATION = uno.Option("min_block_duration", color=uno.Color.GREEN)
    MAX_BLOCK_DURATION = uno.Option("max_block_duration", color=uno.Color.RED)
    GRANULARITY_PREFERENCE = uno.Option("granularity_preference", color=uno.Color.GRAY)
    ORDER_BEFORE_AFTER = uno.Option("order_before_after", color=uno.Color.ORANGE)
    BUFFER_AFTER = uno.Option("buffer_after", color=uno.Color.ORANGE)
    BUFFER_BEFORE = uno.Option("buffer_before", color=uno.Color.ORANGE)
    MIN_GAP_BETWEEN = uno.Option("min_gap_between", color=uno.Color.ORANGE)
    NO_ADJACENCY = uno.Option("no_adjacency", color=uno.Color.RED)
    DAY_TEMPLATE = uno.Option("day_template", color=uno.Color.BLUE)


class ScalarRequirement(uno.OptionNS):
    DURATION_MIN = uno.Option("duration_min")
    DURATION_MAX = uno.Option("duration_max")
    COUNT = uno.Option("count")
    MINUTES = uno.Option("minutes")
    FRACTION = uno.Option("fraction")
    CONTIGUITY = uno.Option("contiguity")
    ANCHOR_TIME = uno.Option("anchor_time")
    GAP_MINUTES = uno.Option("gap_minutes")
    BUFFER_MINUTES = uno.Option("buffer_minutes")


class TypeStatus(uno.OptionNS):
    PROPOSED = uno.Option("proposed", color=uno.Color.GRAY)
    LOCKED = uno.Option("locked", color=uno.Color.GREEN)


class DecisionScope(uno.OptionNS):
    PLACE_DW_BLOCKS = uno.Option("place_dw_blocks")
    PLACE_HABITS = uno.Option("place_habits")
    PLACE_MEETINGS = uno.Option("place_meetings")
    PLACE_MEALS = uno.Option("place_meals")
    PLACE_COMMUTE = uno.Option("place_commute")
    ADJUST_SCHEDULE = uno.Option("adjust_schedule")
    OTHER = uno.Option("other")


class ExtractionAction(uno.OptionNS):
    UPSERT = uno.Option("upsert", color=uno.Color.GREEN)
    CLARIFY = uno.Option("clarify", color=uno.Color.YELLOW)
    NOOP = uno.Option("noop", color=uno.Color.GRAY)


# ----------------------------
# Database schemas (Ultimate Notion ORM)
# ----------------------------


class TBTopic(uno.Schema, db_title="TB Topics"):
    """Timeboxing topic taxonomy used as a routing/index layer."""

    name = uno.PropType.Title("Name")
    description = uno.PropType.Text("Description")
    parent = uno.PropType.Relation("Parent", schema=uno.SelfRef)  # one-way


class TBConstraint(uno.Schema, db_title="TB Constraints"):
    """Durable timeboxing constraints/preferences (operational fields are properties)."""

    # Core identity / governance
    name = uno.PropType.Title("Name")
    description = uno.PropType.Text("Description")
    uid = uno.PropType.Text("UID")  # stable idempotency key

    necessity = uno.PropType.Select("Necessity", options=Necessity)
    status = uno.PropType.Select("Status", options=CStatus)
    source = uno.PropType.Select("Source", options=CSource)
    confidence = uno.PropType.Number("Confidence")
    scope = uno.PropType.Select("Scope", options=Scope)

    # Applicability
    start_date = uno.PropType.Date("Start Date")
    end_date = uno.PropType.Date("End Date")
    days_of_week = uno.PropType.MultiSelect("Days Of Week", options=DOW)
    timezone = uno.PropType.Text("Timezone")
    recurrence = uno.PropType.Text("Recurrence")
    ttl_days = uno.PropType.Number("TTL Days")

    # Routing metadata
    applies_stages = uno.PropType.MultiSelect("Applies Stages", options=Stage)
    applies_event_types = uno.PropType.MultiSelect(
        "Applies Event Types", options=EventType
    )
    topics = uno.PropType.Relation("Topics", schema=TBTopic)

    # Rule payload (typed core)
    rule_kind = uno.PropType.Select("Rule Kind", options=RuleKind)
    duration_min = uno.PropType.Number("Duration Min (min)")
    duration_max = uno.PropType.Number("Duration Max (min)")
    contiguity = uno.PropType.Select("Contiguity", options=Contiguity)

    # Supersession
    supersedes = uno.PropType.Relation("Supersedes", schema=uno.SelfRef)  # one-way


class TBConstraintWindow(uno.Schema, db_title="TB Constraint Windows"):
    """Repeating time windows linked to a constraint (prefer/avoid)."""

    name = uno.PropType.Title("Name")  # e.g., "prefer 16:00-20:00"
    constraint = uno.PropType.Relation("Constraint", schema=TBConstraint)
    kind = uno.PropType.Select("Kind", options=WindowKind)
    start_time_local = uno.PropType.Text("Start Time (local)")  # HH:MM
    end_time_local = uno.PropType.Text("End Time (local)")  # HH:MM


class TBConstraintType(uno.Schema, db_title="TB Constraint Types"):
    """Catalog of constraint types (rule shapes + routing defaults)."""

    name = uno.PropType.Title("Name")
    type_id = uno.PropType.Text("Type ID")
    rule_shape = uno.PropType.Select("Rule Shape", options=RuleShape)
    status = uno.PropType.Select("Status", options=TypeStatus)

    default_applies_stages = uno.PropType.MultiSelect(
        "Default Applies Stages", options=Stage
    )
    default_applies_event_types = uno.PropType.MultiSelect(
        "Default Applies Event Types", options=EventType
    )
    requires_windows = uno.PropType.Checkbox("Requires Windows")
    requires_scalars = uno.PropType.MultiSelect(
        "Requires Scalars", options=ScalarRequirement
    )
    suggested_topics = uno.PropType.Relation("Suggested Topics", schema=TBTopic)
    synonyms = uno.PropType.Text("Synonyms/Examples")
    constraints = uno.PropType.Relation("Constraints", schema=TBConstraint)
    active_constraint_count = uno.PropType.Rollup(
        "Active Constraint Count",
        relation=constraints,
        rollup=TBConstraint.uid,
        calculate=AggFunc.COUNT_ALL,
    )


class TBConstraintEvent(uno.Schema, db_title="TB Constraint Events"):
    """Event-sourced audit log for preference extraction and drift analysis."""

    name = uno.PropType.Title("Name")
    occurred_at = uno.PropType.Date("Occurred At")
    user_utterance = uno.PropType.Text("User Utterance")
    triggering_suggestion = uno.PropType.Text("Triggering Suggestion")
    stage = uno.PropType.Select("Stage", options=Stage)
    event_types = uno.PropType.MultiSelect("Event Types", options=EventType)
    decision_scope = uno.PropType.Select("Decision Scope", options=DecisionScope)
    action = uno.PropType.Select("Action", options=ExtractionAction)
    overrode_planner = uno.PropType.Checkbox("Overrode Planner")
    extracted_uid = uno.PropType.Text("Extracted UID")
    extraction_confidence = uno.PropType.Number("Extraction Confidence")
    constraint = uno.PropType.Relation("Constraint", schema=TBConstraint)
    extracted_type = uno.PropType.Relation("Extracted Type", schema=TBConstraintType)


# ----------------------------
# Installer (drops DBs under a page)
# ----------------------------


@dataclass(frozen=True)
class NotionPreferenceDBs:
    topics_db_id: str
    types_db_id: str
    constraints_db_id: str
    windows_db_id: str
    events_db_id: str


def install_preference_dbs(notion: uno.Session, parent_page_id: str) -> NotionPreferenceDBs:
    """Create (or reuse) the preference DBs under the given Notion page."""

    parent = notion.get_page(parent_page_id)

    def get_or_create_db_under_parent(schema: type[uno.Schema]):
        title = getattr(schema, "_db_title", None)
        if not title:
            raise ValueError(f"Schema {schema.__name__} is missing db_title")
        matches = [db for db in notion.search_db(title) if db.parent == parent]
        if not matches:
            db = notion.create_db(parent, schema=schema)
            while not [db for db in notion.search_db(title) if db.parent == parent]:
                time.sleep(1)
            return db
        return matches[0]

    # Dependency order matters (relations need targets to exist).
    topics_db = get_or_create_db_under_parent(TBTopic)
    TBTopic._bind_db(topics_db)

    constraints_db = get_or_create_db_under_parent(TBConstraint)
    TBConstraint._bind_db(constraints_db)

    types_db = get_or_create_db_under_parent(TBConstraintType)
    TBConstraintType._bind_db(types_db)

    windows_db = get_or_create_db_under_parent(TBConstraintWindow)
    TBConstraintWindow._bind_db(windows_db)

    events_db = get_or_create_db_under_parent(TBConstraintEvent)
    TBConstraintEvent._bind_db(events_db)

    return NotionPreferenceDBs(
        topics_db_id=str(topics_db.id),
        types_db_id=str(types_db.id),
        constraints_db_id=str(constraints_db.id),
        windows_db_id=str(windows_db.id),
        events_db_id=str(events_db.id),
    )


@dataclass(frozen=True)
class ConstraintQueryFilters:
    as_of: date
    stage: Optional[str] = None
    event_types_any: Optional[List[str]] = None
    scopes_any: Optional[List[str]] = None
    statuses_any: Optional[List[str]] = None
    necessities_any: Optional[List[str]] = None
    text_query: Optional[str] = None
    require_active: bool = True


SortSpec = List[Tuple[str, str]]  # [("Confidence", "desc"), ("Name", "asc")]


class NotionConstraintStore:
    """Notion-backed constraint persistence/retrieval with deterministic entrypoints."""

    def __init__(self, notion: uno.Session, dbs: NotionPreferenceDBs):
        self.notion = notion

        self.topics_db = notion.get_db(dbs.topics_db_id)
        self.types_db = notion.get_db(dbs.types_db_id)
        self.constraints_db = notion.get_db(dbs.constraints_db_id)
        self.windows_db = notion.get_db(dbs.windows_db_id)
        self.events_db = notion.get_db(dbs.events_db_id)

        TBTopic._bind_db(self.topics_db)
        TBConstraintType._bind_db(self.types_db)
        TBConstraint._bind_db(self.constraints_db)
        TBConstraintWindow._bind_db(self.windows_db)
        TBConstraintEvent._bind_db(self.events_db)

    @classmethod
    def from_parent_page(
        cls,
        *,
        parent_page_id: str,
        notion: Optional[uno.Session] = None,
        notion_token: Optional[str] = None,
        write_registry_block: bool = False,
    ) -> "NotionConstraintStore":
        """Convenience constructor: ensure DBs exist under a page, then return a bound store."""

        session = notion or get_notion_session(notion_token=notion_token)
        dbs = install_preference_dbs(session, parent_page_id)
        if write_registry_block:
            _write_registry_block(session, parent_page_id, dbs)
        return cls(session, dbs)

    # ---------- deterministic entry point #1 ----------
    def query_types(
        self, stage: Optional[str], event_types: Optional[Sequence[str]]
    ) -> List[Dict[str, Any]]:
        """Return ranked constraint types relevant to the current stage/event-types."""

        cond = uno.prop("Type ID").is_not_empty()

        cond = cond & (
            uno.prop("Status").is_empty() | (uno.prop("Status") == TypeStatus.LOCKED)
        )

        if stage:
            stage_term = uno.prop("Default Applies Stages").contains(self._to_stage(stage))
            cond = cond & (uno.prop("Default Applies Stages").is_empty() | stage_term)

        if event_types:
            ev_cond = None
            for et in event_types:
                term = uno.prop("Default Applies Event Types").contains(
                    self._to_event_type(et)
                )
                ev_cond = term if ev_cond is None else (ev_cond | term)
            if ev_cond is not None:
                cond = cond & (uno.prop("Default Applies Event Types").is_empty() | ev_cond)

        pages = self.types_db.query.filter(cond).execute()

        ranked: List[Dict[str, Any]] = []
        for page in pages:
            count = getattr(page.props, "active_constraint_count", None)
            count_val = int(count) if isinstance(count, (int, float)) else 0
            rule_shape = getattr(page.props, "rule_shape", None)
            requires_windows = getattr(page.props, "requires_windows", None)
            requires_scalars = getattr(page.props, "requires_scalars", None) or []
            ranked.append(
                {
                    "type_id": getattr(page.props, "type_id", None),
                    "name": getattr(page.props, "name", None),
                    "rule_shape": rule_shape.name if rule_shape else None,
                    "count": count_val,
                    "requires_windows": bool(requires_windows)
                    if requires_windows is not None
                    else False,
                    "requires_scalars": [opt.name for opt in requires_scalars],
                }
            )

        ranked.sort(key=lambda item: item["count"], reverse=True)
        return ranked

    # ---------- deterministic entry point #2 ----------
    def query_constraints(
        self,
        filters: ConstraintQueryFilters,
        type_ids: Optional[Sequence[str]] = None,
        tags: Optional[Sequence[str]] = None,
        sort: Optional[SortSpec] = None,
        limit: int = 50,
    ) -> List[uno.Page]:
        """Deterministically query constraints by structured filters and routing metadata."""

        cond = self._filters_to_condition(filters)

        if type_ids:
            type_cond = None
            for type_id in type_ids:
                try:
                    rk = self._to_rule_kind(type_id)
                except ValueError:
                    continue
                term = uno.prop("Rule Kind") == rk
                type_cond = term if type_cond is None else (type_cond | term)
            if type_cond is None:
                return []
            cond = cond & type_cond

        if tags:
            topic_pages = self._resolve_topics_by_name(tags)
            if topic_pages:
                tag_cond = None
                for topic_page in topic_pages:
                    term = uno.prop("Topics").contains(topic_page)
                    tag_cond = term if tag_cond is None else (tag_cond | term)
                if tag_cond is not None:
                    cond = cond & tag_cond

        query = self.constraints_db.query.filter(cond)

        if sort:
            sort_terms = []
            for prop_name, direction in sort:
                if direction.lower() == "desc":
                    sort_terms.append(uno.prop(prop_name).desc())
                else:
                    sort_terms.append(uno.prop(prop_name))
            query = query.sort(*sort_terms)

        view = query.execute()
        return list(view)[: max(0, limit)]

    # ---------- deterministic entry point #3 ----------
    def upsert_constraint(self, record: Dict[str, Any]) -> uno.Page:
        """Upsert a constraint record (supports supersede + window replacement)."""

        constraint = record.get("constraint_record", record)
        lifecycle = constraint.get("lifecycle", {}) or {}
        applicability = constraint.get("applicability", {}) or {}
        payload = constraint.get("payload", {}) or {}

        uid = lifecycle.get("uid") or constraint.get("uid")
        if not uid:
            raise ValueError("upsert_constraint: missing lifecycle.uid / uid")

        existing = self._get_constraint_by_uid(uid)

        scalar = payload.get("scalar_params", {}) or {}
        props: Dict[str, Any] = {
            "name": constraint.get("name", ""),
            "description": constraint.get("description", ""),
            "uid": uid,
            "necessity": self._to_necessity(constraint.get("necessity")),
            "status": self._to_status(constraint.get("status")),
            "source": self._to_source(constraint.get("source")),
            "confidence": constraint.get("confidence"),
            "scope": self._to_scope(constraint.get("scope")),
            "start_date": applicability.get("start_date"),
            "end_date": applicability.get("end_date"),
            "timezone": applicability.get("timezone"),
            "recurrence": constraint.get("recurrence") or applicability.get("recurrence"),
            "ttl_days": lifecycle.get("ttl_days") or constraint.get("ttl_days"),
            "rule_kind": self._to_rule_kind(payload.get("rule_kind")),
            "duration_min": scalar.get("duration_min"),
            "duration_max": scalar.get("duration_max"),
            "contiguity": self._to_contiguity(scalar.get("contiguity")),
        }

        dows = applicability.get("days_of_week")
        if dows:
            props["days_of_week"] = [self._to_dow(value) for value in dows]

        stages = constraint.get("applies_stages") or []
        if stages:
            props["applies_stages"] = [self._to_stage(value) for value in stages]

        event_types = constraint.get("applies_event_types") or []
        if event_types:
            props["applies_event_types"] = [
                self._to_event_type(value) for value in event_types
            ]

        topics = constraint.get("topics") or []
        if topics:
            topic_pages = self._resolve_topics_by_name(topics)
            if topic_pages:
                props["topics"] = topic_pages

        type_id = constraint.get("type_id") or payload.get("type_id")

        supersedes_uids = (
            lifecycle.get("supersedes_uids")
            or constraint.get("supersedes_uids")
            or []
        )
        superseded_pages: List[uno.Page] = []
        for supersede_uid in supersedes_uids:
            page = self._get_constraint_by_uid(supersede_uid)
            if page:
                superseded_pages.append(page)
        if superseded_pages:
            props["supersedes"] = superseded_pages

        if existing:
            existing.update_props(**props)
            constraint_page = existing
        else:
            constraint_page = TBConstraint.create(**props)

        if superseded_pages:
            self._apply_supersede_side_effects(
                superseded_pages=superseded_pages,
                new_start=applicability.get("start_date"),
            )

        windows = payload.get("windows") or []
        if windows:
            self._replace_windows(constraint_page, windows)

        if type_id:
            self._attach_constraint_type(constraint_page, type_id)

        return constraint_page

    def upsert_constraint_type(self, payload: Dict[str, Any]) -> uno.Page:
        type_id = payload.get("type_id")
        if not type_id:
            raise ValueError("constraint type requires type_id")
        existing = self._resolve_types_by_id([type_id])
        rule_shape = self._to_rule_shape(payload.get("rule_shape"))
        status = self._to_type_status(payload.get("status") or "locked")
        requires_windows = bool(payload.get("requires_windows", False))
        scalars = payload.get("requires_scalars") or []
        stages = payload.get("default_applies_stages") or []
        event_types = payload.get("default_applies_event_types") or []
        topics = payload.get("suggested_topics") or []
        synonyms = payload.get("synonyms") or ""

        props: Dict[str, Any] = {
            "name": payload.get("name", type_id),
            "type_id": type_id,
            "rule_shape": rule_shape,
            "status": status,
            "requires_windows": requires_windows,
            "synonyms": synonyms,
        }

        if scalars:
            props["requires_scalars"] = [
                self._to_scalar_requirement(value) for value in scalars
            ]
        if stages:
            props["default_applies_stages"] = [
                self._to_stage(value) for value in stages
            ]
        if event_types:
            props["default_applies_event_types"] = [
                self._to_event_type(value) for value in event_types
            ]
        if topics:
            topic_pages = self._resolve_topics_by_name(topics)
            if topic_pages:
                props["suggested_topics"] = topic_pages

        if existing:
            page = existing[0]
            page.update_props(**props)
            return page
        return TBConstraintType.create(**props)

    def log_extraction_event(
        self,
        *,
        occurred_at: Optional[datetime] = None,
        user_utterance: str,
        triggering_suggestion: Optional[str],
        extracted_uid: str,
        extraction_confidence: Optional[float],
        constraint_page: uno.Page,
        stage: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        decision_scope: Optional[str] = None,
        action: Optional[str] = None,
        overrode_planner: Optional[bool] = None,
        extracted_type_id: Optional[str] = None,
    ) -> uno.Page:
        """Append an event-log entry linked to the active constraint page."""

        name = f"Extracted {extracted_uid}"
        props: Dict[str, Any] = {
            "name": name,
            "user_utterance": user_utterance,
            "triggering_suggestion": triggering_suggestion or "",
            "extracted_uid": extracted_uid,
            "extraction_confidence": extraction_confidence,
            "constraint": [constraint_page],
        }
        if stage:
            props["stage"] = self._to_stage(stage)
        if event_types:
            props["event_types"] = [self._to_event_type(value) for value in event_types]
        if decision_scope:
            props["decision_scope"] = self._to_decision_scope(decision_scope)
        if action:
            props["action"] = self._to_extraction_action(action)
        if overrode_planner is not None:
            props["overrode_planner"] = bool(overrode_planner)
        if extracted_type_id:
            type_pages = self._resolve_types_by_id([extracted_type_id])
            if type_pages:
                props["extracted_type"] = type_pages
        if occurred_at:
            props["occurred_at"] = occurred_at
        return TBConstraintEvent.create(**props)

    # ----------------------------
    # Internals
    # ----------------------------

    def _active_condition(self, as_of: date):
        start_ok = uno.prop("Start Date").is_empty() | (uno.prop("Start Date") <= as_of)
        end_ok = uno.prop("End Date").is_empty() | (uno.prop("End Date") >= as_of)
        return start_ok & end_ok

    def _filters_to_condition(self, filters: ConstraintQueryFilters):
        cond = uno.prop("UID").is_not_empty()

        if filters.require_active:
            cond = cond & self._active_condition(filters.as_of)

        if filters.stage:
            cond = cond & uno.prop("Applies Stages").contains(self._to_stage(filters.stage))

        if filters.event_types_any:
            ev_cond = None
            for et in filters.event_types_any:
                term = uno.prop("Applies Event Types").contains(self._to_event_type(et))
                ev_cond = term if ev_cond is None else (ev_cond | term)
            if ev_cond is not None:
                cond = cond & ev_cond

        if filters.scopes_any:
            scope_cond = None
            for scope in filters.scopes_any:
                term = uno.prop("Scope") == self._to_scope(scope)
                scope_cond = term if scope_cond is None else (scope_cond | term)
            if scope_cond is not None:
                cond = cond & scope_cond

        if filters.statuses_any:
            st_cond = None
            for st in filters.statuses_any:
                term = uno.prop("Status") == self._to_status(st)
                st_cond = term if st_cond is None else (st_cond | term)
            if st_cond is not None:
                cond = cond & st_cond

        if filters.necessities_any:
            ne_cond = None
            for ne in filters.necessities_any:
                term = uno.prop("Necessity") == self._to_necessity(ne)
                ne_cond = term if ne_cond is None else (ne_cond | term)
            if ne_cond is not None:
                cond = cond & ne_cond

        if filters.text_query:
            tq = filters.text_query
            cond = cond & (uno.prop("Name").contains(tq) | uno.prop("Description").contains(tq))

        return cond

    def _get_constraint_by_uid(self, uid: str) -> Optional[uno.Page]:
        view = self.constraints_db.query.filter(uno.prop("UID") == uid).execute()
        pages = list(view)
        return pages[0] if pages else None

    def _resolve_topics_by_name(self, names: Sequence[str]) -> List[uno.Page]:
        out: List[uno.Page] = []
        for name in names:
            exact = list(self.topics_db.query.filter(uno.prop("Name") == name).execute())
            if exact:
                out.append(exact[0])
                continue
            contains = list(
                self.topics_db.query.filter(uno.prop("Name").contains(name)).execute()
            )
            if contains:
                out.append(contains[0])
                continue
            out.append(TBTopic.create(name=name, description=""))
        return out

    def _resolve_types_by_id(self, type_ids: Sequence[str]) -> List[uno.Page]:
        out: List[uno.Page] = []
        for type_id in type_ids:
            exact = list(
                self.types_db.query.filter(uno.prop("Type ID") == type_id).execute()
            )
            if exact:
                out.append(exact[0])
                continue
            by_name = list(
                self.types_db.query.filter(uno.prop("Name") == type_id).execute()
            )
            if by_name:
                out.append(by_name[0])
        return out

    def _attach_constraint_type(self, constraint_page: uno.Page, type_id: str) -> None:
        type_pages = self._resolve_types_by_id([type_id])
        if not type_pages:
            return
        type_page = type_pages[0]
        existing = getattr(type_page.props, "constraints", None) or []
        if constraint_page in existing:
            return
        updated = list(existing) + [constraint_page]
        type_page.update_props(constraints=updated)

    def _apply_supersede_side_effects(
        self, *, superseded_pages: List[uno.Page], new_start: Optional[str]
    ) -> None:
        if new_start:
            try:
                end_dt = date.fromisoformat(new_start)
            except Exception:
                end_dt = date.today()
        else:
            end_dt = date.today()

        for page in superseded_pages:
            page.update_props(end_date=end_dt)

    def _replace_windows(self, constraint_page: uno.Page, windows: List[Dict[str, Any]]):
        old = self.windows_db.query.filter(
            uno.prop("Constraint").contains(constraint_page)
        ).execute()
        for window_page in list(old):
            if hasattr(window_page, "delete"):
                window_page.delete()

        for window in windows:
            kind = self._to_window_kind(window.get("kind"))
            start = window.get("start_time_local")
            end = window.get("end_time_local")
            title = f"{kind.name} {start}-{end}"
            TBConstraintWindow.create(
                name=title,
                constraint=[constraint_page],
                kind=kind,
                start_time_local=start,
                end_time_local=end,
            )

    # ----------------------------
    # Enum coercion helpers
    # ----------------------------

    def _to_necessity(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        if value == "must":
            return Necessity.MUST
        if value == "should":
            return Necessity.SHOULD
        raise ValueError(f"Unknown necessity: {value}")

    def _to_status(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        if value == "locked":
            return CStatus.LOCKED
        if value == "proposed":
            return CStatus.PROPOSED
        raise ValueError(f"Unknown status: {value}")

    def _to_source(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        mapping = {
            "user": CSource.USER,
            "calendar": CSource.CALENDAR,
            "system": CSource.SYSTEM,
            "feedback": CSource.FEEDBACK,
        }
        if value in mapping:
            return mapping[value]
        raise ValueError(f"Unknown source: {value}")

    def _to_scope(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        mapping = {
            "session": Scope.SESSION,
            "profile": Scope.PROFILE,
            "datespan": Scope.DATESPAN,
        }
        if value in mapping:
            return mapping[value]
        raise ValueError(f"Unknown scope: {value}")

    def _to_rule_kind(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        for opt in RuleKind.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown rule_kind: {value}")

    def _to_contiguity(self, value: Optional[str]) -> Optional[uno.Option]:
        if not value:
            return None
        mapping = {
            "prefer": Contiguity.PREFER,
            "require": Contiguity.REQUIRE,
            "irrelevant": Contiguity.IRRELEVANT,
        }
        if value in mapping:
            return mapping[value]
        raise ValueError(f"Unknown contiguity: {value}")

    def _to_stage(self, value: str) -> uno.Option:
        for opt in Stage.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown stage: {value}")

    def _to_event_type(self, value: str) -> uno.Option:
        for opt in EventType.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown event type: {value}")

    def _to_dow(self, value: str) -> uno.Option:
        for opt in DOW.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown day-of-week: {value}")

    def _to_window_kind(self, value: str) -> uno.Option:
        if value == "avoid":
            return WindowKind.AVOID
        return WindowKind.PREFER

    def _to_decision_scope(self, value: str) -> uno.Option:
        for opt in DecisionScope.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown decision_scope: {value}")

    def _to_extraction_action(self, value: str) -> uno.Option:
        for opt in ExtractionAction.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown action: {value}")

    def _to_rule_shape(self, value: Optional[str]) -> uno.Option:
        if not value:
            raise ValueError("rule_shape is required")
        for opt in RuleShape.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown rule_shape: {value}")

    def _to_scalar_requirement(self, value: str) -> uno.Option:
        for opt in ScalarRequirement.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown scalar requirement: {value}")

    def _to_type_status(self, value: str) -> uno.Option:
        for opt in TypeStatus.to_list():
            if opt.name == value:
                return opt
        raise ValueError(f"Unknown type status: {value}")


def get_notion_session(*, notion_token: Optional[str] = None) -> uno.Session:
    """Return a shared ultimate-notion Session, optionally seeding `NOTION_TOKEN`."""

    if notion_token and not os.environ.get("NOTION_TOKEN"):
        os.environ["NOTION_TOKEN"] = notion_token
    return uno.Session.get_or_create()


def _write_registry_block(
    notion: uno.Session, parent_page_id: str, dbs: NotionPreferenceDBs
) -> None:
    parent = notion.get_page(parent_page_id)
    registry = {
        "TB Topics": dbs.topics_db_id,
        "TB Constraint Types": dbs.types_db_id,
        "TB Constraints": dbs.constraints_db_id,
        "TB Constraint Windows": dbs.windows_db_id,
        "TB Constraint Events": dbs.events_db_id,
    }
    parent.append(
        [
            uno.Heading2("Timeboxing Preference Memory (Registry)"),
            uno.Code(
                json.dumps(registry, indent=2),
                language=uno.CodeLang.JSON,
                caption="DB IDs",
            ),
        ]
    )


__all__ = [
    "CSource",
    "CStatus",
    "ConstraintQueryFilters",
    "Contiguity",
    "DOW",
    "DecisionScope",
    "EventType",
    "ExtractionAction",
    "Necessity",
    "NotionConstraintStore",
    "NotionPreferenceDBs",
    "RuleKind",
    "RuleShape",
    "ScalarRequirement",
    "Scope",
    "Stage",
    "TBConstraint",
    "TBConstraintEvent",
    "TBConstraintWindow",
    "TBConstraintType",
    "TBTopic",
    "TypeStatus",
    "WindowKind",
    "install_preference_dbs",
    "get_notion_session",
    "seed_default_constraint_types",
]


def seed_default_constraint_types(store: NotionConstraintStore) -> List[uno.Page]:
    defaults = [
        {
            "type_id": "prefer_window",
            "name": "Prefer Window",
            "rule_shape": "prefer_window",
            "requires_windows": True,
            "requires_scalars": [],
        },
        {
            "type_id": "avoid_window",
            "name": "Avoid Window",
            "rule_shape": "avoid_window",
            "requires_windows": True,
            "requires_scalars": [],
        },
        {
            "type_id": "must_window",
            "name": "Must Window",
            "rule_shape": "must_window",
            "requires_windows": True,
            "requires_scalars": [],
        },
        {
            "type_id": "fixed_anchor_time",
            "name": "Fixed Anchor Time",
            "rule_shape": "fixed_anchor_time",
            "requires_windows": True,
            "requires_scalars": ["anchor_time"],
        },
        {
            "type_id": "fixed_anchor_interval",
            "name": "Fixed Anchor Interval",
            "rule_shape": "fixed_anchor_interval",
            "requires_windows": True,
            "requires_scalars": [],
        },
        {
            "type_id": "count_target_per_day",
            "name": "Count Target Per Day",
            "rule_shape": "count_target_per_day",
            "requires_windows": False,
            "requires_scalars": ["count"],
        },
        {
            "type_id": "count_cap_per_day",
            "name": "Count Cap Per Day",
            "rule_shape": "count_cap_per_day",
            "requires_windows": False,
            "requires_scalars": ["count"],
        },
        {
            "type_id": "minutes_target_per_day",
            "name": "Minutes Target Per Day",
            "rule_shape": "minutes_target_per_day",
            "requires_windows": False,
            "requires_scalars": ["minutes"],
        },
        {
            "type_id": "minutes_cap_per_day",
            "name": "Minutes Cap Per Day",
            "rule_shape": "minutes_cap_per_day",
            "requires_windows": False,
            "requires_scalars": ["minutes"],
        },
        {
            "type_id": "fraction_cap",
            "name": "Fraction Cap",
            "rule_shape": "fraction_cap",
            "requires_windows": False,
            "requires_scalars": ["fraction"],
        },
        {
            "type_id": "duration_range",
            "name": "Duration Range",
            "rule_shape": "duration_range",
            "requires_windows": False,
            "requires_scalars": ["duration_min", "duration_max"],
        },
        {
            "type_id": "min_block_duration",
            "name": "Min Block Duration",
            "rule_shape": "min_block_duration",
            "requires_windows": False,
            "requires_scalars": ["duration_min"],
        },
        {
            "type_id": "max_block_duration",
            "name": "Max Block Duration",
            "rule_shape": "max_block_duration",
            "requires_windows": False,
            "requires_scalars": ["duration_max"],
        },
        {
            "type_id": "granularity_preference",
            "name": "Granularity Preference",
            "rule_shape": "granularity_preference",
            "requires_windows": False,
            "requires_scalars": ["contiguity"],
        },
        {
            "type_id": "order_before_after",
            "name": "Order Before After",
            "rule_shape": "order_before_after",
            "requires_windows": False,
            "requires_scalars": [],
        },
        {
            "type_id": "buffer_after",
            "name": "Buffer After",
            "rule_shape": "buffer_after",
            "requires_windows": False,
            "requires_scalars": ["buffer_minutes"],
        },
        {
            "type_id": "buffer_before",
            "name": "Buffer Before",
            "rule_shape": "buffer_before",
            "requires_windows": False,
            "requires_scalars": ["buffer_minutes"],
        },
        {
            "type_id": "min_gap_between",
            "name": "Min Gap Between",
            "rule_shape": "min_gap_between",
            "requires_windows": False,
            "requires_scalars": ["gap_minutes"],
        },
        {
            "type_id": "no_adjacency",
            "name": "No Adjacency",
            "rule_shape": "no_adjacency",
            "requires_windows": False,
            "requires_scalars": [],
        },
        {
            "type_id": "day_template",
            "name": "Day Template",
            "rule_shape": "day_template",
            "requires_windows": False,
            "requires_scalars": [],
        },
    ]
    pages: List[uno.Page] = []
    for payload in defaults:
        pages.append(store.upsert_constraint_type(payload))
    return pages
