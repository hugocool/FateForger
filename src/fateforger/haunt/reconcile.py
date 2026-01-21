from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dateutil import parser as date_parser

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
    summary_keywords: tuple[str, ...] = ("plan", "planning", "review", "timebox")
    calendar_id: str = "primary"


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
        try:
            result = await self._workbench.call_tool("get-event", arguments=args)
        except Exception:
            # Older MCP server versions may not expose get-event.
            return None
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
        return _normalize_events(payload)


class PlanningSessionRule:
    rule_id = "next_planning_session"

    def __init__(
        self,
        *,
        calendar_client: CalendarClient,
        config: PlanningRuleConfig | None = None,
    ) -> None:
        self._calendar_client = calendar_client
        self._config = config or PlanningRuleConfig()

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

        if planning_event_id:
            anchor = await self._calendar_client.get_event(
                calendar_id=self._config.calendar_id,
                event_id=planning_event_id,
            )
            if anchor and _event_within_window(anchor, start, end):
                return []

        events = await self._calendar_client.list_events(
            calendar_id=self._config.calendar_id,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
        )

        if self._has_planning_session(events):
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

        return jobs

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

    def _has_planning_session(self, events: Iterable[dict]) -> bool:
        for event in events:
            if self._is_planning_event(event):
                return True
        return False

    def _is_planning_event(self, event: dict) -> bool:
        summary = (event.get("summary") or "").lower()
        return any(keyword in summary for keyword in self._config.summary_keywords)

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
        dispatcher: Callable[[PlanningReminder], Awaitable[None]] | None = None,
        rule: PlanningSessionRule | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._calendar_client = calendar_client
        self._dispatcher = dispatcher or self._log_dispatch
        self._rule = rule or PlanningSessionRule(calendar_client=calendar_client)

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
        current_ids = {
            job.id for job in self._scheduler.get_jobs() if job.id.startswith(prefix)
        }
        desired_ids = {job.key.as_id() for job in desired}

        for job_id in current_ids - desired_ids:
            self._scheduler.remove_job(job_id)

        for job in desired:
            self._scheduler.add_job(
                self._emit_reminder,
                trigger="date",
                run_date=job.run_at,
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
    import json

    if isinstance(result, dict):
        return result

    # Handle ToolResult objects from MCP
    payload = getattr(result, "result", None)
    if payload is not None:
        # result is often a list of TextResultContent objects
        if isinstance(payload, list) and len(payload) > 0:
            first = payload[0]
            # TextResultContent has a 'content' attribute with JSON string
            content = getattr(first, "content", None)
            if isinstance(content, str):
                try:
                    return json.loads(content)
                except (json.JSONDecodeError, TypeError):
                    pass
        return payload

    payload = getattr(result, "content", None)
    if payload is not None:
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                pass
        return payload

    return {}


def _normalize_events(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        # MCP returns {"events": [...]} or Google API returns {"items": [...]}
        items = payload.get("events") or payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _normalize_event(payload: Any) -> dict | None:
    if isinstance(payload, dict):
        if "id" in payload or "summary" in payload:
            return payload
        item = payload.get("item")
        if isinstance(item, dict):
            return item
        event = payload.get("event")
        if isinstance(event, dict):
            return event
    return None


def _parse_event_dt(raw: Any, *, tz: timezone) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = date_parser.isoparse(raw)
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed
    if isinstance(raw, dict):
        if raw.get("dateTime"):
            try:
                parsed = date_parser.isoparse(raw["dateTime"])
            except Exception:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed
        if raw.get("date"):
            try:
                day = date_parser.isoparse(raw["date"]).date()
            except Exception:
                return None
            return datetime.combine(day, time(0, 0), tz)
    return None


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
