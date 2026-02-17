"""Reconciliation helpers for desired-vs-remote calendar planning.

This module matches desired TBPlan events to remote TBPlan events using
deterministic pass ordering so sync can emit stable calendar operations
without duplicate creates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from typing import Any, Literal

from .tb_models import TBPlan

MatchKind = Literal["id", "canonical", "fuzzy"]


def event_key(summary: str, start_time: time) -> str:
    """Return the canonical key used by legacy event-id maps."""
    return f"{summary}|{start_time.isoformat()}"


def canonical_tuple(summary: str, start_time: time, end_time: time) -> tuple[str, str, str]:
    """Return a hashable canonical identity tuple."""
    return (summary, start_time.isoformat(), end_time.isoformat())


def _normalize_summary(summary: str) -> str:
    return " ".join(summary.strip().lower().split())


def _minutes_between(a: time, b: time) -> int:
    return abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))


def _overlap_minutes(a_start: time, a_end: time, b_start: time, b_end: time) -> int:
    start = max(a_start.hour * 60 + a_start.minute, b_start.hour * 60 + b_start.minute)
    end = min(a_end.hour * 60 + a_end.minute, b_end.hour * 60 + b_end.minute)
    return max(0, end - start)


@dataclass(frozen=True)
class RemoteEventRecord:
    """Resolved remote event enriched with identity metadata."""

    index: int
    summary: str
    start_time: time
    end_time: time
    event_id: str | None
    is_owned: bool
    resolved: dict[str, Any]

    @property
    def key(self) -> str:
        return event_key(self.summary, self.start_time)

    @property
    def canonical(self) -> tuple[str, str, str]:
        return canonical_tuple(self.summary, self.start_time, self.end_time)


@dataclass(frozen=True)
class DesiredEventRecord:
    """Resolved desired event enriched with prior lineage hints."""

    index: int
    summary: str
    start_time: time
    end_time: time
    hinted_event_id: str | None
    resolved: dict[str, Any]

    @property
    def key(self) -> str:
        return event_key(self.summary, self.start_time)

    @property
    def canonical(self) -> tuple[str, str, str]:
        return canonical_tuple(self.summary, self.start_time, self.end_time)


@dataclass(frozen=True)
class EventMatch:
    """A one-to-one match between desired and remote event records."""

    desired: DesiredEventRecord
    remote: RemoteEventRecord
    match_kind: MatchKind


@dataclass(frozen=True)
class SkippedItem:
    """An explicitly skipped operation candidate and the reason."""

    reason: str
    desired_index: int | None = None
    remote_index: int | None = None


@dataclass
class CalendarOpPlan:
    """Reconciliation output used by the sync engine."""

    matches: list[EventMatch] = field(default_factory=list)
    creates: list[DesiredEventRecord] = field(default_factory=list)
    updates: list[EventMatch] = field(default_factory=list)
    deletes: list[RemoteEventRecord] = field(default_factory=list)
    noops: list[EventMatch] = field(default_factory=list)
    skips: list[SkippedItem] = field(default_factory=list)


def build_remote_records(
    *,
    remote: TBPlan,
    event_id_map: dict[str, str],
    remote_event_ids_by_index: list[str] | None = None,
    owned_prefix: str = "fftb",
) -> list[RemoteEventRecord]:
    """Build resolved remote records with robust identity lookup."""
    resolved = remote.resolve_times()
    records: list[RemoteEventRecord] = []
    for index, item in enumerate(resolved):
        summary = str(item["n"])
        start_time = item["start_time"]
        end_time = item["end_time"]
        key = event_key(summary, start_time)
        event_id: str | None = None
        if remote_event_ids_by_index and index < len(remote_event_ids_by_index):
            candidate = remote_event_ids_by_index[index]
            event_id = candidate if candidate else None
        if event_id is None:
            event_id = event_id_map.get(key)
        records.append(
            RemoteEventRecord(
                index=index,
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                event_id=event_id,
                is_owned=bool(event_id and event_id.startswith(owned_prefix)),
                resolved=item,
            )
        )
    return records


def build_desired_records(
    *,
    desired: TBPlan,
    event_id_map: dict[str, str],
) -> list[DesiredEventRecord]:
    """Build resolved desired records with lineage hints from event-id map."""
    resolved = desired.resolve_times()
    return [
        DesiredEventRecord(
            index=index,
            summary=str(item["n"]),
            start_time=item["start_time"],
            end_time=item["end_time"],
            hinted_event_id=event_id_map.get(event_key(str(item["n"]), item["start_time"])),
            resolved=item,
        )
        for index, item in enumerate(resolved)
    ]


def reconcile_calendar_ops(
    *,
    remote: TBPlan,
    desired: TBPlan,
    event_id_map: dict[str, str],
    remote_event_ids_by_index: list[str] | None = None,
    fuzzy_start_tolerance_min: int = 20,
) -> CalendarOpPlan:
    """Reconcile desired and remote plans into deterministic op candidates."""
    remote_records = build_remote_records(
        remote=remote,
        event_id_map=event_id_map,
        remote_event_ids_by_index=remote_event_ids_by_index,
    )
    desired_records = build_desired_records(desired=desired, event_id_map=event_id_map)

    remaining_remote: set[int] = {record.index for record in remote_records}
    remaining_desired: set[int] = {record.index for record in desired_records}
    remote_by_index = {record.index: record for record in remote_records}
    desired_by_index = {record.index: record for record in desired_records}

    matches: list[EventMatch] = []

    # Pass 1: explicit ID lineage.
    remote_by_id: dict[str, list[RemoteEventRecord]] = {}
    for record in remote_records:
        if record.event_id:
            remote_by_id.setdefault(record.event_id, []).append(record)
    for desired_record in desired_records:
        if desired_record.index not in remaining_desired:
            continue
        if not desired_record.hinted_event_id:
            continue
        candidates = [
            record
            for record in remote_by_id.get(desired_record.hinted_event_id, [])
            if record.index in remaining_remote
        ]
        if not candidates:
            continue
        remote_record = min(candidates, key=lambda record: record.index)
        matches.append(
            EventMatch(
                desired=desired_record,
                remote=remote_record,
                match_kind="id",
            )
        )
        remaining_desired.discard(desired_record.index)
        remaining_remote.discard(remote_record.index)

    # Pass 2: exact canonical identity.
    remote_by_canonical: dict[tuple[str, str, str], list[RemoteEventRecord]] = {}
    for index in sorted(remaining_remote):
        record = remote_by_index[index]
        remote_by_canonical.setdefault(record.canonical, []).append(record)
    for desired_index in sorted(remaining_desired):
        desired_record = desired_by_index[desired_index]
        candidates = remote_by_canonical.get(desired_record.canonical, [])
        candidates = [record for record in candidates if record.index in remaining_remote]
        if not candidates:
            continue
        remote_record = min(candidates, key=lambda record: record.index)
        matches.append(
            EventMatch(
                desired=desired_record,
                remote=remote_record,
                match_kind="canonical",
            )
        )
        remaining_desired.discard(desired_record.index)
        remaining_remote.discard(remote_record.index)

    # Pass 3: conservative fuzzy match.
    for desired_index in sorted(remaining_desired):
        desired_record = desired_by_index[desired_index]
        desired_summary = _normalize_summary(desired_record.summary)
        best_score: tuple[int, int, int] | None = None
        best_remote: RemoteEventRecord | None = None
        for remote_index in sorted(remaining_remote):
            remote_record = remote_by_index[remote_index]
            if _normalize_summary(remote_record.summary) != desired_summary:
                continue
            overlap = _overlap_minutes(
                desired_record.start_time,
                desired_record.end_time,
                remote_record.start_time,
                remote_record.end_time,
            )
            start_delta = _minutes_between(desired_record.start_time, remote_record.start_time)
            if overlap <= 0 and start_delta > fuzzy_start_tolerance_min:
                continue
            duration_delta = abs(
                _minutes_between(desired_record.start_time, desired_record.end_time)
                - _minutes_between(remote_record.start_time, remote_record.end_time)
            )
            score = (overlap, -start_delta, -duration_delta)
            if best_score is None or score > best_score:
                best_score = score
                best_remote = remote_record
        if best_remote is None:
            continue
        matches.append(
            EventMatch(
                desired=desired_record,
                remote=best_remote,
                match_kind="fuzzy",
            )
        )
        remaining_desired.discard(desired_record.index)
        remaining_remote.discard(best_remote.index)

    creates = [desired_by_index[index] for index in sorted(remaining_desired)]
    updates: list[EventMatch] = []
    noops: list[EventMatch] = []
    skips: list[SkippedItem] = []
    for match in matches:
        if not match.remote.event_id:
            skips.append(
                SkippedItem(
                    reason="matched-remote-without-event-id",
                    desired_index=match.desired.index,
                    remote_index=match.remote.index,
                )
            )
            continue
        if match.remote.is_owned:
            updates.append(match)
            continue
        noops.append(match)

    deletes: list[RemoteEventRecord] = []
    for remote_index in sorted(remaining_remote):
        remote_record = remote_by_index[remote_index]
        if remote_record.is_owned and remote_record.event_id:
            deletes.append(remote_record)
            continue
        skips.append(
            SkippedItem(
                reason="unmatched-foreign-remote",
                remote_index=remote_record.index,
            )
        )

    return CalendarOpPlan(
        matches=matches,
        creates=creates,
        updates=updates,
        deletes=deletes,
        noops=noops,
        skips=skips,
    )


__all__ = [
    "CalendarOpPlan",
    "DesiredEventRecord",
    "EventMatch",
    "RemoteEventRecord",
    "SkippedItem",
    "build_desired_records",
    "build_remote_records",
    "event_key",
    "reconcile_calendar_ops",
]
