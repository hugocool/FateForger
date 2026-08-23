"""Tests for the reply-selection logic in `scripts/slack_e2e.py`.

Offline and hermetic: nothing here reaches Slack, spawns a process, or opens a
socket. The live driver itself is not a pytest file and is never collected --
`pyproject.toml` restricts collection to `tests/` and to `test_*.py`, and it is
`scripts/slack_e2e.py`.

The message fixtures are the two shapes actually observed in #plan-sessions on
2026-08-23, trimmed. They differ in a way that matters: one carries `user` and
`bot_id`, the other is a `bot_message` with a display name and no `user` field
at all, because the bot posts some messages under a custom username.
"""

from __future__ import annotations

import pytest

from scripts.slack_e2e import (
    BotIdentity,
    first_bot_reply,
    is_after,
    is_from_bot,
    reply_text,
)

IDENTITY = BotIdentity(user_id="U09A0RE6WQ6", bot_id="B09A0RE611C")

# Posted under a custom display name: no `user` key anywhere in the payload.
BOT_MESSAGE_SHAPE = {
    "subtype": "bot_message",
    "text": "Timeboxing for Tomorrow — Sunday 23 August started.",
    "username": "The Schedular",
    "type": "message",
    "ts": "1787428890.951339",
    "bot_id": "B09A0RE611C",
    "app_id": "A09A5GYRHSL",
}

# Posted as the app's own bot user: carries both keys.
BOT_USER_SHAPE = {
    "user": "U09A0RE6WQ6",
    "type": "message",
    "ts": "1787428890.933359",
    "bot_id": "B09A0RE611C",
    "app_id": "A09A5GYRHSL",
    "text": ":warning: I can't record anything you say in this thread.",
}

HUMAN_MESSAGE = {
    "user": "U095637NL8P",
    "type": "message",
    "ts": "1787428887.666659",
    "text": "sup",
}


# ---------------------------------------------------------------------------
# is_from_bot -- both shapes, or half the conversation is invisible
# ---------------------------------------------------------------------------


def test_a_custom_username_post_is_recognised_as_the_bot() -> None:
    """No `user` key at all. A check on `user` alone misses this entirely --
    and it is the shape the bot uses for its stage announcements."""
    assert is_from_bot(BOT_MESSAGE_SHAPE, IDENTITY) is True


def test_a_post_from_the_apps_bot_user_is_recognised_as_the_bot() -> None:
    assert is_from_bot(BOT_USER_SHAPE, IDENTITY) is True


def test_a_human_message_is_not_the_bot() -> None:
    assert is_from_bot(HUMAN_MESSAGE, IDENTITY) is False


def test_another_app_in_the_same_workspace_is_not_our_bot() -> None:
    other = dict(BOT_MESSAGE_SHAPE, bot_id="B0000000000")
    assert is_from_bot(other, IDENTITY) is False


def test_a_workspace_with_no_bot_id_still_matches_on_the_user_id() -> None:
    identity = BotIdentity(user_id="U09A0RE6WQ6", bot_id=None)
    assert is_from_bot(BOT_USER_SHAPE, identity) is True
    assert is_from_bot(HUMAN_MESSAGE, identity) is False


def test_an_unknown_bot_is_not_matched_just_because_it_has_a_bot_id() -> None:
    identity = BotIdentity(user_id="U09A0RE6WQ6", bot_id=None)
    assert is_from_bot(BOT_MESSAGE_SHAPE, identity) is False


# ---------------------------------------------------------------------------
# is_after -- strictly newer than the message we just posted
# ---------------------------------------------------------------------------


def test_a_newer_message_is_after() -> None:
    assert is_after({"ts": "1787428890.951339"}, "1787428890.933359") is True


def test_an_older_message_is_not_after() -> None:
    assert is_after({"ts": "1787428890.933359"}, "1787428890.951339") is False


def test_the_very_message_we_posted_is_not_after_itself() -> None:
    """`after_ts` is the ts of the message this driver posted. If equality
    counted, the driver could hand back the human's own message."""
    assert is_after({"ts": "1787428890.951339"}, "1787428890.951339") is False


def test_a_message_with_no_timestamp_is_not_after() -> None:
    assert is_after({}, "1787428890.951339") is False


def test_a_malformed_timestamp_does_not_abort_the_poll() -> None:
    assert is_after({"ts": "not-a-timestamp"}, "1787428890.951339") is False


# ---------------------------------------------------------------------------
# first_bot_reply
# ---------------------------------------------------------------------------


def test_the_bots_answer_is_found_among_the_threads_messages() -> None:
    found = first_bot_reply(
        [HUMAN_MESSAGE, BOT_MESSAGE_SHAPE], IDENTITY, HUMAN_MESSAGE["ts"]
    )
    assert found is BOT_MESSAGE_SHAPE


def test_an_answer_the_thread_already_had_is_not_this_turns_answer() -> None:
    """The trap this whole timestamp filter exists for: re-running the driver
    in a thread the bot has already spoken in would otherwise report success
    the instant it polled, having proved nothing."""
    assert first_bot_reply([BOT_MESSAGE_SHAPE], IDENTITY, "1787428899.000000") is None


def test_a_thread_where_only_the_human_has_spoken_has_no_answer_yet() -> None:
    assert first_bot_reply([HUMAN_MESSAGE], IDENTITY, "1787428880.000000") is None


def test_the_earliest_qualifying_reply_is_returned_not_the_latest() -> None:
    """A stage announcement lands before the stage's content. Returning the
    last message would mean whatever happened to be there when polling
    stopped, which changes between runs."""
    early = dict(BOT_USER_SHAPE, ts="1787428891.000000")
    late = dict(BOT_MESSAGE_SHAPE, ts="1787428999.000000")
    found = first_bot_reply([late, early], IDENTITY, "1787428890.000000")
    assert found is not None and found["ts"] == "1787428891.000000"


def test_the_order_slack_returned_them_in_does_not_decide_the_answer() -> None:
    early = dict(BOT_USER_SHAPE, ts="1787428891.000000")
    late = dict(BOT_MESSAGE_SHAPE, ts="1787428999.000000")
    forwards = first_bot_reply([early, late], IDENTITY, "1787428890.000000")
    backwards = first_bot_reply([late, early], IDENTITY, "1787428890.000000")
    assert forwards is not None and backwards is not None
    assert forwards["ts"] == backwards["ts"]


def test_an_empty_thread_has_no_answer() -> None:
    assert first_bot_reply([], IDENTITY, "1787428890.000000") is None


# ---------------------------------------------------------------------------
# reply_text
# ---------------------------------------------------------------------------


def test_plain_text_is_returned_as_is() -> None:
    assert reply_text(BOT_MESSAGE_SHAPE) == BOT_MESSAGE_SHAPE["text"]


def test_a_block_only_message_is_not_reported_as_silence() -> None:
    """Most of the bot's real output is blocks with an empty `text`. Printing
    nothing would read as a bot that answered and said nothing."""
    rendered = reply_text({"ts": "1", "text": "", "blocks": [{"type": "header"}]})
    assert "header" in rendered


def test_a_message_with_neither_text_nor_blocks_says_so() -> None:
    assert reply_text({"ts": "1"}) == "<empty message>"


# ---------------------------------------------------------------------------
# The driver refuses to guess a channel
# ---------------------------------------------------------------------------


def test_no_channel_is_a_refusal_rather_than_a_default(monkeypatch) -> None:
    """The bot answers ordinary channel messages. A default channel would mean
    a mistyped command starting a real planning session in a real channel."""
    from scripts import slack_e2e

    monkeypatch.delenv("SLACK_E2E_CHANNEL_ID", raising=False)
    with pytest.raises(SystemExit):
        slack_e2e.main([])
