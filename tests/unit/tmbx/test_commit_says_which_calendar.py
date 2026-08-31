"""A commit must say which calendar it reached, not just that it succeeded.

Every commit driven through the session kernel landed in FakeCalendar -- an
in-memory dict -- while the receipt read {"committed": true, "tx_id": ...},
the Slack card read committed, and the user's calendar stayed empty. Three
surfaces, each true on its own terms, composing into a false one.

The fake default is right and stays (tmbx/server.py argues it well: a real
write should be an opt-in, never a side effect of running the server). What
was missing is that nothing downstream could tell the two apart.
"""

from tmbx.calendar.fake import FakeCalendar


def test_a_port_says_what_it_is() -> None:
    """The port knows; nothing downstream should have to infer it from env."""

    assert FakeCalendar().backend == "fake"


def test_a_fake_is_not_durable() -> None:
    """The question a caller actually has: will this survive?"""

    assert FakeCalendar().durable is False


def test_the_real_adapter_is_durable() -> None:
    """Imported lazily: gcal pulls the MCP client stack."""

    from tmbx.calendar.gcal import GoogleCalendarAdapter

    adapter = GoogleCalendarAdapter(tz="Europe/Amsterdam")
    assert adapter.backend == "google"
    assert adapter.durable is True


def test_a_commit_result_carries_the_backend() -> None:
    """The receipt is built from this; if it is absent there, it cannot appear."""

    from tmbx.service import CommitResult

    assert "calendar_backend" in CommitResult.model_fields


def _committed_message(payload: dict):
    """Render a Committed outcome the way Slack would."""

    import logging

    from fateforger.agents.timeboxing.session_contracts import (
        ArtifactKind,
        Committed,
        PlanningArtifact,
        PlanningSessionSnapshot,
    )
    from fateforger.slack_bot.timeboxing_cards import (
        PendingTimeboxCandidates,
        render_outcome,
    )

    receipt = PlanningArtifact.create(
        kind=ArtifactKind.COMMIT_RECEIPT,
        revision=1,
        payload=payload,
        dependency_revisions={"validated_candidate": 1},
    )
    snapshot = PlanningSessionSnapshot(
        session_key="C:1", revision=1, owner_user_id="U1"
    )
    return render_outcome(
        Committed(receipt=receipt),
        pending=PendingTimeboxCandidates(),
        snapshot=snapshot,
        session_key="C:1",
        actor_user_id="U1",
        channel_id="C",
        thread_ts="1",
        logger=logging.getLogger(__name__),
    )


def test_a_fake_commit_does_not_render_as_success() -> None:
    """The exact composite that made an empty calendar read as a planned day."""

    msg = _committed_message({
        "committed": True, "tx_id": "abc", "calendar_backend": "fake",
        "durable": False,
    })
    assert "fake" in msg.text
    assert "nothing reached your real one" in msg.text


def test_a_real_commit_still_reads_as_success() -> None:
    msg = _committed_message({
        "committed": True, "tx_id": "abc", "calendar_backend": "google",
        "durable": True,
    })
    assert "Committed the plan you approved" in msg.text
    assert "nothing reached" not in msg.text
