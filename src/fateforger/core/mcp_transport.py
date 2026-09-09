"""Shared classification of transient MCP transport failures.

Both `McpCalendarClient` (timeboxing) and `PlannerAgent` (schedular) sit on
top of AutoGen's `McpWorkbench`, and both need to tell "the connection to the
MCP server is temporarily broken" apart from "the tool call itself failed".
That distinction used to live as a private copy inside `McpCalendarClient`;
this module is the one place it is defined, so the two clients cannot drift
out of agreement about what counts as recoverable.

The strings matched here are library/transport exception text -- raised by
AutoGen's MCP session and actor machinery, not authored by Hugo or a Slack
user. The project's ban on string/keyword matching over user content
(CLAUDE.md) does not apply: this is the same kind of system-minted text as an
HTTP status code or an exception class name, and no judgement about what a
person meant is being made here.
"""

from __future__ import annotations

RECOVERABLE_TRANSPORT_ERROR_MARKERS: tuple[str, ...] = (
    "mcp actor not running",
    "all connection attempts failed",
    "timed out while waiting for response to clientrequest",
    "connection refused",
    "server disconnected",
)
"""Substrings of AutoGen/MCP transport exception messages that mean the
session died out from under a workbench rather than the tool call being
invalid or the server rejecting it. Lower-cased for a case-insensitive match.
"""


def is_recoverable_transport_error(exc: Exception) -> bool:
    """Return True when `exc` looks like a transient MCP transport failure.

    A "recoverable" error here means: the cached `McpWorkbench`'s session is
    dead (crashed server, dropped connection, actor never started), and a
    caller should reset the workbench and may retry -- not that the request
    was necessarily safe to resend. Callers still decide retry safety for
    themselves (a create is not the same risk as a read).
    """
    text = str(exc or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in RECOVERABLE_TRANSPORT_ERROR_MARKERS)
