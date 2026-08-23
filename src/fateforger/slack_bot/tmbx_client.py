"""Reversing a commit from Slack, without going back through the model.

A harness turn can write to the calendar. Until now nothing could take that
back from the same surface: `plan_undo` existed as a tool the model could call,
and no control was attached to it. This project's standing rule is that
everything should be reversible through the UI -- admonishments excepted,
because a notification cannot be unsent -- and a write that can be made but not
reversed breaks it in the direction that matters.

**Why this calls tmbx directly rather than asking the harness to undo.** A
button that means "reverse that" must reverse that. Routing it through a
planning turn would put a judgement in a mechanical path: the model would
decide whether to call `plan_undo`, with which id, and could reasonably do
something else. It would also cost a full turn to press a button.

**Why it reaches the warm server rather than building a service in-process.**
The calendar backend holds state in the process that owns it, so a
`TmbxService` constructed here would act on a different calendar from the one
the harness just wrote to -- reading empty, or undoing nothing, with complete
confidence. The write and its reversal have to go through the same mount.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from fateforger.tools.mcp_http_client import StreamableHttpMcpClient
from fateforger.tools.mcp_url_validation import McpEndpointPolicy, McpEndpointResolver

logger = logging.getLogger(__name__)

_TMBX_ENDPOINT = McpEndpointResolver(
    McpEndpointPolicy(
        name="tmbx MCP",
        env_vars=("TMBX_MCP_URL",),
        default_url="http://127.0.0.1:8011/mcp",
    )
)


class UndoUnavailable(RuntimeError):
    """The server could not be reached, so nothing is known about the undo.

    Distinct from a refusal: a refusal means tmbx considered the request and
    declined it for a stated reason, which the user needs to see. This means we
    never got an answer, and reporting it as a refusal would invent one.
    """


class TmbxClient:
    """The only thing here that knows tmbx exists."""

    def __init__(self, *, server_url: str | None = None, timeout: float = 20.0) -> None:
        url = server_url or os.environ.get("TMBX_MCP_URL") or _TMBX_ENDPOINT.policy.default_url
        self._client = StreamableHttpMcpClient(
            resolver=_TMBX_ENDPOINT, server_url=url, timeout=timeout
        )

    async def undo(self, tx_id: str) -> dict[str, Any]:
        """Reverse one committed transaction, by the id that commit returned.

        Returns tmbx's own answer parsed, rather than a boolean, because a
        refusal carries a reason the user has to be told -- `unknown_transaction`
        and a day that has drifted since are different problems with different
        remedies, and flattening them to "it did not work" strands the user.
        """
        from autogen_core import CancellationToken

        try:
            tools = await self._client.get_tools()
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            raise UndoUnavailable(f"{type(exc).__name__}: {exc}") from exc

        tool = next((t for t in tools if t.name == "plan_undo"), None)
        if tool is None:
            raise UndoUnavailable("the tmbx mount does not publish plan_undo")

        try:
            raw = await tool.run_json({"tx_id": tx_id}, CancellationToken())
        except Exception as exc:  # noqa: BLE001
            raise UndoUnavailable(f"{type(exc).__name__}: {exc}") from exc

        return _as_payload(raw)


def _as_payload(raw: Any) -> dict[str, Any]:
    """tmbx answers with a JSON string; give callers the object.

    A response that will not parse is returned as an unreversed outcome with
    the text attached rather than raising, so a shape change downgrades the
    message the user sees instead of losing the fact that undo ran at all.
    """
    text = _text_of(raw)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("plan_undo returned unparseable output: %s", text[:200])
        return {"committed": False, "reason": "unparseable_response", "raw": text[:200]}
    if not isinstance(payload, dict):
        return {"committed": False, "reason": "unexpected_response", "raw": text[:200]}
    return payload


def _text_of(raw: Any) -> str:
    """Pull the payload text out of whatever the tool layer handed back.

    ``run_json`` answers with a list of MCP content blocks, not a string --
    ``[TextContent(type='text', text='{"committed": false, ...}')]``. Calling
    ``str()`` on that yields the *repr*, which is not JSON, so every real
    refusal was being reported as an unparseable response while the unit tests
    passed on hand-fed strings. Found by running it against the live server;
    no amount of stubbing the shape I assumed would have caught it.
    """
    if isinstance(raw, str):
        return raw
    blocks = raw if isinstance(raw, (list, tuple)) else [raw]
    parts = [
        block.text
        for block in blocks
        if isinstance(getattr(block, "text", None), str)
    ]
    return "".join(parts) if parts else str(raw)


__all__ = ["TmbxClient", "UndoUnavailable"]
