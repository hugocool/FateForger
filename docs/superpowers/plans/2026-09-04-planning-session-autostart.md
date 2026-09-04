# Planning session auto-start — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the daily planning event's start time arrives, the agent opens the timeboxing session itself (root + one host-built `ConfirmPlanningDay` turn + DM link) and the Admonisher walks Hugo to it at +2/+5/+10/+20/+40 minutes, cancelled by any activity in the session; a passed, uncommitted planning event becomes a missing one again.

**Architecture:** The reconciler (`haunt/reconcile.py`) derives two more jobs from the anchor event — `session_start` and `session_expire` — the way it derives nudges, and its rule stops treating a past, uncommitted event as "planned". Policy (day rule, offsets, nudge lines, expire delay) is data in `haunt/session_start.py`. Orchestration that touches Slack and the kernel lives in `slack_bot/session_start.py` (`SessionStarter`), reached through the existing reminder dispatcher by reminder kind. The session surface builder is extracted from `route_slack_event` into `slack_bot/session_surface.py` so the request path and the auto-open share one function. Cancel-on-reply is wired once, in `_run_adaptive_timebox_turn`, which every reply and press already passes through.

**Tech Stack:** Python 3.11, APScheduler (`AsyncIOScheduler`, in-memory jobstore, `date` triggers), AutoGen runtime (`UserFacingMessage` → user channel → Slack delivery sink), Slack Bolt async, pytest + pytest-asyncio auto mode.

Spec: `docs/superpowers/specs/2026-09-04-planning-session-autostart-design.md`.

## Global Constraints

- **No keyword/regex/substring judgement on user text, ever** (CLAUDE.md). Nothing in this increment judges user text; an AST guard on `haunt/session_start.py` keeps it so. Comparisons over values the system minted (`reminder.kind`, `session.status`, dates) are fine.
- **Re-derive, never persist**: new jobs are `DesiredJob`s returned by `evaluate`, keyed by `JobKey`, `replace_existing=True`. No `add_job` anywhere else.
- **One guard source**: the timeboxing session store's `standing_for` (indexed query), never the activity tracker or a flag.
- **Buttons and the auto-open converge**: the auto-open's turn is a `TimeboxActionEnvelope` delivered through `_deliver_timebox_turn`, the same path a card press takes.
- **Failure stays loud**: every failure path metered with `record_error(component="session_start", error_type=…)` and logged; nothing degrades silently.
- **Policy values, verbatim from the spec**: offsets `(2, 5, 10, 20, 40)` minutes; five nudge lines (Task 1); day cutoff hour `14` in the event's timezone; `expire_after` 60 minutes; follow-up `escalation="gentle"`, `cancel_on_user_reply=True`.
- **Worktree:** `.worktrees/planning-session-autostart`, branch `feat/planning-session-autostart` (off `main` at `a71f213`). Build the venv with `uv venv --allow-existing --python 3.11 .venv && uv sync`, then the poetry dev group via `tomllib` → `uv pip install -r` (see memory `worktree-venv-poetry-repoints-parent`); **never `poetry install` in a worktree**. Run tests as `.venv/bin/python -m pytest … -q`. Known pre-existing order-dependent flake: `tests/unit/test_harness_approval_action.py::test_approval_owns_thread_before_any_slack_or_calendar_await` (passes alone).
- **Never commit** `uv.lock`, `.env`, `data/`, `logs/`, `.superpowers/`.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File structure

| file | responsibility |
|---|---|
| `src/fateforger/haunt/session_start.py` (new) | Policy as data + pure functions: `planning_day_for`, `LADDER_OFFSETS`, `NUDGE_LINES`, `nudge_line`, kind constants. No I/O. |
| `src/fateforger/haunt/messages.py`, `service.py`, `agents.py` | `FollowUpSpec.offsets` and `.lines`; the service schedules by offsets; the agent formats by attempt line. |
| `src/fateforger/haunt/reconcile.py` | `PlanningReminder` carries the event window; `PlanningRuleConfig.expire_after`; `PlanningSessionRule(timeboxing_ledger=…)`; `evaluate` emits `session_start`/`session_expire` and applies the changed rule. |
| `src/fateforger/core/runtime.py` | passes `timeboxing_ledger=timeboxing_session_store` to the rule. |
| `src/fateforger/slack_bot/session_surface.py` (new) | `open_session_surface(...) -> SessionSurface` extracted from `route_slack_event`. |
| `src/fateforger/slack_bot/handlers.py` | uses `open_session_surface`; `_run_adaptive_timebox_turn` records activity and cancels follow-ups. |
| `src/fateforger/slack_bot/session_start.py` (new) | `SessionStarter.start(reminder)` / `.expire(reminder)`: guard, day, surface, turn, DM, arming; expire hand-over. |
| `src/fateforger/slack_bot/planning.py` | `dispatch_planning_reminder` routes the two new kinds to `SessionStarter`. |
| tests | `tests/unit/test_session_start_policy.py`, `test_haunting_offsets.py`, `test_reconcile_session_jobs.py`, `test_session_surface.py`, `test_session_starter.py`, additions to `test_slack_timeboxing_routing.py`. |

---

### Task 1: Policy as data (`haunt/session_start.py`)

**Files:**
- Create: `src/fateforger/haunt/session_start.py`
- Test: `tests/unit/test_session_start_policy.py`

**Interfaces:**
- Produces:
  - `SESSION_START_KIND = "session_start"`, `SESSION_EXPIRE_KIND = "session_expire"`
  - `DAY_CUTOFF_HOUR = 14`
  - `LADDER_OFFSETS: tuple[timedelta, ...] = (2m, 5m, 10m, 20m, 40m)`
  - `NUDGE_LINES: tuple[str, ...]` — five lines with `{permalink}` and `{start}` placeholders
  - `def planning_day_for(event_start: datetime) -> date` — `event_start` must be tz-aware; rule in its own tz
  - `def nudge_line(attempt: int, *, permalink: str, start: str) -> str` — attempt is 0-based
  - `def dm_open_line(*, day_label: str, permalink: str) -> str`
  - `def missed_line() -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_session_start_policy.py
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import pytest

from fateforger.haunt import session_start
from fateforger.haunt.session_start import (
    LADDER_OFFSETS,
    NUDGE_LINES,
    dm_open_line,
    missed_line,
    nudge_line,
    planning_day_for,
)

AMS = ZoneInfo("Europe/Amsterdam")


def test_a_morning_event_plans_its_own_day() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 9, 0, tzinfo=AMS)) == date(2026, 9, 4)


def test_an_evening_event_plans_the_next_day() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 18, 0, tzinfo=AMS)) == date(2026, 9, 5)


def test_the_cutoff_is_fourteen_in_the_events_own_zone() -> None:
    assert planning_day_for(datetime(2026, 9, 4, 13, 59, tzinfo=AMS)) == date(2026, 9, 4)
    assert planning_day_for(datetime(2026, 9, 4, 14, 0, tzinfo=AMS)) == date(2026, 9, 5)
    # 13:00 UTC is 15:00 Amsterdam: an afternoon session, planning tomorrow.
    utc_afternoon = datetime(2026, 9, 4, 13, 0, tzinfo=ZoneInfo("UTC")).astimezone(AMS)
    assert planning_day_for(utc_afternoon) == date(2026, 9, 5)


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone"):
        planning_day_for(datetime(2026, 9, 4, 9, 0))


def test_the_ladder_is_the_agreed_five_offsets() -> None:
    assert LADDER_OFFSETS == tuple(timedelta(minutes=m) for m in (2, 5, 10, 20, 40))
    assert len(NUDGE_LINES) == len(LADDER_OFFSETS)


def test_every_nudge_line_carries_the_permalink() -> None:
    for attempt in range(len(NUDGE_LINES)):
        line = nudge_line(attempt, permalink="https://x/p", start="09:00")
        assert "https://x/p" in line
    assert "09:00" in nudge_line(2, permalink="https://x/p", start="09:00")


def test_an_attempt_past_the_ladder_uses_the_last_line() -> None:
    assert nudge_line(99, permalink="p", start="s") == nudge_line(4, permalink="p", start="s")


def test_open_and_missed_lines() -> None:
    assert "https://x/p" in dm_open_line(day_label="Fri 4 Sep", permalink="https://x/p")
    assert "Fri 4 Sep" in dm_open_line(day_label="Fri 4 Sep", permalink="https://x/p")
    assert missed_line()


def test_the_policy_module_never_reads_user_text() -> None:
    """CLAUDE.md: nothing here may judge what a user wrote."""

    tree = ast.parse(inspect.getsource(session_start))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "re" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"lower", "upper", "startswith", "endswith", "split", "find"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_session_start_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.haunt.session_start'`

- [ ] **Step 3: Write the module**

```python
# src/fateforger/haunt/session_start.py
"""Policy for the planning session that starts itself.

Every value here is a decision Hugo made on 2026-09-04, kept as data so the
harness port (#164) can lift it into a skill or config without re-deciding.
Nothing in this module does I/O and nothing reads what a user wrote.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

#: Reminder kinds the reconciler emits beside its nudges.
SESSION_START_KIND = "session_start"
SESSION_EXPIRE_KIND = "session_expire"

#: A session starting before this hour (in the event's own timezone) plans the
#: event's day; from this hour on it plans the next day. #282 replaces this
#: rule with the day recorded on the event.
DAY_CUTOFF_HOUR = 14

#: Minutes after the open at which the Admonisher speaks, measured from the
#: moment the DM link is posted.
LADDER_OFFSETS: tuple[timedelta, ...] = (
    timedelta(minutes=2),
    timedelta(minutes=5),
    timedelta(minutes=10),
    timedelta(minutes=20),
    timedelta(minutes=40),
)

#: One line per rung, escalating. `{permalink}` is the session thread,
#: `{start}` the event's start as HH:MM.
NUDGE_LINES: tuple[str, ...] = (
    "Your planning session is open — {permalink}",
    "Still waiting — the day isn't planned yet — {permalink}",
    "{start} has passed. Ten minutes in — {permalink}",
    "Twenty minutes. Plan the day or tell me when you will — {permalink}",
    "Last call: the session closes at the end of the hour — {permalink}",
)


def planning_day_for(event_start: datetime) -> date:
    """Which day a session starting at ``event_start`` plans. Host arithmetic."""

    if event_start.tzinfo is None:
        raise ValueError("event_start must carry a timezone; the cutoff is local")
    if event_start.hour < DAY_CUTOFF_HOUR:
        return event_start.date()
    return event_start.date() + timedelta(days=1)


def nudge_line(attempt: int, *, permalink: str, start: str) -> str:
    """The Admonisher's line for rung ``attempt`` (0-based); past the end, the last."""

    index = min(max(attempt, 0), len(NUDGE_LINES) - 1)
    return NUDGE_LINES[index].format(permalink=permalink, start=start)


def dm_open_line(*, day_label: str, permalink: str) -> str:
    return f"Your planning session for {day_label} is open — {permalink}"


def missed_line() -> str:
    return "Missed today's planning session."


__all__ = [
    "DAY_CUTOFF_HOUR",
    "LADDER_OFFSETS",
    "NUDGE_LINES",
    "SESSION_EXPIRE_KIND",
    "SESSION_START_KIND",
    "dm_open_line",
    "missed_line",
    "nudge_line",
    "planning_day_for",
]
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_session_start_policy.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/haunt/session_start.py tests/unit/test_session_start_policy.py
git commit -m "feat(haunt): the auto-start policy is data

Day rule, ladder offsets, nudge lines and the two reminder kinds, kept as
plain values so the harness port can lift them.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Follow-ups by explicit offsets, with per-rung lines

**Files:**
- Modify: `src/fateforger/haunt/messages.py:11-16` (`FollowUpSpec`)
- Modify: `src/fateforger/haunt/service.py:69-110` (`schedule_followup`), `:204-255` (`_dispatch_followup`)
- Modify: `src/fateforger/haunt/agents.py:76-84` (`_format_followup`)
- Test: `tests/unit/test_haunting_offsets.py` (new)

**Interfaces:**
- Produces: `FollowUpSpec(should_schedule, after=None, max_attempts=None, escalation=None, cancel_on_user_reply=None, offsets: tuple[timedelta, ...] | None = None, lines: tuple[str, ...] | None = None)`. When `offsets` is set: attempt *n* fires at `created_at + offsets[n]`; the ladder ends after `len(offsets)` attempts; `after`/`max_attempts` are ignored. When `lines` is set, `HauntingAgent` sends `lines[attempt]` (last line past the end) instead of the prefixed `content`.
- Consumes: nothing from Task 1 (the service stays policy-agnostic).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_haunting_offsets.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.agents import HauntingAgent
from fateforger.haunt.messages import FollowUpDue, FollowUpSpec
from fateforger.haunt.service import HauntingService, PendingFollowUp


class _FakeScheduler:
    """Records date jobs; the test fires them by hand."""

    def __init__(self) -> None:
        self.jobs: dict[str, tuple[datetime, dict]] = {}

    def add_job(self, func, trigger, run_date, id, kwargs, replace_existing, **_):  # noqa: A002
        self.jobs[id] = (run_date, {"func": func, **kwargs})

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


def _clock(start: datetime):
    state = {"now": start}

    def now() -> datetime:
        return state["now"]

    return now, state


@pytest.mark.asyncio
async def test_offsets_schedule_each_rung_from_the_arming_time() -> None:
    t0 = datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc)
    now, state = _clock(t0)
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    fired: list[int] = []

    async def dispatcher(due: FollowUpDue) -> None:
        fired.append(due.attempt)

    service.set_dispatcher(dispatcher)
    offsets = (timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=10))
    await service.schedule_followup(
        message_id="planning_session:C1:1.0",
        topic_id="C1:1.0",
        task_id=None,
        user_id="U1",
        channel_id="D1",
        content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=offsets, cancel_on_user_reply=True),
    )

    run_at, job = next(iter(scheduler.jobs.values()))
    assert run_at == t0 + timedelta(minutes=2)

    for expected_minutes in (5, 10):
        state["now"] = run_at
        await job["func"](message_id=job["message_id"])
        run_at, job = next(iter(scheduler.jobs.values()))
        assert run_at == t0 + timedelta(minutes=expected_minutes)

    state["now"] = run_at
    await job["func"](message_id=job["message_id"])
    assert fired == [0, 1, 2]
    assert scheduler.jobs == {}  # the ladder ended after the last offset


@pytest.mark.asyncio
async def test_activity_on_the_topic_cancels_an_offsets_ladder() -> None:
    now, _ = _clock(datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc))
    scheduler = _FakeScheduler()
    service = HauntingService(scheduler, now=now)
    service.set_dispatcher(lambda due: None)
    await service.schedule_followup(
        message_id="planning_session:C1:1.0", topic_id="C1:1.0", task_id=None,
        user_id="U1", channel_id="D1", content="open",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2),), cancel_on_user_reply=True),
    )

    cancelled = await service.record_user_activity(topic_id="C1:1.0", task_id=None, user_id="U1")

    assert cancelled == 1
    assert scheduler.jobs == {}


def test_lines_select_by_attempt_and_clamp_at_the_end() -> None:
    record = PendingFollowUp(
        message_id="m", topic_id="t", task_id=None, user_id="U1", channel_id="D1",
        content="ignored when lines are set",
        spec=FollowUpSpec(should_schedule=True, offsets=(timedelta(minutes=2), timedelta(minutes=5)), lines=("first", "second")),
        attempt=0, created_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
    )
    due = lambda attempt: FollowUpDue(message_id="m", topic_id="t", task_id=None, attempt=attempt, escalation="gentle", user_id="U1")  # noqa: E731

    assert HauntingAgent._format_followup(record, due(0)) == "first"
    assert HauntingAgent._format_followup(record, due(1)) == "second"
    assert HauntingAgent._format_followup(record, due(7)) == "second"


def test_without_lines_the_old_prefixes_still_apply() -> None:
    record = PendingFollowUp(
        message_id="m", topic_id="t", task_id=None, user_id="U1", channel_id="D1",
        content="Follow up here", spec=FollowUpSpec(should_schedule=True, after=timedelta(minutes=10)),
        attempt=0, created_at=datetime(2026, 9, 4, 7, 0, tzinfo=timezone.utc),
    )
    due = FollowUpDue(message_id="m", topic_id="t", task_id=None, attempt=0, escalation="firm", user_id="U1")
    assert HauntingAgent._format_followup(record, due) == "Reminder: Follow up here"
```

Check `FollowUpDue`'s field list in `messages.py` before running; if it lacks `escalation` or `user_id` as keyword args, match the constructor exactly as defined.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_haunting_offsets.py -q`
Expected: FAIL — `TypeError: FollowUpSpec.__init__() got an unexpected keyword argument 'offsets'`

- [ ] **Step 3: Implement**

`messages.py` — add two fields to `FollowUpSpec`:

```python
@dataclass(frozen=True)
class FollowUpSpec:
    should_schedule: bool
    after: Optional[timedelta] = None
    max_attempts: int | None = None
    escalation: FollowUpEscalation | None = None
    cancel_on_user_reply: bool | None = None
    #: Explicit rungs measured from the arming time. When set, `after` and
    #: `max_attempts` are ignored and the ladder ends after the last rung.
    offsets: tuple[timedelta, ...] | None = None
    #: One line per rung; past the end, the last. When set, replaces the
    #: prefixed `content`.
    lines: tuple[str, ...] | None = None
```

`service.py` — in `schedule_followup`, replace the two early returns on `max_attempts`/`after` with:

```python
        if effective_spec.offsets:
            if not effective_spec.offsets:
                return None
        else:
            if effective_spec.max_attempts is not None and effective_spec.max_attempts < 1:
                return None
            if not effective_spec.after or effective_spec.after.total_seconds() <= 0:
                return None
```

and the first scheduling line becomes:

```python
            first_at = (
                record.created_at + effective_spec.offsets[0]
                if effective_spec.offsets
                else record.created_at + effective_spec.after
            )
            self._schedule_job(record, first_at)
```

Check `_apply_settings` (service.py ~278-330): it rebuilds the spec field by field. Carry `offsets` and `lines` through unchanged there, or the fields are lost before scheduling — add `offsets=spec.offsets, lines=spec.lines` to the `FollowUpSpec(...)` it constructs.

In `_dispatch_followup`, replace the tail from `next_attempt = record.attempt + 1` to the end with:

```python
            next_attempt = record.attempt + 1
            offsets = record.spec.offsets
            if offsets:
                if next_attempt >= len(offsets):
                    await self._remove_record(record)
                    return
            else:
                max_attempts = record.spec.max_attempts or 1
                if next_attempt >= max_attempts:
                    await self._remove_record(record)
                    return

            updated = PendingFollowUp(
                message_id=record.message_id,
                topic_id=record.topic_id,
                task_id=record.task_id,
                user_id=record.user_id,
                channel_id=record.channel_id,
                content=record.content,
                spec=record.spec,
                attempt=next_attempt,
                created_at=record.created_at,
            )
            self._pending[message_id] = updated

            if offsets:
                self._schedule_job(updated, record.created_at + offsets[next_attempt])
                return
            if not record.spec.after:
                await self._remove_record(record)
                return
            delay = _next_delay(record.spec.after, next_attempt)
            self._schedule_job(updated, self._now() + delay)
```

`agents.py` — `_format_followup`:

```python
    @staticmethod
    def _format_followup(record: PendingFollowUp, due: FollowUpDue) -> str:
        lines = record.spec.lines
        if lines:
            return lines[min(max(due.attempt, 0), len(lines) - 1)]
        prefixes = {
            "gentle": "Just checking in",
            "firm": "Reminder",
            "menacing": "Following up",
        }
        prefix = prefixes.get(due.escalation or "gentle", "Checking in")
        return f"{prefix}: {record.content}"
```

- [ ] **Step 4: Run the new tests and the existing haunt tests**

Run: `.venv/bin/python -m pytest tests/unit/test_haunting_offsets.py tests/integration/test_haunting_service.py tests/unit/test_haunt_delivery_observability.py tests/unit/test_haunt_slack_delivery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/haunt/messages.py src/fateforger/haunt/service.py src/fateforger/haunt/agents.py tests/unit/test_haunting_offsets.py
git commit -m "feat(haunt): a follow-up can run on explicit rungs with a line per rung

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: The reconciler emits `session_start`/`session_expire` and stops counting a passed event as planned

**Files:**
- Modify: `src/fateforger/haunt/reconcile.py` — `PlanningRuleConfig` (~44-58), `PlanningReminder` (~77-84), `PlanningSessionRule.__init__` (~141-153), `evaluate` anchor branch (~166-192)
- Modify: `src/fateforger/core/runtime.py:777-784` (rule construction)
- Test: `tests/unit/test_reconcile_session_jobs.py` (new)

**Interfaces:**
- Consumes: `SESSION_START_KIND`, `SESSION_EXPIRE_KIND`, `planning_day_for` (Task 1); `standing_for(owner_user_id, open_since, planned_from, planned_to) -> TimeboxingStanding(open_session_key, committed_session_key)` on `SqlAlchemyTimeboxingSessionRepository`.
- Produces:
  - `PlanningRuleConfig.expire_after: timedelta = timedelta(minutes=60)`
  - `PlanningReminder` gains `event_start: str | None = None`, `event_end: str | None = None` (ISO, tz-aware), `event_tz: str | None = None`
  - `PlanningSessionRule(..., timeboxing_ledger=None)`; `evaluate` returns `[session_start, session_expire]` jobs while the anchor's end is ahead; a passed anchor with no committed session for `planning_day_for(start)` falls through to the nudge ladder; with a committed session it returns `[]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_reconcile_session_jobs.py
"""A planning event ahead schedules its own start; a passed one is missing again."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import TimeboxingStanding
from fateforger.haunt.reconcile import PlanningReconciler, PlanningRuleConfig, PlanningSessionRule
from fateforger.haunt.session_start import SESSION_EXPIRE_KIND, SESSION_START_KIND

from .test_reconcile import DummyCalendarClient, FakeScheduler

AMS = "Europe/Amsterdam"


def _event(start: datetime, minutes: int = 30) -> dict:
    return {
        "id": "ffplanning1",
        "summary": "Daily planning session",
        "start": {"dateTime": start.isoformat(), "timeZone": AMS},
        "end": {"dateTime": (start + timedelta(minutes=minutes)).isoformat(), "timeZone": AMS},
    }


class _Ledger:
    def __init__(self, committed_key: str | None = None, open_key: str | None = None):
        self._standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)
        self.asked: list[dict] = []

    async def standing_for(self, **kwargs):
        self.asked.append(kwargs)
        return self._standing


def _reconciler(scheduler, *, event: dict | None, ledger=None):
    client = DummyCalendarClient(events=[])
    if event is not None:
        client.event_lookup[("primary", "ffplanning1")] = event
    rule = PlanningSessionRule(calendar_client=client, config=PlanningRuleConfig(), timeboxing_ledger=ledger)

    async def dispatch(reminder):
        return None

    return PlanningReconciler(scheduler, calendar_client=client, dispatcher=dispatch, rule=rule)


def _jobs_by_kind(scheduler):
    return {job.kwargs["reminder"].kind: job for job in scheduler.get_jobs()}


@pytest.mark.asyncio
async def test_an_event_ahead_schedules_its_start_and_expiry_and_no_nudges() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start))
    now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    jobs = _jobs_by_kind(scheduler)
    assert set(jobs) == {SESSION_START_KIND, SESSION_EXPIRE_KIND}
    assert jobs[SESSION_START_KIND].trigger.run_date == start
    assert jobs[SESSION_EXPIRE_KIND].trigger.run_date == start + timedelta(minutes=30) + timedelta(minutes=60)
    reminder = jobs[SESSION_START_KIND].kwargs["reminder"]
    assert reminder.event_start == start.isoformat()
    assert reminder.event_tz == AMS
    assert reminder.user_id == "U1" and reminder.channel_id == "D1"


@pytest.mark.asyncio
async def test_a_restart_inside_the_window_starts_now_not_in_the_past() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start))
    now = start + timedelta(minutes=10)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now.astimezone(timezone.utc)
    )

    jobs = _jobs_by_kind(scheduler)
    assert jobs[SESSION_START_KIND].trigger.run_date >= now.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_a_passed_event_with_no_committed_session_is_missing_again() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    ledger = _Ledger(committed_key=None)
    reconciler = _reconciler(scheduler, event=_event(start), ledger=ledger)
    now = (start + timedelta(hours=2)).astimezone(timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    kinds = set(_jobs_by_kind(scheduler))
    assert SESSION_START_KIND not in kinds
    assert any(kind.startswith("nudge") for kind in kinds)
    assert ledger.asked and ledger.asked[0]["planned_from"] == start.date()


@pytest.mark.asyncio
async def test_a_passed_event_with_a_committed_session_schedules_nothing() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    reconciler = _reconciler(scheduler, event=_event(start), ledger=_Ledger(committed_key="C1:1.0"))
    now = (start + timedelta(hours=2)).astimezone(timezone.utc)

    await reconciler.reconcile_missing_planning(
        scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now
    )

    assert scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_a_moved_event_moves_its_jobs_under_the_same_keys() -> None:
    scheduler = FakeScheduler()
    start = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))
    client = DummyCalendarClient(events=[])
    client.event_lookup[("primary", "ffplanning1")] = _event(start)
    rule = PlanningSessionRule(calendar_client=client, config=PlanningRuleConfig())

    async def dispatch(reminder):
        return None

    reconciler = PlanningReconciler(scheduler, calendar_client=client, dispatcher=dispatch, rule=rule)
    now = datetime(2026, 9, 4, 6, 0, tzinfo=timezone.utc)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now)
    first_ids = {job.id for job in scheduler.get_jobs()}

    client.event_lookup[("primary", "ffplanning1")] = _event(start + timedelta(hours=1))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", channel_id="D1", planning_event_id="ffplanning1", now=now)

    assert {job.id for job in scheduler.get_jobs()} == first_ids
    assert _jobs_by_kind(scheduler)[SESSION_START_KIND].trigger.run_date == start + timedelta(hours=1)
```

Note the last test relies on `reconcile_missing_planning` re-timing a job whose `run_at` changed. Read the "retime" comment at reconcile.py:~600: without `first_nudge_offset`, an already-scheduled id keeps its old time. That rule exists so *nudge* ladders don't re-anchor to `now`; `session_start`/`session_expire` are anchored to the *event*, not to `now`, so they must always take the new time. Implement: in the loop, `if not retime and not job.key.kind in (SESSION_START_KIND, SESSION_EXPIRE_KIND): keep already`. (Comparison over a kind the system minted.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_reconcile_session_jobs.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'timeboxing_ledger'`

- [ ] **Step 3: Implement**

`PlanningRuleConfig` — add:

```python
    # How long after the planning event's end the auto-opened session is
    # declared missed and the ordinary missing-planning ladder takes over.
    expire_after: timedelta = timedelta(minutes=60)
```

`PlanningReminder` — add three optional fields after `channel_id`:

```python
    event_start: str | None = None
    event_end: str | None = None
    event_tz: str | None = None
```

`PlanningSessionRule.__init__` — add `timeboxing_ledger: Any | None = None` and store `self._timeboxing_ledger = timeboxing_ledger`. Import `from .session_start import SESSION_EXPIRE_KIND, SESSION_START_KIND, planning_day_for` and `from zoneinfo import ZoneInfo`.

`evaluate` — replace the `if anchor_in_window:` block with:

```python
        if anchor_found and anchor_in_window:
            anchor_tz_name = _event_tz_name(anchor)
            anchor_tz = ZoneInfo(anchor_tz_name) if anchor_tz_name else timezone.utc
            anchor_start = _parse_event_dt(anchor.get("start"), tz=anchor_tz)
            anchor_end = _parse_event_dt(anchor.get("end"), tz=anchor_tz)
            if anchor_start is not None and anchor_end is not None:
                local_start = anchor_start.astimezone(anchor_tz)
                if anchor_end > start:
                    # Ahead or under way: the event owns the window. Start the
                    # session at its start (now, if the bot came up mid-window)
                    # and declare it missed expire_after past its end.
                    window_start = start.date().isoformat()
                    session_run_at = max(anchor_start, start + timedelta(seconds=5))
                    reminder_fields = dict(
                        scope=scope,
                        user_id=user_id,
                        channel_id=channel_id,
                        event_start=anchor_start.isoformat(),
                        event_end=anchor_end.isoformat(),
                        event_tz=anchor_tz_name or "UTC",
                    )
                    jobs = [
                        DesiredJob(
                            key=JobKey("rule", self.rule_id, scope, window_start, SESSION_START_KIND),
                            run_at=session_run_at,
                            payload=PlanningReminder(kind=SESSION_START_KIND, attempt=1, message="", **reminder_fields),
                        ),
                        DesiredJob(
                            key=JobKey("rule", self.rule_id, scope, window_start, SESSION_EXPIRE_KIND),
                            run_at=anchor_end + self._config.expire_after,
                            payload=PlanningReminder(kind=SESSION_EXPIRE_KIND, attempt=1, message="", **reminder_fields),
                        ),
                    ]
                    self._log_evaluate_outcome(outcome="anchor_ahead", scope=scope, user_id=user_id, planning_event_id=planning_event_id, start=start, end=end, anchor_found=anchor_found, anchor_in_window=anchor_in_window, stored_hit=stored_hit, list_count=list_count, fallback_hit=fallback_hit, jobs_count=len(jobs))
                    return jobs
                # The event has passed. It counts only if the day it planned
                # was committed; otherwise the planning session is missing again.
                if await self._committed_for(user_id=user_id, day=planning_day_for(local_start), now=start):
                    self._log_evaluate_outcome(outcome="anchor_past_committed", scope=scope, user_id=user_id, planning_event_id=planning_event_id, start=start, end=end, anchor_found=anchor_found, anchor_in_window=anchor_in_window, stored_hit=stored_hit, list_count=list_count, fallback_hit=fallback_hit, jobs_count=0)
                    return []
                # fall through to the stored/fallback checks and the nudge ladder
            else:
                self._log_evaluate_outcome(outcome="anchor_match", scope=scope, user_id=user_id, planning_event_id=planning_event_id, start=start, end=end, anchor_found=anchor_found, anchor_in_window=anchor_in_window, stored_hit=stored_hit, list_count=list_count, fallback_hit=fallback_hit, jobs_count=0)
                return []
```

Add two helpers to the rule / module:

```python
    async def _committed_for(self, *, user_id: str | None, day: date, now: datetime) -> bool:
        if not user_id or self._timeboxing_ledger is None:
            return False
        try:
            standing = await self._timeboxing_ledger.standing_for(
                owner_user_id=user_id,
                open_since=now - timedelta(hours=1),
                planned_from=day,
                planned_to=day,
            )
        except Exception:
            logger.exception("committed-session lookup failed for user=%s day=%s", user_id, day)
            return False
        return standing.committed_session_key is not None


def _event_tz_name(event: dict) -> str | None:
    start = event.get("start")
    if isinstance(start, dict):
        name = start.get("timeZone")
        return str(name) if name else None
    return None
```

(`open_since` mirrors `PlanningCoordinator.OPEN_SESSION_UNDER_WAY`; `standing_for` needs it even though only `committed_session_key` is read here.)

Then, in `reconcile_missing_planning`'s loop, make the event-anchored kinds always take the new time:

```python
        for job in desired:
            run_at = job.run_at
            event_anchored = job.key.kind in (SESSION_START_KIND, SESSION_EXPIRE_KIND)
            if not retime and not event_anchored:
                already = scheduled.get(job.key.as_id())
                if already is not None:
                    run_at = already
```

`runtime.py` — pass the ledger:

```python
        rule=PlanningSessionRule(
            calendar_client=calendar_client,
            planning_session_store=planning_session_store,
            timeboxing_ledger=timeboxing_session_store,
            config=PlanningRuleConfig(
                calendar_id=timeboxing_calendar_id or "primary"
            ),
        ),
```

- [ ] **Step 4: Run the new and existing reconciler tests**

Run: `.venv/bin/python -m pytest tests/unit/test_reconcile_session_jobs.py tests/unit/test_reconcile.py tests/unit/test_nudge_ladder_is_idempotent_in_time.py tests/unit/test_planning_reminder_suppression.py tests/unit/test_runtime_startup_reconcile.py -q`
Expected: PASS. If an existing test asserted that an anchor in the window schedules *nothing*, it encoded the old rule: update it to expect the two session jobs and say so in the commit body.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/haunt/reconcile.py src/fateforger/core/runtime.py tests/unit/test_reconcile_session_jobs.py
git commit -m "feat(haunt): the planning event schedules its own session start, and a passed one is missing again

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Extract `open_session_surface`

**Files:**
- Create: `src/fateforger/slack_bot/session_surface.py`
- Modify: `src/fateforger/slack_bot/handlers.py` — the `_begin_timeboxing_session_surface` closure inside `route_slack_event` (grep `async def _begin_timeboxing_session_surface`; today ~2659-2735 for root/focus, the turn follows)
- Test: `tests/unit/test_session_surface.py` (new); `tests/unit/test_slack_timeboxing_routing.py` must pass unchanged

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class SessionSurface:
    channel_id: str
    root_ts: str
    session_key: str          # f"{channel_id}:{root_ts}"

async def open_session_surface(
    client, focus, *, user_id: str, target_channel: str,
    origin_key: str | None = None, existing_root: dict | None = None,
) -> SessionSurface
```

It posts (or repurposes) the root header with the timeboxing persona, sets the thread label, sets focus on the new key (and the redirect from `origin_key` when given), sets user focus, and invites the user. It does **not** post the "thinking" message or run a turn — the request path keeps doing that after calling it, and the auto-open uses `_deliver_timebox_turn`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_surface.py
from __future__ import annotations

import pytest

from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.session_surface import SessionSurface, open_session_surface


class _Client:
    def __init__(self):
        self.posted = []
        self.updates = []
        self.invited = []

    async def chat_postMessage(self, **payload):
        self.posted.append(payload)
        return {"channel": payload["channel"], "ts": "root.1"}

    async def chat_update(self, **payload):
        self.updates.append(payload)
        return {"ok": True}

    async def conversations_invite(self, **payload):
        self.invited.append(payload)
        return {"ok": True}


@pytest.mark.asyncio
async def test_opening_a_surface_posts_a_root_and_claims_it_for_timeboxing() -> None:
    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent", "receptionist_agent"])

    surface = await open_session_surface(client, focus, user_id="U1", target_channel="C1")

    assert surface == SessionSurface(channel_id="C1", root_ts="root.1", session_key="C1:root.1")
    assert client.posted and client.posted[0]["channel"] == "C1"
    assert focus.get_focus("C1:root.1").agent_type == "timeboxing_agent"
    assert focus.get_user_focus("U1") == "timeboxing_agent"
    assert focus.get_thread_label("C1:root.1") is not None


@pytest.mark.asyncio
async def test_an_origin_key_gets_a_redirect_to_the_new_root() -> None:
    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    surface = await open_session_surface(client, focus, user_id="U1", target_channel="C1", origin_key="D1:dm")

    redirect = focus.get_redirect("D1:dm")
    assert redirect is not None and redirect.target_key == surface.session_key
    assert focus.get_focus("D1:dm").agent_type == "timeboxing_agent"


@pytest.mark.asyncio
async def test_an_existing_root_is_repurposed_not_duplicated() -> None:
    client = _Client()
    focus = FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"])

    surface = await open_session_surface(
        client, focus, user_id="U1", target_channel="C1", existing_root={"channel": "C1", "ts": "ack.9"}
    )

    assert surface.root_ts == "ack.9"
    assert client.posted == []
    assert client.updates and client.updates[0]["ts"] == "ack.9"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_session_surface.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.slack_bot.session_surface'`

- [ ] **Step 3: Extract**

Create `session_surface.py` by moving the root/label/focus part of the closure. The helpers it needs (`_persona_for_agent`, `_persona_payload`, `_timeboxing_thread_root_text`, `_invite_user_to_channels_best_effort`) live in `handlers.py`; import them from there **only if** `handlers.py` does not import `session_surface` at module level (it will — so move those four helpers, or thin copies, into `session_surface.py` and have `handlers.py` import them back from there to avoid a cycle). Check each helper's dependencies first with a grep; `_persona_for_agent` depends on `WorkspaceRegistry`/`DEFAULT_PERSONAS`, both importable without `handlers`.

```python
# src/fateforger/slack_bot/session_surface.py
"""The one surface a timeboxing session gets: a root header with the working
card threaded under it. Every door -- a message, a slash command, the planning
event's own start -- opens a session through this function."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fateforger.slack_bot.focus import FocusManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionSurface:
    channel_id: str
    root_ts: str
    session_key: str


async def open_session_surface(
    client,
    focus: FocusManager,
    *,
    user_id: str,
    target_channel: str,
    origin_key: str | None = None,
    existing_root: dict | None = None,
) -> SessionSurface:
    persona = persona_for_agent("timeboxing_agent")
    try:
        await invite_user_to_channels_best_effort(client, user_id=user_id, channel_ids=[target_channel])
    except Exception:
        logger.debug("invite failed for %s", user_id, exc_info=True)

    root_text = timeboxing_thread_root_text(title="Timeboxing session", request_excerpt=None, state="pending")
    if existing_root is not None:
        root_ts = existing_root["ts"]
        await client.chat_update(channel=target_channel, ts=root_ts, text=root_text)
    else:
        root_payload = {"channel": target_channel, "text": root_text}
        root_payload.update(persona_payload(persona))
        root = await client.chat_postMessage(**root_payload)
        root_ts = root["ts"]

    session_key = f"{target_channel}:{root_ts}"
    focus.set_thread_label(session_key, title="Timeboxing session", request_excerpt=None, state="pending", by_user=user_id)
    if origin_key is not None:
        redirect = focus.set_redirect(origin_key, target_channel=target_channel, target_thread_ts=root_ts, agent_type="timeboxing_agent", by_user=user_id, note="session-surface")
        focus.set_focus(redirect.target_key, "timeboxing_agent", by_user=user_id, note="session-surface")
        focus.set_focus(origin_key, "timeboxing_agent", by_user=user_id, note="session-surface")
    else:
        focus.set_focus(session_key, "timeboxing_agent", by_user=user_id, note="session-surface")
    focus.set_user_focus(user_id, "timeboxing_agent")
    return SessionSurface(channel_id=target_channel, root_ts=root_ts, session_key=session_key)
```

Where `persona_for_agent`, `persona_payload`, `timeboxing_thread_root_text`, `invite_user_to_channels_best_effort` are the moved helpers (drop the leading underscore on the moved copies; keep `handlers.py`'s old names as `from .session_surface import persona_for_agent as _persona_for_agent`, etc., so the rest of `handlers.py` is untouched).

In `route_slack_event`, the closure becomes:

```python
    async def _begin_timeboxing_session_surface(*, target_channel: str, origin_key: str, existing_root: dict | None = None) -> None:
        surface = await open_session_surface(
            client, focus, user_id=user, target_channel=target_channel, origin_key=origin_key, existing_root=existing_root
        )
        root_ts = surface.root_ts
        redirect = focus.get_redirect(origin_key)
        persona = _persona_for_agent("timeboxing_agent")
        try:
            if not is_dm and channel != target_channel:
                await _origin_link_to_thread(channel_id=target_channel, thread_ts=root_ts, agent_label=(persona.username if persona else "timeboxing_agent"))
            processing_payload = {...}   # unchanged from here down
```

Everything from the "thinking" message onward stays as it is. Verify the `redirect.target_key` uses after the call still resolve (they read the redirect `open_session_surface` set).

- [ ] **Step 4: Run the surface test and the routing suite**

Run: `.venv/bin/python -m pytest tests/unit/test_session_surface.py tests/unit/test_slack_timeboxing_routing.py tests/unit/test_harness_timeboxing_session_route.py -q` (the last exists under `tests/integration/`; run it from there if the unit path is wrong)
Expected: PASS, routing suite unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/session_surface.py src/fateforger/slack_bot/handlers.py tests/unit/test_session_surface.py
git commit -m "refactor(slack): one function opens a session surface, callable without a Slack event

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Cancel-on-activity wiring in `_run_adaptive_timebox_turn`

**Files:**
- Modify: `src/fateforger/slack_bot/handlers.py` — `_run_adaptive_timebox_turn` (grep the def; the body around `timeboxing_activity.mark_active(...)` and `if current.status != "open":`)
- Test: `tests/unit/test_slack_timeboxing_routing.py` (append)

**Interfaces:**
- Consumes: `runtime.haunting_service` (set in `runtime.py`; `getattr(runtime, "haunting_service", None)` — absent in unit fakes unless the test sets it).
- Produces: every kernel turn calls `haunting_service.record_user_activity(topic_id=session_key, task_id=None, user_id=actor_user_id)` before the turn, and `haunting_service.cancel_followups(topic_id=session_key)` after a turn that leaves the session `committed` or `cancelled`. Both best-effort: an exception is logged and metered (`component="session_start", error_type="cancel_failure"`), never raised.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_slack_timeboxing_routing.py`:

```python
class _HauntingService:
    def __init__(self):
        self.activity: list[dict] = []
        self.cancelled: list[dict] = []

    async def record_user_activity(self, **kwargs):
        self.activity.append(kwargs)
        return 0

    async def cancel_followups(self, **kwargs):
        self.cancelled.append(kwargs)
        return 0


@pytest.mark.asyncio
async def test_a_turn_records_activity_on_the_session_topic(monkeypatch):
    from fateforger.slack_bot.handlers import _run_adaptive_timebox_turn

    haunting = _HauntingService()
    runtime = SimpleNamespace(haunting_service=haunting, timeboxing_session_store=_SessionStore({}))
    kernel_calls = []

    class _Kernel:
        async def turn(self, request, *, progress):
            kernel_calls.append(request)
            return SimpleNamespace()

    class _Repo:
        async def load_or_create(self, key, *, owner_user_id):
            return SimpleNamespace(revision=0, status="open", planning_day=None, artifacts=[])

    runtime.timeboxing_session_store = _Repo()
    monkeypatch.setattr("fateforger.slack_bot.handlers._timeboxing_kernel", lambda *a, **k: _Kernel())
    monkeypatch.setattr("fateforger.slack_bot.handlers.derive_timebox_intent", _fake_derive)
    monkeypatch.setattr("fateforger.slack_bot.handlers.present_outcome", lambda *a, **k: (SlackBlockMessage(text="ok", blocks=[]), None))

    await _run_adaptive_timebox_turn(
        runtime=runtime, client=_FakeClient(), logger=logging.getLogger("t"), session_key="C1:1.0",
        actor_user_id="U1", interaction_id="i1", progress_channel="C1", progress_ts="p1",
        card_channel="C1", card_thread_ts="1.0", user_text="hello", focus=None,
    )

    assert haunting.activity == [{"topic_id": "C1:1.0", "task_id": None, "user_id": "U1"}]
    assert haunting.cancelled == []


@pytest.mark.asyncio
async def test_a_turn_that_ends_the_session_cancels_the_ladder(monkeypatch):
    ...  # same shape, with _Repo returning status="committed" on the second load; assert haunting.cancelled == [{"topic_id": "C1:1.0"}]
```

Before writing these, read `_run_adaptive_timebox_turn` end to end and the existing test `tests/unit/test_back_press_reaches_the_kernel.py`, which already drives this function with fakes — copy its fixture shape (`_fake_derive`, the kernel/repository/progress fakes, `present_outcome` patching) rather than inventing new ones. Replace the `...` in the second test with that shape; the two assertions above are what matter.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py -q -k "activity or cancels_the_ladder"`
Expected: FAIL — `haunting.activity == []`

- [ ] **Step 3: Implement**

In `_run_adaptive_timebox_turn`, immediately after `timeboxing_activity.mark_active(...)`:

```python
    # Any turn is activity on this session: it cancels a pending Admonisher
    # ladder (the planning session that started itself, #164 increment A).
    # Best-effort: a haunt failure must never block the turn.
    haunting_service = getattr(runtime, "haunting_service", None)
    if haunting_service is not None:
        try:
            await haunting_service.record_user_activity(
                topic_id=session_key, task_id=None, user_id=actor_user_id
            )
        except Exception:
            logger.exception("activity record failed session_key=%s", session_key)
            record_error(component="session_start", error_type="cancel_failure")
```

and inside `if current.status != "open":`, after `mark_inactive`:

```python
            if haunting_service is not None:
                try:
                    await haunting_service.cancel_followups(topic_id=session_key)
                except Exception:
                    logger.exception("ladder cancel failed session_key=%s", session_key)
                    record_error(component="session_start", error_type="cancel_failure")
```

- [ ] **Step 4: Run the routing suite**

Run: `.venv/bin/python -m pytest tests/unit/test_slack_timeboxing_routing.py tests/unit/test_back_press_reaches_the_kernel.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/handlers.py tests/unit/test_slack_timeboxing_routing.py
git commit -m "feat(slack): any turn on a session cancels the Admonisher's ladder for it

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: `SessionStarter` — start and expire

**Files:**
- Create: `src/fateforger/slack_bot/session_start.py`
- Modify: `src/fateforger/slack_bot/planning.py` — `dispatch_planning_reminder` (grep; today ~402-410) routes by kind; `__init__` builds the starter
- Test: `tests/unit/test_session_starter.py` (new)

**Interfaces:**
- Consumes: Task 1 (`planning_day_for`, `LADDER_OFFSETS`, `nudge_line`, `dm_open_line`, `missed_line`, kinds); Task 2 (`FollowUpSpec(offsets=, lines=)`); Task 3 (`PlanningReminder.event_start/event_end/event_tz`); Task 4 (`open_session_surface`); `_deliver_timebox_turn(runtime, client, logger, session_key, actor_user_id, interaction_id, channel_id, thread_ts, action, candidate_id=None, focus=None)` from `handlers.py`; `TimeboxActionEnvelope`, `ConfirmPlanningDay`, `CancelSession`, `PlanningDay.lock_default(value, timezone, lock_revision, day_type=None)`; `UserFacingMessage`; `USER_CHANNEL_AGENT_TYPE` from `fateforger.core.runtime`; `_plan_sessions_channel_id()` / `_channel_for_agent("timeboxing_agent")` from `handlers.py`.
- Produces:

```python
class SessionStarter:
    def __init__(self, *, runtime, client, focus, guardian, ledger, haunting_service, target_channel: str, now=None): ...
    async def start(self, reminder: PlanningReminder) -> None
    async def expire(self, reminder: PlanningReminder) -> None
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_session_starter.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import TimeboxingStanding
from fateforger.agents.timeboxing.session_contracts import CancelSession, ConfirmPlanningDay
from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import LADDER_OFFSETS, SESSION_EXPIRE_KIND, SESSION_START_KIND
from fateforger.slack_bot.focus import FocusManager
from fateforger.slack_bot.session_start import SessionStarter

AMS = "Europe/Amsterdam"
START = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo(AMS))


def _reminder(kind: str = SESSION_START_KIND) -> PlanningReminder:
    return PlanningReminder(
        scope="U1", kind=kind, attempt=1, message="", user_id="U1", channel_id="D1",
        event_start=START.isoformat(), event_end=(START + timedelta(minutes=30)).isoformat(), event_tz=AMS,
    )


class _Ledger:
    def __init__(self, open_key=None, committed_key=None):
        self.standing = TimeboxingStanding(open_session_key=open_key, committed_session_key=committed_key)

    async def standing_for(self, **_):
        return self.standing

    async def load(self, key):
        return SimpleNamespace(revision=3, status="open")


class _Haunting:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    async def schedule_followup(self, **kwargs):
        self.scheduled.append(kwargs)

    async def cancel_followups(self, **kwargs):
        self.cancelled.append(kwargs)


class _Runtime:
    def __init__(self):
        self.sent = []

    async def send_message(self, message, recipient):
        self.sent.append((message, recipient))


class _Guardian:
    def __init__(self):
        self.reconciled = []

    async def reconcile_user(self, *, user_id):
        self.reconciled.append(user_id)


class _Client:
    def __init__(self):
        self.posted, self.updates = [], []

    async def chat_postMessage(self, **p):
        self.posted.append(p)
        return {"channel": p["channel"], "ts": "root.1"}

    async def chat_update(self, **p):
        self.updates.append(p)
        return {"ok": True}

    async def chat_getPermalink(self, **_):
        return {"permalink": "https://slack/p"}

    async def conversations_invite(self, **_):
        return {"ok": True}


def _starter(monkeypatch, *, ledger, haunting=None, runtime=None, guardian=None, turns=None):
    turns = [] if turns is None else turns

    async def _deliver(**kwargs):
        turns.append(kwargs)

    monkeypatch.setattr("fateforger.slack_bot.session_start._deliver_timebox_turn", _deliver)
    return SessionStarter(
        runtime=runtime or _Runtime(), client=_Client(),
        focus=FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"]),
        guardian=guardian or _Guardian(), ledger=ledger, haunting_service=haunting or _Haunting(),
        target_channel="C1", now=lambda: START.astimezone(timezone.utc),
    ), turns


@pytest.mark.asyncio
async def test_start_opens_confirms_the_day_dms_and_arms_the_ladder(monkeypatch):
    haunting, runtime = _Haunting(), _Runtime()
    starter, turns = _starter(monkeypatch, ledger=_Ledger(), haunting=haunting, runtime=runtime)

    await starter.start(_reminder())

    assert len(turns) == 1
    envelope = turns[0]["action"]
    assert envelope.session_key == "C1:root.1" and envelope.expected_revision == 0
    assert isinstance(envelope.intent, ConfirmPlanningDay)
    assert envelope.intent.planning_day.date.isoformat() == "2026-09-04"
    assert envelope.intent.planning_day.timezone == AMS
    dm, recipient = runtime.sent[0]
    assert "https://slack/p" in dm.content and dm.user_id == "U1"
    armed = haunting.scheduled[0]
    assert armed["topic_id"] == "C1:root.1"
    assert armed["message_id"] == "planning_session:C1:root.1"
    assert armed["spec"].offsets == LADDER_OFFSETS and armed["spec"].cancel_on_user_reply is True
    assert len(armed["spec"].lines) == len(LADDER_OFFSETS) and "https://slack/p" in armed["spec"].lines[0]


@pytest.mark.asyncio
async def test_an_evening_event_plans_the_next_day(monkeypatch):
    starter, turns = _starter(monkeypatch, ledger=_Ledger())
    evening = START.replace(hour=18)
    reminder = PlanningReminder(scope="U1", kind=SESSION_START_KIND, attempt=1, message="", user_id="U1", channel_id="D1",
                                event_start=evening.isoformat(), event_end=(evening + timedelta(minutes=30)).isoformat(), event_tz=AMS)

    await starter.start(reminder)

    assert turns[0]["action"].intent.planning_day.date.isoformat() == "2026-09-05"


@pytest.mark.asyncio
@pytest.mark.parametrize("standing", [_Ledger(open_key="C1:old"), _Ledger(committed_key="C1:done")])
async def test_an_open_or_committed_session_blocks_the_start(monkeypatch, standing):
    haunting = _Haunting()
    starter, turns = _starter(monkeypatch, ledger=standing, haunting=haunting)

    await starter.start(_reminder())

    assert turns == [] and haunting.scheduled == []


@pytest.mark.asyncio
async def test_a_failed_turn_relabels_the_root_and_arms_nothing(monkeypatch):
    haunting = _Haunting()

    async def _boom(**_):
        raise RuntimeError("kernel down")

    monkeypatch.setattr("fateforger.slack_bot.session_start._deliver_timebox_turn", _boom)
    starter = SessionStarter(runtime=_Runtime(), client=_Client(), focus=FocusManager(ttl_seconds=60, allowed_agents=["timeboxing_agent"]),
                             guardian=_Guardian(), ledger=_Ledger(), haunting_service=haunting, target_channel="C1",
                             now=lambda: START.astimezone(timezone.utc))

    await starter.start(_reminder())

    assert haunting.scheduled == []
    assert any("canceled" in (u.get("text") or "") for u in starter._client.updates)


@pytest.mark.asyncio
async def test_expire_without_a_commit_cancels_marks_missed_and_hands_over(monkeypatch):
    haunting, guardian, runtime = _Haunting(), _Guardian(), _Runtime()
    ledger = _Ledger(open_key="C1:root.1")
    starter, turns = _starter(monkeypatch, ledger=ledger, haunting=haunting, runtime=runtime, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert haunting.cancelled == [{"topic_id": "C1:root.1"}]
    assert isinstance(turns[0]["action"].intent, CancelSession) and turns[0]["action"].expected_revision == 3
    assert any("Missed" in m.content for m, _ in runtime.sent)
    assert guardian.reconciled == ["U1"]


@pytest.mark.asyncio
async def test_expire_after_a_commit_does_nothing(monkeypatch):
    haunting, guardian = _Haunting(), _Guardian()
    starter, turns = _starter(monkeypatch, ledger=_Ledger(committed_key="C1:root.1"), haunting=haunting, guardian=guardian)

    await starter.expire(_reminder(SESSION_EXPIRE_KIND))

    assert turns == [] and haunting.cancelled == [] and guardian.reconciled == []
```

The failed-turn test reads `starter._client`; expose the client as `self._client` on the starter (it needs it anyway).

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_session_starter.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'fateforger.slack_bot.session_start'`

- [ ] **Step 3: Write `session_start.py`**

```python
# src/fateforger/slack_bot/session_start.py
"""Open the planning session when its calendar event starts; close it when the
event is over and nothing was planned.

`start` and `expire` are what the reconciler's `session_start` and
`session_expire` jobs run. Policy comes from `haunt.session_start`; this module
only touches Slack and the kernel, through the same functions a card press uses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from autogen_core import AgentId
from dateutil import parser as date_parser

from fateforger.agents.timeboxing.session_contracts import CancelSession, ConfirmPlanningDay, PlanningDay
from fateforger.core.runtime import USER_CHANNEL_AGENT_TYPE
from fateforger.haunt.messages import FollowUpSpec, UserFacingMessage
from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.session_start import (
    LADDER_OFFSETS,
    dm_open_line,
    missed_line,
    nudge_line,
    planning_day_for,
)
from fateforger.observability import record_error
from fateforger.slack_bot.session_surface import open_session_surface, timeboxing_thread_root_text
from fateforger.slack_bot.timeboxing_intents import TimeboxActionEnvelope

logger = logging.getLogger(__name__)

#: Mirrors PlanningCoordinator.OPEN_SESSION_UNDER_WAY.
OPEN_SESSION_UNDER_WAY_HOURS = 1


def _deliver_timebox_turn(**kwargs):  # pragma: no cover - resolved at call time so tests can patch it
    from fateforger.slack_bot.handlers import _deliver_timebox_turn as deliver

    return deliver(**kwargs)


class SessionStarter:
    def __init__(
        self,
        *,
        runtime,
        client,
        focus,
        guardian,
        ledger,
        haunting_service,
        target_channel: str,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._client = client
        self._focus = focus
        self._guardian = guardian
        self._ledger = ledger
        self._haunting = haunting_service
        self._target_channel = target_channel
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- start ---------------------------------------------------------------

    async def start(self, reminder: PlanningReminder) -> None:
        user_id = reminder.user_id or ""
        if not user_id or not reminder.event_start:
            logger.warning("session_start reminder without user or event start: %r", reminder)
            return
        tz = ZoneInfo(reminder.event_tz or "UTC")
        event_start = date_parser.isoparse(reminder.event_start).astimezone(tz)
        day = planning_day_for(event_start)
        logger.info("session_start user=%s event_start=%s -> planning day %s", user_id, event_start.isoformat(), day)

        if await self._blocked(user_id=user_id, day=day):
            return

        try:
            surface = await open_session_surface(
                self._client, self._focus, user_id=user_id, target_channel=self._target_channel
            )
        except Exception:
            logger.exception("session_start: surface failed for %s", user_id)
            record_error(component="session_start", error_type="open_failure")
            return

        envelope = TimeboxActionEnvelope(
            session_key=surface.session_key,
            expected_revision=0,
            intent=ConfirmPlanningDay(
                planning_day=PlanningDay.lock_default(value=day, timezone=reminder.event_tz or "UTC", lock_revision=1)
            ),
        )
        try:
            await _deliver_timebox_turn(
                runtime=self._runtime,
                client=self._client,
                logger=logger,
                session_key=surface.session_key,
                actor_user_id=user_id,
                interaction_id=f"session_start:{surface.session_key}",
                channel_id=surface.channel_id,
                thread_ts=surface.root_ts,
                action=envelope,
                focus=self._focus,
            )
        except Exception:
            logger.exception("session_start: opening turn failed for %s", surface.session_key)
            record_error(component="session_start", error_type="open_failure")
            await self._relabel_root(surface.channel_id, surface.root_ts, state="canceled")
            return

        permalink = await self._permalink(surface.channel_id, surface.root_ts)
        day_label = day.strftime("%a %-d %b")
        await self._dm(user_id=user_id, content=dm_open_line(day_label=day_label, permalink=permalink))

        start_hhmm = event_start.strftime("%H:%M")
        lines = tuple(nudge_line(i, permalink=permalink, start=start_hhmm) for i in range(len(LADDER_OFFSETS)))
        try:
            await self._haunting.schedule_followup(
                message_id=f"planning_session:{surface.session_key}",
                topic_id=surface.session_key,
                task_id=None,
                user_id=user_id,
                channel_id=None,
                content=lines[0],
                spec=FollowUpSpec(
                    should_schedule=True, offsets=LADDER_OFFSETS, lines=lines,
                    escalation="gentle", cancel_on_user_reply=True,
                ),
            )
        except Exception:
            logger.exception("session_start: arming the ladder failed for %s", surface.session_key)
            record_error(component="session_start", error_type="arm_failure")

    # -- expire --------------------------------------------------------------

    async def expire(self, reminder: PlanningReminder) -> None:
        user_id = reminder.user_id or ""
        if not user_id or not reminder.event_start:
            return
        tz = ZoneInfo(reminder.event_tz or "UTC")
        day = planning_day_for(date_parser.isoparse(reminder.event_start).astimezone(tz))
        standing = await self._standing(user_id=user_id, day=day)
        if standing is None:
            return
        if standing.committed_session_key is not None:
            return

        session_key = standing.open_session_key
        if session_key is not None:
            try:
                await self._haunting.cancel_followups(topic_id=session_key)
            except Exception:
                logger.exception("session_expire: cancel failed for %s", session_key)
                record_error(component="session_start", error_type="cancel_failure")
            channel_id, root_ts = session_key.split(":", 1)
            snapshot = await self._ledger.load(session_key)
            if snapshot is not None and snapshot.status == "open":
                try:
                    await _deliver_timebox_turn(
                        runtime=self._runtime, client=self._client, logger=logger,
                        session_key=session_key, actor_user_id=user_id,
                        interaction_id=f"session_expire:{session_key}",
                        channel_id=channel_id, thread_ts=root_ts,
                        action=TimeboxActionEnvelope(session_key=session_key, expected_revision=snapshot.revision, intent=CancelSession()),
                        focus=self._focus,
                    )
                except Exception:
                    logger.exception("session_expire: cancel turn failed for %s", session_key)
                    record_error(component="session_start", error_type="expire_failure")
            await self._relabel_root(channel_id, root_ts, state="missed")
            try:
                await self._client.chat_postMessage(channel=channel_id, thread_ts=root_ts, text=missed_line())
            except Exception:
                logger.debug("session_expire: thread line failed", exc_info=True)

        await self._dm(user_id=user_id, content=missed_line())
        try:
            await self._guardian.reconcile_user(user_id=user_id)
        except Exception:
            logger.exception("session_expire: reconcile_user failed for %s", user_id)
            record_error(component="session_start", error_type="expire_failure")

    # -- helpers -------------------------------------------------------------

    async def _standing(self, *, user_id: str, day):
        try:
            return await self._ledger.standing_for(
                owner_user_id=user_id,
                open_since=self._now() - __import__("datetime").timedelta(hours=OPEN_SESSION_UNDER_WAY_HOURS),
                planned_from=day,
                planned_to=day,
            )
        except Exception:
            logger.exception("session guard: standing lookup failed for %s", user_id)
            record_error(component="session_start", error_type="guard_failure")
            return None

    async def _blocked(self, *, user_id: str, day) -> bool:
        standing = await self._standing(user_id=user_id, day=day)
        if standing is None:
            return True
        if standing.open_session_key is not None:
            logger.info("session_start: %s already has open session %s; not starting", user_id, standing.open_session_key)
            return True
        if standing.committed_session_key is not None:
            logger.info("session_start: %s already committed %s for %s; not starting", user_id, standing.committed_session_key, day)
            return True
        return False

    async def _permalink(self, channel_id: str, ts: str) -> str:
        try:
            res = await self._client.chat_getPermalink(channel=channel_id, message_ts=ts)
            return str(res.get("permalink") or "")
        except Exception:
            logger.debug("permalink failed", exc_info=True)
            return ""

    async def _dm(self, *, user_id: str, content: str) -> None:
        try:
            await self._runtime.send_message(
                UserFacingMessage(content=content, user_id=user_id, channel_id=None),
                recipient=AgentId(USER_CHANNEL_AGENT_TYPE, key=user_id),
            )
        except Exception:
            logger.exception("session_start: DM failed for %s", user_id)
            record_error(component="session_start", error_type="dm_failure")

    async def _relabel_root(self, channel_id: str, root_ts: str, *, state: str) -> None:
        try:
            await self._client.chat_update(
                channel=channel_id, ts=root_ts,
                text=timeboxing_thread_root_text(title="Timeboxing session", request_excerpt=None, state=state),
            )
        except Exception:
            logger.debug("root relabel failed", exc_info=True)


__all__ = ["SessionStarter"]
```

Replace the `__import__("datetime").timedelta` with a normal `from datetime import timedelta` import (written inline above only to keep the block self-contained). `record_error` — import from wherever `handlers.py` imports it (grep `record_error` there). `timeboxing_thread_root_text` — check it accepts `state="missed"`; if its states are a closed `Literal`, add `"missed"` (a value this system mints).

`planning.py` — in `__init__`, after the ledger is read:

```python
        from fateforger.slack_bot.session_start import SessionStarter  # local: session_start imports handlers lazily
        target_channel = _channel_for_agent("timeboxing_agent") or _plan_sessions_channel_id() or ""
        self._session_starter = SessionStarter(
            runtime=runtime, client=client, focus=focus, guardian=self._guardian,
            ledger=self._timeboxing_ledger, haunting_service=getattr(runtime, "haunting_service", None),
            target_channel=target_channel,
        )
```

(`_channel_for_agent` and `_plan_sessions_channel_id` live in `handlers.py`, which imports `planning.py` — resolve them the way `planning.py` already resolves the DM channel: check how `dispatch_planning_reminder` finds channels and reuse; if nothing suitable exists, read `settings.slack_timeboxing_channel_id` and the `WorkspaceRegistry` "plan-sessions" lookup directly, which is what those two helpers do.)

At the top of `dispatch_planning_reminder`:

```python
        if reminder.kind == SESSION_START_KIND:
            await self._session_starter.start(reminder)
            return
        if reminder.kind == SESSION_EXPIRE_KIND:
            await self._session_starter.expire(reminder)
            return
```

- [ ] **Step 4: Run the starter tests, then the full unit suite**

Run: `.venv/bin/python -m pytest tests/unit/test_session_starter.py -q` then `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS (the known flake aside).

- [ ] **Step 5: Commit**

```bash
git add src/fateforger/slack_bot/session_start.py src/fateforger/slack_bot/planning.py tests/unit/test_session_starter.py
git commit -m "feat(slack): the planning session starts itself and the Admonisher walks you to it

At the event's start: guard on the session store, open the surface, one
host-built ConfirmPlanningDay turn, a DM link, and a five-rung ladder on
the session key. At expire: cancel, mark missed, hand the user back to the
missing-planning rule.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Delivery

- [ ] **Step 1: Rebase, full suite, push, PR**

```bash
git fetch origin && git rebase origin/main
.venv/bin/python -m pytest tests/unit -q
git push -u origin feat/planning-session-autostart
gh pr create --base main --title "feat(haunt): the planning session starts itself, and the Admonisher walks you to it" --body-file <body: problem (nothing fires at the event's start; a passed event counted as planned), what changed, unit counts, an empty "## E2E in Slack" section, "## Before merging" checklist; ends with 🤖 Generated with [Claude Code](https://claude.com/claude-code)>
```

- [ ] **Step 2: Restart from the parent checkout at the branch tip** (coordinate with peer sessions first)

```bash
cd /Users/hugoevers/VScode-projects/admonish-1 && git fetch origin && git checkout --detach <branch tip sha>
git status --short | wc -l            # 0
.venv/bin/python -c "import fateforger; print(fateforger.__file__)"   # parent src
grep -c "|| ''" ~/.dsh/profiles/tmbx/cordis.patch.yml               # 3
.venv/bin/python scripts/demo.py start && .venv/bin/python scripts/demo.py status   # sha = branch tip, exit 0
```

- [ ] **Step 3: E2E in Slack** (tell Hugo before each calendar write; DM `D09A0RE9P7G`, `#plan-sessions` `C0AA6HC1RJL`)

1. Post a planning card for **now + 3 min** (the `post_test_card.py` approach from the previous PR, in the scratchpad) and press *Add to calendar* as Hugo (or reply "Okay!"). The add calls `reconcile_user` → `demo.py`'s bot log shows `session_start` and `session_expire` jobs scheduled.
2. At the event's start: root + capture card appear in `#plan-sessions`; DM link arrives. At +2 min: first nudge in the DM. Reply anything in the thread → no nudge at +5 or +10.
3. Second card at now + 3 min, no reply: nudges at +2/+5/+10; then (set `expire_after` to 2 minutes in `PlanningRuleConfig` for the test build only, or wait 90 min) the *"Missed"* line in thread and DM and a fresh planning card in the DM.
4. Paste both threads, the DM, the journal lines and `demo.py status` into the PR's E2E section.

- [ ] **Step 4: Merge, return the parent to `main`, restart**

```bash
gh pr merge <n> --merge
cd /Users/hugoevers/VScode-projects/admonish-1 && git checkout main && git pull --ff-only origin main
.venv/bin/python scripts/demo.py start && .venv/bin/python scripts/demo.py status
```

---

## Self-review

**Spec coverage.** Section 1 (two jobs, changed rule, guard, day rule) → Tasks 1, 3, 6. Section 2 (extracted builder, one `ConfirmPlanningDay` turn, DM, failure) → Tasks 4, 6. Section 3 (offsets, arming, lines, cancel wiring, expire hand-over) → Tasks 2, 5, 6. Section 4 (tests) → each task's tests; e2e → Task 7. Section 5 → Task 7.

**Placeholders.** Task 5's second test says "same shape" — it names the fixture file to copy (`test_back_press_reaches_the_kernel.py`) and the two assertions; acceptable. Task 4's closure rewrite shows the changed head and says the rest is unchanged, which it is. No TBDs.

**Type consistency.** `PlanningReminder(event_start, event_end, event_tz)` (Task 3) is what Task 6 reads. `FollowUpSpec(offsets, lines)` (Task 2) is what Task 6 arms. `open_session_surface(client, focus, *, user_id, target_channel, origin_key=None, existing_root=None) -> SessionSurface(channel_id, root_ts, session_key)` (Task 4) is what Task 6 calls. `standing_for(owner_user_id, open_since, planned_from, planned_to)` is used identically in Tasks 3 and 6. `SESSION_START_KIND`/`SESSION_EXPIRE_KIND` (Task 1) are the kinds Task 3 emits and `planning.py` routes in Task 6.
