# tests/integration/test_eval_planning_card_intent.py
"""Quality of the planning-card reply interpreter against the live model.

Unit tests stub the model and prove the plumbing; this proves the prompt and
the offered-controls context. Every case resamples -- one draw tests the
model's luck -- and the rate is the assertion. No temperature pin.
"""

from __future__ import annotations

import asyncio
import os
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"),
]

SAMPLES = 8
THRESHOLD = 7


def _report(results: list) -> str:
    lines = []
    for r in results:
        if isinstance(r, BaseException):
            lines.append("".join(traceback.format_exception(r)).rstrip())
        else:
            lines.append(repr(r))
    return "\n---\n".join(lines)


#: The draft's timezone. `planning_view` renders `now` in it, so it is the
#: timezone "the same day" has to be read in.
_TZ = ZoneInfo("Europe/Amsterdam")

#: The slot the card proposes: Thursday 3 September, 10:38 Amsterdam.
_START = datetime(2026, 9, 3, 8, 38, tzinfo=timezone.utc)

#: An hour and a half before that slot, on its own day -- derived from it, so
#: moving the draft moves `now` with it and cannot silently change which day
#: "tomorrow" names. Fixed against the draft rather than read from the clock:
#: "plan tomorrow for me" means Friday here on every day of the week, and a
#: view that fetched today's date would make this case pass on a Wednesday and
#: fail on a Thursday.
_NOW = _START - timedelta(hours=1, minutes=38)

# Subtracting 98 minutes from an instant is not the same as staying on its
# local day, and the gap is silent: any `_START` whose *local* clock reads
# earlier than 01:38 puts `_NOW` on the previous local date, at which point
# "plan tomorrow for me" names the card's own day -- agreement, not a
# non-press -- and both `test_a_non_press_is_none[plan tomorrow for me]` and
# the break-it case invert their meaning with nothing turning red. Compared as
# local dates rather than as a UTC bound, because the offset that decides it
# is the zone's and moves with DST. Asserted at import, so it is checked even
# on a run that skips these evals for want of a key.
assert _NOW.astimezone(_TZ).date() == _START.astimezone(_TZ).date(), (
    "`now` must land on the draft's own local day, else the cases that turn on "
    f"'tomorrow' change meaning: now={_NOW.astimezone(_TZ)} "
    f"start={_START.astimezone(_TZ)}"
)

# "friday at 14:00" is asserted as another day. Move the draft onto a Friday
# and it names the proposal's own day, where a time is a press -- the case
# would invert with nothing turning red. Weekday numbers are the calendar's,
# not the user's words.
assert _START.astimezone(_TZ).weekday() != 4, "the draft moved onto a Friday"


def _draft(status_name: str = "DRAFT"):
    from fateforger.haunt.event_draft_store import DraftStatus, EventDraftPayload

    return EventDraftPayload(
        draft_id="draft_eval",
        user_id="U1",
        channel_id="D1",
        message_ts="1.0",
        calendar_id="primary",
        event_id="ffplanningeval",
        title="Daily planning session",
        description="Plan tomorrow's priorities and prep for shutdown.",
        timezone=_TZ.key,
        start_at_utc=_START.isoformat(),  # 10:38 local
        duration_min=30,
        status=DraftStatus[status_name],
        event_url=None,
        last_error="calendar unreachable" if status_name == "FAILURE" else None,
    )


async def _presses(text: str, *, status_name: str = "DRAFT", strip_effect: bool = False) -> list:
    from fateforger.llm.factory import build_intent_interpreter_client
    from fateforger.slack_bot.planning_surface import (
        PLANNING_PROMPT_FRAGMENT,
        InterpretedPlanningTurn,
        bind,
        planning_view,
    )
    from fateforger.slack_bot.surface_intents import SurfaceIntentInterpreter

    view = planning_view(_draft(status_name), now=_NOW)
    if strip_effect:
        # The break-it check: without the effect text the model has only a label.
        view = view.model_copy(
            update={"offered_options": tuple(o.model_copy(update={"effect": "-"}) for o in view.offered_options)}
        )
    interpreter = SurfaceIntentInterpreter(build_intent_interpreter_client())

    async def one():
        turn = await interpreter.interpret(
            view=view,
            user_text=text,
            schema=InterpretedPlanningTurn,
            prompt_fragment=PLANNING_PROMPT_FRAGMENT,
            attribution=("planning_card_intent_interpreter", "planning_card_intent", "eval"),
        )
        return bind(turn)

    return await asyncio.gather(*(one() for _ in range(SAMPLES)), return_exceptions=True)


def _count(results: list, *, kind: str | None, time: str | None = None) -> int:
    hits = 0
    for r in results:
        if isinstance(r, BaseException):
            continue
        if kind is None:
            hits += r is None
        else:
            hits += r is not None and r.kind == kind and r.selected_time == time
    return hits


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Okay!", "yes", "sure, do it"])
async def test_consent_is_the_add_press(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="add") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["no, let's do 13:45", "13:45"])
async def test_a_time_with_consent_updates_and_adds(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind="update_time_and_add", time="13:45") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_a_time_without_consent_only_updates() -> None:
    results = await _presses("make it 17:00 but don't add yet")
    assert _count(results, kind="update_time", time="17:00") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["why 10:38?", "plan tomorrow for me", "later"])
async def test_a_non_press_is_none(text: str) -> None:
    results = await _presses(text)
    assert _count(results, kind=None) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["tomorrow at 9", "friday at 14:00"])
async def test_another_day_with_a_time_is_none(text: str) -> None:
    """A time does not make a reply about a different day a press.

    `planning.py` applies `selected_time` to the draft's own date, so reading
    "tomorrow at 9" as a time press writes 09:00 on the day the user just
    turned down, possibly in the past. Friday is the day after this draft.
    """

    results = await _presses(text)
    assert _count(results, kind=None) >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_the_proposals_own_day_with_a_time_updates_and_adds() -> None:
    """The guard against over-correcting: naming the card's own day with a
    time is still the time press."""

    results = await _presses("today at 14:00")
    assert _count(results, kind="update_time_and_add", time="14:00") >= THRESHOLD, _report(results)


@pytest.mark.asyncio
async def test_try_again_on_a_failed_card_is_retry() -> None:
    results = await _presses("try again", status_name="FAILURE")
    assert _count(results, kind="retry") >= THRESHOLD, _report(results)


def _expected_unbroken_here() -> bool:
    """Whether this run is a plain one on a pin where the clause is not load-bearing.

    Resolved at test time, never at import: a module-level production import
    would load settings before the key-less skip could decide anything. Both
    sides of the comparison are model ids this project pinned, not user text.
    """

    if os.environ.get("INTERPRETER_TIER_CONFIG"):
        # The bench reads the raw outcome and buckets a failure as `unbroken`
        # itself; an xfail here would move the case out of that column.
        return False
    from fateforger.core.config import settings
    from fateforger.llm.factory import INTENT_INTERPRETER, _model_for_agent

    return _model_for_agent(INTENT_INTERPRETER) != settings.openrouter_default_model_flash


@pytest.mark.asyncio
async def test_break_it_without_the_day_clause_a_non_press_becomes_a_press(monkeypatch, request) -> None:
    """A discriminator that passes without its discriminating sentence is not one.

    Only "plan tomorrow for me" is asserted here. "later" needed no clause:
    with `now` in the payload it went 6/8 to 8/8 on the base fragment alone,
    so claiming the clause carries it would be asserting something the
    measurement says is false. This case is the one the clause is for -- 1/8
    on flash with the date available and the base fragment, 8/8 with it.

    It runs on every pin, and on the pro pin it fails: with `now` present and
    the clause stripped, pro still answers `none` at the bar, at `low` and at
    `high` alike (the 2026-09-11 record), so the paragraph is not load-bearing
    there. That is the flip not happening, not a quality
    loss, and the bench already tells the two apart -- `interpreter_tier.py`
    matches this function's `test_break_it_` name and buckets the outcome as
    `unbroken` in its own column. Skipping instead would hide the case from
    the very table the pin decision reads, on the pin that table defaults to.

    So off the flash pin it is an expected failure, except under the bench:
    a plain `pytest -m slow` on the default pin reports xfail instead of red,
    and the bench, which sets `INTERPRETER_TIER_CONFIG`, still sees the raw
    failure. Not strict -- pro's count is a rate, and a draw can still land.
    """

    if _expected_unbroken_here():
        request.applymarker(
            pytest.mark.xfail(
                reason=(
                    "off the flash pin the stripped clause is not load-bearing: on the pro pin it "
                    "stayed unbroken at both low and high (results-interpreter-tier-2026-09-11)"
                ),
                strict=False,
            )
        )

    import fateforger.slack_bot.planning_surface as ps

    # `_presses` imports the fragment from the module inside its own body, on
    # every call, so patching the module global is what it reads.
    monkeypatch.setattr(ps, "PLANNING_PROMPT_FRAGMENT", ps._PLANNING_PROMPT_FRAGMENT_BASE)
    results = await _presses("plan tomorrow for me")
    assert _count(results, kind=None) < THRESHOLD, _report(results)
