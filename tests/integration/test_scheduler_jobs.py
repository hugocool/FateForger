from freezegun import freeze_time

from src.core.scheduler import get_scheduler, reset_scheduler
from src.haunters.bootstrap import PlanningBootstrapHaunter


class TestSchedulerJobs:
    def test_daily_job_registration(self):
        reset_scheduler()
        sched = get_scheduler()
        PlanningBootstrapHaunter.schedule_daily(sched)
        job = sched.get_job("daily-planning-bootstrap")
        assert job is not None
        assert job.id == "daily-planning-bootstrap"
        reset_scheduler()
