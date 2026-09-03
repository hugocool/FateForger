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
from tmbx.build_identity import RESOURCE_URI as BUILD_IDENTITY_URI
from tmbx.build_identity import BuildIdentity

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


class CommitUnavailable(RuntimeError):
    """The validated candidate could not be submitted to tmbx."""


class CommitOutcomeUnknown(RuntimeError):
    """A commit was dispatched but no definitive result could be reconciled."""


class ReadUnavailable(RuntimeError):
    """The calendar snapshot could not be read from tmbx."""


class TmbxClient:
    """The only thing here that knows tmbx exists."""

    def __init__(self, *, server_url: str | None = None, timeout: float = 20.0) -> None:
        url = (
            server_url
            or os.environ.get("TMBX_MCP_URL")
            or _TMBX_ENDPOINT.policy.default_url
        )
        self._client = StreamableHttpMcpClient(
            resolver=_TMBX_ENDPOINT, server_url=url, timeout=timeout
        )

    async def build_identity(self) -> BuildIdentity | None:
        """Which src/tmbx the running server imported, as it reports it.

        None means the server answered but publishes no identity -- one older
        than #255 -- which the caller must show as *unknown*, not as a match.
        Unreachable raises, like every other call here.
        """
        text = await self._client.read_resource_text(BUILD_IDENTITY_URI)
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except ValueError:
            return None
        return BuildIdentity.from_dict(payload)

    async def read(self, calendar_id: str, day: str) -> dict[str, Any]:
        """Read the exact host-selected calendar/day without external effects.

        A read can be retried safely because it precedes any planner-owned
        write. Provider exception text stays behind this boundary: callers get
        one stable typed failure and logs retain only the exception class.
        """
        from autogen_core import CancellationToken

        unavailable_message = "calendar service unavailable"
        request = {"calendar_id": calendar_id, "day": day}
        for attempt in range(2):
            try:
                tools = await self._client.get_tools()
            except Exception as exc:  # noqa: BLE001 - provider boundary is sanitized
                logger.warning(
                    "plan_read tool discovery failed attempt=%d error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                continue
            tool = next(
                (candidate for candidate in tools if candidate.name == "plan_read"),
                None,
            )
            if tool is None:
                unavailable_message = "calendar read unavailable"
                continue
            try:
                raw = await tool.run_json(request, CancellationToken())
            except Exception as exc:  # noqa: BLE001 - provider boundary is sanitized
                logger.warning(
                    "plan_read failed attempt=%d error_type=%s",
                    attempt + 1,
                    type(exc).__name__,
                )
                continue
            payload = _as_payload(raw, operation="plan_read")
            if payload.get("reason") in {
                "unparseable_response",
                "unexpected_response",
            }:
                unavailable_message = "calendar read returned no trustworthy snapshot"
                continue
            return payload
        raise ReadUnavailable(unavailable_message)

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
        except Exception as exc:
            raise UndoUnavailable(f"{type(exc).__name__}: {exc}") from exc

        tool = next((t for t in tools if t.name == "plan_undo"), None)
        if tool is None:
            raise UndoUnavailable("the tmbx mount does not publish plan_undo")

        try:
            raw = await tool.run_json({"tx_id": tx_id}, CancellationToken())
        except Exception as exc:
            raise UndoUnavailable(f"{type(exc).__name__}: {exc}") from exc

        return _as_payload(raw, operation="plan_undo")

    async def commit(
        self,
        snapshot: dict[str, Any],
        patch: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Commit exactly the candidate already validated and approved."""

        from autogen_core import CancellationToken

        try:
            tools = await self._client.get_tools()
        except Exception as exc:
            logger.warning(
                "plan_commit tool discovery failed error_type=%s",
                type(exc).__name__,
            )
            raise CommitUnavailable("calendar service unavailable") from exc
        tool = next((tool for tool in tools if tool.name == "plan_commit"), None)
        if tool is None:
            raise CommitUnavailable("the tmbx mount does not publish plan_commit")
        request = {
            "snapshot": snapshot,
            "patch": patch,
            "expect": "clean",
            "idempotency_key": idempotency_key,
        }
        ambiguous_dispatch = False
        for attempt in range(2):
            try:
                raw = await tool.run_json(request, CancellationToken())
            except Exception as exc:
                ambiguous_dispatch = True
                if attempt == 0:
                    continue
                raise CommitOutcomeUnknown(
                    "calendar commit was dispatched but its outcome could not be confirmed"
                ) from exc
            payload = _as_payload(raw, operation="plan_commit")
            if payload.get("reason") in {
                "unparseable_response",
                "unexpected_response",
            }:
                ambiguous_dispatch = True
                if attempt == 0:
                    continue
                raise CommitOutcomeUnknown(
                    "calendar commit returned no trustworthy outcome"
                )
            if ambiguous_dispatch and payload.get("committed") is not True:
                raise CommitOutcomeUnknown(
                    "calendar state changed after a lost commit response"
                )
            return payload
        raise AssertionError("commit reconciliation loop did not return")


def _as_payload(raw: Any, *, operation: str = "plan_undo") -> dict[str, Any]:
    """tmbx answers with a JSON string; give callers the object.

    A response that will not parse fails closed with a stable reason and
    bounded metadata logging. Provider text is neither returned nor logged,
    because it may contain bodies, URLs, identifiers, or other private data.
    """
    text = _text_of(raw)
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        logger.warning(
            "%s returned unparseable output (response_bytes=%d)",
            operation,
            len(text.encode("utf-8", errors="replace")),
        )
        return {"committed": False, "reason": "unparseable_response"}
    if not isinstance(payload, dict):
        logger.warning(
            "%s returned unexpected response type %s",
            operation,
            type(payload).__name__,
        )
        return {"committed": False, "reason": "unexpected_response"}
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
        block.text for block in blocks if isinstance(getattr(block, "text", None), str)
    ]
    return "".join(parts) if parts else str(raw)


__all__ = [
    "CommitOutcomeUnknown",
    "CommitUnavailable",
    "ReadUnavailable",
    "TmbxClient",
    "UndoUnavailable",
]
