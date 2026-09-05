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
from datetime import UTC, date, datetime, time, timedelta
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
#: How many of the day's active rules the watcher reads. The read takes no
#: cursor, so a full page means rules were dropped -- possibly the only one
#: requiring a block.
_CONSTRAINT_PAGE = 200


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


def bounds_of(event: dict, *, day: date, tz: str, sleep: str | None) -> bool | None:
    """Starts on `day` in `tz` and ends no later than the sleep boundary.

    None when the start or end will not parse -- a malformed timestamp is not
    evidence the block left its bounds, and `_parse_event_dt` returns None for
    one rather than raising, so an unguarded boolean read would silently score
    it out of bounds (#213).
    """
    zone = ZoneInfo(tz)
    start = _parse_event_dt(event.get("start"), tz=zone)
    end = _parse_event_dt(event.get("end"), tz=zone)
    if start is None or end is None:
        return None
    if start.astimezone(zone).date() != day:
        return False
    return end <= _sleep_boundary(day, tz, sleep)


def within_bounds(event: dict, *, day: date, tz: str, sleep: str | None) -> bool:
    """Starts on `day` in `tz` and ends no later than the sleep boundary."""
    return bounds_of(event, day=day, tz=tz, sleep=sleep) is True


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

    def _evict_other_days(self, day: date) -> None:
        """Drop every entry not about `day`.

        The process runs for weeks and the key carries the day, so without this
        every day the watcher ever looked at stays in the dict -- entries
        nothing will ask for again, since a tick only ever asks about today.
        Comparing two ISO dates this system wrote decides nothing about the
        user.
        """
        today = day.isoformat()
        for key in [k for k in self._cache if k[1] != today]:
            del self._cache[key]

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
            limit=_CONSTRAINT_PAGE,
        )
        if isinstance(rows, list) and len(rows) == _CONSTRAINT_PAGE:
            # A full page and no cursor: whatever the store had past the cap is
            # not in `rows`, and a required kind sitting there is one the
            # watcher will never look for.
            logger.warning("required_blocks_truncated user=%s day=%s limit=%d",
                           user_id, day, _CONSTRAINT_PAGE)
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
        start = now.astimezone(UTC)
        day = now.astimezone(ZoneInfo(self._config.tz)).date()
        self._evict_other_days(day)
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
        try:
            sleep = await self._sleep(user_id, day)
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            logger.warning("required_blocks_unreadable user=%s day=%s error_type=%s error=%s (day_frame)",
                           user_id, day, type(exc).__name__, exc)
            # The sleep boundary is half of `within_bounds`; without it a block
            # inside its bounds cannot be told from one past them. No slug is
            # judged, so no slug's ladder is pruned.
            return RequiredBlockOutcome(
                undecided=[self.slug_prefix(scope, day, slug) for slug in required]
            )

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

    async def _list_kind(self, *, user_id: str, slug: str, day: date, tz: str,
                          sleep: str | None) -> tuple[list[dict], list[dict]] | None:
        """The day's events of `slug`'s kind, split into `(carrying, inside)`.

        None on a failed read, or on any event of the kind whose start or end
        will not parse -- a day with an unreadable event of the kind it is
        being asked about is an unread day for that kind, not a day the
        watcher may judge from whatever else parsed.
        """
        try:
            events = await self._calendar.list_day(calendar_id=self._config.calendar_id, day=day, tz=tz)
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            logger.warning("calendar_unreadable user=%s slug=%s day=%s error_type=%s error=%s (list_day)",
                           user_id, slug, day, type(exc).__name__, exc)
            return None
        if events is None:
            logger.warning("calendar_unreadable user=%s slug=%s day=%s (list_day)", user_id, slug, day)
            return None
        try:
            carrying = [e for e in events if isinstance(e, dict) and _is_kind(e, slug)]
            decisions = [(e, bounds_of(e, day=day, tz=tz, sleep=sleep)) for e in carrying]
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            # A day whose events cannot be read is an unread day. Judging on
            # the ones that did parse would haunt for a block that is there.
            logger.warning("calendar_unreadable user=%s slug=%s day=%s error_type=%s error=%s (parse)",
                           user_id, slug, day, type(exc).__name__, exc)
            return None
        for event, bounds in decisions:
            if bounds is None:
                logger.warning("required_block_event_unparseable user=%s slug=%s day=%s event_id=%s",
                               user_id, slug, day, event.get("id"))
                return None
        if len(carrying) > 1:
            logger.info("required_block_duplicates user=%s slug=%s count=%d", user_id, slug, len(carrying))
        inside = [event for event, bounds in decisions if bounds]
        return carrying, inside

    async def _check(self, *, user_id: str, day: date, slug: str, sleep: str | None) -> str | None:
        """'present', REASON_MISSING, REASON_MOVED_OUT, or None for no verdict."""
        tz = self._config.tz
        remembered = self.cached(user_id=user_id, day=day, slug=slug)
        if remembered:
            try:
                event = await self._calendar.get_event(calendar_id=self._config.calendar_id, event_id=remembered)
                of_kind = event is not None and _is_kind(event, slug)
                bounds = bounds_of(event, day=day, tz=tz, sleep=sleep) if of_kind else None
            except Exception as exc:  # noqa: BLE001 - the fetch or the parse; either way, no verdict
                logger.warning("calendar_unreadable user=%s slug=%s error_type=%s error=%s",
                               user_id, slug, type(exc).__name__, exc)
                return None
            if of_kind:
                if bounds is None:
                    logger.warning("required_block_event_unparseable user=%s slug=%s event_id=%s",
                                   user_id, slug, remembered)
                    return None
                if bounds:
                    return "present"
                # R4: the id resolves and the kind still matches -- the block
                # was dragged to another day or pushed past sleep. List the
                # day once for a replacement: the user may have booked a NEW
                # block of the kind instead of dragging the old one back, and
                # the cache would otherwise keep confirming `moved_out` until
                # the day rolls over. No in-bounds replacement -> the verdict
                # is still `moved_out`, cache left as is.
                listed = await self._list_kind(user_id=user_id, slug=slug, day=day, tz=tz, sleep=sleep)
                if listed is None:
                    return None
                _carrying, inside = listed
                if inside:
                    self.remember(user_id=user_id, day=day, slug=slug, event_id=str(inside[0].get("id") or ""))
                    return "present"
                return REASON_MOVED_OUT
        listed = await self._list_kind(user_id=user_id, slug=slug, day=day, tz=tz, sleep=sleep)
        if listed is None:
            return None
        carrying, inside = listed
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


#: One line per rung, escalating, keyed by why the haunt started. Three rungs
#: for a five-rung ladder: past the third the line stops changing, the way
#: `session_start.NUDGE_LINES` runs out. `{slug}` is the registry kind, an
#: identifier this system minted, shown so the user knows which block is meant.
_MISSING_LINES: tuple[str, ...] = (
    "No `{slug}` block on today's plan. Put one in, or tell me when it happens.",
    "Still no `{slug}` block today — put one in, or tell me it is not happening today.",
    "Today is going and there is still no `{slug}` block. Book it now or write it off.",
)

_MOVED_OUT_LINES: tuple[str, ...] = (
    "Your `{slug}` block has left today's plan. Bring it back, or tell me when it happens.",
    "`{slug}` is still off today's plan — bring it back, or tell me it moved for good.",
    "`{slug}` has been off today's plan all day. Bring it back now or write it off.",
)


def _line(slug: str, reason: str, attempt: int) -> str:
    """The line for rung `attempt` (1-based); past the last rung, the last line."""

    lines = _MISSING_LINES if reason == REASON_MISSING else _MOVED_OUT_LINES
    index = min(max(attempt, 1), len(lines)) - 1
    return lines[index].format(slug=slug)


__all__ = ["PLANNING_SLUG", "REASON_MISSING", "REASON_MOVED_OUT", "REQUIRED_BLOCK_KIND",
           "RequiredBlockConfig", "RequiredBlockRule", "bounds_of", "slug_of", "within_bounds"]
