"""Stage 4 must advance or fail, never re-render the same day forever.

A real session ran Refine nine times. Each pass produced no patch, so the
constraint list was the only thing left to render, and each pass appended
another copy until the message crossed Slack's size limit and `chat.update`
began refusing with `msg_too_long`. The progress channel and the error channel
were the same message, so the session went silent with no way to say why —
twelve minutes indistinguishable from working.
"""

from __future__ import annotations

import pytest

from fateforger.agents.timeboxing.agent import (
    _REFINE_NO_CHANGE_LIMIT,
    RefineMadeNoProgress,
    Session,
)


def _session() -> Session:
    return Session(thread_ts="1787398114.671739", channel_id="C0AA6HC1RJL", user_id="U")


def test_a_fresh_session_has_not_looped():
    assert _session().consecutive_refine_no_change == 0


def test_the_limit_allows_an_ordinary_no_op_pass():
    """One no-change pass is normal.

    A day that already satisfies its constraints legitimately needs no patch,
    and a user's no-op instruction plausibly produces a second. The cap has to
    sit above both or it fires on correct behaviour.
    """
    assert _REFINE_NO_CHANGE_LIMIT > 2


def test_the_limit_sits_below_what_a_real_session_did():
    """The observed loop was nine passes. A cap above that would not have fired."""
    assert _REFINE_NO_CHANGE_LIMIT < 9


@pytest.fixture
def refine_agent():
    """A real agent object with only what Stage 4 touches populated.

    Constructed without __init__ deliberately: the real one builds MCP clients
    and model clients, none of which this path uses, and a test that needs the
    world running tests the world.
    """
    from unittest.mock import AsyncMock

    from fateforger.agents.timeboxing.agent import TimeboxingFlowAgent
    from fateforger.agents.timeboxing.tb_models import TBPlan

    agent = TimeboxingFlowAgent.__new__(TimeboxingFlowAgent)
    agent._session_debug = lambda *a, **k: None
    agent._collect_constraints = AsyncMock(return_value=[])
    agent._select_constraints_for_refine_patcher = lambda **kw: []

    session = Session(thread_ts="1787398114.671739", channel_id="C0AA6HC1RJL", user_id="U")
    session.tb_plan = TBPlan(date="2026-08-24", tz="Europe/Amsterdam", events=[])
    session.timebox = None
    return agent, session


async def _drive_refine(agent, session, *, plan_changes: bool):
    """Run one real Stage 4 pass, stubbing only the patcher itself."""
    from unittest.mock import AsyncMock

    from fateforger.agents.timeboxing import agent as agent_mod

    returned = session.tb_plan.model_copy(deep=True)
    if plan_changes:
        returned.tz = "UTC"

    async def _apply_patch(*, plan_validator, **kw):
        # The real patcher calls the validator, which is what populates
        # validated_timebox. A stub that skips it makes the method raise before
        # reaching the counter logic under test.
        plan_validator(returned)
        return returned, None

    agent._timebox_patcher = type("P", (), {"apply_patch": staticmethod(_apply_patch)})()

    original = agent_mod.tb_plan_to_timebox
    # Must survive a second pass: the method deep-copies session.timebox at
    # the top, so a bare object() breaks on the loop this test is about.
    class _Timebox:
        def model_copy(self, deep: bool = False):
            return self

    agent_mod.tb_plan_to_timebox = lambda _p: _Timebox()
    try:
        return await agent._execute_refine_patch_and_sync(
            session=session, patch_message="tighten the morning"
        )
    finally:
        agent_mod.tb_plan_to_timebox = original


async def test_a_plan_change_clears_the_counter_in_the_real_pass(refine_agent):
    """Only *consecutive* no-change passes indicate a loop.

    Previously this set the counter itself and asserted on its own
    assignment — it passed with the reset deleted from agent.py. Without that
    reset, no-op passes accumulate across a whole session and the cap fires on
    a day that legitimately needed three separate no-op patches.
    """
    agent, session = refine_agent
    session.consecutive_refine_no_change = 2
    await _drive_refine(agent, session, plan_changes=True)
    assert session.consecutive_refine_no_change == 0


async def test_consecutive_no_change_passes_accumulate_then_raise(refine_agent):
    agent, session = refine_agent
    for _ in range(_REFINE_NO_CHANGE_LIMIT - 1):
        await _drive_refine(agent, session, plan_changes=False)
    assert session.consecutive_refine_no_change == _REFINE_NO_CHANGE_LIMIT - 1

    with pytest.raises(RefineMadeNoProgress):
        await _drive_refine(agent, session, plan_changes=False)


async def test_the_raised_error_carries_counts_from_the_pass_itself(refine_agent):
    """These numbers are the only probe into the unidentified root cause.

    Previously this built the f-string in the test body, so it asserted the
    test's own formatting — it passed with the real message replaced by the
    literal "Refine looped."
    """
    agent, session = refine_agent
    session.consecutive_refine_no_change = _REFINE_NO_CHANGE_LIMIT - 1
    agent._select_constraints_for_refine_patcher = lambda **kw: [object()] * 7

    from unittest.mock import AsyncMock

    agent._collect_constraints = AsyncMock(return_value=[object()] * 19)

    with pytest.raises(RefineMadeNoProgress) as caught:
        await _drive_refine(agent, session, plan_changes=False)

    text = str(caught.value)
    assert "19 constraints" in text, text
    assert "selected 7" in text, text


def test_it_is_an_exception_so_it_reaches_the_user():
    """Returned quietly it would be another thing only a log file knows."""
    assert issubclass(RefineMadeNoProgress, Exception)
    with pytest.raises(RefineMadeNoProgress):
        raise RefineMadeNoProgress("looped")
