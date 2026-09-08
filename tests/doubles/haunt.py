"""Doubles for the planning reconciler and the scheduler it drives."""

from __future__ import annotations


class DummyCalendarClient:
    """A calendar that returns a fixed event list and records every query."""

    def __init__(self, events):
        self._events = events
        self.calls = []
        self.event_lookup = {}

    async def list_events(self, *, calendar_id: str, time_min: str, time_max: str):
        self.calls.append((calendar_id, time_min, time_max))
        return list(self._events)

    async def get_event(self, *, calendar_id: str, event_id: str):
        return self.event_lookup.get((calendar_id, event_id))


class FakeScheduler:
    """An APScheduler stand-in that keeps jobs in a dict."""

    def __init__(self):
        self._jobs = {}

    def get_jobs(self):
        return list(self._jobs.values())

    def add_job(self, func, trigger, run_date, id, kwargs, replace_existing, **_):
        # Shaped like a real APScheduler Job: the scheduled time lives on the
        # trigger (`DateTrigger.run_date`), which is what production reads. A
        # double that exposed only a flat `run_date` would let a change pass
        # here and fail live -- which is how the len()-over-a-count bug shipped.
        job_trigger = type("DateTrigger", (), {"run_date": run_date})()
        self._jobs[id] = type(
            "Job",
            (),
            {"id": id, "run_date": run_date, "trigger": job_trigger, "kwargs": kwargs},
        )

    def remove_job(self, job_id):
        self._jobs.pop(job_id, None)
