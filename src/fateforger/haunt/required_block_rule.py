"""The required-block watcher (spec §4, #213).

A memory rule can say a block of a registered kind must be on the day
(`requires_block`). This rule, evaluated on the haunt's reconcile tick beside
the planning ladder, checks each required kind against the calendar and starts
the nudge ladder when the block is gone or has left its bounds.

Presence is equality over identifiers this system minted: the registry slug
against the `tmbx.slug` private property tmbx writes at commit, plus -- for the
`planning` kind only -- the mark the nudge's own booking carries. Never a title.

The register is a cache. The fast path fetches the remembered event by id; a
miss lists the day once and re-derives from the mark. It may be wrong for one
tick and no longer, and it holds nothing that cannot be recomputed.

A failed read gives no verdict (#226). An unreadable calendar and an empty one
are different outcomes, and haunting on the first would teach the user to
ignore the nudge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.required_blocks import required_blocks_value
from fateforger.agents.timeboxing.session_contracts import PlanningDay

from .reconcile import (
    DesiredJob,
    JobKey,
    PlanningReminder,
    PlanningRuleConfig,
    RequiredBlockOutcome,
    _carries_planning_mark,
    _parse_event_dt,
    nudge_offsets,
)

logger = logging.getLogger(__name__)

REQUIRED_BLOCK_KIND = "required_block"
REASON_MISSING = "missing"
REASON_MOVED_OUT = "moved_out"

#: The registry kind the planning ladder already books. Its events may carry
#: the `ffplanning…` mark instead of a slug, and count. Exported so the one
#: place that must tell this kind from the others compares against the constant
#: rather than re-typing the word.
PLANNING_SLUG = "planning"
_SLUG_PROPERTY = "tmbx.slug"
#: A sleep time earlier than this is after midnight and belongs to the next day.
_AFTER_MIDNIGHT_CUTOFF = time(4, 0)


@dataclass(frozen=True)
class RequiredBlockConfig:
    calendar_id: str = "primary"
    tz: str = "Europe/Amsterdam"
    ladder: PlanningRuleConfig = field(default_factory=PlanningRuleConfig)


def slug_of(event: dict) -> str | None:
    """The kind tmbx wrote on this event, or None. A field this system minted."""
    extended = event.get("extendedProperties")
    private = extended.get("private") if isinstance(extended, dict) else None
    slug = private.get(_SLUG_PROPERTY) if isinstance(private, dict) else None
    return slug if isinstance(slug, str) and slug else None


def _sleep_boundary(day: date, tz: str, sleep: str | None) -> datetime:
    zone = ZoneInfo(tz)
    if not sleep:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    try:
        hh, mm = (int(part) for part in sleep.split(":")[:2])
        at = time(hh, mm)
    except (ValueError, TypeError):
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    on = day + timedelta(days=1) if at < _AFTER_MIDNIGHT_CUTOFF else day
    return datetime.combine(on, at, tzinfo=zone)


def within_bounds(event: dict, *, day: date, tz: str, sleep: str | None) -> bool:
    """Starts on `day` in `tz` and ends no later than the sleep boundary."""
    zone = ZoneInfo(tz)
    start = _parse_event_dt(event.get("start"), tz=zone)
    end = _parse_event_dt(event.get("end"), tz=zone)
    if start is None or end is None:
        return False
    if start.astimezone(zone).date() != day:
        return False
    return end <= _sleep_boundary(day, tz, sleep)


def _is_kind(event: dict, slug: str) -> bool:
    if slug_of(event) == slug:
        return True
    return slug == PLANNING_SLUG and _carries_planning_mark(event, None)


class RequiredBlockRule:
    rule_id = "required_blocks"

    def __init__(
        self,
        *,
        calendar_client: Any,
        constraint_store: Any,
        ledger: Any,
        config: RequiredBlockConfig | None = None,
    ) -> None:
        self._calendar = calendar_client
        self._store = constraint_store
        self._ledger = ledger
        self._config = config or RequiredBlockConfig()
        self._cache: dict[tuple[str, str, str], str] = {}

    # -- the register, as a cache -------------------------------------------------
    def cached(self, *, user_id: str, day: date, slug: str) -> str | None:
        return self._cache.get((user_id, day.isoformat(), slug))

    def remember(self, *, user_id: str, day: date, slug: str, event_id: str) -> None:
        self._cache[(user_id, day.isoformat(), slug)] = event_id

    # -- inputs ---------------------------------------------------------------------
    async def _day_type(self, user_id: str, day: date) -> str:
        """The day's classification: the session's locked one, else the weekday.

        The locked day is what the host and the user settled on, and it is the
        only place `vacation`, `holiday` and `sick` are recorded. Falling
        straight to weekday arithmetic asks memory for a working day's rules on
        a Tuesday of annual leave -- the wrong day's rules, silently (R6).
        """
        locked = await self._ledger.day_type_for(owner_user_id=user_id, planning_date=day)
        if isinstance(locked, str) and locked:
            return locked
        return PlanningDay.lock_default(
            value=day, timezone=self._config.tz, lock_revision=1
        ).day_type.value

    async def _required(self, *, user_id: str, day: date) -> list[str]:
        rows = await self._store.query_constraints(
            filters={
                "planned_day": day.isoformat(),
                "day_type": await self._day_type(user_id, day),
                "require_active": True,
            },
            limit=200,
        )
        return list(required_blocks_value(rows).get("slugs") or [])

    async def _sleep(self, user_id: str, day: date) -> str | None:
        frame = await self._ledger.day_frame_for(owner_user_id=user_id, planning_date=day)
        sleep = frame.get("sleep") if isinstance(frame, dict) else None
        return sleep if isinstance(sleep, str) and sleep else None

    # -- job-id prefixes, all minted by this system -----------------------------------
    def scope_prefix(self, scope: str) -> str:
        """Every job this rule owns for `scope`."""
        return f"rule:{self.rule_id}:{scope}:"

    def slug_prefix(self, scope: str, day: date, slug: str) -> str:
        """One slug's ladder for one day."""
        return f"{self.scope_prefix(scope)}{day.isoformat()}:{slug}:"

    # -- evaluation -----------------------------------------------------------------
    async def evaluate(
        self,
        *,
        now: datetime,
        scope: str,
        user_id: str | None = None,
        channel_id: str | None = None,
        first_nudge_offset: timedelta | None = None,
    ) -> RequiredBlockOutcome:
        if not user_id:
            return RequiredBlockOutcome()
        start = now.astimezone(timezone.utc)
        day = now.astimezone(ZoneInfo(self._config.tz)).date()
        try:
            required = await self._required(user_id=user_id, day=day)
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            logger.warning("required_blocks_unreadable user=%s day=%s error_type=%s error=%s",
                           user_id, day, type(exc).__name__, exc)
            # Which kinds the day requires is unknown, so no ladder under this
            # rule can be judged stale. Keep the whole scope.
            return RequiredBlockOutcome(undecided=[self.scope_prefix(scope)])
        if not required:
            return RequiredBlockOutcome()
        sleep = await self._sleep(user_id, day)

        jobs: list[DesiredJob] = []
        undecided: list[str] = []
        for slug in required:
            verdict = await self._check(user_id=user_id, day=day, slug=slug, sleep=sleep)
            if verdict is None:
                # No verdict: unreadable. Leave this slug's ladder exactly as it is.
                undecided.append(self.slug_prefix(scope, day, slug))
                continue
            if verdict == "present":
                continue
            if slug == PLANNING_SLUG and verdict == REASON_MISSING:
                # R2: the two ladders are disjoint for `planning`. Whether the
                # session is on the calendar at all is `PlanningSessionRule`'s
                # business -- it nudges, books the card and starts the session.
                # A second ladder here would nudge for the same absence twice,
                # from two schedules neither of which knows about the other.
                # `moved_out` is the half only the watcher can see: the block
                # exists, so the planning ladder is satisfied, and nothing else
                # notices it drifted off the day.
                continue
            jobs.extend(self._ladder(start=start, scope=scope, user_id=user_id,
                                     channel_id=channel_id, day=day, slug=slug,
                                     reason=verdict, first_nudge_offset=first_nudge_offset))
        return RequiredBlockOutcome(jobs=jobs, undecided=undecided)

    async def recheck(self, *, user_id: str, slug: str, now: datetime) -> str | None:
        """The verdict for one kind, right now: 'present', REASON_MISSING,
        REASON_MOVED_OUT, or None for no verdict.

        The dispatcher asks this before it posts a rung. A scheduled reminder
        is a claim about the calendar as it was minutes or hours ago; the block
        may be back, or may have drifted since, and either way the line the
        rung carries would be wrong. Same predicates, same cache, same
        no-verdict rule as the tick -- one check, asked twice.
        """
        day = now.astimezone(ZoneInfo(self._config.tz)).date()
        try:
            sleep = await self._sleep(user_id, day)
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            logger.warning("required_blocks_unreadable user=%s day=%s error_type=%s error=%s (recheck day_frame)",
                           user_id, day, type(exc).__name__, exc)
            return None
        return await self._check(user_id=user_id, day=day, slug=slug, sleep=sleep)

    async def _check(self, *, user_id: str, day: date, slug: str, sleep: str | None) -> str | None:
        """'present', REASON_MISSING, REASON_MOVED_OUT, or None for no verdict."""
        tz = self._config.tz
        remembered = self.cached(user_id=user_id, day=day, slug=slug)
        if remembered:
            try:
                event = await self._calendar.get_event(calendar_id=self._config.calendar_id, event_id=remembered)
            except Exception as exc:  # noqa: BLE001
                logger.warning("calendar_unreadable user=%s slug=%s error_type=%s error=%s",
                               user_id, slug, type(exc).__name__, exc)
                return None
            if event is not None and _is_kind(event, slug):
                if within_bounds(event, day=day, tz=tz, sleep=sleep):
                    return "present"
                # R4: the id resolves and the kind still matches -- the block
                # was dragged to another day or pushed past sleep. That is
                # `moved_out`, and nothing on this day's list can change it, so
                # the list is not worth a call. Listing here also read as
                # `missing` whenever the drag left the day empty, which is the
                # wrong reason and the wrong line.
                return REASON_MOVED_OUT
        events = await self._calendar.list_day(calendar_id=self._config.calendar_id, day=day, tz=tz)
        if events is None:
            logger.warning("calendar_unreadable user=%s slug=%s day=%s (list_day)", user_id, slug, day)
            return None
        carrying = [e for e in events if isinstance(e, dict) and _is_kind(e, slug)]
        if len(carrying) > 1:
            logger.info("required_block_duplicates user=%s slug=%s count=%d", user_id, slug, len(carrying))
        inside = [e for e in carrying if within_bounds(e, day=day, tz=tz, sleep=sleep)]
        if inside:
            self.remember(user_id=user_id, day=day, slug=slug, event_id=str(inside[0].get("id") or ""))
            return "present"
        return REASON_MOVED_OUT if carrying else REASON_MISSING

    def _ladder(self, *, start, scope, user_id, channel_id, day, slug, reason, first_nudge_offset):
        window = f"{day.isoformat()}:{slug}"
        return [
            DesiredJob(
                key=JobKey("rule", self.rule_id, scope, window, f"nudge{idx}"),
                run_at=start + offset,
                payload=PlanningReminder(
                    scope=scope, kind=REQUIRED_BLOCK_KIND, attempt=idx,
                    message=_line(slug, reason, idx), user_id=user_id, channel_id=channel_id,
                    slug=slug, reason=reason,
                ),
            )
            for idx, offset in enumerate(nudge_offsets(self._config.ladder, first_nudge_offset=first_nudge_offset), start=1)
        ]


def _line(slug: str, reason: str, attempt: int) -> str:
    what = "is not on today's calendar" if reason == REASON_MISSING else "has left today's plan"
    return f"Your `{slug}` block {what}. Put it back, or say when."


__all__ = ["PLANNING_SLUG", "REASON_MISSING", "REASON_MOVED_OUT", "REQUIRED_BLOCK_KIND",
           "RequiredBlockConfig", "RequiredBlockRule", "slug_of", "within_bounds"]
