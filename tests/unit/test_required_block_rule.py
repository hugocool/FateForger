"""The watcher (spec §4): present in bounds → nothing; gone or out of bounds →
the nudge ladder; unreadable → no verdict. Presence is equality over the slug
tmbx wrote, never a title."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import PlanningRuleConfig
from fateforger.haunt.required_block_rule import (
    REASON_MISSING,
    REASON_MOVED_OUT,
    REQUIRED_BLOCK_KIND,
    RequiredBlockConfig,
    RequiredBlockRule,
    slug_of,
    within_bounds,
)

AMS = "Europe/Amsterdam"
DAY = date(2026, 9, 7)  # a Monday: working day by arithmetic
NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


def _event(eid: str, start: str, end: str, *, slug: str | None = None, day: date = DAY) -> dict:
    ev = {
        "id": eid, "summary": "whatever the user typed",
        "start": {"dateTime": f"{day.isoformat()}T{start}:00+02:00", "timeZone": AMS},
        "end": {"dateTime": f"{day.isoformat()}T{end}:00+02:00", "timeZone": AMS},
    }
    if slug is not None:
        ev["extendedProperties"] = {"private": {"tmbx.slug": slug, "tmbx.uid": "u1"}}
    return ev


class _Calendar:
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


class _Store:
    def __init__(self, slugs: list[str], *, pad_to: int = 0):
        self._slugs, self._pad_to, self.filters = slugs, pad_to, []

    async def query_constraints(self, *, filters, limit):
        self.filters.append(filters)
        rows = [{"uid": f"c-{s}", "name": f"rule {s}", "requires_block": s} for s in self._slugs]
        # Rows carrying no `requires_block` still count against the limit.
        rows += [{"uid": f"p-{i}", "name": "other rule"} for i in range(self._pad_to - len(rows))]
        return rows


class _UnreadableStore:
    """The store the runtime wires when memory is not reachable: it raises."""

    async def query_constraints(self, *, filters, limit):
        raise RuntimeError("constraint dependency unavailable")


class _Ledger:
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


def _rule(calendar, store, ledger=None) -> RequiredBlockRule:
    return RequiredBlockRule(
        calendar_client=calendar, constraint_store=store, ledger=ledger or _Ledger(),
        config=RequiredBlockConfig(calendar_id="primary", tz=AMS, ladder=PlanningRuleConfig()),
    )


async def _outcome(rule):
    return await rule.evaluate(now=NOW, scope="U1", user_id="U1", channel_id="D1")


async def _jobs(rule):
    return (await _outcome(rule)).jobs


def test_slug_of_reads_the_minted_property_and_nothing_else():
    assert slug_of(_event("e", "10:00", "10:30", slug="planning")) == "planning"
    assert slug_of({"id": "e", "summary": "planning session"}) is None
    assert slug_of({"extendedProperties": {"private": {"tmbx.slug": ""}}}) is None


def test_within_bounds_is_the_day_and_the_sleep_boundary():
    assert within_bounds(_event("e", "17:00", "17:20", slug="planning"), day=DAY, tz=AMS, sleep="23:00")
    assert not within_bounds(_event("e", "22:50", "23:10", slug="planning"), day=DAY, tz=AMS, sleep="23:00")
    assert not within_bounds(_event("e", "17:00", "17:20", slug="planning", day=date(2026, 9, 8)), day=DAY, tz=AMS, sleep="23:00")
    # a sleep time after midnight lands on the next day
    assert within_bounds(_event("e", "23:30", "23:50", slug="planning"), day=DAY, tz=AMS, sleep="00:30")
    # no frame: end of day
    assert within_bounds(_event("e", "23:30", "23:50", slug="planning"), day=DAY, tz=AMS, sleep=None)


@pytest.mark.asyncio
async def test_a_present_block_schedules_nothing_and_is_cached():
    cal = _Calendar(day_events=[_event("e1", "17:00", "17:20", slug="planning")])
    rule = _rule(cal, _Store(["planning"]))
    assert await _jobs(rule) == []
    assert rule.cached(user_id="U1", day=DAY, slug="planning") == "e1"
    assert cal.list_calls == 1


@pytest.mark.asyncio
async def test_the_cache_is_the_fast_path_and_a_hit_never_lists():
    ev = _event("e1", "17:00", "17:20", slug="planning")
    cal = _Calendar(day_events=[ev], by_id={"e1": ev})
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    assert await _jobs(rule) == []
    assert (cal.get_calls, cal.list_calls) == (1, 0)


@pytest.mark.asyncio
async def test_a_missing_block_starts_the_ladder_naming_the_kind():
    # `closure`, not `planning`: an absent planning session is the planning
    # ladder's nudge and never the watcher's (R2).
    cal = _Calendar(day_events=[_event("x", "10:00", "11:00")])  # a block with no slug
    rule = _rule(cal, _Store(["closure"]))
    jobs = await _jobs(rule)
    assert len(jobs) == PlanningRuleConfig().nudge_max_attempts
    first = jobs[0]
    assert first.key.as_id() == f"rule:required_blocks:U1:{DAY.isoformat()}:closure:nudge1"
    assert first.run_at == NOW + timedelta(minutes=10)
    assert first.payload.kind == REQUIRED_BLOCK_KIND
    assert (first.payload.slug, first.payload.reason) == ("closure", REASON_MISSING)
    assert first.payload.user_id == "U1" and first.payload.channel_id == "D1"


@pytest.mark.asyncio
async def test_a_block_moved_past_the_sleep_boundary_haunts_as_moved_out():
    cal = _Calendar(day_events=[_event("e1", "23:10", "23:30", slug="planning")])
    rule = _rule(cal, _Store(["planning"]), _Ledger(sleep="23:00"))
    jobs = await _jobs(rule)
    assert jobs and jobs[0].payload.reason == REASON_MOVED_OUT


@pytest.mark.asyncio
async def test_a_stale_cache_entry_is_rederived_from_the_mark():
    """The registered id is gone (deleted and re-created by hand or by a rebuilt
    patch); the day still carries a block with the slug: re-register, no haunt."""
    ev = _event("e2", "17:00", "17:20", slug="planning")
    cal = _Calendar(day_events=[ev], by_id={"e2": ev})
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1-gone")
    assert await _jobs(rule) == []
    assert rule.cached(user_id="U1", day=DAY, slug="planning") == "e2"


@pytest.mark.asyncio
async def test_a_failed_listing_gives_no_verdict_and_keeps_the_cache():
    cal = _Calendar(fail_list=True)
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    assert await _jobs(rule) == []
    assert rule.cached(user_id="U1", day=DAY, slug="planning") == "e1"


@pytest.mark.asyncio
async def test_a_failed_listing_names_the_slug_whose_jobs_must_survive():
    """No verdict never prunes (R1): the tick hands back the job-id prefix of
    the slug it could not judge, and the reconciler keeps what is under it."""
    rule = _rule(_Calendar(fail_list=True), _Store(["planning"]))
    outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == [f"rule:required_blocks:U1:{DAY.isoformat()}:planning:"]


@pytest.mark.asyncio
async def test_an_unreadable_store_leaves_the_whole_scope_undecided():
    """Nothing is known about which kinds the day requires, so nothing under
    this rule's prefix may be pruned -- not one slug's ladder, all of them."""
    rule = _rule(_Calendar(), _UnreadableStore())
    outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == ["rule:required_blocks:U1:"]


@pytest.mark.asyncio
async def test_a_failed_fetch_of_the_registered_id_gives_no_verdict():
    cal = _Calendar(fail_get=True)
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert cal.list_calls == 0
    assert outcome.undecided == [f"rule:required_blocks:U1:{DAY.isoformat()}:planning:"]


@pytest.mark.asyncio
async def test_a_decided_tick_leaves_nothing_undecided():
    rule = _rule(_Calendar(day_events=[_event("e1", "17:00", "17:20", slug="planning")]), _Store(["planning"]))
    assert (await _outcome(rule)).undecided == []


@pytest.mark.asyncio
async def test_the_planning_kind_also_accepts_the_events_own_planning_mark():
    """A session the nudge itself booked carries an `ffplanning…` id and no slug."""
    ev = {"id": "ffplanningU1", "summary": "x",
          "start": {"dateTime": f"{DAY.isoformat()}T17:00:00+02:00", "timeZone": AMS},
          "end": {"dateTime": f"{DAY.isoformat()}T17:20:00+02:00", "timeZone": AMS}}
    rule = _rule(_Calendar(day_events=[ev]), _Store(["planning"]))
    assert await _jobs(rule) == []


@pytest.mark.asyncio
async def test_no_required_kind_means_nothing_to_watch():
    cal = _Calendar()
    rule = _rule(cal, _Store([]))
    assert await _jobs(rule) == []
    assert cal.list_calls == 0


@pytest.mark.asyncio
async def test_the_store_is_asked_for_the_local_day_and_its_arithmetic_day_type():
    store = _Store(["planning"])
    await _jobs(_rule(_Calendar(day_events=[_event("e1", "17:00", "17:20", slug="planning")]), store))
    assert store.filters[0]["planned_day"] == DAY.isoformat()
    assert store.filters[0]["day_type"] == "working"
    assert store.filters[0]["require_active"] is True


@pytest.mark.asyncio
async def test_the_planning_kind_is_never_haunted_for_being_missing():
    """R2: presence of the planning session is the planning ladder's business.
    An absent planning block is its nudge, not a second one from the watcher."""
    rule = _rule(_Calendar(day_events=[]), _Store(["planning"]))
    outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == []


@pytest.mark.asyncio
async def test_the_planning_kind_is_haunted_when_it_has_left_its_bounds():
    """`moved_out` is the half the planning ladder cannot see: the block exists,
    so the ladder is satisfied, and only the watcher knows it drifted."""
    cal = _Calendar(day_events=[_event("e1", "17:00", "17:20", slug="planning", day=date(2026, 9, 8))])
    rule = _rule(cal, _Store(["planning"]))
    jobs = await _jobs(rule)
    assert jobs and jobs[0].payload.reason == REASON_MOVED_OUT
    assert jobs[0].payload.slug == "planning"


@pytest.mark.asyncio
async def test_every_other_kind_is_haunted_for_being_missing():
    """Only `planning` has a second ladder; nothing else does."""
    rule = _rule(_Calendar(day_events=[]), _Store(["closure"]))
    jobs = await _jobs(rule)
    assert jobs and jobs[0].payload.reason == REASON_MISSING
    assert jobs[0].payload.slug == "closure"


@pytest.mark.asyncio
async def test_a_missing_planning_block_does_not_hide_another_kinds_ladder():
    rule = _rule(_Calendar(day_events=[]), _Store(["closure", "planning"]))
    jobs = await _jobs(rule)
    assert {job.payload.slug for job in jobs} == {"closure"}


@pytest.mark.asyncio
async def test_the_cached_id_dragged_to_another_day_is_moved_out_without_listing():
    """R4: the id resolves, the kind matches, it is simply not on this day any
    more -- that is the drag the spec's end-to-end case names. Nothing about
    the day's other events can change it, so the list is not worth a call."""
    ev = _event("e1", "17:00", "17:20", slug="planning", day=date(2026, 9, 8))
    cal = _Calendar(day_events=[], by_id={"e1": ev})
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    jobs = await _jobs(rule)
    assert jobs and jobs[0].payload.reason == REASON_MOVED_OUT
    assert cal.list_calls == 0


@pytest.mark.asyncio
async def test_the_cached_id_pushed_past_sleep_is_moved_out_without_listing():
    ev = _event("e1", "23:10", "23:40", slug="planning")
    cal = _Calendar(day_events=[ev], by_id={"e1": ev})
    rule = _rule(cal, _Store(["planning"]), _Ledger(sleep="23:00"))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    jobs = await _jobs(rule)
    assert jobs and jobs[0].payload.reason == REASON_MOVED_OUT
    assert cal.list_calls == 0


@pytest.mark.asyncio
async def test_a_cached_id_that_is_no_longer_of_the_kind_still_lists():
    """The slug was stripped off the event the cache points at: the day may
    hold another block carrying it, so this one is re-derived, not judged."""
    was = _event("e1", "17:00", "17:20")  # no slug any more
    now_has = _event("e2", "18:00", "18:20", slug="closure")
    cal = _Calendar(day_events=[was, now_has], by_id={"e1": was, "e2": now_has})
    rule = _rule(cal, _Store(["closure"]))
    rule.remember(user_id="U1", day=DAY, slug="closure", event_id="e1")
    assert await _jobs(rule) == []
    assert cal.list_calls == 1
    assert rule.cached(user_id="U1", day=DAY, slug="closure") == "e2"


@pytest.mark.asyncio
async def test_the_day_type_comes_from_the_session_when_it_has_one():
    """R6: a Tuesday of annual leave is a `vacation` day, and the rules that
    apply to it are not the working day's. Weekday arithmetic cannot know."""
    store = _Store(["planning"])
    rule = _rule(_Calendar(day_events=[]), store, _Ledger(day_type="vacation"))
    await _outcome(rule)
    assert store.filters[0]["day_type"] == "vacation"


@pytest.mark.asyncio
async def test_a_day_with_no_session_falls_back_to_the_weekday():
    store = _Store(["planning"])
    await _outcome(_rule(_Calendar(day_events=[]), store, _Ledger(day_type=None)))
    assert store.filters[0]["day_type"] == "working"


@pytest.mark.asyncio
async def test_a_frame_the_ledger_cannot_answer_gives_no_verdict(caplog):
    """The sleep boundary is half of `within_bounds`. Without it a block inside
    its bounds is indistinguishable from one past them, so nothing is judged --
    and, R1, nothing is pruned."""
    rule = _rule(_Calendar(day_events=[]), _Store(["closure"]), _Ledger(fail_frame=True))
    rule.remember(user_id="U1", day=DAY, slug="closure", event_id="e1")
    with caplog.at_level("WARNING"):
        outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == [f"rule:required_blocks:U1:{DAY.isoformat()}:closure:"]
    assert rule.cached(user_id="U1", day=DAY, slug="closure") == "e1"
    assert any("required_blocks_unreadable" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_day_type_the_ledger_cannot_answer_leaves_the_whole_scope_undecided(caplog):
    """The day type decides which rules are asked for, so a failure here means
    the required set itself is unknown -- not one slug's, all of them."""
    rule = _rule(_Calendar(day_events=[]), _Store(["closure"]), _Ledger(fail_day_type=True))
    with caplog.at_level("WARNING"):
        outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == ["rule:required_blocks:U1:"]
    assert any("required_blocks_unreadable" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_an_unparseable_event_gives_no_verdict_for_that_slug(caplog):
    """A day whose events cannot be read is an unread day. Treating the ones
    that did parse as the whole day would haunt for a block that is there."""
    broken = {"id": "e9", "start": {"dateTime": {"nested": 1}}, "end": {"dateTime": {"nested": 1}},
              "extendedProperties": {"private": {"tmbx.slug": "closure"}}}
    cal = _Calendar(day_events=[broken])
    rule = _rule(cal, _Store(["closure"]))
    with caplog.at_level("WARNING"):
        outcome = await _outcome(rule)
    assert outcome.jobs == []
    assert outcome.undecided == [f"rule:required_blocks:U1:{DAY.isoformat()}:closure:"]
    assert any("calendar_unreadable" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_full_page_of_rules_says_so(caplog):
    """The read is capped at 200 rows and takes no cursor, so a user with more
    active rules than that silently loses the ones past the cap -- including,
    possibly, the only rule that requires a block."""
    rule = _rule(_Calendar(day_events=[]), _Store(["closure"], pad_to=200))
    with caplog.at_level("WARNING"):
        await _outcome(rule)
    assert any("required_blocks_truncated" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_page_under_the_cap_is_quiet(caplog):
    rule = _rule(_Calendar(day_events=[]), _Store(["closure"]))
    with caplog.at_level("WARNING"):
        await _outcome(rule)
    assert not any("required_blocks_truncated" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_yesterdays_cache_entries_are_dropped():
    """The cache is keyed by day and the process runs for weeks. Nothing ever
    asks for a past day again, so those entries are held for the life of the
    process and never read."""
    rule = _rule(_Calendar(day_events=[_event("e1", "17:00", "17:20", slug="closure")]), _Store(["closure"]))
    yesterday = DAY - timedelta(days=1)
    rule.remember(user_id="U1", day=yesterday, slug="closure", event_id="old")
    rule.remember(user_id="U2", day=yesterday, slug="closure", event_id="old-too")
    await _outcome(rule)
    assert rule.cached(user_id="U1", day=yesterday, slug="closure") is None
    assert rule.cached(user_id="U2", day=yesterday, slug="closure") is None
    assert rule.cached(user_id="U1", day=DAY, slug="closure") == "e1"
