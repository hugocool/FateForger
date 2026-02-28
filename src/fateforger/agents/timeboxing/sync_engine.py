"""Deterministic, incremental, reversible calendar sync engine.

Computes minimal create / update / delete ops by diffing a ``TBPlan``
(desired schedule) against the remote Google Calendar state fetched via
MCP.  Every remote mutation is logged in a ``SyncTransaction`` for
deterministic undo.

Key design decisions
--------------------
* **DeepDiff** detects meaningful field changes (summary, start, end,
  description, colorId) and ignores GCal noise (etag, updated, sequence).
* Agent-owned events are identified by a deterministic ``fftb*`` event-ID
  prefix (base32hex).  Foreign events are never mutated.
* ``undo_sync`` replays compensating ops in reverse order.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from deepdiff import DeepDiff

from fateforger.adapters.calendar.models import GCalEventsResponse

from .calendar_reconciliation import reconcile_calendar_ops
from .tb_models import ET_COLOR_MAP, FixedWindow, TBEvent, TBPlan, gcal_color_to_et

logger = logging.getLogger(__name__)

FFTB_PREFIX = "fftb"
"""Prefix for agent-owned GCal event IDs."""


# ── Helpers ──────────────────────────────────────────────────────────────


def base32hex_id(seed: str, *, prefix: str = FFTB_PREFIX, max_len: int = 64) -> str:
    """Deterministic GCal-safe event ID.

    GCal event IDs must contain only lowercase ``a-v`` and ``0-9``
    (base32hex alphabet).

    Args:
        seed: Seed string (typically ``date|name|start|index``).
        prefix: Prefix for owned events.
        max_len: Maximum ID length (GCal allows up to 1024).

    Returns:
        A deterministic, GCal-safe event ID string.
    """
    digest = hashlib.sha1(seed.encode("utf-8")).digest()
    token = base64.b32hexencode(digest).decode("ascii").lower().rstrip("=")
    return (prefix + token)[:max_len]


def is_owned_event(event_id: str) -> bool:
    """Return ``True`` if this event was created by the agent.

    Args:
        event_id: Google Calendar event ID.

    Returns:
        Whether the event ID starts with the agent prefix.
    """
    return event_id.startswith(FFTB_PREFIX)


def _calendar_mcp_datetime(value: datetime) -> str:
    """Format datetimes to MCP-required ISO8601 without timezone suffix."""
    return value.replace(tzinfo=None, microsecond=0).isoformat()


# ── Canonical representation (for DeepDiff) ──────────────────────────────


def _canonical(resolved: dict) -> dict[str, str]:
    """Reduce a resolved event to the fields we care about for diffing.

    Args:
        resolved: A dict from ``TBPlan.resolve_times()``.

    Returns:
        Dict with summary, start, end, description, colorId.
    """
    return {
        "summary": resolved["n"],
        "start": resolved["start_time"].isoformat(),
        "end": resolved["end_time"].isoformat(),
        "description": resolved.get("d", ""),
        "colorId": ET_COLOR_MAP.get(resolved["t"], "0"),
    }


# ── GCal response → TBPlan ──────────────────────────────────────────────


def gcal_response_to_tb_plan(
    resp: GCalEventsResponse,
    *,
    plan_date: date_type,
    tz_name: str = "Europe/Amsterdam",
) -> tuple[TBPlan, dict[str, str]]:
    """Convert a GCal MCP ``list-events`` response into a ``TBPlan``.

    All existing calendar events become ``FixedWindow`` anchors since
    they already have concrete start/end times.

    Args:
        resp: Parsed GCal events response from MCP.
        plan_date: The date we're planning for.
        tz_name: IANA timezone name.

    Returns:
        Tuple of ``(plan, event_id_map)`` where ``event_id_map`` maps
        ``(summary, start_iso)`` → ``gcal_event_id``.
    """
    plan, event_id_map, _event_ids_by_index = gcal_response_to_tb_plan_with_identity(
        resp,
        plan_date=plan_date,
        tz_name=tz_name,
    )
    return plan, event_id_map


def gcal_response_to_tb_plan_with_identity(
    resp: GCalEventsResponse,
    *,
    plan_date: date_type,
    tz_name: str = "Europe/Amsterdam",
) -> tuple[TBPlan, dict[str, str], list[str]]:
    """Convert list-events response into a TBPlan and ordered remote IDs.

    Returns:
        ``(plan, event_id_map, event_ids_by_index)`` where:
        - ``event_id_map`` preserves legacy ``summary|start`` lookups.
        - ``event_ids_by_index`` aligns with ``plan.resolve_times()`` ordering.
    """
    tz = ZoneInfo(tz_name)
    event_pairs: list[tuple[TBEvent, str]] = []
    event_id_map: dict[str, str] = {}

    for ge in resp.events:
        if not ge.start.date_time or not ge.end.date_time:
            continue
        if ge.status and ge.status.lower() == "cancelled":
            continue

        start_dt = date_parser.isoparse(ge.start.date_time).astimezone(tz)
        end_dt = date_parser.isoparse(ge.end.date_time).astimezone(tz)
        if start_dt.date() != plan_date:
            continue
        if end_dt <= start_dt:
            continue

        color_id = getattr(ge, "colorId", None) or getattr(ge, "color_id", None)
        et = gcal_color_to_et(color_id)
        summary = ge.summary or "Busy"
        tb_event = TBEvent(
            n=summary,
            d="",
            t=et,
            p=FixedWindow(st=start_dt.time(), et=end_dt.time()),
        )
        event_pairs.append((tb_event, ge.id))
        key = f"{summary}|{start_dt.time().isoformat()}"
        event_id_map.setdefault(key, ge.id)

    event_pairs.sort(key=lambda pair: pair[0].p.st if hasattr(pair[0].p, "st") else time(0, 0))
    events = [pair[0] for pair in event_pairs]
    event_ids_by_index = [pair[1] for pair in event_pairs]
    plan = TBPlan(events=events, date=plan_date, tz=tz_name)
    return plan, event_id_map, event_ids_by_index


# ── SyncOp / SyncTransaction ────────────────────────────────────────────


class SyncOpType(str, Enum):
    """Type of remote calendar operation."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass
class SyncOp:
    """A single MCP calendar mutation.

    Attributes:
        op_type: create / update / delete.
        gcal_event_id: The target GCal event ID.
        after_payload: The MCP tool arguments for the forward op.
        before_payload: Snapshot before mutation (for undo).
        tool_name: MCP tool name (``create-event``, etc.).
    """

    op_type: SyncOpType
    gcal_event_id: str
    after_payload: dict[str, Any]
    before_payload: dict[str, Any] | None = None
    tool_name: str = ""

    def __post_init__(self) -> None:
        """Derive ``tool_name`` from ``op_type`` if not set."""
        if not self.tool_name:
            self.tool_name = f"{self.op_type.value}-event"


@dataclass
class SyncTransaction:
    """A batch of sync ops with per-op result tracking.

    Attributes:
        ops: List of SyncOps in execution order.
        results: Per-op result dicts (populated after execution).
        status: Overall status (pending → committed / failed / undone).
        timestamp: When the transaction was created.
    """

    ops: list[SyncOp] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── Plan sync (DeepDiff-based) ───────────────────────────────────────────


def plan_sync(
    remote: TBPlan,
    desired: TBPlan,
    event_id_map: dict[str, str],
    *,
    remote_event_ids_by_index: list[str] | None = None,
    calendar_id: str = "primary",
) -> list[SyncOp]:
    """Compute minimal create/update/delete ops with explicit reconciliation."""
    tz = ZoneInfo(desired.tz)
    ops: list[SyncOp] = []
    plan = reconcile_calendar_ops(
        remote=remote,
        desired=desired,
        event_id_map=event_id_map,
        remote_event_ids_by_index=remote_event_ids_by_index,
    )

    for desired_record in plan.creates:
        resolved = desired_record.resolved
        start_dt = datetime.combine(desired.date, desired_record.start_time, tzinfo=tz)
        end_dt = datetime.combine(desired.date, desired_record.end_time, tzinfo=tz)
        seed = (
            f"{desired.date}|{desired_record.summary}|"
            f"{desired_record.start_time}|{desired_record.index}"
        )
        event_id = base32hex_id(seed)
        payload = _build_mcp_payload(
            resolved,
            event_id=event_id,
            start_dt=start_dt,
            end_dt=end_dt,
            tz_name=desired.tz,
            calendar_id=calendar_id,
        )
        ops.append(
            SyncOp(
                op_type=SyncOpType.CREATE,
                gcal_event_id=event_id,
                after_payload=payload,
            )
        )

    for match in plan.updates:
        if not match.remote.event_id:
            continue
        remote_canonical = _canonical(match.remote.resolved)
        desired_canonical = _canonical(match.desired.resolved)
        diff = DeepDiff(
            remote_canonical,
            desired_canonical,
            ignore_order=True,
            verbose_level=2,
        )
        if not diff:
            continue
        if not match.remote.is_owned:
            logger.info(
                "Skipping update for foreign event %s (%s).",
                match.remote.event_id,
                match.match_kind,
            )
            continue

        before_start = datetime.combine(remote.date, match.remote.start_time, tzinfo=tz)
        before_end = datetime.combine(remote.date, match.remote.end_time, tzinfo=tz)
        after_start = datetime.combine(desired.date, match.desired.start_time, tzinfo=tz)
        after_end = datetime.combine(desired.date, match.desired.end_time, tzinfo=tz)
        before_payload = _build_mcp_payload(
            match.remote.resolved,
            event_id=match.remote.event_id,
            start_dt=before_start,
            end_dt=before_end,
            tz_name=remote.tz,
            calendar_id=calendar_id,
        )
        after_payload = _build_mcp_payload(
            match.desired.resolved,
            event_id=match.remote.event_id,
            start_dt=after_start,
            end_dt=after_end,
            tz_name=desired.tz,
            calendar_id=calendar_id,
        )
        ops.append(
            SyncOp(
                op_type=SyncOpType.UPDATE,
                gcal_event_id=match.remote.event_id,
                after_payload=after_payload,
                before_payload=before_payload,
            )
        )

    for foreign_match in plan.noops:
        remote_canonical = _canonical(foreign_match.remote.resolved)
        desired_canonical = _canonical(foreign_match.desired.resolved)
        diff = DeepDiff(
            remote_canonical,
            desired_canonical,
            ignore_order=True,
            verbose_level=2,
        )
        if diff:
            logger.info(
                "No-op for foreign event match (kind=%s, summary=%s).",
                foreign_match.match_kind,
                foreign_match.remote.summary,
            )

    for remote_record in plan.deletes:
        if not remote_record.event_id:
            continue
        before_start = datetime.combine(remote.date, remote_record.start_time, tzinfo=tz)
        before_end = datetime.combine(remote.date, remote_record.end_time, tzinfo=tz)
        before_payload = _build_mcp_payload(
            remote_record.resolved,
            event_id=remote_record.event_id,
            start_dt=before_start,
            end_dt=before_end,
            tz_name=remote.tz,
            calendar_id=calendar_id,
        )
        ops.append(
            SyncOp(
                op_type=SyncOpType.DELETE,
                gcal_event_id=remote_record.event_id,
                after_payload={"calendarId": calendar_id, "eventId": remote_record.event_id},
                before_payload=before_payload,
            )
        )

    for skipped in plan.skips:
        logger.debug(
            "Sync skip: %s (desired_index=%s remote_index=%s)",
            skipped.reason,
            skipped.desired_index,
            skipped.remote_index,
        )

    order = {SyncOpType.CREATE: 0, SyncOpType.UPDATE: 1, SyncOpType.DELETE: 2}
    ops.sort(key=lambda op: order.get(op.op_type, 99))
    return ops


# ── Execute / Undo ───────────────────────────────────────────────────────


async def execute_sync(
    ops: list[SyncOp],
    mcp_workbench: Any,
) -> SyncTransaction:
    """Execute sync ops against the MCP calendar server.

    Args:
        ops: Ordered list of ``SyncOp`` to execute.
        mcp_workbench: An ``McpWorkbench`` instance for MCP tool calls.

    Returns:
        A ``SyncTransaction`` with per-op results and overall status.
    """
    tx = SyncTransaction(ops=ops)
    all_ok = True

    for op in ops:
        try:
            result = await mcp_workbench.call_tool(
                op.tool_name,
                arguments=op.after_payload,
            )
            is_error = getattr(result, "is_error", False)
            content = _extract_result_content(result)
            tx.results.append(
                {
                    "tool": op.tool_name,
                    "event_id": op.gcal_event_id,
                    "ok": not is_error,
                    "content": content,
                }
            )
            if is_error:
                all_ok = False
                logger.warning(
                    "Sync op failed: %s %s — %s",
                    op.tool_name,
                    op.gcal_event_id,
                    content,
                )
        except Exception as exc:
            all_ok = False
            tx.results.append(
                {
                    "tool": op.tool_name,
                    "event_id": op.gcal_event_id,
                    "ok": False,
                    "error": str(exc),
                }
            )
            logger.exception("Sync op exception: %s %s", op.tool_name, op.gcal_event_id)

    tx.status = "committed" if all_ok else "partial"
    return tx


async def undo_sync(
    tx: SyncTransaction,
    mcp_workbench: Any,
) -> SyncTransaction:
    """Undo a committed sync transaction via compensating ops.

    * Created events → delete
    * Updated events → update with ``before_payload``
    * Deleted events → create with ``before_payload``

    Args:
        tx: The transaction to undo.
        mcp_workbench: An ``McpWorkbench`` instance.

    Returns:
        A new ``SyncTransaction`` representing the undo.
    """
    if len(tx.results) != len(tx.ops):
        raise ValueError(
            "Cannot undo sync transaction without complete per-op execution results."
        )

    successful_forward_ops = [
        op for index, op in enumerate(tx.ops) if bool(tx.results[index].get("ok"))
    ]
    undo_ops: list[SyncOp] = []

    for op in reversed(successful_forward_ops):
        if op.op_type == SyncOpType.CREATE:
            # Undo create → delete
            undo_ops.append(
                SyncOp(
                    op_type=SyncOpType.DELETE,
                    gcal_event_id=op.gcal_event_id,
                    after_payload={
                        "calendarId": op.after_payload.get("calendarId", "primary"),
                        "eventId": op.gcal_event_id,
                    },
                )
            )
        elif op.op_type == SyncOpType.UPDATE and op.before_payload:
            # Undo update → restore previous state
            undo_ops.append(
                SyncOp(
                    op_type=SyncOpType.UPDATE,
                    gcal_event_id=op.gcal_event_id,
                    after_payload=op.before_payload,
                )
            )
        elif op.op_type == SyncOpType.DELETE and op.before_payload:
            # Undo delete → recreate
            undo_ops.append(
                SyncOp(
                    op_type=SyncOpType.CREATE,
                    gcal_event_id=op.gcal_event_id,
                    after_payload=op.before_payload,
                )
            )

    if not undo_ops:
        return SyncTransaction(status="undone")

    undo_tx = await execute_sync(undo_ops, mcp_workbench)
    undo_tx.status = "undone" if undo_tx.status == "committed" else "undo_partial"
    return undo_tx


# ── Internal helpers ─────────────────────────────────────────────────────


def _build_mcp_payload(
    resolved: dict,
    *,
    event_id: str,
    start_dt: datetime,
    end_dt: datetime,
    tz_name: str,
    calendar_id: str,
) -> dict[str, Any]:
    """Build the MCP tool argument dict for a calendar event.

    Args:
        resolved: A dict from ``TBPlan.resolve_times()``.
        event_id: GCal event ID.
        start_dt: Timezone-aware start datetime.
        end_dt: Timezone-aware end datetime.
        tz_name: IANA timezone name.
        calendar_id: Target calendar ID.

    Returns:
        Dict suitable for ``create-event`` or ``update-event`` MCP tools.
    """
    return {
        "calendarId": calendar_id,
        "eventId": event_id,
        "summary": resolved["n"],
        "description": resolved.get("d", ""),
        "start": _calendar_mcp_datetime(start_dt),
        "end": _calendar_mcp_datetime(end_dt),
        "timeZone": tz_name,
        "colorId": ET_COLOR_MAP.get(resolved["t"], "0"),
    }


def _extract_result_content(result: Any) -> str:
    """Extract content text from an MCP tool result.

    Args:
        result: Raw MCP tool result object.

    Returns:
        Content string.
    """
    # result.result is a list of content objects
    inner = getattr(result, "result", None)
    if isinstance(inner, list):
        parts = []
        for item in inner:
            text = getattr(item, "text", None) or getattr(item, "content", None)
            if text:
                parts.append(str(text))
        if parts:
            return "\n".join(parts)

    text = getattr(result, "text", None) or getattr(result, "content", None)
    if text:
        return str(text)

    return str(result)


__all__ = [
    "FFTB_PREFIX",
    "SyncOp",
    "SyncOpType",
    "SyncTransaction",
    "base32hex_id",
    "execute_sync",
    "gcal_response_to_tb_plan",
    "gcal_response_to_tb_plan_with_identity",
    "is_owned_event",
    "plan_sync",
    "undo_sync",
]
