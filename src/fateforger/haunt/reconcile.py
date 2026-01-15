from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from fateforger.agents.schedular.models.calendar import EventType

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
    ) -> list[dict]:
        ...


@dataclass(frozen=True)
class PlanningRuleConfig:
    horizon: timedelta = timedelta(hours=24)
    nudge_offsets: tuple[timedelta, ...] = (
        timedelta(minutes=10),
        timedelta(hours=2),
        timedelta(hours=8),
    )
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
    ) -> list[DesiredJob]:
        start = now.astimezone(timezone.utc)
        end = start + self._config.horizon
        events = await self._calendar_client.list_events(
            calendar_id=self._config.calendar_id,
            time_min=start.isoformat(),
            time_max=end.isoformat(),
        )

        if self._has_planning_session(events):
            return []

        window_start = start.date().isoformat()
        jobs: list[DesiredJob] = []
        for idx, offset in enumerate(self._config.nudge_offsets, start=1):
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
                    attempt=len(self._config.nudge_offsets) + 1,
                    message="Still no planning session on the calendar. Want me to block time?",
                    user_id=user_id,
                    channel_id=channel_id,
                ),
            )
        )

        return jobs

    def _has_planning_session(self, events: Iterable[dict]) -> bool:
        for event in events:
            if self._is_planning_event(event):
                return True
        return False

    def _is_planning_event(self, event: dict) -> bool:
        color_id = event.get("colorId")
        if color_id:
            try:
                if EventType.get_event_type_from_color_id(color_id) is EventType.PLAN_REVIEW:
                    return True
            except KeyError:
                pass
        summary = (event.get("summary") or "").lower()
        return any(keyword in summary for keyword in self._config.summary_keywords)

    @staticmethod
    def _message_for_nudge(attempt: int) -> str:
        if attempt == 1:
            return "No planning session on the calendar yet. Want me to schedule one?"
        if attempt == 2:
            return "Reminder: we still need a planning session on the calendar."
        return "Heads-up: planning session still missing. Want to lock one in?"


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

    async def reconcile_missing_planning(
        self,
        *,
        scope: str,
        user_id: str | None = None,
        channel_id: str | None = None,
        now: datetime | None = None,
    ) -> list[DesiredJob]:
        now_dt = now or datetime.now(timezone.utc)
        desired = await self._rule.evaluate(
            now=now_dt, scope=scope, user_id=user_id, channel_id=channel_id
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
        try:
            await self._dispatcher(reminder)
        except Exception:
            logger.exception("Planning reminder dispatch failed for %s", reminder.scope)

    @staticmethod
    async def _log_dispatch(reminder: PlanningReminder) -> None:
        logger.info("Planning reminder (%s): %s", reminder.scope, reminder.message)


def _extract_tool_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    payload = getattr(result, "content", None)
    if payload is not None:
        return payload
    payload = getattr(result, "result", None)
    if payload is not None:
        return payload
    return {}


def _normalize_events(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


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
