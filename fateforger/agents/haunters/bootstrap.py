from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy.ext.asyncio import AsyncSession

from fateforger.actions.haunt_payload import HauntPayload
from fateforger.agents.planning import PlanningAgent
from fateforger.infra.models import PlanningSession, SessionStatus

from .base import BaseHaunter


class PlanningBootstrapHaunter(BaseHaunter):
    """Simplified bootstrap haunter."""

    backoff_base = 20
    backoff_cap = 240

    @classmethod
    def schedule_daily(cls, scheduler: AsyncIOScheduler) -> None:
        scheduler.add_job(
            cls._daily_check,
            trigger="cron",
            hour=17,
            id="daily-planning-bootstrap",
            replace_existing=True,
        )

    @staticmethod
    async def _daily_check() -> None:
        pass  # placeholder for daily check logic

    def __init__(
        self,
        session_id: int,
        slack: AsyncWebClient,
        scheduler: AsyncIOScheduler,
        db: AsyncSession,
        planner: PlanningAgent,
        channel: str = "D123",
    ) -> None:
        super().__init__(session_id, slack, scheduler, channel)
        self.db = db
        self.planner = planner
        self.scheduled_ids: list[str] = []

    async def start(self) -> None:
        ts = await self.schedule_slack("When will you plan?", datetime.utcnow())
        self.scheduled_ids.append(ts)

    async def handle_reply(self, text: str) -> None:
        payload = HauntPayload(
            session_id=self.session_id, action="create_event", commit_time_str=text
        )
        await self.planner.handle_router_message(payload)
        self.scheduler.remove_all_jobs()
        for sid in self.scheduled_ids:
            await self.delete_scheduled(sid)
        session = PlanningSession(id=self.session_id, status=SessionStatus.COMPLETE)
        self.db.add(session)
        await self.db.commit()
