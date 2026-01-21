from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .planning_store import SqlAlchemyPlanningAnchorStore
from .reconcile import PlanningReconciler

logger = logging.getLogger(__name__)


class PlanningGuardian:
    """Runs the missing-planning reconciliation over all configured users."""

    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        *,
        anchor_store: SqlAlchemyPlanningAnchorStore,
        reconciler: PlanningReconciler,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._scheduler = scheduler
        self._anchor_store = anchor_store
        self._reconciler = reconciler
        self._now = now

    def schedule_daily(self, *, hour_utc: int = 6, minute_utc: int = 0) -> None:
        self._scheduler.add_job(
            self.reconcile_all,
            trigger="cron",
            hour=hour_utc,
            minute=minute_utc,
            id="planning_guardian:daily_reconcile",
            replace_existing=True,
        )

    async def reconcile_all(self) -> None:
        anchors = await self._anchor_store.list_all()
        if not anchors:
            return
        now = self._now()
        for anchor in anchors:
            try:
                await self._reconciler.reconcile_missing_planning(
                    scope=anchor.user_id,
                    user_id=anchor.user_id,
                    channel_id=anchor.channel_id,
                    planning_event_id=anchor.event_id,
                    now=now,
                )
            except Exception:
                logger.exception("Planning reconcile failed for %s", anchor.user_id)

    async def reconcile_user(self, *, user_id: str) -> None:
        anchor = await self._anchor_store.get(user_id=user_id)
        if not anchor:
            return
        await self._reconciler.reconcile_missing_planning(
            scope=anchor.user_id,
            user_id=anchor.user_id,
            channel_id=anchor.channel_id,
            planning_event_id=anchor.event_id,
            first_nudge_offset=timedelta(seconds=0),
            now=self._now(),
        )

    def schedule_reconcile_after_deletion(self, *, user_id: str, delay_minutes: int = 5) -> None:
        """Schedule reconciliation so the first nudge lands delay_minutes from now."""

        run_at = self._now() + timedelta(minutes=delay_minutes)
        self._scheduler.add_job(
            self._reconcile_deletion_bridge,
            trigger="date",
            run_date=run_at,
            id=f"planning_guardian:deleted:{user_id}",
            kwargs={"user_id": user_id, "delay_minutes": delay_minutes},
            replace_existing=True,
        )

    async def _reconcile_deletion_bridge(self, *, user_id: str, delay_minutes: int) -> None:
        anchor = await self._anchor_store.get(user_id=user_id)
        if not anchor:
            return
        # Default rule nudge1 is 10 minutes. Shift "now" backwards so nudge1 lands in delay_minutes.
        shifted_now = self._now() - timedelta(minutes=max(10 - delay_minutes, 0))
        await self._reconciler.reconcile_missing_planning(
            scope=anchor.user_id,
            user_id=anchor.user_id,
            channel_id=anchor.channel_id,
            planning_event_id=anchor.event_id,
            now=shifted_now,
        )


__all__ = ["PlanningGuardian"]
