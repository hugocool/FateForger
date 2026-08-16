# src/memory/mcp_server.py
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from mcp.server.fastmcp import FastMCP

from memory.models import Channel
from memory.service import MemoryService

INSTRUCTIONS = """\
Durable memory for a personal scheduling agent.

memory_observe records something the user said; the server decides what it
means (which anchors, durable rule or today's fact, interaction chatter,
restatement of a known rule) and files it. memory_get_active_constraints
returns the durable rules applying on a day — structural filtering only, so
expect every applicable rule rather than a semantically ranked subset.\
"""


def build_server(service: MemoryService) -> FastMCP:
    """The MCP face of a MemoryService.

    A factory rather than module state so tests bind a stub judge and a tmp
    database, and so a host can run several isolated servers.
    """
    mcp = FastMCP(name="memory", instructions=INSTRUCTIONS)

    @mcp.tool(name="memory_observe")
    async def memory_observe(
        text: str,
        session_id: str,
        channel: str = "planning",
        observed_at: str | None = None,
    ) -> dict:
        """Record one user statement and file it into memory."""
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

    return mcp


def main() -> None:
    from memory.openrouter_judge import openrouter_judge_from_env

    db_path = os.environ.get("MEMORY_DB_PATH", "data/memory.db")
    service = MemoryService(db_path, openrouter_judge_from_env())
    build_server(service).run()  # stdio transport


if __name__ == "__main__":
    main()
