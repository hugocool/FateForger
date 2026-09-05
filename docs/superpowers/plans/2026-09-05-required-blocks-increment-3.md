# Required Blocks — Increment 3 (the watcher: `RequiredBlockRule` in the haunt) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On every reconcile tick the haunt checks that a block of each kind the day's rules require is on the calendar and inside its bounds, and starts the nudge ladder when one is gone or has moved out; a failed read gives no verdict.

**Architecture:** A second rule beside `PlanningSessionRule`, `RequiredBlockRule`, evaluated by the same `PlanningReconciler` on the same tick with its own job-key prefix. It reads the day's required slugs from the constraint store (memory's read path, arithmetic), keeps an in-process cache of `(user, day, slug) → event_id` as the fast path, re-derives presence from the `tmbx.slug` private property on a miss, and refuses to haunt when the calendar cannot be read. Its reminders reuse the existing dispatcher: the `planning` slug rides the existing planning card, any other slug gets a plain DM line. Spec: `docs/superpowers/specs/2026-09-04-required-blocks-design.md` §4, §5, decisions 1–4, 7, 9.

**Tech Stack:** Python 3.12, APScheduler (in-memory `AsyncIOScheduler`), the calendar MCP client (`McpCalendarClient`), SQLAlchemy session store, pytest (`uv run pytest`).

## Global Constraints

- Worktree `.worktrees/required-blocks-haunt`, branch `feat/required-blocks-haunt`, off `origin/main` 2f79157 (increment 2 merged). Tests: `uv run pytest <files> -q -p no:cacheprovider` from the worktree root. Three failures are known pre-existing on `main`: `tests/e2e/test_slack_handoff_flow.py::test_slack_handoff_sets_focus_and_forwards` and two date-dependent cases in `tests/unit/test_planning_reminder_suppression.py`.
- **No keyword, substring or regex matching on user content, anywhere, tests included** (CLAUDE.md). Presence is equality over identifiers the system minted: the registry slug against `extendedProperties.private["tmbx.slug"]`, plus the existing `_carries_planning_mark` for the `planning` kind (an `ffplanning…` id or the `ff_planning` property). Never a title.
- **A failed read gives no verdict (#226):** if the calendar cannot be listed or the registered event cannot be fetched, the rule returns the jobs it already has (none), logs the error with its type and message, and leaves the cache untouched. Absence of a read is not absence of a block.
- **The register is a cache** (decision 4): in-process, per rule instance, rebuilt from the calendar on any miss. It may be wrong for one tick and no longer. (Deviation from spec §4's "re-keyed `planning_session_refs`": a persistent register needs an alembic migration and buys nothing a cache does not, since the miss path lists once; recorded in the spec by Task 7.)
- **Bounds** (decision 2): the block starts on the day (in the planning timezone) and ends no later than the sleep boundary — the `DAY_FRAME` fact's `sleep` of the user's session for that day when one exists, else end of day. A sleep time before 04:00 is after midnight and lands on the next day.
- **No haunting while a session is open** is the dispatcher's existing `_timeboxing_silences` check; the rule does not repeat it.
- The reconciler keeps one prefix per rule (`rule:<rule_id>:<scope>:`) and removes only its own stale jobs; the two rules never delete each other's.
- Every test asserts something that fails without the change. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## File structure

| file | responsibility |
|---|---|
| `src/fateforger/haunt/required_block_rule.py` | **new** — `REQUIRED_BLOCK_KIND`, `REASON_MISSING`, `REASON_MOVED_OUT`, `RequiredBlockConfig`, `RequiredBlockRule` (cache, predicates, evaluate) |
| `src/fateforger/haunt/reconcile.py` | `PlanningReminder.slug`/`.reason`; `CalendarClient.list_day` + `McpCalendarClient.list_day` (None on failure); `nudge_offsets()` module function shared by both rules; `PlanningReconciler(required_block_rule=…)` reconciling two prefixes |
| `src/fateforger/slack_bot/timeboxing_session_store.py`, `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` | `day_frame_for(owner_user_id, planning_date)` on the SQL repository and the in-memory one |
| `src/fateforger/slack_bot/planning.py` | `dispatch_planning_reminder` handles `REQUIRED_BLOCK_KIND` |
| `src/fateforger/core/runtime.py` | wires `RequiredBlockRule` into the reconciler |
| `docs/superpowers/specs/2026-09-04-required-blocks-design.md` | §4 records the cache decision and the day-frame source |
| tests | `tests/unit/test_required_block_rule.py` (new), `tests/unit/test_reconcile_two_rules.py` (new), `tests/unit/test_required_block_dispatch.py` (new), `tests/unit/test_reconcile_event_utils.py`, `tests/unit/test_timeboxing_session_store*.py` |

---

### Task 1: Reminder fields, the failed-read-aware `list_day`, and the shared nudge offsets

**Files:**
- Modify: `src/fateforger/haunt/reconcile.py` (`PlanningReminder`, `CalendarClient`, `McpCalendarClient`, `nudge_offsets`, `PlanningSessionRule._resolve_nudge_offsets`)
- Test: `tests/unit/test_reconcile_event_utils.py`, `tests/unit/test_reconcile.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass class PlanningReminder: ... ; slug: str | None = None; reason: str | None = None
  class CalendarClient(Protocol):
      async def list_day(self, *, calendar_id: str, day: date, tz: str) -> list[dict] | None: ...
  def nudge_offsets(config: PlanningRuleConfig, *, first_nudge_offset: timedelta | None) -> list[timedelta]
  ```
  `McpCalendarClient.list_day` lists `[00:00, 24:00)` of `day` in `tz` and returns `None` when the tool answers an error payload or raises; it never returns `[]` for a failure. `PlanningSessionRule._resolve_nudge_offsets` delegates to `nudge_offsets` (behaviour unchanged).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_reconcile_event_utils.py`:

```python
def test_nudge_offsets_is_the_ladder_both_rules_share():
    from datetime import timedelta
    from fateforger.haunt.reconcile import PlanningRuleConfig, PlanningSessionRule, nudge_offsets

    config = PlanningRuleConfig()
    shared = nudge_offsets(config, first_nudge_offset=None)
    rule = PlanningSessionRule(calendar_client=object(), config=config)
    assert shared == rule._resolve_nudge_offsets(first_nudge_offset=None)
    assert shared[0] == timedelta(minutes=10) and len(shared) == config.nudge_max_attempts
    assert nudge_offsets(config, first_nudge_offset=timedelta(0))[0] == timedelta(0)


def test_a_reminder_can_name_a_required_kind_and_a_reason():
    from fateforger.haunt.reconcile import PlanningReminder

    reminder = PlanningReminder(scope="U1", kind="required_block", attempt=1, message="m",
                                slug="planning", reason="moved_out")
    assert (reminder.slug, reminder.reason) == ("planning", "moved_out")
    assert PlanningReminder(scope="U1", kind="nudge1", attempt=1, message="m").slug is None
```

Append to `tests/unit/test_reconcile.py`:

```python
@pytest.mark.asyncio
async def test_list_day_returns_none_on_a_tool_error_not_an_empty_day(monkeypatch):
    """#226 for the watcher: an unreadable calendar must not read as an empty one."""
    from datetime import date
    from fateforger.haunt import reconcile as r

    class _Workbench:
        def __init__(self, payload): self._payload = payload
        async def call_tool(self, name, arguments): return self._payload

    client = r.McpCalendarClient.__new__(r.McpCalendarClient)
    client._workbench = _Workbench("MCP error -32603: calendar unreachable")
    assert await client.list_day(calendar_id="primary", day=date(2026, 9, 7), tz="Europe/Amsterdam") is None

    class _Raises:
        async def call_tool(self, name, arguments): raise RuntimeError("boom")
    client._workbench = _Raises()
    assert await client.list_day(calendar_id="primary", day=date(2026, 9, 7), tz="Europe/Amsterdam") is None

    client._workbench = _Workbench({"items": [{"id": "e1", "summary": "x"}]})
    assert await client.list_day(calendar_id="primary", day=date(2026, 9, 7), tz="Europe/Amsterdam") == [{"id": "e1", "summary": "x"}]
```

(Check how `_extract_tool_payload` unwraps a plain dict/str; if it expects a `result` attribute, wrap the payload in `type("R", (), {"result": payload})()` in `_Workbench`.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_reconcile_event_utils.py tests/unit/test_reconcile.py -q -p no:cacheprovider -k "nudge_offsets or required_kind or list_day"`
Expected: FAIL — `ImportError: nudge_offsets` / unexpected keyword `slug` / `AttributeError: list_day`.

- [ ] **Step 3: Implement**

In `reconcile.py`:

`PlanningReminder` gains, after `event_tz`:

```python
    #: For a required-block reminder: which registered kind, and why the haunt
    #: started -- "missing" or "moved_out". None on the planning ladder.
    slug: str | None = None
    reason: str | None = None
```

`CalendarClient` protocol gains:

```python
    async def list_day(
        self, *, calendar_id: str, day: date, tz: str
    ) -> list[dict] | None: ...
```

`McpCalendarClient` gains:

```python
    async def list_day(
        self, *, calendar_id: str, day: date, tz: str
    ) -> list[dict] | None:
        """Every event on `day` in `tz`, or None when the read failed.

        `list_events` returns [] for a tool error and always has; the planning
        ladder inherited that and nudges on an unreadable calendar. The
        required-block watcher must not (#226), so this is the one call whose
        failure is distinguishable from an empty day.
        """
        zone = ZoneInfo(tz)
        start = datetime.combine(day, time.min, tzinfo=zone)
        end = start + timedelta(days=1)
        args = {
            "calendarId": calendar_id,
            "timeMin": _format_mcp_datetime(start),
            "timeMax": _format_mcp_datetime(end),
            "singleEvents": True,
            "orderBy": "startTime",
        }
        try:
            result = await self._workbench.call_tool("list-events", arguments=args)
        except Exception as exc:  # noqa: BLE001 - a failed read is a named outcome
            logger.warning("calendar list_day failed error_type=%s error=%s", type(exc).__name__, exc)
            return None
        payload = _extract_tool_payload(result)
        if isinstance(payload, str) and payload.strip().lower().startswith("mcp error"):
            logger.warning("calendar list_day returned a tool error: %s", payload.strip())
            return None
        return _normalize_events(payload)
```

(`from datetime import time` and `from zoneinfo import ZoneInfo` — check which are already imported.) The `"mcp error"` prefix test is the same one `list_events` already makes on a payload the MCP layer minted; it is not user content.

Extract the ladder:

```python
def nudge_offsets(
    config: PlanningRuleConfig, *, first_nudge_offset: timedelta | None
) -> list[timedelta]:
    """The nudge ladder: explicit offsets, or exponential backoff from `base`
    capped at `cap`, `max_attempts` rungs, all inside `horizon`. Shared by the
    planning ladder and the required-block watcher so the two cannot drift."""
    <the body of PlanningSessionRule._resolve_nudge_offsets, with self._config → config>
```

and make `_resolve_nudge_offsets` a one-liner `return nudge_offsets(self._config, first_nudge_offset=first_nudge_offset)`. Add `nudge_offsets` to `__all__`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_reconcile_event_utils.py tests/unit/test_reconcile.py tests/unit/test_reconcile_session_jobs.py tests/unit/test_haunting_offsets.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/haunt/reconcile.py tests/unit/test_reconcile_event_utils.py tests/unit/test_reconcile.py
git commit -m "feat(haunt): a day listing whose failure is distinguishable from an empty day; the nudge ladder shared; reminders can name a kind (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: The session store answers the day's frame

**Files:**
- Modify: `src/fateforger/slack_bot/timeboxing_session_store.py` (`day_frame_for`)
- Modify: `src/fateforger/agents/timeboxing/adaptive_timeboxing.py` (`InMemoryPlanningSessionRepository.day_frame_for`)
- Test: `tests/unit/test_timeboxing_session_store_day_frame.py` (new)

**Interfaces:**
- Produces: `async def day_frame_for(self, *, owner_user_id: str, planning_date: date) -> dict | None` on both repositories — the `value` of the newest `DAY_FRAME` fact (`{"wake": "HH:MM"|None, "sleep": "HH:MM"|None, ...}`) among the user's sessions for that day (`status` open or committed, newest `updated_at` first), or `None` when no session or no frame.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_timeboxing_session_store_day_frame.py`:

```python
"""The watcher's sleep boundary comes from the day's session, not from a model.

The session already holds the user's frame as a `DAY_FRAME` fact (typed, or
from memory at skeleton time). Reading it back for a day is one row's JSON,
no judgement.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from fateforger.agents.timeboxing.adaptive_timeboxing import InMemoryPlanningSessionRepository
from fateforger.agents.timeboxing.session_contracts import (
    FactKind,
    PlanningDay,
    PlanningFact,
    PlanningSessionSnapshot,
)


def _snapshot(key: str, day: date, *, sleep: str | None, status: str = "committed") -> PlanningSessionSnapshot:
    facts = []
    if sleep is not None:
        facts.append(PlanningFact(fact_id=f"frame-{key}", kind=FactKind.DAY_FRAME,
                                  value={"wake": "07:00", "sleep": sleep}, source="user"))
    return PlanningSessionSnapshot(
        session_key=key, revision=3, owner_user_id="U1", status=status,
        planning_day=PlanningDay.lock_default(value=day, timezone="Europe/Amsterdam", lock_revision=1),
        facts=facts,
    )


@pytest.mark.asyncio
async def test_the_in_memory_repository_returns_the_days_frame_or_none():
    repo = InMemoryPlanningSessionRepository([
        _snapshot("C1:1.0", date(2026, 9, 7), sleep="23:00"),
        _snapshot("C1:2.0", date(2026, 9, 8), sleep=None),
    ])
    assert (await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 7)))["sleep"] == "23:00"
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 8)) is None
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 9)) is None
    assert await repo.day_frame_for(owner_user_id="U2", planning_date=date(2026, 9, 7)) is None


@pytest.mark.asyncio
async def test_the_sql_repository_reads_one_rows_frame(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from fateforger.slack_bot.timeboxing_session_store import (
        SqlAlchemyTimeboxingSessionRepository,
        ensure_timeboxing_session_schema,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}")
    await ensure_timeboxing_session_schema(engine)
    repo = SqlAlchemyTimeboxingSessionRepository(async_sessionmaker(engine, expire_on_commit=False))
    day = date(2026, 9, 7)
    snapshot = await repo.load_or_create("C1:1.0", owner_user_id="U1")
    await repo.save(_snapshot("C1:1.0", day, sleep="22:30"), interaction_id="i1", outcome=None, expected_revision=snapshot.revision)
    frame = await repo.day_frame_for(owner_user_id="U1", planning_date=day)
    assert frame is not None and frame["sleep"] == "22:30"
    assert await repo.day_frame_for(owner_user_id="U1", planning_date=date(2026, 9, 8)) is None
```

(Check `SqlAlchemyTimeboxingSessionRepository.save`'s real signature and the schema helper's name in `timeboxing_session_store.py` and adapt the SQL test's setup lines; the assertions stay. If `save` needs an outcome object, pass the file's simplest `TurnOutcome`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_timeboxing_session_store_day_frame.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: day_frame_for`.

- [ ] **Step 3: Implement**

`timeboxing_session_store.py`, beside `open_sessions_for_day`:

```python
    async def day_frame_for(
        self, *, owner_user_id: str, planning_date: date
    ) -> dict | None:
        """The user's day frame for one planned day, from the newest session
        that holds one. Reads one row's snapshot JSON -- the one exception to
        `standing_for`'s indexed-columns rule, because the frame lives only in
        the snapshot and the watcher's sleep boundary needs it. No model call.
        """
        async with self._sessionmaker() as session:
            result = await session.execute(
                select(_TimeboxingSessionState.snapshot_json)
                .where(
                    _TimeboxingSessionState.owner_user_id == owner_user_id,
                    _TimeboxingSessionState.planning_date == planning_date,
                    _TimeboxingSessionState.status.in_(("open", "committed")),
                )
                .order_by(_TimeboxingSessionState.updated_at.desc())
            )
            rows = result.all()
        for (payload,) in rows:
            snapshot = self._parse_envelope(payload).snapshot
            frame = _day_frame(snapshot)
            if frame is not None:
                return frame
        return None
```

with a module-level helper (import `FactKind`):

```python
def _day_frame(snapshot: PlanningSessionSnapshot) -> dict | None:
    for fact in reversed(snapshot.facts):
        if fact.kind is FactKind.DAY_FRAME and isinstance(fact.value, dict):
            return dict(fact.value)
    return None
```

`adaptive_timeboxing.py`, `InMemoryPlanningSessionRepository`:

```python
    async def day_frame_for(
        self, *, owner_user_id: str, planning_date: date
    ) -> dict | None:
        candidates = sorted(
            (s for s in self._snapshots.values()
             if s.owner_user_id == owner_user_id and s.status in ("open", "committed")
             and s.planning_day is not None and s.planning_day.date == planning_date),
            key=lambda s: self._updated_at.get(s.session_key, self._clock()),
            reverse=True,
        )
        for snapshot in candidates:
            for fact in reversed(snapshot.facts):
                if fact.kind is FactKind.DAY_FRAME and isinstance(fact.value, dict):
                    return dict(fact.value)
        return None
```

If the repository protocol (`PlanningSessionRepository` in `adaptive_timeboxing.py`) enumerates methods, add `day_frame_for` there too.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_timeboxing_session_store_day_frame.py tests/unit/test_adaptive_timeboxing.py -q -p no:cacheprovider` (plus any existing `test_timeboxing_session_store*.py`).
Expected: all PASS.

```bash
git add src/fateforger/slack_bot/timeboxing_session_store.py src/fateforger/agents/timeboxing/adaptive_timeboxing.py tests/unit/test_timeboxing_session_store_day_frame.py
git commit -m "feat(timeboxing): the session store answers the day's frame, so the watcher's sleep boundary needs no model (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: `RequiredBlockRule`

**Files:**
- Create: `src/fateforger/haunt/required_block_rule.py`
- Test: `tests/unit/test_required_block_rule.py` (new)

**Interfaces:**
- Consumes: `CalendarClient.get_event` / `.list_day` (Task 1); a constraint store with `query_constraints(filters={"planned_day", "day_type", "require_active"}, limit)` returning rows with `requires_block` (increment 2); a ledger with `day_frame_for` (Task 2); `nudge_offsets` (Task 1); `fateforger.agents.timeboxing.required_blocks.required_blocks_value(rows)`; `fateforger.haunt.reconcile._carries_planning_mark`, `_parse_event_dt`, `JobKey`, `DesiredJob`, `PlanningReminder`, `PlanningRuleConfig`.
- Produces:
  ```python
  REQUIRED_BLOCK_KIND = "required_block"; REASON_MISSING = "missing"; REASON_MOVED_OUT = "moved_out"
  @dataclass(frozen=True) class RequiredBlockConfig: calendar_id: str = "primary"; tz: str = "Europe/Amsterdam"; ladder: PlanningRuleConfig = PlanningRuleConfig()
  class RequiredBlockRule:
      rule_id = "required_blocks"
      def __init__(self, *, calendar_client, constraint_store, ledger, config: RequiredBlockConfig | None = None)
      async def evaluate(self, *, now, scope, user_id, channel_id=None, first_nudge_offset=None) -> list[DesiredJob]
      def cached(self, *, user_id, day, slug) -> str | None       # test seam
      def remember(self, *, user_id, day, slug, event_id) -> None  # test seam
  def slug_of(event: dict) -> str | None            # extendedProperties.private["tmbx.slug"] or None
  def within_bounds(event: dict, *, day: date, tz: str, sleep: str | None) -> bool
  ```
  Job keys: `JobKey("rule", "required_blocks", scope, f"{day}:{slug}", f"nudge{idx}")`; payload `PlanningReminder(scope, kind=REQUIRED_BLOCK_KIND, attempt=idx, message=<line>, user_id, channel_id, slug, reason)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_required_block_rule.py`:

```python
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
    def __init__(self, slugs: list[str]):
        self._slugs, self.filters = slugs, []

    async def query_constraints(self, *, filters, limit):
        self.filters.append(filters)
        return [{"uid": f"c-{s}", "name": f"rule {s}", "requires_block": s} for s in self._slugs]


class _Ledger:
    def __init__(self, sleep: str | None = "23:00"):
        self._sleep = sleep

    async def day_frame_for(self, *, owner_user_id, planning_date):
        return None if self._sleep is None else {"wake": "07:00", "sleep": self._sleep}


def _rule(calendar, store, ledger=None) -> RequiredBlockRule:
    return RequiredBlockRule(
        calendar_client=calendar, constraint_store=store, ledger=ledger or _Ledger(),
        config=RequiredBlockConfig(calendar_id="primary", tz=AMS, ladder=PlanningRuleConfig()),
    )


async def _jobs(rule):
    return await rule.evaluate(now=NOW, scope="U1", user_id="U1", channel_id="D1")


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
    cal = _Calendar(day_events=[_event("x", "10:00", "11:00")])  # a block with no slug
    rule = _rule(cal, _Store(["planning"]))
    jobs = await _jobs(rule)
    assert len(jobs) == PlanningRuleConfig().nudge_max_attempts
    first = jobs[0]
    assert first.key.as_id() == f"rule:required_blocks:U1:{DAY.isoformat()}:planning:nudge1"
    assert first.run_at == NOW + timedelta(minutes=10)
    assert first.payload.kind == REQUIRED_BLOCK_KIND
    assert (first.payload.slug, first.payload.reason) == ("planning", REASON_MISSING)
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
async def test_a_failed_fetch_of_the_registered_id_gives_no_verdict():
    cal = _Calendar(fail_get=True)
    rule = _rule(cal, _Store(["planning"]))
    rule.remember(user_id="U1", day=DAY, slug="planning", event_id="e1")
    assert await _jobs(rule) == []
    assert cal.list_calls == 0


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_required_block_rule.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: fateforger.haunt.required_block_rule`.

- [ ] **Step 3: Implement**

Create `src/fateforger/haunt/required_block_rule.py`:

```python
"""The required-block watcher (spec §4, #213).

A memory rule can say a block of a registered kind must be on the day
(`requires_block`). This rule, evaluated on the haunt's reconcile tick beside
the planning ladder, checks each required kind against the calendar and starts
the nudge ladder when the block is gone or has left its bounds.

Presence is equality over identifiers this system minted: the registry slug
against the `tmbx.slug` private property tmbx writes at commit, plus -- for the
`planning` kind only -- the mark the nudge's own booking carries. Never a title.

The register is a cache. The fast path fetches the remembered event by id; a
miss lists the day once and re-derives from the mark. It may be wrong for one
tick and no longer, and it holds nothing that cannot be recomputed.

A failed read gives no verdict (#226). An unreadable calendar and an empty one
are different outcomes, and haunting on the first would teach the user to
ignore the nudge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fateforger.agents.timeboxing.required_blocks import required_blocks_value
from fateforger.agents.timeboxing.session_contracts import PlanningDay

from .reconcile import (
    DesiredJob,
    JobKey,
    PlanningReminder,
    PlanningRuleConfig,
    _carries_planning_mark,
    _parse_event_dt,
    nudge_offsets,
)

logger = logging.getLogger(__name__)

REQUIRED_BLOCK_KIND = "required_block"
REASON_MISSING = "missing"
REASON_MOVED_OUT = "moved_out"

#: The registry kind the planning ladder already books. Its events may carry
#: the `ffplanning…` mark instead of a slug, and count.
_PLANNING_SLUG = "planning"
_SLUG_PROPERTY = "tmbx.slug"
#: A sleep time earlier than this is after midnight and belongs to the next day.
_AFTER_MIDNIGHT_CUTOFF = time(4, 0)


@dataclass(frozen=True)
class RequiredBlockConfig:
    calendar_id: str = "primary"
    tz: str = "Europe/Amsterdam"
    ladder: PlanningRuleConfig = field(default_factory=PlanningRuleConfig)


def slug_of(event: dict) -> str | None:
    """The kind tmbx wrote on this event, or None. A field this system minted."""
    extended = event.get("extendedProperties")
    private = extended.get("private") if isinstance(extended, dict) else None
    slug = private.get(_SLUG_PROPERTY) if isinstance(private, dict) else None
    return slug if isinstance(slug, str) and slug else None


def _sleep_boundary(day: date, tz: str, sleep: str | None) -> datetime:
    zone = ZoneInfo(tz)
    if not sleep:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    try:
        hh, mm = (int(part) for part in sleep.split(":")[:2])
        at = time(hh, mm)
    except (ValueError, TypeError):
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    on = day + timedelta(days=1) if at < _AFTER_MIDNIGHT_CUTOFF else day
    return datetime.combine(on, at, tzinfo=zone)


def within_bounds(event: dict, *, day: date, tz: str, sleep: str | None) -> bool:
    """Starts on `day` in `tz` and ends no later than the sleep boundary."""
    zone = ZoneInfo(tz)
    start = _parse_event_dt(event.get("start"), tz=zone)
    end = _parse_event_dt(event.get("end"), tz=zone)
    if start is None or end is None:
        return False
    if start.astimezone(zone).date() != day:
        return False
    return end <= _sleep_boundary(day, tz, sleep)


def _is_kind(event: dict, slug: str) -> bool:
    if slug_of(event) == slug:
        return True
    return slug == _PLANNING_SLUG and _carries_planning_mark(event, None)


class RequiredBlockRule:
    rule_id = "required_blocks"

    def __init__(
        self,
        *,
        calendar_client: Any,
        constraint_store: Any,
        ledger: Any,
        config: RequiredBlockConfig | None = None,
    ) -> None:
        self._calendar = calendar_client
        self._store = constraint_store
        self._ledger = ledger
        self._config = config or RequiredBlockConfig()
        self._cache: dict[tuple[str, str, str], str] = {}

    # -- the register, as a cache -------------------------------------------------
    def cached(self, *, user_id: str, day: date, slug: str) -> str | None:
        return self._cache.get((user_id, day.isoformat(), slug))

    def remember(self, *, user_id: str, day: date, slug: str, event_id: str) -> None:
        self._cache[(user_id, day.isoformat(), slug)] = event_id

    # -- inputs ---------------------------------------------------------------------
    async def _required(self, day: date) -> list[str]:
        planning_day = PlanningDay.lock_default(value=day, timezone=self._config.tz, lock_revision=1)
        rows = await self._store.query_constraints(
            filters={
                "planned_day": day.isoformat(),
                "day_type": planning_day.day_type.value,
                "require_active": True,
            },
            limit=200,
        )
        return list(required_blocks_value(rows).get("slugs") or [])

    async def _sleep(self, user_id: str, day: date) -> str | None:
        frame = await self._ledger.day_frame_for(owner_user_id=user_id, planning_date=day)
        sleep = frame.get("sleep") if isinstance(frame, dict) else None
        return sleep if isinstance(sleep, str) and sleep else None

    # -- evaluation -----------------------------------------------------------------
    async def evaluate(
        self,
        *,
        now: datetime,
        scope: str,
        user_id: str | None = None,
        channel_id: str | None = None,
        first_nudge_offset: timedelta | None = None,
    ) -> list[DesiredJob]:
        if not user_id:
            return []
        start = now.astimezone(timezone.utc)
        day = now.astimezone(ZoneInfo(self._config.tz)).date()
        try:
            required = await self._required(day)
        except Exception as exc:  # noqa: BLE001 - named, and no verdict
            logger.warning("required_blocks_unreadable user=%s day=%s error_type=%s error=%s",
                           user_id, day, type(exc).__name__, exc)
            return []
        if not required:
            return []
        sleep = await self._sleep(user_id, day)

        jobs: list[DesiredJob] = []
        listed: list[dict] | None | bool = False  # False = not yet listed
        for slug in required:
            verdict = await self._check(user_id=user_id, day=day, slug=slug, sleep=sleep)
            if verdict is None:
                continue  # no verdict: unreadable, leave everything as it is
            if verdict == "present":
                continue
            jobs.extend(self._ladder(start=start, scope=scope, user_id=user_id,
                                     channel_id=channel_id, day=day, slug=slug,
                                     reason=verdict, first_nudge_offset=first_nudge_offset))
        return jobs

    async def _check(self, *, user_id: str, day: date, slug: str, sleep: str | None) -> str | None:
        """'present', REASON_MISSING, REASON_MOVED_OUT, or None for no verdict."""
        tz = self._config.tz
        remembered = self.cached(user_id=user_id, day=day, slug=slug)
        if remembered:
            try:
                event = await self._calendar.get_event(calendar_id=self._config.calendar_id, event_id=remembered)
            except Exception as exc:  # noqa: BLE001
                logger.warning("calendar_unreadable user=%s slug=%s error_type=%s error=%s",
                               user_id, slug, type(exc).__name__, exc)
                return None
            if event is not None and _is_kind(event, slug) and within_bounds(event, day=day, tz=tz, sleep=sleep):
                return "present"
        events = await self._calendar.list_day(calendar_id=self._config.calendar_id, day=day, tz=tz)
        if events is None:
            logger.warning("calendar_unreadable user=%s slug=%s day=%s (list_day)", user_id, slug, day)
            return None
        carrying = [e for e in events if isinstance(e, dict) and _is_kind(e, slug)]
        if len(carrying) > 1:
            logger.info("required_block_duplicates user=%s slug=%s count=%d", user_id, slug, len(carrying))
        inside = [e for e in carrying if within_bounds(e, day=day, tz=tz, sleep=sleep)]
        if inside:
            self.remember(user_id=user_id, day=day, slug=slug, event_id=str(inside[0].get("id") or ""))
            return "present"
        return REASON_MOVED_OUT if carrying else REASON_MISSING

    def _ladder(self, *, start, scope, user_id, channel_id, day, slug, reason, first_nudge_offset):
        window = f"{day.isoformat()}:{slug}"
        return [
            DesiredJob(
                key=JobKey("rule", self.rule_id, scope, window, f"nudge{idx}"),
                run_at=start + offset,
                payload=PlanningReminder(
                    scope=scope, kind=REQUIRED_BLOCK_KIND, attempt=idx,
                    message=_line(slug, reason, idx), user_id=user_id, channel_id=channel_id,
                    slug=slug, reason=reason,
                ),
            )
            for idx, offset in enumerate(nudge_offsets(self._config.ladder, first_nudge_offset=first_nudge_offset), start=1)
        ]


def _line(slug: str, reason: str, attempt: int) -> str:
    what = "is not on today's calendar" if reason == REASON_MISSING else "has left today's plan"
    return f"Your `{slug}` block {what}. Put it back, or say when."


__all__ = ["REASON_MISSING", "REASON_MOVED_OUT", "REQUIRED_BLOCK_KIND",
           "RequiredBlockConfig", "RequiredBlockRule", "slug_of", "within_bounds"]
```

(`_parse_event_dt`'s `tz` parameter is a `tzinfo`; pass the `ZoneInfo`. Check `PlanningDay.lock_default` derives `day_type` from the weekday — it does on the host.)

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_required_block_rule.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/haunt/required_block_rule.py tests/unit/test_required_block_rule.py
git commit -m "feat(haunt): RequiredBlockRule — presence by the minted slug, bounds by the day and the frame, no verdict on a failed read (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: The reconciler runs both rules and owns one prefix per rule

**Files:**
- Modify: `src/fateforger/haunt/reconcile.py` (`PlanningReconciler.__init__`, `reconcile_missing_planning`)
- Test: `tests/unit/test_reconcile_two_rules.py` (new)

**Interfaces:**
- Produces: `PlanningReconciler(scheduler, *, calendar_client, planning_session_store=None, dispatcher=None, rule=None, required_block_rule=None)`. `reconcile_missing_planning` evaluates the planning rule and, when configured, the required-block rule with the same arguments, unions their jobs, and removes stale jobs only under the two prefixes `rule:next_planning_session:<scope>:` and `rule:required_blocks:<scope>:`. A required-block rule that raises is logged and does not stop the planning ladder.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_reconcile_two_rules.py`:

```python
"""Two rules, one tick, one prefix each: neither deletes the other's jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fateforger.haunt.reconcile import DesiredJob, JobKey, PlanningReconciler, PlanningReminder

from .test_reconcile import DummyCalendarClient, FakeScheduler

NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)


class _Rule:
    def __init__(self, rule_id: str, kinds: list[str]):
        self.rule_id, self._kinds, self.calls = rule_id, kinds, 0

    async def evaluate(self, *, now, scope, **kwargs):
        self.calls += 1
        return [
            DesiredJob(
                key=JobKey("rule", self.rule_id, scope, "2026-09-07", kind),
                run_at=now + timedelta(minutes=10),
                payload=PlanningReminder(scope=scope, kind=kind, attempt=1, message="m", user_id=scope),
            )
            for kind in self._kinds
        ]


class _Explodes(_Rule):
    async def evaluate(self, **kwargs):
        raise RuntimeError("store down")


async def _noop(reminder):
    return None


@pytest.mark.asyncio
async def test_both_rules_run_and_each_keeps_its_own_jobs():
    scheduler = FakeScheduler()
    planning, required = _Rule("next_planning_session", ["nudge1"]), _Rule("required_blocks", ["nudge1"])
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=planning, required_block_rule=required)
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    ids = {job.id for job in scheduler.get_jobs()}
    assert ids == {"rule:next_planning_session:U1:2026-09-07:nudge1", "rule:required_blocks:U1:2026-09-07:nudge1"}
    assert (planning.calls, required.calls) == (1, 1)

    # the required block comes back: only its job goes
    required._kinds = []
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}


@pytest.mark.asyncio
async def test_a_failing_required_block_rule_does_not_stop_the_planning_ladder(caplog):
    scheduler = FakeScheduler()
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", ["nudge1"]),
                                    required_block_rule=_Explodes("required_blocks", []))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}
    assert any("required_blocks" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_without_a_required_block_rule_nothing_changes():
    scheduler = FakeScheduler()
    reconciler = PlanningReconciler(scheduler, calendar_client=DummyCalendarClient([]), dispatcher=_noop,
                                    rule=_Rule("next_planning_session", ["nudge1"]))
    await reconciler.reconcile_missing_planning(scope="U1", user_id="U1", now=NOW)
    assert {job.id for job in scheduler.get_jobs()} == {"rule:next_planning_session:U1:2026-09-07:nudge1"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_reconcile_two_rules.py -q -p no:cacheprovider`
Expected: FAIL — unexpected keyword `required_block_rule`.

- [ ] **Step 3: Implement**

In `PlanningReconciler.__init__` add `required_block_rule: Any | None = None` and `self._required_block_rule = required_block_rule`. In `reconcile_missing_planning`, replace the evaluate-and-prefix section:

```python
        desired = list(
            await self._rule.evaluate(
                now=now_dt, scope=scope, user_id=user_id, channel_id=channel_id,
                planning_event_id=planning_event_id, first_nudge_offset=first_nudge_offset,
            )
        )
        prefixes = {f"rule:{self._rule.rule_id}:{scope}:"}
        if self._required_block_rule is not None:
            prefixes.add(f"rule:{self._required_block_rule.rule_id}:{scope}:")
            try:
                desired.extend(
                    await self._required_block_rule.evaluate(
                        now=now_dt, scope=scope, user_id=user_id, channel_id=channel_id,
                        first_nudge_offset=first_nudge_offset,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - one rule's failure is not the other's
                logger.exception(
                    "required_blocks rule failed for %s error_type=%s error=%s",
                    scope, type(exc).__name__, exc,
                )
        scheduled = {
            job.id: getattr(getattr(job, "trigger", None), "run_date", None)
            for job in self._scheduler.get_jobs()
            if any(job.id.startswith(prefix) for prefix in prefixes)
        }
```

The rest (remove stale, keep existing run times, add) is unchanged and now spans both prefixes. `event_anchored` stays as is.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_reconcile_two_rules.py tests/unit/test_reconcile.py tests/unit/test_reconcile_session_jobs.py tests/unit/test_runtime_startup_reconcile.py -q -p no:cacheprovider`
Expected: all PASS.

```bash
git add src/fateforger/haunt/reconcile.py tests/unit/test_reconcile_two_rules.py
git commit -m "feat(haunt): the reconciler runs the required-block rule beside the planning ladder, one prefix each (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: The dispatcher delivers a required-block reminder

**Files:**
- Modify: `src/fateforger/slack_bot/planning.py` (`dispatch_planning_reminder`)
- Test: `tests/unit/test_required_block_dispatch.py` (new)

**Interfaces:**
- Produces: for `reminder.kind == REQUIRED_BLOCK_KIND`: the `_timeboxing_silences(at="start")` guard applies as for any nudge; then if `reminder.slug == "planning"` the reminder falls into the existing planning-card flow unchanged (the card books a planning session, which is the block); for any other slug it posts one DM line (`reminder.message`) to the user's DM channel via `chat_postMessage` and returns.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_required_block_dispatch.py` (reuse `_NoSlotRuntime`, `DummyClient`, `_DummyAnchorStore` from `tests/unit/test_planning_reminder_suppression.py` — import them; if they are private to that module, copy the minimal versions):

```python
"""A required-block reminder reaches the user: the planning kind rides the
existing card, any other kind is one line in the DM."""
from __future__ import annotations

import pytest

from fateforger.haunt.reconcile import PlanningReminder
from fateforger.haunt.required_block_rule import REASON_MISSING, REQUIRED_BLOCK_KIND
from fateforger.slack_bot.planning import PlanningCoordinator

from .test_planning_reminder_suppression import DummyClient, _NoSlotRuntime


def _reminder(slug: str) -> PlanningReminder:
    return PlanningReminder(scope="U1", kind=REQUIRED_BLOCK_KIND, attempt=1,
                            message=f"Your `{slug}` block is not on today's calendar.",
                            user_id="U1", channel_id="D1", slug=slug, reason=REASON_MISSING)


@pytest.mark.asyncio
async def test_a_non_planning_kind_is_one_dm_line(monkeypatch):
    runtime = _NoSlotRuntime()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    async def _dm(*, user_id): return "D1"
    monkeypatch.setattr(coordinator, "_resolve_dm_channel", _dm)
    async def _quiet(*, user_id, at): return False
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _quiet)

    await coordinator.dispatch_planning_reminder(_reminder("sleep"))

    posts = [c for c in client.calls if c[0] == "chat_postMessage"] if hasattr(client, "calls") else client.posted
    assert len(posts) == 1
    assert "`sleep`" in str(posts[0])
    assert not getattr(runtime, "event_draft_store", None) or not getattr(runtime.event_draft_store, "created", [])


@pytest.mark.asyncio
async def test_the_planning_kind_takes_the_existing_card_path(monkeypatch):
    runtime = _NoSlotRuntime()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=DummyClient())  # type: ignore[arg-type]
    seen = []
    async def _card(reminder): seen.append(reminder)
    monkeypatch.setattr(coordinator, "_dispatch_planning_card", _card)
    async def _quiet(*, user_id, at): return False
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _quiet)

    await coordinator.dispatch_planning_reminder(_reminder("planning"))

    assert seen and seen[0].slug == "planning"


@pytest.mark.asyncio
async def test_an_open_session_silences_a_required_block_reminder(monkeypatch):
    runtime = _NoSlotRuntime()
    client = DummyClient()
    coordinator = PlanningCoordinator(runtime=runtime, focus=object(), client=client)  # type: ignore[arg-type]
    async def _busy(*, user_id, at): return True
    monkeypatch.setattr(coordinator, "_timeboxing_silences", _busy)

    await coordinator.dispatch_planning_reminder(_reminder("sleep"))

    posts = [c for c in client.calls if c[0] == "chat_postMessage"] if hasattr(client, "calls") else client.posted
    assert posts == []
```

(Adapt the `DummyClient` call-recording attribute to what that class actually records; the assertions are the point.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_required_block_dispatch.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: _dispatch_planning_card` / no DM posted.

- [ ] **Step 3: Implement**

In `planning.py`, `dispatch_planning_reminder`: extract everything after the `_timeboxing_silences` check (from `if not self._draft_store:` to the end of the card flow) into `async def _dispatch_planning_card(self, reminder: PlanningReminder) -> None`, and make the body:

```python
        if await self._timeboxing_silences(user_id=reminder.user_id, at="start"):
            return
        if reminder.kind == REQUIRED_BLOCK_KIND and reminder.slug != "planning":
            # A required kind the planning card cannot book. One line, the
            # user's own words for the kind, no card (#213).
            dm_channel = await self._resolve_dm_channel(user_id=reminder.user_id)
            if not dm_channel:
                logger.warning("required_block reminder: could not resolve DM channel for %s", reminder.user_id)
                return
            await self._client.chat_postMessage(channel=dm_channel, text=reminder.message)
            return
        await self._dispatch_planning_card(reminder)
```

Import `REQUIRED_BLOCK_KIND` from `fateforger.haunt.required_block_rule`. A `planning`-slug reminder takes the card path: `_planning_still_missing` there re-checks the anchor, which is right — the block the card books *is* the planning session.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/unit/test_required_block_dispatch.py tests/unit/test_planning_reminder_suppression.py tests/unit/test_haunt_slack_delivery.py tests/unit/test_planning_reminder_blocks_include_dismiss.py -q -p no:cacheprovider`
Expected: all PASS except the two known date-dependent cases in `test_planning_reminder_suppression.py`.

```bash
git add src/fateforger/slack_bot/planning.py tests/unit/test_required_block_dispatch.py
git commit -m "feat(slack): a required-block reminder reaches the DM; the planning kind rides the existing card (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Wire the rule into the runtime

**Files:**
- Modify: `src/fateforger/core/runtime.py` (where `PlanningReconciler(...)` is built)
- Test: `tests/unit/test_runtime_startup_reconcile.py` (extend if it constructs the reconciler through the runtime; otherwise a smoke test that the wiring imports and constructs)

**Interfaces:**
- Consumes: `timeboxing_constraint_store` (already on the runtime), `timeboxing_session_store` (the SQL repository), `timeboxing_calendar_id`, the planning timezone from settings (`planning_timezone()` in `fateforger.slack_bot.timeboxing_host` reads it; use the same source).

- [ ] **Step 1: Implement the wiring**

At the `PlanningReconciler(...)` construction in `runtime.py`:

```python
    required_block_rule = (
        RequiredBlockRule(
            calendar_client=calendar_client,
            constraint_store=timeboxing_constraint_store,
            ledger=timeboxing_session_store,
            config=RequiredBlockConfig(
                calendar_id=timeboxing_calendar_id or "primary",
                tz=planning_timezone(),
            ),
        )
        if timeboxing_constraint_store is not None
        else None
    )
    reconciler = PlanningReconciler(
        scheduler,
        calendar_client=calendar_client,
        dispatcher=dispatch_planning,
        planning_session_store=planning_session_store,
        rule=PlanningSessionRule(...unchanged...),
        required_block_rule=required_block_rule,
    )
```

with imports `from fateforger.haunt.required_block_rule import RequiredBlockConfig, RequiredBlockRule` and `from fateforger.slack_bot.timeboxing_host import planning_timezone`. Log one line at startup: `logger.info("required_blocks watcher: %s", "on" if required_block_rule else "off (no constraint store)")`.

- [ ] **Step 2: Test**

If `tests/unit/test_runtime_startup_reconcile.py` builds the runtime's reconciler, add an assertion that `reconciler._required_block_rule is not None` when a constraint store is configured. Otherwise add to `tests/unit/test_reconcile_two_rules.py`:

```python
def test_the_runtime_wiring_constructs_the_rule_with_the_calendar_and_timezone():
    from fateforger.haunt.required_block_rule import RequiredBlockConfig, RequiredBlockRule
    rule = RequiredBlockRule(calendar_client=object(), constraint_store=object(), ledger=object(),
                             config=RequiredBlockConfig(calendar_id="hugo@example.com", tz="Europe/Amsterdam"))
    assert rule.rule_id == "required_blocks"
```

Run: `uv run pytest tests/unit/test_runtime_startup_reconcile.py tests/unit/test_reconcile_two_rules.py -q -p no:cacheprovider` and `uv run python -c "import fateforger.core.runtime"`.

- [ ] **Step 3: Commit**

```bash
git add src/fateforger/core/runtime.py tests/unit/test_runtime_startup_reconcile.py tests/unit/test_reconcile_two_rules.py
git commit -m "feat(runtime): the required-block watcher runs on the reconcile tick (#213)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Spec note, whole suite, push, PR

- [ ] **Step 1: Spec §4** — in `docs/superpowers/specs/2026-09-04-required-blocks-design.md` §4 "The watcher", replace the `planning_session_refs` re-keying sentence with: "The register is an in-process cache on the rule, `(user, day, slug) → event_id`, rebuilt from the calendar on any miss; a persistent register would need a migration and buys nothing the miss path does not (one list per rebuild)." Replace "the sleep time from the same frame rule the session uses" with "the `sleep` of the user's session's `DAY_FRAME` fact for that day (`day_frame_for`), else end of day; a sleep time before 04:00 is on the next day." Add under "Cache writes": "The commit does not write the cache; the first tick after a commit lists once." Commit as `docs(specs): required blocks §4 follows the watcher as built (#213)`.

- [ ] **Step 2: Full offline suite**

Run: `uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -3`
Expected: all pass except the three known pre-existing failures.

- [ ] **Step 3: Rebase, rerun, push, PR**

```bash
git fetch origin && git rebase origin/main
uv run pytest -q -p no:cacheprovider -m "not slow" 2>&1 | tail -2
git push -u origin feat/required-blocks-haunt
gh pr create --base main --title "haunt: the required-block watcher — the planning block is verified every tick and haunted when it is gone or out of bounds (#213, increment 3 of required blocks)" --body "..."
```

PR body: what lands per task; the cache decision; the no-verdict rule; the human checklist — after merge and restart, commit a plan with a `planning` block, then in Google Calendar (a) drag it to tomorrow and (b) delete it, and see the DM nudge within a reconcile tick each time; then put it back and see the ladder clear. End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

---

## Self-review

**Spec coverage (§4, §5, decisions).** Inputs: required slugs from the read path with `day_type` (Task 3 `_required`), register as cache (Task 3, deviation recorded in Task 7), bounds day + sleep (Task 2 + Task 3 `within_bounds`). Predicates `present`/`within_bounds` as separable functions (Task 3). Evaluation fast path, miss path re-deriving from the mark, re-register silently, `moved_out` / `missing` reasons (Task 3). Failed read → no verdict, state untouched (Task 1 `list_day` None; Task 3). Haunting through the existing machinery (Tasks 4, 5); no haunting while a session is open (Task 5 via `_timeboxing_silences`). §5 codes: `required_blocks_unreadable` and `calendar_unreadable` appear as log lines in Task 3. Cache write after commit: dropped, recorded (Task 7). Duplicate blocks of one kind: counted and logged, never resolved (Task 3).

**Placeholders.** None; the PR body sketch in Task 7 is an instruction, not plan text with gaps.

**Type consistency.** `list_day(*, calendar_id, day, tz) -> list[dict] | None`; `nudge_offsets(config, *, first_nudge_offset)`; `PlanningReminder.slug/.reason`; `day_frame_for(*, owner_user_id, planning_date) -> dict | None`; `RequiredBlockRule(calendar_client=, constraint_store=, ledger=, config=)`, `.evaluate(now=, scope=, user_id=, channel_id=, first_nudge_offset=)`, `.cached(user_id=, day=, slug=)`, `.remember(user_id=, day=, slug=, event_id=)`; `REQUIRED_BLOCK_KIND`, `REASON_MISSING`, `REASON_MOVED_OUT`; `PlanningReconciler(..., required_block_rule=)`; job id `rule:required_blocks:<scope>:<day>:<slug>:nudge<n>` — used identically across Tasks 1–6.
