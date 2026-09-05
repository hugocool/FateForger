from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
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
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
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

    #: How often the reconciler re-derives every user's ladder. The watcher
    #: sees a block leave the plan only on a tick, so this is the upper bound
    #: on "how long until the haunt starts" -- the spec's end-to-end case reads
    #: "drag it to tomorrow, see it within a tick".
    DEFAULT_INTERVAL_MINUTES = 15

    def schedule_interval(self, *, minutes: int = DEFAULT_INTERVAL_MINUTES) -> None:
        """Reconcile every `minutes` minutes; a non-positive value disables it.

        `coalesce` and `max_instances=1` because a tick that overran must not
        stack behind itself: two reconciles racing over one scope would each
        prune the jobs the other just added.
        """

        if minutes <= 0:
            logger.info("planning_guardian: interval reconcile disabled (minutes=%s)", minutes)
            return
        self._scheduler.add_job(
            self.reconcile_all,
            trigger="interval",
            minutes=minutes,
            id="planning_guardian:interval_reconcile",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        logger.info("planning_guardian: reconciling every %d minutes", minutes)

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
