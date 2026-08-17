# src/memory/mcp_server.py
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Callable

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.server.session import ServerSession
from mcp.shared.exceptions import McpError

from memory.models import Channel
from memory.sampling import SamplingDeclined, SamplingJudge, SamplingUnavailable
from memory.service import MemoryService

INSTRUCTIONS = """\
Durable memory for a personal scheduling agent.

memory_observe records something the user said; the server decides what it
means (which anchors, durable rule or today's fact, interaction chatter,
restatement of a known rule) and files it. It needs the sampling capability:
the server has no model of its own and asks yours.

memory_get_active_constraints returns the durable rules applying on a day.
It never samples — structural filtering only, so it is fast, repeatable, and
safe to call inside a planning loop. Expect every applicable rule rather than
a semantically ranked subset. memory_get_faded_constraints returns the rules
withheld because nothing has re-stated them lately; they are candidates for
review, not for planning.\
"""

# Judgements are small JSON objects with a short rationale. Generous enough
# that no answer is truncated, small enough that a runaway reply stops early —
# truncation would arrive as unparseable JSON rather than a wrong judgement.
MAX_TOKENS = 1024


class McpSampler:
    """Borrows the connected host's model via `sampling/createMessage`.

    Holds no API key and names no model: whoever drives this server decides
    what answers. The session is resolved per call rather than stored, because
    it belongs to the request in flight — one server instance serves many.
    """

    def __init__(
        self,
        session_provider: Callable[[], ServerSession],
        *,
        max_tokens: int = MAX_TOKENS,
        temperature: float = 0.0,
    ) -> None:
        self._session_provider = session_provider
        self._max_tokens = max_tokens
        # Pinned to 0. These are extraction judgements in the write path, and
        # a sampled judgement makes a store's contents depend on luck: two
        # identical statements can canonicalise differently. A host is free to
        # ignore this — the MCP spec makes every sampling parameter advisory —
        # so it is a request, not a guarantee.
        self._temperature = temperature

    async def complete(self, system: str, user: str) -> str:
        try:
            session = self._session_provider()
        except ValueError as exc:
            # No request in flight. Reached only by calling the write path
            # outside a tool invocation — a wiring error, and a loud one.
            raise SamplingUnavailable(
                "no MCP request in flight, so there is no host session to "
                "sample from; memory_observe must be driven by a tool call"
            ) from exc

        params = session.client_params
        capabilities = params.capabilities if params else None
        if capabilities is None or capabilities.sampling is None:
            client = params.clientInfo.name if params and params.clientInfo else "unknown"
            raise SamplingUnavailable(
                f"host {client!r} did not declare the sampling capability; "
                f"this server has no model of its own, so nothing can be "
                f"recorded until the host enables sampling"
            )

        try:
            result = await session.create_message(
                messages=[
                    types.SamplingMessage(
                        role="user", content=types.TextContent(type="text", text=user)
                    )
                ],
                system_prompt=system,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
        except McpError as exc:
            # The host answered the request with an error — a user rejecting
            # the sampling prompt lands here. Recoverable in principle, so it
            # is a distinct type, but still raised: the caller is entitled to
            # know its statement was not recorded.
            raise SamplingDeclined(f"host declined the sampling request: {exc}") from exc

        if result.stopReason == "maxTokens":
            raise ValueError(
                f"host truncated the reply at {self._max_tokens} tokens; the "
                f"judgement is incomplete and must not be parsed"
            )
        if result.content.type != "text":
            raise ValueError(
                f"host returned {result.content.type!r} content for a question "
                f"that has only a textual answer"
            )
        return result.content.text


def register_tools(mcp: FastMCP, service: MemoryService) -> None:
    """Bind a service's verbs onto an MCP server."""

    @mcp.tool(name="memory_observe")
    async def memory_observe(
        text: str,
        session_id: str,
        channel: str = "planning",
        observed_at: str | None = None,
    ) -> dict:
        """Record one user statement and file it into memory.

        `session_id` scopes duplicate detection: pass the same value for every
        statement in one conversation, and a fresh value per conversation. A
        value that changes per call makes dedup a no-op; a constant value
        makes the candidate list grow without bound.
        """
        when = (
            datetime.fromisoformat(observed_at)
            if observed_at
            else datetime.now(timezone.utc)
        )
        outcome = await service.observe(
            text,
            channel=Channel(channel),
            session_id=session_id,
            observed_at=when,
        )
        return outcome.model_dump(mode="json")

    @mcp.tool(name="memory_get_active_constraints")
    def memory_get_active_constraints(
        day: str, stage: str | None = None
    ) -> list[dict]:
        """Durable rules applying on `day` (YYYY-MM-DD). No model call."""
        views = service.get_active_constraints(date.fromisoformat(day), stage)
        return [v.model_dump(mode="json") for v in views]

    @mcp.tool(name="memory_get_faded_constraints")
    def memory_get_faded_constraints(
        day: str, stage: str | None = None
    ) -> list[dict]:
        """Rules withheld on `day` because they have gone stale. No model call.

        Fading is how the server stops serving rules nobody has restated. That
        is silent deletion unless someone can see what was withheld — which is
        what this is for. Surface these for review; do not plan against them.
        """
        views = service.get_faded_constraints(date.fromisoformat(day), stage)
        return [v.model_dump(mode="json") for v in views]


def build_server(service: MemoryService) -> FastMCP:
    """The MCP face of an already-built MemoryService.

    A factory rather than module state so tests bind a stub judge and a tmp
    database, and so a host can run several isolated servers.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)
    register_tools(mcp, service)
    return mcp


def build_sampling_server(db_path: str) -> FastMCP:
    """A server that judges with the host's model rather than its own.

    The default, and the reason this package needs no API key: the server is
    built before the service so the sampler can resolve the live request's
    session on every call.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)
    sampler = McpSampler(lambda: mcp.get_context().session)
    register_tools(mcp, MemoryService(db_path, SamplingJudge(sampler)))
    return mcp


def main() -> None:
    db_path = os.environ.get("MEMORY_DB_PATH", "data/memory.db")
    build_sampling_server(db_path).run()  # stdio transport


if __name__ == "__main__":
    main()
