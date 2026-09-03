"""The failure card of 2026-09-02 said "nothing reached your calendar" ninety
seconds after nineteen blocks had. Both of its sentences were false for the
user reading it, and the log beside it kept only `error_type=ValueError`.

Assertions are over identifiers this system minted -- artifact kinds, failure
codes, log record fields -- never over the user's words.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from fateforger.agents.timeboxing.session_contracts import (
    ArtifactKind,
    PlanningArtifact,
    PlanningDay,
    PlanningSessionSnapshot,
)
from fateforger.slack_bot.timeboxing_cards import (
    TIMEBOX_TURN_FAILED_TEXT,
    timebox_failure_message,
)


def _receipt() -> PlanningArtifact:
    return PlanningArtifact.create(
        artifact_id="receipt-1",
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload={"committed": True, "tx_id": "tx-1"},
        dependency_revisions={"validated_candidate": 1},
    )


def _snapshot(*, committed: bool) -> PlanningSessionSnapshot:
    return PlanningSessionSnapshot(
        session_key="C1:1.0",
        revision=5,
        owner_user_id="U1",
        planning_day=PlanningDay.lock_default(
            value=date(2026, 9, 2), timezone="Europe/Amsterdam", lock_revision=1
        ),
        artifacts=[_receipt()] if committed else [],
        status="committed" if committed else "open",
    )


def test_a_committed_day_is_not_told_its_calendar_is_untouched() -> None:
    message = timebox_failure_message(snapshot=_snapshot(committed=True))

    assert message.text != TIMEBOX_TURN_FAILED_TEXT
    assert "nothing reached your calendar" not in message.text.lower()
    assert "calendar" in message.text.lower()


def test_a_reopened_day_that_committed_earlier_is_also_told_so() -> None:
    """Status is `open` again after a revision; the receipt is what is true."""

    reopened = _snapshot(committed=True).model_copy(update={"status": "open"})

    message = timebox_failure_message(snapshot=reopened)

    assert "nothing reached your calendar" not in message.text.lower()


def test_an_uncommitted_day_keeps_the_stable_sentence() -> None:
    message = timebox_failure_message(snapshot=_snapshot(committed=False))

    assert message.text == TIMEBOX_TURN_FAILED_TEXT


def test_no_snapshot_keeps_the_stable_sentence() -> None:
    assert timebox_failure_message().text == TIMEBOX_TURN_FAILED_TEXT


def test_the_stable_sentence_does_not_send_the_user_to_a_dead_end() -> None:
    """"Ask me where the session stands" pointed at the same raise."""

    assert "where the session stands" not in TIMEBOX_TURN_FAILED_TEXT


def test_refusing_to_cancel_a_committed_day_has_its_own_sentence() -> None:
    message = timebox_failure_message("session_committed")

    assert message.text != TIMEBOX_TURN_FAILED_TEXT
    assert "calendar" in message.text.lower()


class _StubCard:
    def __init__(self, *a, **k):
        pass

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_the_turn_handler_logs_what_the_exception_said(monkeypatch, caplog) -> None:
    """Catches `error_type=ValueError` and nothing else.

    The 2026-09-02 root cause -- "the planning session does not accept another
    intent" -- was in the exception's message and reconstructed from source,
    because the log line kept only the class name.
    """

    import fateforger.slack_bot.handlers as handlers

    class Kernel:
        async def turn(self, request, progress):
            raise ValueError("the planning session does not accept another intent")

    class Repo:
        async def load_or_create(self, key, owner_user_id):
            return _snapshot(committed=True)

    class Runtime:
        timeboxing_session_store = Repo()

    async def _intent(*a, **k):
        from fateforger.agents.timeboxing.session_contracts import Advance

        return Advance()

    monkeypatch.setattr(handlers, "_timeboxing_kernel", lambda *a, **k: Kernel())
    monkeypatch.setattr(handlers, "derive_timebox_intent", _intent)
    monkeypatch.setattr(handlers, "HarnessProgressCard", _StubCard)

    logger = logging.getLogger("test.turn.failure")
    with caplog.at_level(logging.ERROR, logger=logger.name):
        message = await handlers._run_adaptive_timebox_turn(
            runtime=Runtime(), client=object(), logger=logger,
            session_key="C1:1.0", actor_user_id="U1", interaction_id="1.1",
            progress_channel="C1", progress_ts="1.0",
            card_channel="C1", card_thread_ts="1.0", user_text="move the work",
        )

    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    record = records[0]
    assert "does not accept another intent" in record.getMessage()
    assert record.exc_info is not None, "the traceback is the diagnosis"
    # And the card that went with it did not claim an untouched calendar.
    assert "nothing reached your calendar" not in message.text.lower()


def test_the_bot_log_carries_a_timestamp() -> None:
    """tmbx.log has them and slack-bot.log did not; cross-service time was guessed."""

    from fateforger.core.logging_config import _install_extra_aware_formatter

    root = logging.getLogger()
    handler = logging.StreamHandler()
    root.addHandler(handler)
    try:
        _install_extra_aware_formatter()
        assert "%(asctime)s" in handler.formatter._fmt
    finally:
        root.removeHandler(handler)
