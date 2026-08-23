"""The last check before an agent's reply becomes a Slack message.

Four call sites used to end the same way — ``{"text": chat_message.content or "(no response)"}``
— and every one of them would post whatever the runtime happened to hand back. In #180 what it
handed back was an MCP ``CallToolResult``: The Schedular published a Google Calendar htmlLink,
the account email, an iCalUID and a TickTick task id into ``#scheduling`` as its answer.

The guard lives here rather than in any one handler module so that fixing it once fixes it for
every agent, including ones not written yet.
"""

from __future__ import annotations

import logging

from autogen_agentchat.messages import TextMessage

from fateforger.core.logging_config import record_error

try:
    from autogen_agentchat.messages import HandoffMessage
except Exception:  # pragma: no cover - optional dependency wiring
    HandoffMessage = None

logger = logging.getLogger(__name__)


# Message types whose ``content`` is prose the model actually wrote, and which may therefore be
# posted as-is. This is an allowlist rather than a denylist of known-bad types because the two
# ways of guessing wrong are not symmetric: an unknown type wrongly allowed leaks whatever it
# happens to carry, silently and forever, while an unknown type wrongly withheld posts a warning
# and logs an error — which is a bug report, and gets fixed.
PROSE_REPLY_TYPES: tuple[type, ...] = tuple(
    t for t in (TextMessage, HandoffMessage) if t is not None
)

NON_PROSE_REPLY_TEXT = (
    ":warning: That agent returned a raw tool result instead of an answer, so it was "
    "withheld. Nothing was lost — check the bot logs and ask again."
)

NO_RESPONSE_TEXT = "(no response)"


def is_model_authored_prose(message: object) -> bool:
    """Whether ``message`` is an answer the model wrote, rather than a tool result.

    The one that shipped to ``#scheduling`` was an AutoGen ``ToolCallSummaryMessage``: an agent
    running with ``reflect_on_tool_use=False`` returns the tool output *as* its reply, and the
    MCP adapter serialises the ``CallToolResult`` into it verbatim. That message subclasses
    ``BaseTextChatMessage`` and its ``content`` is a plain ``str``, so by the time anything
    downstream sees the text there is nothing left in it that separates a leak from an answer.

    The type of the object the runtime handed us is the only honest signal, so that is what this
    asks. Sniffing the text for a JSON-ish shape was rejected twice over: it is a guess about
    what the words mean, which this project bans outright, and it is also simply wrong in both
    directions — a model may legitimately quote a payload back to the user, and a tool may
    legitimately return a bare sentence.
    """
    if not isinstance(message, PROSE_REPLY_TYPES):
        return False
    # An allowlisted type could still be widened to one carrying a non-``str`` content
    # (``MultiModalMessage`` and friends). Slack renders whatever it is given, so insist here.
    return isinstance(getattr(message, "content", None), str)


def agent_reply_text(reply: object) -> str:
    """The text of ``reply``, or an honest stand-in when it must not be posted.

    Never returns an empty string: an empty Slack message is indistinguishable from an agent
    that had nothing to say, which is exactly the silent-wrong-answer shape this guard exists
    to stop. The three outcomes — an answer, nothing at all, a withheld payload — each read
    differently in the channel.
    """
    content = getattr(reply, "content", None)
    if content is None:
        # Nothing came back. A different failure from a withheld payload, and not a leak.
        return NO_RESPONSE_TEXT
    if not is_model_authored_prose(reply):
        record_error(component="slack_boundary", error_type="non_prose_agent_reply")
        # Loud, and it names the type, because the fix always belongs in the agent that produced
        # the reply and never here. The content is deliberately not logged: it is the thing we
        # just decided was unsafe to show.
        logger.error(
            "Refusing to post a non-prose agent reply to Slack: type=%s content_type=%s. "
            "The agent returned a tool result instead of an answer.",
            type(reply).__name__,
            type(content).__name__,
        )
        return NON_PROSE_REPLY_TEXT
    return content or NO_RESPONSE_TEXT


__all__ = [
    "NON_PROSE_REPLY_TEXT",
    "NO_RESPONSE_TEXT",
    "PROSE_REPLY_TYPES",
    "agent_reply_text",
    "is_model_authored_prose",
]
