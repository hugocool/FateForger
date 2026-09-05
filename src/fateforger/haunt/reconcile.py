from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Awaitable, Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as date_parser

from .session_start import SESSION_EXPIRE_KIND, SESSION_START_KIND, planning_day_for

logger = logging.getLogger(__name__)

try:
    from autogen_ext.tools.mcp import McpWorkbench, StreamableHttpServerParams
except Exception:  # pragma: no cover - optional dependency
    McpWorkbench = None
    StreamableHttpServerParams = None


class CalendarClient(Protocol):
    async def list_events(
        self,
        *,
        calendar_id: str,
        time_min: str,
        time_max: str,
    ) -> list[dict]: ...

    async def get_event(self, *, calendar_id: str, event_id: str) -> dict | None: ...


class PlanningSessionStore(Protocol):
    async def list_for_user_between(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date,
        statuses: tuple[Any, ...],
    ) -> list[Any]: ...

    async def upsert(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class PlanningRuleConfig:
    horizon: timedelta = timedelta(hours=24)
    # If set, use these explicit offsets (relative to `now`).
    nudge_offsets: tuple[timedelta, ...] | None = None
    # Otherwise, generate offsets using exponential backoff:
    # base, base*2, base*4, ... capped at nudge_backoff_cap, up to nudge_max_attempts.
    nudge_backoff_base: timedelta = timedelta(minutes=10)
    nudge_backoff_cap: timedelta = timedelta(hours=8)
    nudge_max_attempts: int = 5
    # Grace window for eventual-consistency races right after local upsert success.
    stored_session_consistency_grace: timedelta = timedelta(minutes=5)
    # TODO(refactor,typed-contracts): Remove summary keyword list and use a typed
    # event marker/label schema for planning-session detection.
    calendar_id: str = "primary"
    # How long after the planning event's end the auto-opened session is
    # declared missed and the ordinary missing-planning ladder takes over.
    expire_after: timedelta = timedelta(minutes=60)


@dataclass(frozen=True)
class JobKey:
    namespace: str
    rule_id: str
    scope: str
    window_start: str
    kind: str

    def as_id(self) -> str:
        return f"{self.namespace}:{self.rule_id}:{self.scope}:{self.window_start}:{self.kind}"


@dataclass
class PlanningReminder:
    scope: str
    kind: str
    attempt: int
    message: str
    user_id: str | None = None
    channel_id: str | None = None
    event_start: str | None = None
    event_end: str | None = None
    event_tz: str | None = None


@dataclass
class DesiredJob:
    key: JobKey
    run_at: datetime
    payload: PlanningReminder
    replace_existing: bool = True
    misfire_grace_time_s: int = 300
    max_instances: int = 1
    coalesce: bool = True


class McpCalendarClient:
    def __init__(self, *, server_url: str, timeout: float = 10.0) -> None:
        if McpWorkbench is None or StreamableHttpServerParams is None:
            raise RuntimeError("autogen_ext tools are required for MCP calendar access")
        params = StreamableHttpServerParams(url=server_url, timeout=timeout)
        self._workbench = McpWorkbench(params)

    async def get_event(self, *, calendar_id: str, event_id: str) -> dict | None:
        args = {"calendarId": calendar_id, "eventId": event_id}
        result = await self._workbench.call_tool("get-event", arguments=args)
        payload = _extract_tool_payload(result)
        event = _normalize_event(payload)
        if not event:
            return None
        if (event.get("status") or "").lower() == "cancelled":
            return None
        return event

    async def list_events(
        self,
        *,
        calendar_id: str,
        time_min: str,
        time_max: str,
    ) -> list[dict]:
        args = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        result = await self._workbench.call_tool("list-events", arguments=args)
        payload = _extract_tool_payload(result)
        if isinstance(payload, str) and payload.strip().lower().startswith("mcp error"):
            logger.warning(
                "calendar-mcp list-events returned tool error payload: %s",
                payload.strip(),
            )
            return []
        return _normalize_events(payload)

    async def close(self) -> None:
        await self._workbench.stop()


class PlanningSessionRule:
    rule_id = "next_planning_session"

    def __init__(
        self,
        *,
        calendar_client: CalendarClient,
        config: PlanningRuleConfig | None = None,
        planning_session_store: PlanningSessionStore | None = None,
        timeboxing_ledger: Any | None = None,
    ) -> None:
        self._calendar_client = calendar_client
        self._config = config or PlanningRuleConfig()
        self._planning_session_store = planning_session_store
        self._timeboxing_ledger = timeboxing_ledger

    async def evaluate(
        self,
        *,
        now: datetime,
        scope: str,
        user_id: str | None = None,
        channel_id: str | None = None,
        planning_event_id: str | None = None,
        first_nudge_offset: timedelta | None = None,
    ) -> list[DesiredJob]:
        start = now.astimezone(timezone.utc)
        end = start + self._config.horizon
        anchor_found = False
        anchor_before_horizon = False
        stored_hit = False
        fallback_hit = False
        list_count = 0

        if planning_event_id:
            anchor = await self._calendar_client.get_event(
                calendar_id=self._config.calendar_id,
                event_id=planning_event_id,
            )
            anchor_found = anchor is not None
            anchor_tz_name = _event_tz_name(anchor) if anchor else None
            # No `timeZone` name means read the offset the event's own
            # `dateTime` carries (e.g. "+02:00") rather than assuming UTC --
            # that offset decides which side of the 14:00 cutoff the event
            # falls on, and forcing UTC silently shifted it.
            anchor_tz: tzinfo = (
                (
                    ZoneInfo(anchor_tz_name)
                    if anchor_tz_name
                    else (_event_native_tzinfo(anchor) or timezone.utc)
                )
                if anchor
                else timezone.utc
            )
            # Parsed once, with the resolved zone, and reused below for both
            # the horizon probe and the decisive branch -- it used to be
            # parsed a second time here with the same inputs.
            anchor_start = (
                _parse_event_dt(anchor.get("start"), tz=anchor_tz) if anchor else None
            )
            # Bounded above by the horizon (an anchor booked further out than
            # `end` is not this window's concern yet) but not below: a passed
            # anchor must still reach the branch below to be told apart from
            # one still ahead, rather than being excluded by an overlap test
            # that only recognises events yet to happen. It has no lower
            # bound, so "in the window" is the wrong name for what this
            # tests -- it means "starts before the horizon ends".
            anchor_before_horizon = bool(
                anchor_start is not None and anchor_start <= end
            )
            if anchor_found and anchor_start is None:
                # A planning event nobody can read (an all-day event, a
                # malformed payload) must not silence the nudge ladder --
                # that is the silent-wrong-answer shape CLAUDE.md exists to
                # stop. Fall through to the stored/fallback checks and the
                # nudge ladder below, exactly as an absent anchor does.
                logger.warning(
                    "planning_reconcile evaluate outcome=anchor_unreadable scope=%s user_id=%s planning_event_id=%s event_id=%s unreadable_field=%s",
                    scope,
                    user_id,
                    planning_event_id,
                    anchor.get("id"),
                    "start",
                )
            elif anchor_found and anchor_before_horizon:
                anchor_end = _parse_event_dt(anchor.get("end"), tz=anchor_tz)
                if anchor_end is not None:
                    local_start = anchor_start.astimezone(anchor_tz)
                    anchor_expiry = anchor_end + self._config.expire_after
                    if start < anchor_expiry:
                        # The anchor owns the window until its session expires,
                        # not until the event ends. Reconciles fire from idle
                        # timers and messages too, and every one of them deletes
                        # the scheduled ids this call does not ask for -- so a
                        # reconcile in the hour between end and expiry used to
                        # sweep the session_expire job that is the only thing
                        # left to close the session.
                        window_start = start.date().isoformat()
                        session_run_at = max(anchor_start, start + timedelta(seconds=5))
                        reminder_fields = dict(
                            scope=scope,
                            user_id=user_id,
                            channel_id=channel_id,
                            event_start=anchor_start.isoformat(),
                            event_end=anchor_end.isoformat(),
                            # None when there is no IANA name: the consumer reads
                            # the offset already embedded in event_start's ISO
                            # string rather than being handed a lossy "UTC".
                            event_tz=anchor_tz_name,
                        )
                        jobs = []
                        if anchor_end > start:
                            # Ahead or under way: start the session at its start
                            # (now, if the bot came up mid-window). Past the end
                            # there is nothing left to open.
                            jobs.append(
                                DesiredJob(
                                    key=JobKey("rule", self.rule_id, scope, window_start, SESSION_START_KIND),
                                    run_at=session_run_at,
                                    payload=PlanningReminder(kind=SESSION_START_KIND, attempt=1, message="", **reminder_fields),
                                )
                            )
                        jobs.append(
                            DesiredJob(
                                key=JobKey("rule", self.rule_id, scope, window_start, SESSION_EXPIRE_KIND),
                                run_at=anchor_expiry,
                                payload=PlanningReminder(kind=SESSION_EXPIRE_KIND, attempt=1, message="", **reminder_fields),
                            )
                        )
                        outcome = "anchor_ahead" if anchor_end > start else "anchor_expiring"
                        self._log_evaluate_outcome(outcome=outcome, scope=scope, user_id=user_id, planning_event_id=planning_event_id, start=start, end=end, anchor_found=anchor_found, anchor_before_horizon=anchor_before_horizon, stored_hit=stored_hit, list_count=list_count, fallback_hit=fallback_hit, jobs_count=len(jobs))
                        return jobs
                    # The event has passed and its session has expired. It
                    # counts only if the day it
                    # planned is still relevant to this window (today or
                    # later) *and* was committed. The anchor id is one
                    # stable id reused every day (`planning_event_id_for_user`),
                    # so a stale event -- get_event still resolves it days
                    # after the day it named -- must not be read as "today is
                    # planned" forever; that is indistinguishable from an
                    # absent anchor and must fall through exactly like one.
                    day = planning_day_for(local_start)
                    # `day` is computed in the anchor's own zone; comparing
                    # it against `start.date()` (UTC) put the two bases on
                    # different sides of midnight. West of UTC, `start`'s
                    # UTC date rolls over to tomorrow while it is still
                    # today at the anchor's offset, which read a
                    # legitimately committed day as no longer relevant and
                    # re-nudged it every evening. Comparing against
                    # `start.astimezone(anchor_tz).date()` -- not
                    # `planning_day_for(...)` -- is right here: this asks
                    # what calendar date `now` is at the anchor's offset,
                    # not which day an event starting at that moment plans;
                    # the 14:00 cutoff belongs to an anchor's own start
                    # time, not to the instant being compared against it.
                    if day >= start.astimezone(anchor_tz).date() and await self._committed_for(
                        user_id=user_id, day=day, now=start
                    ):
                        self._log_evaluate_outcome(outcome="anchor_past_committed", scope=scope, user_id=user_id, planning_event_id=planning_event_id, start=start, end=end, anchor_found=anchor_found, anchor_before_horizon=anchor_before_horizon, stored_hit=stored_hit, list_count=list_count, fallback_hit=fallback_hit, jobs_count=0)
                        return []
                    # fall through to the stored/fallback checks and the nudge ladder
                else:
                    # The end couldn't be read either (all-day, malformed
                    # payload) -- same silent-wrong-answer shape as the
                    # unreadable start above. Fall through rather than
                    # silencing the ladder.
                    logger.warning(
                        "planning_reconcile evaluate outcome=anchor_unreadable scope=%s user_id=%s planning_event_id=%s event_id=%s unreadable_field=%s",
                        scope,
                        user_id,
                        planning_event_id,
                        anchor.get("id"),
                        "end",
                    )

        stored = await self._resolve_planning_from_stored_sessions(
            user_id=user_id, start=start, end=end
        )
        stored_hit = stored is not None
        if stored:
            self._log_evaluate_outcome(
                outcome="stored_match",
                scope=scope,
                user_id=user_id,
                planning_event_id=planning_event_id,
                start=start,
                end=end,
                anchor_found=anchor_found,
                anchor_before_horizon=anchor_before_horizon,
                stored_hit=stored_hit,
                list_count=list_count,
                fallback_hit=fallback_hit,
                jobs_count=0,
            )
            return []

        events = await self._calendar_client.list_events(
            calendar_id=self._config.calendar_id,
            time_min=_format_mcp_datetime(start),
            time_max=_format_mcp_datetime(end),
        )
        list_count = len(events)

        fallback = self._resolve_planning_from_fallback(
            events, start=start, end=end, planning_event_id=planning_event_id
        )
        fallback_hit = fallback is not None
        if fallback:
            await self._sync_fallback_session_record(
                user_id=user_id, event=fallback, start=start
            )
            self._log_evaluate_outcome(
                outcome="fallback_match",
                scope=scope,
                user_id=user_id,
                planning_event_id=planning_event_id,
                start=start,
                end=end,
                anchor_found=anchor_found,
                anchor_before_horizon=anchor_before_horizon,
                stored_hit=stored_hit,
                list_count=list_count,
                fallback_hit=fallback_hit,
                jobs_count=0,
            )
            return []

        nudge_offsets = self._resolve_nudge_offsets(
            first_nudge_offset=first_nudge_offset
        )
        if not nudge_offsets:
            # Safety: always schedule at least one nudge, otherwise the reconcile can't work.
            nudge_offsets = [timedelta(minutes=10)]

        window_start = start.date().isoformat()
        jobs: list[DesiredJob] = []
        for idx, offset in enumerate(nudge_offsets, start=1):
            jobs.append(
                DesiredJob(
                    key=JobKey(
                        "rule",
                        self.rule_id,
                        scope,
                        window_start,
                        f"nudge{idx}",
                    ),
                    run_at=start + offset,
                    payload=PlanningReminder(
                        scope=scope,
                        kind=f"nudge{idx}",
                        attempt=idx,
                        message=self._message_for_nudge(idx),
                        user_id=user_id,
                        channel_id=channel_id,
                    ),
                )
            )

        jobs.append(
            DesiredJob(
                key=JobKey("rule", self.rule_id, scope, window_start, "expire"),
                run_at=start + self._config.horizon,
                payload=PlanningReminder(
                    scope=scope,
                    kind="expire",
                    attempt=len(nudge_offsets) + 1,
                    message="Still no planning session on the calendar. Want me to block time?",
                    user_id=user_id,
                    channel_id=channel_id,
                ),
            )
        )
        self._log_evaluate_outcome(
            outcome="nudges_scheduled",
            scope=scope,
            user_id=user_id,
            planning_event_id=planning_event_id,
            start=start,
            end=end,
            anchor_found=anchor_found,
            anchor_before_horizon=anchor_before_horizon,
            stored_hit=stored_hit,
            list_count=list_count,
            fallback_hit=fallback_hit,
            jobs_count=len(jobs),
        )
        return jobs

    def _log_evaluate_outcome(
        self,
        *,
        outcome: str,
        scope: str,
        user_id: str | None,
        planning_event_id: str | None,
        start: datetime,
        end: datetime,
        anchor_found: bool,
        anchor_before_horizon: bool,
        stored_hit: bool,
        list_count: int,
        fallback_hit: bool,
        jobs_count: int,
    ) -> None:
        logger.info(
            "planning_reconcile evaluate outcome=%s scope=%s user_id=%s planning_event_id=%s window_start=%s window_end=%s anchor_found=%s anchor_before_horizon=%s stored_hit=%s list_count=%d fallback_hit=%s jobs_count=%d",
            outcome,
            scope,
            user_id,
            planning_event_id,
            start.isoformat(),
            end.isoformat(),
            anchor_found,
            anchor_before_horizon,
            stored_hit,
            list_count,
            fallback_hit,
            jobs_count,
        )

    def _resolve_nudge_offsets(
        self, *, first_nudge_offset: timedelta | None
    ) -> list[timedelta]:
        if self._config.nudge_offsets is not None:
            offsets = list(self._config.nudge_offsets)
            if first_nudge_offset is not None and offsets:
                offsets[0] = first_nudge_offset
            return [o for o in offsets if o < self._config.horizon]

        base = self._config.nudge_backoff_base
        cap = self._config.nudge_backoff_cap
        max_attempts = max(int(self._config.nudge_max_attempts or 0), 1)

        offsets: list[timedelta] = []
        if first_nudge_offset is not None:
            offsets.append(first_nudge_offset)
        else:
            offsets.append(base)

        # Fill remaining attempts with an exponential series using `base`.
        # Ensure monotonic growth even if first_nudge_offset is 0 or custom.
        exponent = 0
        while len(offsets) < max_attempts:
            candidate = base * (2**exponent)
            if candidate > cap:
                candidate = cap
            if candidate <= offsets[-1]:
                exponent += 1
                if candidate == cap:
                    break
                continue
            if candidate >= self._config.horizon:
                break
            offsets.append(candidate)
            exponent += 1

        return offsets

    async def _resolve_planning_from_stored_sessions(
        self, *, user_id: str | None, start: datetime, end: datetime
    ) -> dict | None:
        if not user_id or not self._planning_session_store:
            return None
        try:
            statuses = ("planned", "in_progress")
            stored = await self._planning_session_store.list_for_user_between(
                user_id=user_id,
                start_date=start.date(),
                end_date=end.date(),
                statuses=statuses,
            )
        except Exception:
            logger.exception(
                "Stored planning session lookup failed for user=%s", user_id
            )
            return None

        seen: set[tuple[str, str]] = set()
        for session in stored:
            calendar_id = str(getattr(session, "calendar_id", "") or "primary")
            event_id = str(getattr(session, "event_id", "") or "")
            if not event_id:
                continue
            key = (calendar_id, event_id)
            if key in seen:
                continue
            seen.add(key)

            event = await self._calendar_client.get_event(
                calendar_id=calendar_id,
                event_id=event_id,
            )
            if event and _event_within_window(event, start, end):
                return event
            if self._is_recent_local_stored_session(session=session, now=start):
                logger.info(
                    "Using recent local planning_session_ref as consistency bridge (user=%s event_id=%s)",
                    user_id,
                    event_id,
                )
                return {
                    "id": event_id,
                    "summary": getattr(session, "title", None)
                    or "Daily planning session",
                }
        return None

    def _is_recent_local_stored_session(self, *, session: Any, now: datetime) -> bool:
        status = str(getattr(session, "status", "") or "").strip().lower()
        if status not in {"planned", "in_progress"}:
            return False
        updated_at = getattr(session, "updated_at", None)
        if not isinstance(updated_at, datetime):
            return False
        updated_utc = (
            updated_at.replace(tzinfo=timezone.utc)
            if updated_at.tzinfo is None
            else updated_at.astimezone(timezone.utc)
        )
        delta = now.astimezone(timezone.utc) - updated_utc
        if delta < timedelta(0):
            delta = timedelta(0)
        return delta <= self._config.stored_session_consistency_grace

    def _resolve_planning_from_fallback(
        self,
        events: Iterable[dict],
        *,
        start: datetime,
        end: datetime,
        planning_event_id: str | None = None,
    ) -> dict | None:
        """The planning event in this window, identified by a mark we minted.

        This used to score calendar titles: a set of exact titles, a list of
        phrases, substring tests, and a hand-tuned table that subtracted 40 for
        `" with "` and had a special case for `"poker"`. That decides what a
        title someone wrote *means*, which CLAUDE.md sends to a model and never
        to a pattern -- and it decided when Hugo gets nudged, so it was wrong in
        both directions silently. "Planning with wife" scored as a planning
        session until a guardrail was hand-added for it; "poker" needed its own.
        There is no end to that list, because it is a list of every way a person
        might phrase something.

        The judgement is not made better here, it is removed. The planning
        event is one this system CREATES, and creation is where identity
        belongs: `planning_event_id_for_user` mints a deterministic
        `ffplanning...` id per user. Matching that is comparing two identifiers
        this system minted, which is identification and carries no opinion about
        anyone's words.

        The cost is honest and small: an event Hugo booked by hand, with no
        mark, is no longer adopted. He is nudged, the nudge books one, and the
        booked one carries the mark. Nudging about a session that exists is
        visible and correctable in one reply. Silently adopting the wrong event
        -- a poker night, a call with his wife -- is neither.
        """
        for event in events:
            if not isinstance(event, dict):
                continue
            if not _event_within_window(event, start, end):
                continue
            if _carries_planning_mark(event, planning_event_id):
                return event
        return None

    async def _sync_fallback_session_record(
        self, *, user_id: str | None, event: dict, start: datetime
    ) -> None:
        if not user_id or not self._planning_session_store:
            return
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            return
        parsed_start = _parse_event_dt(
            event.get("start"), tz=start.tzinfo or timezone.utc
        )
        if not parsed_start:
            return
        try:
            await self._planning_session_store.upsert(
                user_id=user_id,
                planned_date=parsed_start.date(),
                calendar_id=self._config.calendar_id,
                event_id=event_id,
                status="planned",
                title=event.get("summary"),
                event_url=event.get("htmlLink")
                or event.get("html_link")
                or event.get("url"),
                source="calendar_fallback_scan",
                channel_id=None,
                thread_ts=None,
            )
        except Exception:
            logger.exception(
                "Failed to sync fallback planning event into local store for user=%s event_id=%s",
                user_id,
                event_id,
            )

    async def _committed_for(self, *, user_id: str | None, day: date, now: datetime) -> bool:
        if not user_id or self._timeboxing_ledger is None:
            return False
        try:
            standing = await self._timeboxing_ledger.standing_for(
                owner_user_id=user_id,
                open_since=now - timedelta(hours=1),
                planned_from=day,
                planned_to=day,
            )
        except Exception:
            logger.exception("committed-session lookup failed for user=%s day=%s", user_id, day)
            return False
        return standing.committed_session_key is not None

    @staticmethod
    def _message_for_nudge(attempt: int) -> str:
        attempt = max(int(attempt or 1), 1)
        # Escalate tone over time; the card will surface this message directly.
        if attempt <= 1:
            return (
                "No planning session on the calendar yet. Pick a time and I’ll book it."
            )
        if attempt == 2:
            return "Still no planning session. Choose a time now — this is your daily anchor."
        if attempt == 3:
            return ":warning: Still missing. Pick a slot — I’m going to keep asking until it’s booked."
        if attempt == 4:
            return ":rotating_light: Planning session is overdue. Pick a time or tell me when you’ll do it."
        return ":rotating_light: Final warning: no planning session booked. Choose a time now so tomorrow isn’t chaos."


class PlanningReconciler:
    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        *,
        calendar_client: CalendarClient,
        planning_session_store: PlanningSessionStore | None = None,
        dispatcher: Callable[[PlanningReminder], Awaitable[None]] | None = None,
        rule: PlanningSessionRule | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._calendar_client = calendar_client
        self._dispatcher = dispatcher or self._log_dispatch
        self._rule = rule or PlanningSessionRule(
            calendar_client=calendar_client,
            planning_session_store=planning_session_store,
        )

    @property
    def calendar_client(self) -> CalendarClient:
        return self._calendar_client

    def set_dispatcher(
        self, dispatcher: Callable[[PlanningReminder], Awaitable[None]]
    ) -> None:
        logger.info(
            "PlanningReconciler: dispatcher updated to %s",
            (
                dispatcher.__qualname__
                if hasattr(dispatcher, "__qualname__")
                else dispatcher
            ),
        )
        self._dispatcher = dispatcher

    async def reconcile_missing_planning(
        self,
        *,
        scope: str,
        user_id: str | None = None,
        channel_id: str | None = None,
        planning_event_id: str | None = None,
        first_nudge_offset: timedelta | None = None,
        now: datetime | None = None,
    ) -> list[DesiredJob]:
        now_dt = now or datetime.now(timezone.utc)
        desired = await self._rule.evaluate(
            now=now_dt,
            scope=scope,
            user_id=user_id,
            channel_id=channel_id,
            planning_event_id=planning_event_id,
            first_nudge_offset=first_nudge_offset,
        )
        prefix = f"rule:{self._rule.rule_id}:{scope}:"
        scheduled = {
            job.id: getattr(getattr(job, "trigger", None), "run_date", None)
            for job in self._scheduler.get_jobs()
            if job.id.startswith(prefix)
        }
        current_ids = set(scheduled)
        desired_ids = {job.key.as_id() for job in desired}

        for job_id in current_ids - desired_ids:
            self._scheduler.remove_job(job_id)

        # A caller supplying `first_nudge_offset` is deliberately re-timing the
        # ladder (planning_guardian does this), so let it move. Otherwise keep
        # the time a job already has: `evaluate` anchors every offset to `now`,
        # so recomputing on each pass re-arms nudge1 forever and the exponential
        # backoff never advances. Identity was already stable -- JobKey carries
        # the day -- so this makes reconciliation idempotent in time as well.
        retime = first_nudge_offset is not None

        for job in desired:
            run_at = job.run_at
            event_anchored = job.key.kind in (SESSION_START_KIND, SESSION_EXPIRE_KIND)
            if not retime and not event_anchored:
                already = scheduled.get(job.key.as_id())
                if already is not None:
                    run_at = already
            self._scheduler.add_job(
                self._emit_reminder,
                trigger="date",
                run_date=run_at,
                id=job.key.as_id(),
                kwargs={"reminder": job.payload},
                replace_existing=job.replace_existing,
                misfire_grace_time=job.misfire_grace_time_s,
                max_instances=job.max_instances,
                coalesce=job.coalesce,
            )

        return desired

    async def _emit_reminder(self, reminder: PlanningReminder) -> None:
        logger.info(
            "Emitting planning reminder for %s (kind=%s, attempt=%d) via %s",
            reminder.scope,
            reminder.kind,
            reminder.attempt,
            (
                self._dispatcher.__qualname__
                if hasattr(self._dispatcher, "__qualname__")
                else "dispatcher"
            ),
        )
        try:
            await self._dispatcher(reminder)
        except Exception:
            logger.exception("Planning reminder dispatch failed for %s", reminder.scope)

    @staticmethod
    async def _log_dispatch(reminder: PlanningReminder) -> None:
        logger.info("Planning reminder (%s): %s", reminder.scope, reminder.message)


def _extract_tool_payload(result: Any) -> Any:
    """Normalize an MCP tool result into a parsed payload (dict, list, or {}).

    Probes ``result.result`` then ``result.content`` in order, attempting JSON
    decode on any str values found.  Falls back to ``{}`` when no attribute is
    found.
    """
    import json

    def _try_json(text: str) -> Any:
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    if isinstance(result, dict):
        return result

    for attr in ("result", "content"):
        payload = getattr(result, attr, None)
        if payload is None:
            continue
        if isinstance(payload, list) and payload:
            first = payload[0]
            content = getattr(first, "content", None)
            if isinstance(content, str):
                return _try_json(content)
            return payload
        if isinstance(payload, str):
            return _try_json(payload)
        return payload

    return {}


def _normalize_events(payload: Any) -> list[dict]:
    """Coerce MCP / Google API payloads into a flat list of event dicts."""
    if isinstance(payload, dict):
        for key in ("events", "items"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


#: The prefix `planning_event_id_for_user` stamps on every planning event this
#: system creates. A system-minted identifier, so comparing it decides nothing
#: about what anyone wrote.
_PLANNING_ID_PREFIX = "ffplanning"

#: A private extended property carried by events this system creates, for the
#: case where the id cannot be relied on. Read but not yet written -- #210 is
#: measuring whether Google preserves these across a UI edit, and until that
#: answers, an event whose id changed is simply not adopted rather than guessed
#: at. Absent is treated as absent; nothing here infers a mark from a title.
_PLANNING_MARK_PROPERTY = "ff_planning"


def _carries_planning_mark(event: dict, planning_event_id: str | None) -> bool:
    """Whether this event is one we created, by identity alone."""
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return False
    if planning_event_id and event_id == str(planning_event_id).strip():
        return True
    # `startswith` on an id this system minted, not on anything a user typed.
    if event_id.lower().startswith(_PLANNING_ID_PREFIX):
        return True
    extended = event.get("extendedProperties")
    if isinstance(extended, dict):
        private = extended.get("private")
        if isinstance(private, dict) and private.get(_PLANNING_MARK_PROPERTY):
            return True
    return False


def _normalize_event(payload: Any) -> dict | None:
    """Extract a single event dict from direct or wrapper-keyed payloads."""
    if not isinstance(payload, dict):
        return None
    if "id" in payload or "summary" in payload:
        return payload
    for key in ("item", "event"):
        val = payload.get(key)
        if isinstance(val, dict):
            return val
    return None


def _event_tz_name(event: dict) -> str | None:
    start = event.get("start")
    if isinstance(start, dict):
        name = start.get("timeZone")
        return str(name) if name else None
    return None


def _event_native_tzinfo(event: dict) -> Any | None:
    """The offset the event's own ``dateTime`` string carries, if any.

    Distinct from ``_event_tz_name``: a "timeZone" key is an IANA name
    ("Europe/Amsterdam"); this reads the fixed UTC offset embedded directly
    in ``dateTime`` (e.g. the ``+02:00`` in ``2026-09-03T15:00:00+02:00``)
    when no such name is present. Returns ``None`` for a naive value.
    """
    start = event.get("start")
    if isinstance(start, dict):
        raw = start.get("dateTime")
    elif isinstance(start, str):
        raw = start
    else:
        return None
    if not isinstance(raw, str):
        return None
    try:
        parsed = date_parser.isoparse(raw)
    except Exception:
        return None
    return parsed.tzinfo


def _parse_event_dt(raw: Any, *, tz: tzinfo) -> datetime | None:
    """Parse a calendar datetime value (str ISO, EventDateTime dict, or None)."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = date_parser.isoparse(raw)
        except Exception:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)
    if isinstance(raw, dict):
        from fateforger.contracts import EventDateTime  # noqa: PLC0415

        return EventDateTime.model_validate(raw).to_datetime(tz)
    return None


def _format_mcp_datetime(dt: datetime) -> str:
    """Return MCP-compatible datetime strings without fractional seconds."""
    return dt.replace(microsecond=0).isoformat()


def _event_within_window(event: dict, start: datetime, end: datetime) -> bool:
    tz = start.tzinfo or timezone.utc
    start_dt = _parse_event_dt(event.get("start"), tz=tz)
    end_dt = _parse_event_dt(event.get("end"), tz=tz)
    if start_dt is None and end_dt is None:
        return False
    if start_dt and end_dt:
        return not (end_dt < start or start_dt > end)
    if start_dt:
        return start <= start_dt <= end
    if end_dt:
        return start <= end_dt <= end
    return False


__all__ = [
    "CalendarClient",
    "DesiredJob",
    "JobKey",
    "McpCalendarClient",
    "PlanningReconciler",
    "PlanningReminder",
    "PlanningRuleConfig",
    "PlanningSessionRule",
]
