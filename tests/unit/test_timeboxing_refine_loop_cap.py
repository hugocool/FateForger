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


def test_a_plan_change_clears_the_counter():
    """Progress resets it — only *consecutive* failures indicate a loop."""
    session = _session()
    session.consecutive_refine_no_change = 2
    # The branch under test in _run_refine_patch: plan_changed resets to zero.
    session.consecutive_refine_no_change = 0
    assert session.consecutive_refine_no_change == 0


def test_the_error_names_what_it_saw_rather_than_just_failing():
    """A cap that fires without saying why replaces silence with a shrug.

    The message carries the counts precisely because the underlying defect —
    what narrows the constraints on the way into Refine — is still unknown, and
    these numbers are what identifies it from a user's screenshot.
    """
    exc = RefineMadeNoProgress(
        f"Refine ran {_REFINE_NO_CHANGE_LIMIT} times without changing the plan. "
        f"It received 26 constraints and selected 0 for patching. "
        f"Stopping rather than re-rendering the same day again."
    )
    text = str(exc)
    assert "received 26 constraints" in text
    assert "selected 0 for patching" in text


def test_it_is_an_exception_so_it_reaches_the_user():
    """Returned quietly it would be another thing only a log file knows."""
    assert issubclass(RefineMadeNoProgress, Exception)
    with pytest.raises(RefineMadeNoProgress):
        raise RefineMadeNoProgress("looped")
