"""Doubles for the required-block watcher: calendar, constraint store, ledger."""

from __future__ import annotations

from datetime import date

from fateforger.haunt.reconcile import PlanningRuleConfig
from fateforger.haunt.required_block_rule import RequiredBlockConfig, RequiredBlockRule

AMS = "Europe/Amsterdam"
DAY = date(2026, 9, 7)  # a Monday: working day by arithmetic


def event(eid: str, start: str, end: str, *, slug: str | None = None, day: date = DAY) -> dict:
    """A calendar event, optionally carrying the private slug tmbx writes."""
    ev = {
        "id": eid, "summary": "whatever the user typed",
        "start": {"dateTime": f"{day.isoformat()}T{start}:00+02:00", "timeZone": AMS},
        "end": {"dateTime": f"{day.isoformat()}T{end}:00+02:00", "timeZone": AMS},
    }
    if slug is not None:
        ev["extendedProperties"] = {"private": {"tmbx.slug": slug, "tmbx.uid": "u1"}}
    return ev


class Calendar:
    def __init__(self, *, day_events=None, by_id=None, fail_list=False, fail_get=False):
        self.day_events = list(day_events or [])
        self.by_id = dict(by_id or {})
        self.fail_list, self.fail_get = fail_list, fail_get
        self.list_calls, self.get_calls = 0, 0

    async def list_day(self, *, calendar_id, day, tz):
        self.list_calls += 1
        return None if self.fail_list else list(self.day_events)

    async def get_event(self, *, calendar_id, event_id):
        self.get_calls += 1
        if self.fail_get:
            raise RuntimeError("calendar unreachable")
        return self.by_id.get(event_id)

    async def list_events(self, **_):  # protocol completeness; unused here
        return list(self.day_events)


class Store:
    def __init__(self, slugs: list[str], *, pad_to: int = 0):
        self._slugs, self._pad_to, self.filters = slugs, pad_to, []

    async def query_constraints(self, *, filters, limit):
        self.filters.append(filters)
        rows = [{"uid": f"c-{s}", "name": f"rule {s}", "requires_block": s} for s in self._slugs]
        # Rows carrying no `requires_block` still count against the limit.
        rows += [{"uid": f"p-{i}", "name": "other rule"} for i in range(self._pad_to - len(rows))]
        return rows


class UnreadableStore:
    """The store the runtime wires when memory is not reachable: it raises."""

    async def query_constraints(self, *, filters, limit):
        raise RuntimeError("constraint dependency unavailable")


class Ledger:
    def __init__(self, sleep: str | None = "23:00", day_type: str | None = None,
                 fail_frame=False, fail_day_type=False):
        self._sleep, self._day_type = sleep, day_type
        self._fail_frame, self._fail_day_type = fail_frame, fail_day_type

    async def day_frame_for(self, *, owner_user_id, planning_date):
        if self._fail_frame:
            raise RuntimeError("session store unreachable")
        return None if self._sleep is None else {"wake": "07:00", "sleep": self._sleep}

    async def day_type_for(self, *, owner_user_id, planning_date):
        if self._fail_day_type:
            raise RuntimeError("session store unreachable")
        return self._day_type


def rule(calendar, store, ledger=None) -> RequiredBlockRule:
    return RequiredBlockRule(
        calendar_client=calendar, constraint_store=store, ledger=ledger or Ledger(),
        config=RequiredBlockConfig(calendar_id="primary", tz=AMS, ladder=PlanningRuleConfig()),
    )



