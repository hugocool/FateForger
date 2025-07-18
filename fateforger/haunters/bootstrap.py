from apscheduler.schedulers.asyncio import AsyncIOScheduler

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

    async def handle_reply(self, text: str) -> None:
        self.logger.info("Received reply: %s", text)
