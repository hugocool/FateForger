"""The Slack boundary must never post a payload the model did not write.

Regression cover for #180, where The Schedular's reply to a #scheduling handoff was an MCP
``CallToolResult`` content array — Google Calendar htmlLink, the account email, an iCalUID and a
TickTick task id, verbatim in the channel.
"""

import logging

import pytest

pytest.importorskip("autogen_agentchat")

from autogen_agentchat.messages import (
    HandoffMessage,
    MultiModalMessage,
    TextMessage,
    ToolCallSummaryMessage,
)

from fateforger.slack_bot.handlers import _slack_payload_from_result
from fateforger.slack_bot.messages import SlackBlockMessage, SlackThreadStateMessage
from fateforger.slack_bot.timeboxing_commit import (
    _slack_payload_from_result as _commit_payload,
)
from fateforger.slack_bot.timeboxing_stage_actions import (
    _slack_payload_from_result as _stage_payload,
)
from fateforger.slack_bot.timeboxing_submit import (
    _slack_payload_from_result as _submit_payload,
)


# The shape that actually reached Slack: autogen's MCP adapter json.dumps()es the
# CallToolResult content list into the ToolCallSummaryMessage's `content` str.
LEAKED_PAYLOAD = (
    '[{"type": "text", "text": "[{\\"id\\":\\"49acd6b2\\",\\"summary\\":\\"Weekly review\\",'
    '\\"htmlLink\\":\\"https://www.google.com/calendar/event?eid=abc\\",'
    '\\"creator\\":{\\"email\\":\\"hugo.evers@gmail.com\\"},'
    '\\"iCalUID\\":\\"49acd6b2@google.com\\",'
    '\\"tickTaskId\\":\\"690288be8f08ae584c506307\\"}]", "annotations": null}]'
)

# Substrings of the leak that must not survive the boundary. Asserting on these is not
# pattern-matching a judgement: they are literal secrets from a fixture this test wrote itself.
LEAKED_SECRETS = (
    "hugo.evers@gmail.com",
    "google.com/calendar/event",
    "690288be8f08ae584c506307",
    "49acd6b2@google.com",
)


class _Response:
    """Stands in for autogen's Response, which the runtime hands to the boundary."""

    def __init__(self, chat_message):
        self.chat_message = chat_message


def _tool_summary(content):
    return ToolCallSummaryMessage(
        content=content, source="planner_agent", tool_calls=[], results=[]
    )


def test_tool_result_reply_is_withheld_not_posted(caplog):
    with caplog.at_level(logging.ERROR, logger="fateforger.slack_bot.handlers"):
        payload = _slack_payload_from_result(_Response(_tool_summary(LEAKED_PAYLOAD)))

    text = payload["text"]
    for secret in LEAKED_SECRETS:
        assert secret not in text, f"{secret!r} reached the Slack payload"
    # Honest, not empty: an empty message is indistinguishable from an agent with nothing to say.
    assert text.strip()
    assert text != "(no response)"
    assert "withheld" in text

    # Loud, and it names the type so the fix can be traced to the agent that produced it.
    assert any(
        record.levelno >= logging.ERROR and "ToolCallSummaryMessage" in record.getMessage()
        for record in caplog.records
    ), "withholding a tool result must log an error"


def test_bare_tool_summary_without_response_wrapper_is_withheld():
    # Some call sites hand the message itself through rather than a Response.
    payload = _slack_payload_from_result(_tool_summary(LEAKED_PAYLOAD))
    assert LEAKED_PAYLOAD not in payload["text"]
    assert "withheld" in payload["text"]


def test_normal_text_reply_passes_through_untouched():
    prose = "You have one event on Sunday: Weekly review, 09:00-10:00."
    assert _slack_payload_from_result(_Response(TextMessage(content=prose, source="a"))) == {
        "text": prose
    }
    assert _slack_payload_from_result(TextMessage(content=prose, source="a")) == {"text": prose}


def test_text_reply_that_quotes_json_still_passes_through():
    # The model deliberately writing a payload is an answer, not a leak. This is why the
    # decision is made on the message type and never on what the text looks like.
    prose = 'The raw event came back as {"summary": "Weekly review"} — want me to expand it?'
    assert _slack_payload_from_result(_Response(TextMessage(content=prose, source="a"))) == {
        "text": prose
    }


def test_handoff_message_passes_through():
    msg = HandoffMessage(content="Handing you to the timeboxer.", source="a", target="b")
    assert _slack_payload_from_result(_Response(msg)) == {"text": msg.content}


def test_message_type_outside_the_allowlist_is_withheld():
    # Not a tool result, but not something anyone decided was safe to post either. The allowlist
    # fails closed so an unrecognised type shows up as a bug report rather than as content.
    msg = MultiModalMessage(content=["a chunk", "another chunk"], source="a")
    assert _slack_payload_from_result(_Response(msg))["text"].startswith(":warning:")


def test_allowlisted_type_carrying_non_string_content_is_withheld():
    """Guards the case where someone widens the allowlist to a type whose content is a list.

    ``TextMessage`` validates ``content`` as a ``str``, so this has to be built past pydantic to
    exist at all — which is the point: the check is here so that adding, say, MultiModalMessage
    to ``_PROSE_REPLY_TYPES`` fails loudly instead of handing Slack a list to render.
    """
    msg = TextMessage.model_construct(content=["a chunk"], source="a")
    assert _slack_payload_from_result(_Response(msg))["text"].startswith(":warning:")


def test_slack_block_messages_are_unaffected():
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]
    assert _slack_payload_from_result(SlackBlockMessage(text="hi", blocks=blocks)) == {
        "text": "hi",
        "blocks": blocks,
    }
    assert _slack_payload_from_result(_Response(SlackBlockMessage(text="hi", blocks=blocks))) == {
        "text": "hi",
        "blocks": blocks,
    }
    assert _slack_payload_from_result(SlackThreadStateMessage(text="hi", blocks=blocks)) == {
        "text": "hi",
        "blocks": blocks,
    }
    assert _slack_payload_from_result(SlackThreadStateMessage(text="hi")) == {"text": "hi"}


def test_absent_content_still_reads_as_no_response():
    # Nothing came back at all. Different failure from a withheld payload, different message.
    assert _slack_payload_from_result(None) == {"text": "(no response)"}
    assert _slack_payload_from_result(_Response(None)) == {"text": "(no response)"}


# Four modules build Slack payloads from agent results. All of them ended in the same two lines
# before #180, so all of them could post the same leak; they now share one guard, and this
# parametrisation is what keeps a future fifth copy from drifting back.
@pytest.mark.parametrize(
    "build_payload",
    [_slack_payload_from_result, _commit_payload, _stage_payload, _submit_payload],
    ids=["handlers", "timeboxing_commit", "timeboxing_stage_actions", "timeboxing_submit"],
)
def test_every_slack_payload_builder_withholds_tool_results(build_payload):
    withheld = build_payload(_Response(_tool_summary(LEAKED_PAYLOAD)))["text"]
    for secret in LEAKED_SECRETS:
        assert secret not in withheld, f"{secret!r} reached the Slack payload"
    assert "withheld" in withheld

    prose = "You have one event on Sunday."
    assert build_payload(_Response(TextMessage(content=prose, source="a")))["text"] == prose
